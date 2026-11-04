"""Loopback gateway with atomic inference admission and idle shutdown.

All advertised Fable requests pass here. Closing admission and checking active
requests use the same lock, so shutdown cannot race a newly accepted request.
"""
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FableGateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, backend_port, *, omlx=None):
        self.backend_port = backend_port
        self.omlx = omlx
        self.started = False
        self.gate = threading.Lock()
        self.active = 0
        self.draining = False
        self.stop_event = threading.Event()
        super().__init__(address, _Handler)

    def admit(self):
        with self.gate:
            if self.draining:
                return False
            self.active += 1
            return True

    def finish(self):
        with self.gate:
            self.active -= 1

    def quiesce(self):
        with self.gate:
            if self.active:
                return False
            self.draining = True
            self.stop_event.set()
            return True


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _json(self, status, doc):
        body = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.server.omlx is not None:
            from .director_omlx import MODEL, CONTEXT
            if self.path == "/health":
                return self._json(200, {"draining": self.server.draining})
            if self.path not in ("/ready", "/review-ready", "/v1/models"):
                return self._json(404, {"error": "Unknown director endpoint"})
            try:
                entry = self.server.omlx.entry()
                ready = self.server.started and not self.server.draining
                if self.path == "/review-ready":
                    ready = ready and entry["loaded"] and not entry["is_loading"]
                if self.path == "/v1/models":
                    settings = entry.get("settings") or {}
                    return self._json(200, {"data": [{"id": MODEL, "object": "model",
                        "owned_by": "omlx", "context_length": min(CONTEXT,
                            settings.get("max_context_window") or entry["model_context_length"]),
                        "vision": entry.get("model_type") == "vlm"}]})
                return self._json(200 if ready else 503, {"ready": ready})
            except (OSError, ValueError, KeyError, StopIteration):
                return self._json(503, {"error": "oMLX director unavailable"})
        self._forward()

    def do_POST(self):
        if self.path == "/shutdown":
            if not self.server.quiesce():
                return self._json(409, {"error": "En modellforespørsel arbeider fortsatt"})
            return self._json(200, {"draining": True})
        if self.server.omlx is not None and self.path != "/v1/chat/completions":
            return self._json(404, {"error": "Unknown director endpoint"})
        self._forward()

    def _forward(self):
        inference = self.command == "POST"
        if inference and not self.server.admit():
            return self._json(503, {"error": "Fable frigjør minnet; prøv igjen"})
        timeout = 2 if self.command == "GET" and self.path in ("/health", "/ready") else 1200
        connection = http.client.HTTPConnection("127.0.0.1", self.server.backend_port, timeout=timeout)
        headers_sent = False
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > 32 * 1024 * 1024 or self.headers.get("Transfer-Encoding"):
                return self._json(413, {"error": "Invalid request length"})
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "connection", "transfer-encoding")}
            if self.server.omlx is not None:
                from .director_omlx import MODEL
                if self.headers.get("Origin"):
                    return self._json(403, {"error": "Browser origins are not accepted"})
                if not isinstance(doc := json.loads(body or b"{}"), dict) or doc.get("model") != MODEL:
                    return self._json(400, {"error": "Unknown director model"})
                # OpenAI clients commonly omit effort when their UI says off.
                # Preserve explicit tiers; the checkpoint defaults to thinking.
                if doc.get("reasoning_effort") is None and not (doc.get("chat_template_kwargs") or {}).get("enable_thinking"):
                    doc["reasoning_effort"] = "none"
                    body = json.dumps(doc).encode()
                    headers = {k: v for k, v in headers.items() if k.lower() != "content-length"}
                    headers["Content-Length"] = str(len(body))
                headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
                headers["Authorization"] = "Bearer " + self.server.omlx.api_key()
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in ("connection", "transfer-encoding"):
                    self.send_header(key, value)
            self.end_headers()
            headers_sent = True
            connected = True
            while chunk := response.read1(16384):
                if connected:
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        # Drain the owned backend request even if the caller
                        # disconnects. It stays active until inference ends.
                        connected = False
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if not headers_sent:
                self._json(503, {"error": "Director backend unavailable"})
        finally:
            connection.close()
            if inference:
                self.server.finish()

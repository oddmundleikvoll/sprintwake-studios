from __future__ import annotations

import json
from pathlib import Path
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

import sys
sys.path.insert(0, str(Path(__file__).parents[1]))

from backend import Backend, BackendError
from backend_host import write_ready


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upload = b""

    def _reply(self, body: bytes, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/ping":
            return self._reply(b'{"ok":true}')
        if self.path == "/media/file.txt":
            return self._reply(b"downloaded", content_type="text/plain")
        return self._reply(b'{"error":"missing"}', 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        FixtureHandler.upload = self.rfile.read(length)
        if self.path in {"/api/chat","/api/raven/chat"}:
            body = b'{"type":"progress","text":"working"}\n{"type":"result","result":{"ok":true}}\n'
            return self._reply(body, content_type="application/x-ndjson")
        return self._reply(b'{"uploaded":true}', 201)

    def log_message(self, *_):
        pass


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.backend = Backend(Path(self.tmp.name), engine_root=Path(self.tmp.name))
        self.backend.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def test_json_transport_and_path_guard(self):
        self.assertEqual(self.backend.get("/api/ping"), {"ok": True})
        with self.assertRaises(BackendError):
            self.backend.get("https://example.com/api/ping")
        with self.assertRaises(BackendError):
            self.backend.get("/api/../secret")

    def test_upload_and_atomic_download(self):
        source = Path(self.tmp.name) / "ref.png"
        source.write_bytes(b"pixels")
        self.backend.upload("demo", source)
        self.assertEqual(FixtureHandler.upload, b"pixels")
        target = Path(self.tmp.name) / "nested" / "result.txt"
        self.assertEqual(self.backend.download("/media/file.txt", target), target)
        self.assertEqual(target.read_bytes(), b"downloaded")

    def test_chat_ndjson_progress_and_result(self):
        events = []
        self.assertEqual(self.backend.chat({"slug": "demo", "message": "hello"}, events.append), {"ok": True})
        self.assertEqual([e["type"] for e in events], ["progress", "result"])

    def test_expert_raven_endpoint_uses_same_stream_transport(self):
        events=[]
        self.assertEqual(self.backend.raven_chat({"slug":None,"message":"Lag en film"},events.append),{"ok":True})
        self.assertEqual(events[-1]["type"],"result")

    def test_bounded_authoring_adapts_expert_actions(self):
        self.backend.raven_chat=lambda payload:{"actions":[{"tool":"edit_project","fields":{"sceneDescription":"Film"}},{"tool":"add_character","name":"Ada"},{"tool":"add_shot","title":"Døren","prompt":"A door opens."}]}
        result=self.backend.authoring_chat({"message":"set_board_fields"})
        self.assertEqual(result["actions"][0]["tool"],"set_board_fields")
        self.assertEqual(result["actions"][1]["character"]["name"],"Ada")
        self.assertEqual(result["actions"][2]["shot"]["title"],"Døren")

    def test_chat_stream_error_is_not_hidden(self):
        old = FixtureHandler.do_POST
        def stream_error(handler):
            if handler.path == "/api/chat":
                body = b'{"type":"error","error":"model unavailable"}\n'
                return handler._reply(body, content_type="application/x-ndjson")
            return old(handler)
        FixtureHandler.do_POST = stream_error
        try:
            with self.assertRaisesRegex(BackendError, "model unavailable"):
                self.backend.chat({"slug": "demo", "message": "hello"})
        finally:
            FixtureHandler.do_POST = old

    def test_chat_incomplete_stream_raises(self):
        old = FixtureHandler.do_POST
        def incomplete(handler):
            if handler.path == "/api/chat":
                return handler._reply(b'{"type":"progress"}\n', content_type="application/x-ndjson")
            return old(handler)
        FixtureHandler.do_POST = incomplete
        try:
            with self.assertRaisesRegex(BackendError, "without a result"):
                self.backend.chat({"slug": "demo", "message": "hello"})
        finally:
            FixtureHandler.do_POST = old

    def test_close_refuses_short_timeout(self):
        with self.assertRaises(ValueError):
            self.backend.close(timeout=10)

    def test_child_environment_prepends_engine_tools(self):
        env = self.backend._child_environment()
        self.assertEqual(env["PATH"].split(":", 1)[0], str(self.backend.engine_root / "local" / "bin"))
        self.assertEqual(env["PYTHONPATH"].split(":", 1)[0], str(self.backend.engine_root))

    def test_ready_file_is_atomic_json(self):
        path = Path(self.tmp.name) / "System" / "ready.json"
        write_ready(path, {"baseUrl": "http://127.0.0.1:1"})
        self.assertEqual(json.loads(path.read_text())["baseUrl"], "http://127.0.0.1:1")
        self.assertFalse(list(path.parent.glob(".*.tmp")))


if __name__ == "__main__":
    unittest.main()

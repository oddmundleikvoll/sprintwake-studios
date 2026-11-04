"""Splash lifecycle tests use an HTTP stub and never start a model."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from server.splash import SplashLLM


class SplashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        cli = root / "splash-m1"
        cli.write_text("stub")
        cache = root / "cache"
        cache.mkdir()
        self.ready = False
        self.profile_running = False
        self.release = False
        self.calls: list[tuple[str, str, dict | None]] = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _send(self, value, status=200):
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                parent.calls.append(("GET", self.path, None))
                if self.path == "/ready":
                    self._send({}, 200 if parent.ready else 503)
                elif self.path == "/status":
                    parent_status = {
                        "ready": parent.ready,
                        "model": "incoai/Qwen3.8-27B-Splash",
                        "weights": {"released": parent.release, "idle_release_seconds": 15},
                        "scheduler": {k: 0 for k in (
                            "queued", "waiting_resources", "waiting_prefix", "prefilling",
                            "decoding", "waiting_mask")},
                        "frontend": {"active": 0, "waiting": 0},
                        "transport": {"pending": 0, "recovering": False},
                    }
                    self._send(parent_status)
                elif self.path == "/v1/models":
                    self._send({"data": [{"id": "incoai/Qwen3.8-27B-Splash"}]})
                elif self.path == "/api/status":
                    self._send({"statuses": [{
                        "profile": "qwen38-splash",
                        "running": parent.profile_running,
                        "request_model": "incoai/Qwen3.8-27B-Splash",
                        "missing_artifacts": [],
                    }]})
                else:
                    self._send({}, 404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                body = json.loads(raw) if raw else None
                parent.calls.append(("POST", self.path, body))
                if self.path == "/api/start":
                    parent.profile_running = True
                    parent.ready = True
                    self._send({"started": "qwen38-splash"})
                elif self.path == "/v1/chat/completions":
                    self._send({"choices": [{"message": {"content": "ok"}}]})
                else:
                    self._send({}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        base = f"http://127.0.0.1:{self.server.server_port}"
        self.splash = SplashLLM(
            url=base,
            controller_url=base,
            cli_path=cli,
            cache_dir=cache,
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_health_is_passive_and_never_probes_models(self):
        ok, message = self.splash.health()
        self.assertTrue(ok, message)
        self.assertIn("on demand", message)
        self.assertFalse(any(path == "/v1/models" for _, path, _ in self.calls))
        self.assertFalse(any(method == "POST" for method, _, _ in self.calls))

    def test_start_is_profile_scoped_and_complete_sets_reasoning_none(self):
        self.assertEqual(self.splash.complete("system", "hello", timeout=2), "ok")
        starts = [(method, path, body) for method, path, body in self.calls if path == "/api/start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0][2], {"profile": "qwen38-splash"})
        request = next(body for method, path, body in self.calls if path == "/v1/chat/completions")
        self.assertEqual(request["reasoning_effort"], "none")
        self.assertNotIn("chat_template_kwargs", request)

    def test_release_waits_for_own_idle_state_without_stop_request(self):
        self.ready = True
        self.release = False

        def release_later():
            time.sleep(0.08)
            self.release = True

        threading.Thread(target=release_later, daemon=True).start()
        self.assertTrue(self.splash.release_memory(timeout=1))
        self.assertFalse(any(method == "POST" for method, _, _ in self.calls))

    def test_release_timeout_does_not_stop_anything(self):
        self.ready = True
        with self.assertRaises(TimeoutError):
            self.splash.release_memory(timeout=0.05)
        self.assertFalse(any(method == "POST" for method, _, _ in self.calls))

    def test_missing_profile_artifact_is_not_reported_ready(self):
        def missing(request):
            return {"statuses": [{"profile": "qwen38-splash", "running": False,
                                    "request_model": "incoai/Qwen3.8-27B-Splash",
                                    "missing_artifacts": ["weights"]}]}
        # Replace the handler's response without changing the live server.
        original = self.splash._controller_status
        self.splash._controller_status = lambda timeout=4.0: missing(None)
        ok, message = self.splash.health()
        self.splash._controller_status = original
        self.assertFalse(ok)
        self.assertIn("missing", message)

    def test_running_profile_is_waited_for_without_second_start(self):
        self.ready = False
        self.profile_running = True
        threading.Timer(0.08, setattr, args=(self, "ready", True)).start()
        self.splash.ensure_ready(timeout=1)
        self.assertEqual(sum(path == "/api/start" for _, path, _ in self.calls), 0)

    def test_stopped_profile_after_idle_release_is_accepted(self):
        self.ready = True
        self.release = False
        original_status = self.splash._status
        self.splash._status = lambda timeout=4.0: (_ for _ in ()).throw(
            OSError("frontend stopped"))
        self.splash._controller_status = lambda timeout=4.0: {
            "statuses": [{"profile": "qwen38-splash", "running": False}]
        }
        try:
            self.assertTrue(self.splash.release_memory(timeout=1))
        finally:
            self.splash._status = original_status


if __name__ == "__main__":
    unittest.main()

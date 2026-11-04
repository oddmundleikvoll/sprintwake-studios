"""Fable lifecycle: real HTTP contracts, without loading model weights."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server.fable import FableLLM, MODEL, PROFILE
from server.llm import build_one


class FableTests(unittest.TestCase):
    def setUp(self):
        self.running = False
        self.processing = False
        self.context = 262144
        self.vision = True
        self.calls = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send(self, doc, status=200):
                data = json.dumps(doc).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                parent.calls.append((self.path, None))
                if self.path == "/api/status":
                    self.send({"statuses": [{"profile": "unrelated", "running": True},
                        {"profile": PROFILE, "running": parent.running, "request_model": MODEL}]})
                elif self.path == "/ready":
                    self.send({"status": "ready"}, 200 if parent.running else 503)
                elif self.path == "/review-ready":
                    self.send({"ready": parent.running}, 200 if parent.running else 503)
                elif self.path == "/v1/models":
                    self.send({"data": [{"id": MODEL, "context_length": parent.context,
                                          "vision": parent.vision}]})
                elif self.path == "/slots":
                    self.send([{"is_processing": parent.processing}])
                else:
                    self.send({}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                parent.calls.append((self.path, body))
                if self.path == "/api/start":
                    parent.running = True
                    self.send({})
                elif self.path == "/api/stop":
                    if parent.processing:
                        return self.send({"error": "en modellforespørsel arbeider fortsatt"}, 409)
                    parent.running = False
                    self.send({})
                elif self.path == "/v1/chat/completions":
                    self.send({"choices": [{"message": {"content": "Filmplan"}}]})
                else:
                    self.send({}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.temp = tempfile.TemporaryDirectory()
        artifact = Path(self.temp.name) / "artifact"
        artifact.write_text("stub")
        url = f"http://127.0.0.1:{self.server.server_port}"
        self.llm = FableLLM(url=url, controller_url=url, cli_path=artifact, cache_dir=artifact)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_completions_reuse_named_profile_until_video_handoff(self):
        self.assertEqual(self.llm.complete("Regi", "Planlegg"), "Filmplan")
        self.assertTrue(self.running)
        self.assertEqual(self.llm.complete("Regi", "Fortsett"), "Filmplan")
        self.llm.release_memory()
        self.assertFalse(self.running)
        mutations = [(p, b) for p, b in self.calls if p.startswith("/api/") and b is not None]
        self.assertEqual(mutations, [("/api/start", {"profile": PROFILE}),
                                     ("/api/stop", {"profile": PROFILE})])

    def test_context_and_vision_are_validated_before_generation(self):
        for context, vision in ((8192, True), (262144, False)):
            self.context, self.vision, self.running = context, vision, False
            self.calls.clear()
            with self.assertRaises(RuntimeError):
                self.llm.complete("", "")
            self.assertNotIn("/v1/chat/completions", [p for p, _ in self.calls])
            self.assertFalse(self.running)

    def test_foreign_request_is_never_cancelled_for_video(self):
        self.running = self.processing = True
        with self.assertRaises(HTTPError):
            self.llm.release_memory()
        self.assertTrue(self.running)

    def test_stopped_metadata_does_not_skip_named_stop(self):
        self.llm.release_memory()
        self.assertIn(("/api/stop", {"profile": PROFILE}), self.calls)

    def test_pending_start_cleanup_uses_one_deadline_for_status_stop_and_ack(self):
        clock = [100.0]
        timeouts = []
        def status(timeout=4.0):
            timeouts.append(("status", timeout))
            if len(timeouts) == 1:
                if timeout < 5:
                    raise TimeoutError("controller still starting")
                clock[0] += 5
            return {"statuses": [{"profile": PROFILE, "request_model": MODEL,
                                  "running": len(timeouts) == 1}]}
        def stop(url, **kwargs):
            self.assertTrue(url.endswith("/api/stop"))
            self.assertEqual(kwargs["payload"], {"profile": PROFILE})
            timeouts.append(("stop", kwargs["timeout"]))
            clock[0] += 3
            return {}
        with patch("server.fable.time", SimpleNamespace(monotonic=lambda: clock[0])), \
             patch.object(self.llm, "_controller_status", side_effect=status), \
             patch.object(self.llm, "_json_response", side_effect=stop), \
             patch.object(self.llm, "_ready", return_value=False):
            self.assertTrue(self.llm.release_memory(timeout=12))
        self.assertEqual(timeouts, [("status", 12), ("stop", 7), ("status", 4)])

    def test_review_readiness_never_starts_a_cold_profile(self):
        self.assertFalse(self.llm.review_ready())
        self.assertFalse(self.running)
        self.assertFalse(any(path == "/api/start" for path, _ in self.calls))
        self.running = True
        self.assertTrue(self.llm.review_ready())

    def test_cleanup_timeout_names_the_unconfirmed_handoff(self):
        with patch.object(self.llm, "_controller_status", side_effect=TimeoutError("timed out")):
            with self.assertRaisesRegex(TimeoutError, "minnefrigjøring"):
                self.llm.release_memory(timeout=12)
        self.assertNotIn(("/api/stop", {"profile": PROFILE}), self.calls)

    def test_serial_controller_finishes_timed_out_start_before_named_cleanup(self):
        entered, finish = threading.Event(), threading.Event()
        class StartingHandler(self.server.RequestHandlerClass):
            def send(self, *args, **kwargs):
                try:
                    return super().send(*args, **kwargs)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def do_POST(self):
                if self.path == "/api/start":
                    entered.set()
                    finish.wait(8)
                super().do_POST()
        controller = HTTPServer(("127.0.0.1", 0), StartingHandler)
        thread = threading.Thread(target=controller.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{controller.server_port}"
        service = FableLLM(url=url, controller_url=url, cli_path=self.llm.cli_path, cache_dir=self.llm.cache_dir)
        timer = threading.Timer(4.3, finish.set)
        try:
            request = Request(url + "/api/start", data=json.dumps({"profile": PROFILE}).encode())
            with self.assertRaises(TimeoutError):
                urlopen(request, timeout=.05)
            self.assertTrue(entered.is_set())
            timer.start()
            self.assertTrue(service.release_memory(timeout=8))
            self.assertFalse(self.running)
            self.assertEqual([body for path, body in self.calls if path == "/api/stop"], [{"profile": PROFILE}])
        finally:
            finish.set()
            timer.cancel()
            controller.shutdown()
            controller.server_close()
            thread.join()

    def test_cleanup_runs_after_request_failure_and_cancellation(self):
        self.running = True
        with patch("server.splash.OpenAICompatLLM.complete", side_effect=RuntimeError("request failed")):
            with self.assertRaisesRegex(RuntimeError, "request failed"):
                self.llm.complete("", "")
        self.assertFalse(self.running)
        self.llm.release_memory(cancel=lambda: True)

    def test_settings_select_fable_and_keep_output_budget(self):
        service = build_one({"id": "splash-local", "kind": "fable", "thinking": "medium"})
        self.assertIsInstance(service, FableLLM)
        body = json.loads(service._dumps({"max_tokens": 2400}))
        self.assertEqual(body["max_tokens"], 2400)
        self.assertEqual(body["reasoning_effort"], "medium")
        service.thinking = "none"
        body = json.loads(service._dumps({}))
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["top_k"], 20)


if __name__ == "__main__":
    unittest.main()

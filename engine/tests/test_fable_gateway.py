"""Atomic idle shutdown and phase admission regressions."""
import importlib.util
import json
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server.fable_gateway import FableGateway
from server.director_omlx import MODEL, CONTEXT
from server.orchestrator import _gpu_start


class GatewayTests(unittest.TestCase):
    def test_omlx_gateway_scopes_credentials_model_and_passive_review(self):
        from types import SimpleNamespace
        requests = []
        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                requests.append((self.path, self.headers.get("Authorization"),
                                 json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                body = b'{"choices":[{"message":{"content":"OK"}}]}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        entry = {"loaded": False, "is_loading": False, "model_context_length": CONTEXT,
                 "model_type": "vlm", "settings": {"max_context_window": CONTEXT}}
        client = SimpleNamespace(entry=lambda: entry, api_key=lambda: "private-omlx-test-key")
        backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        gateway = FableGateway(("127.0.0.1", 0), backend.server_port, omlx=client)
        gateway.started = True
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (backend, gateway)]
        for thread in threads:
            thread.start()
        url = f"http://127.0.0.1:{gateway.server_port}"
        try:
            with urlopen(url + "/v1/models") as response:
                model = json.load(response)["data"][0]
            self.assertEqual((model["id"], model["context_length"], model["vision"]), (MODEL, CONTEXT, True))
            with self.assertRaises(HTTPError):
                urlopen(url + "/review-ready")
            entry["loaded"] = True
            with urlopen(url + "/review-ready") as response:
                self.assertTrue(json.load(response)["ready"])
            for path, model_id, expected in (("/admin/api/reload", MODEL, 404),
                                             ("/v1/chat/completions", "foreign", 400)):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(url + path, data=json.dumps({"model": model_id}).encode()))
                self.assertEqual(error.exception.code, expected)
            self.assertEqual(requests, [])
            with urlopen(Request(url + "/v1/chat/completions", data=json.dumps({"model": MODEL}).encode(),
                                 headers={"Authorization": "Bearer desktop-test-key"})) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(requests, [("/v1/chat/completions", "Bearer private-omlx-test-key",
                                        {"model": MODEL, "reasoning_effort": "none"})])
            with urlopen(Request(url + "/v1/chat/completions", data=json.dumps(
                    {"model": MODEL, "reasoning_effort": "medium"}).encode())) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(requests[-1][2]["reasoning_effort"], "medium")
            with urlopen(Request(url + "/v1/chat/completions", data=json.dumps(
                    {"model": MODEL, "chat_template_kwargs": {"enable_thinking": True}}).encode())) as response:
                self.assertEqual(response.status, 200)
            self.assertNotIn("reasoning_effort", requests[-1][2])
        finally:
            for server in (gateway, backend):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join()

    def test_manual_start_cannot_cross_video_admission(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "status.json"
            ready = Path(temp) / "waiting"
            state.write_text('{"busy":false}')
            code = textwrap.dedent('''
                import importlib.util, json, sys
                from pathlib import Path
                from unittest.mock import patch
                from server.gpu_handoff import gpu_admission
                spec=importlib.util.spec_from_file_location("launcher", "local/start_fable.py")
                module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                Path(sys.argv[2]).write_text("waiting")
                with gpu_admission():
                    with patch.object(module, "_local_json", return_value=json.loads(Path(sys.argv[1]).read_text())), patch.object(module, "prepare_language_memory", side_effect=AssertionError("weights must not load")):
                        try: module.start()
                        except RuntimeError as exc:
                            assert "lager bilde/video" in str(exc)
                        else: raise AssertionError("manual start crossed video admission")
            ''')
            children = []
            class Video:
                _start_lock = threading.RLock()
                _llm_active = 0
                def before_gpu_start(self):
                    child = subprocess.Popen([sys.executable, "-c", code, str(state), str(ready)], cwd=root)
                    children.append(child)
                    deadline = time.monotonic() + 5
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    if not ready.exists() or child.poll() is not None:
                        raise AssertionError("manual-start probe did not reach the admission lock")
                @_gpu_start
                def start(self):
                    state.write_text('{"busy":true,"operation":"render"}')
            try:
                Video().start()
                self.assertEqual(children[0].wait(timeout=5), 0)
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill(); child.wait()

    def test_stop_before_gateway_exists_only_terminates_owned_launcher(self):
        path = Path(__file__).resolve().parents[1] / "local/stop_fable.py"
        spec = importlib.util.spec_from_file_location("stop_fable_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        command = f"/python {path.with_name('start_fable.py')}"
        with patch.object(Path, "read_text", return_value="1234"), \
             patch.object(module.subprocess, "check_output", return_value=command), \
             patch.object(module.os, "kill", side_effect=[None, ProcessLookupError]) as kill:
            module.stop_starting_launcher()
            self.assertEqual(kill.call_args_list[0].args, (1234, module.signal.SIGTERM))
        with patch.object(Path, "read_text", return_value="1234"), \
             patch.object(module.subprocess, "check_output", return_value="python /unrelated.py"), \
             patch.object(module.os, "kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "Ukjent"):
                module.stop_starting_launcher()
            kill.assert_not_called()

    def test_http_stream_drains_before_atomic_shutdown(self):
        entered, release = threading.Event(), threading.Event()
        payload = b'data: {"choices":[{"delta":{"content":"Filmplan"}}]}\n\ndata: [DONE]\n\n'
        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(payload[:12])
                self.wfile.flush()
                entered.set()
                release.wait(5)
                self.wfile.write(payload[12:])
        backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        gateway = FableGateway(("127.0.0.1", 0), backend.server_port)
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (backend, gateway)]
        for t in threads:
            t.start()
        url = f"http://127.0.0.1:{gateway.server_port}"
        result = []
        def request():
            with urlopen(Request(url + "/v1/chat/completions", data=b"{}"), timeout=5) as response:
                result.append(response.read())
        client = threading.Thread(target=request)
        client.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url + "/shutdown", data=b"{}"), timeout=2)
            self.assertEqual(error.exception.code, 409)
            release.set()
            client.join(5)
            self.assertEqual(result, [payload])
            with urlopen(Request(url + "/shutdown", data=b"{}"), timeout=2) as response:
                self.assertTrue(json.load(response)["draining"])
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url + "/v1/chat/completions", data=b"{}"), timeout=2)
            self.assertEqual(error.exception.code, 503)
        finally:
            release.set()
            client.join(5)
            for server in (gateway, backend):
                server.shutdown()
                server.server_close()
            for t in threads:
                t.join()

    def test_dead_backend_returns_http_503_for_inference(self):
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            port = closed.getsockname()[1]
        server = FableGateway(("127.0.0.1", 0), port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", data=b"{}"), timeout=2)
            self.assertEqual(error.exception.code, 503)
            self.assertIn("error", json.load(error.exception))
            self.assertEqual(server.active, 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_shutdown_cannot_cross_an_admitted_request(self):
        server = FableGateway(("127.0.0.1", 0), 1)
        try:
            self.assertTrue(server.admit())
            self.assertFalse(server.quiesce())
            self.assertFalse(server.draining)
            server.finish()
            self.assertTrue(server.quiesce())
            self.assertFalse(server.admit())
        finally:
            server.server_close()

    def test_concurrent_shutdown_and_admission_have_one_winner(self):
        for _ in range(50):
            server = FableGateway(("127.0.0.1", 0), 1)
            barrier = threading.Barrier(2)
            admitted = []
            def request():
                barrier.wait()
                admitted.append(server.admit())
            thread = threading.Thread(target=request)
            thread.start()
            barrier.wait()
            stopped = server.quiesce()
            thread.join()
            try:
                self.assertNotEqual(stopped, admitted[0])
            finally:
                server.server_close()

    def test_manual_start_refuses_refine_rendering(self):
        path = Path(__file__).resolve().parents[1] / "local/start_fable.py"
        spec = importlib.util.spec_from_file_location("start_fable_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        status = {"busy": True, "operation": "refine", "languageActive": 0,
                  "refine": {"state": "rendering"}}
        with patch.object(module, "_local_json", return_value=status), \
             patch.object(module, "prepare_language_memory") as prepare:
            with self.assertRaisesRegex(RuntimeError, "lager bilde/video"):
                module.main()
            prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()

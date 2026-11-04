"""HTTP-level qualification for the explicit Comfy API adapter.

The server below is deliberately tiny but speaks the real endpoints used by
WorkflowClient.  No Comfy process, model, keychain, or network service is
involved.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from server.comfy_workflow import ComfyWorkflowError, WorkflowClient
from server.backends.base import ShotPaths
from server.backends.comfy_workflow_backend import ComfyWorkflowBackend


class FakeComfy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state = None

    def log_message(self, *_):
        pass

    def _json(self, value, status=200):
        raw = json.dumps(value).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def _bytes(self, value, content_type):
        self.send_response(200); self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(value))); self.end_headers(); self.wfile.write(value)

    def do_GET(self):
        s = self.state; path = urlsplit(self.path).path
        if path == "/redirect":
            s.requests.append(("GET", path, self.headers.get("Authorization")))
            self.send_response(302); self.send_header("Location", "/queue"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if path == "/object_info": s.requests.append(("GET", path, self.headers.get("Authorization"))); return self._json(s.object_info)
        if path == "/queue": s.requests.append(("GET", path, self.headers.get("Authorization"))); return self._json({"queue_running": [], "queue_pending": s.foreign_queue})
        if path.startswith("/history/"):
            ident = path.rsplit("/", 1)[-1]; s.requests.append(("GET", path, self.headers.get("Authorization")))
            return self._json({ident: s.history.get(ident, {"status": {"status_str": "running"}})})
        if path == "/view":
            query = parse_qs(urlsplit(self.path).query); name = query.get("filename", [""])[0]
            s.requests.append(("GET", path, self.headers.get("Authorization"))); return self._bytes(s.files[name], "image/png" if name.endswith(".png") else "video/mp4")
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        s = self.state; path = urlsplit(self.path).path; length = int(self.headers.get("Content-Length", 0)); body = self.rfile.read(length)
        auth = self.headers.get("Authorization")
        if path == "/prompt":
            payload = json.loads(body); ident = f"job-{len(s.prompts) + 1}"; s.prompts.append(payload); s.requests.append(("POST", path, auth, payload))
            s.history[ident] = {"status": {"completed": True, "status_str": "success"}, "outputs": {"1": {s.output_key: [{"filename": s.output_name, "subfolder": "", "type": "output"}]}}}
            if s.never_finishes: s.history[ident] = {"status": {"status_str": "running"}}
            return self._json({"prompt_id": ident})
        if path == "/queue":
            payload = json.loads(body); s.deletes.append(payload); s.requests.append(("POST", path, auth, payload)); return self._json({})
        if path == "/upload/image":
            s.uploads.append((auth, body)); s.requests.append(("POST", path, auth)); return self._json({"name": "uploaded-reference.png"})
        self._json({"error": "not found"}, 404)


class Server:
    def __init__(self):
        self.state = type("State", (), {})(); s = self.state
        s.object_info = {"SaveImage": {}, "SaveVideo": {}}; s.requests = []; s.prompts = []; s.history = {}; s.files = {}
        s.uploads = []; s.deletes = []; s.foreign_queue = [{"prompt": "foreign"}]; s.output_key = "images"; s.output_name = "image.png"; s.never_finishes = False
        FakeComfy.state = s; self.http = ThreadingHTTPServer(("127.0.0.1", 0), FakeComfy); self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
    @property
    def url(self): return f"http://127.0.0.1:{self.http.server_port}"
    def close(self): self.http.shutdown(); self.http.server_close(); self.thread.join(timeout=2)


def profile(kind="image", anchors=False):
    bindings = {"prompt": {"node": "1", "input": "prompt"}}
    inputs = {"prompt": ""}
    if anchors: bindings["start_image"] = {"node": "1", "input": "image"}; inputs["image"] = ""
    caps = {"maxReferences": 0, "supportsStartAnchor": anchors}
    if kind == "video": caps.update({"fps": 24, "frameRule": {"kind": "any", "step": 1, "minimum": 1, "maximum": 240, "offset": 0}, "maxFrames": 240, "sizeAlign": 64})
    return {"version": 1, "id": "fake", "label": "Fake", "kind": kind, "workflow": {"1": {"class_type": "SaveVideo" if kind == "video" else "SaveImage", "inputs": inputs}}, "bindings": bindings, "output": {"node": "1", "key": "videos" if kind == "video" else "images"}, "capabilities": caps}


def png_bytes():
    # A tiny valid 1x1 PNG, generated without adding a test-time dependency.
    import base64
    return base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


class ComfyHTTPTests(unittest.TestCase):
    def setUp(self): self.server = Server(); self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
    def tearDown(self): self.server.close(); self.tmp.cleanup()

    def client(self, key="token"): return WorkflowClient(self.server.url, api_key=key, timeout=2, deadline=2)

    def test_preflight_is_get_only_and_reports_missing_node(self):
        p = profile(); self.server.state.object_info = {"Other": {}}
        result = self.client().preflight(p)
        self.assertFalse(result["ok"]); self.assertEqual(result["missingNodes"], ["SaveImage"])
        self.assertTrue(all(item[0] == "GET" for item in self.server.state.requests)); self.assertTrue(all(item[2] == "Bearer token" for item in self.server.state.requests))

    def test_image_run_downloads_canonical_png_and_no_secret_in_recipe(self):
        png = self.root / "source.png"; png.write_bytes(png_bytes()); self.server.state.files["image.png"] = png.read_bytes()
        out = self.client().run(profile(), {"prompt": "hello"}, self.root / "out")
        self.assertEqual(out.outputs[0].name, "image.png"); self.assertEqual((self.root / "out/image.png").read_bytes(), png.read_bytes())
        self.assertTrue((self.root / "out/image.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")); self.assertNotIn(b"token", json.dumps(self.server.state.prompts[0]).encode())

    def test_video_backend_honours_resolution_and_ffprobe_contract(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg/ffprobe unavailable in this test environment")
        target = self.root / "video.mp4"; subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=24", "-frames:v", "4", str(target)], check=True, capture_output=True)
        self.server.state.output_key = "videos"; self.server.state.output_name = "clip.mp4"; self.server.state.files["clip.mp4"] = target.read_bytes()
        backend = ComfyWorkflowBackend(profile("video"), self.server.url, self.root)
        paths = ShotPaths(self.root, self.root / "shot", "shot", data_dir=self.root); spec = backend.prepare({"id": "s", "prompt": "x", "frames": 4, "width": 64, "height": 64}, {}, paths)
        result = backend.run(spec, lambda _: None, lambda: False); self.assertEqual(result.exit_code, 0,result.error); self.assertTrue(backend.validate(spec, result).ok)

    def test_anchor_upload_maps_actual_path_and_redirect_does_not_forward_auth(self):
        img = self.root / "anchor.png"; img.write_bytes(png_bytes()); self.server.state.files["image.png"] = img.read_bytes()
        self.client().run(profile(anchors=True), {"prompt": "x", "start_image": str(img)}, self.root / "out")
        self.assertEqual(len(self.server.state.uploads), 1); self.assertEqual(self.server.state.uploads[0][0], "Bearer token")
        with self.assertRaises(ComfyWorkflowError): self.client()._request("GET", "/redirect")
        self.assertEqual([r for r in self.server.state.requests if r[1] == "/redirect"][0][2], "Bearer token")

    def test_pre_cancel_does_not_post_and_invalid_output_does_not_overwrite(self):
        self.assertTrue(self.client().run(profile(), {"prompt": "x"}, self.root / "out", should_cancel=lambda: True).cancelled)
        self.assertFalse(any(x[0] == "POST" for x in self.server.state.requests))
        old = self.root / "out/image.png"; old.parent.mkdir(); old.write_bytes(b"old"); self.server.state.files["image.png"] = b"not png"
        with self.assertRaises(ComfyWorkflowError):
            self.client().run(profile(), {"prompt": "x"}, self.root / "out")
        self.assertEqual(old.read_bytes(), b"old")

    def test_cancel_deletes_only_owned_job_and_foreign_queue_survives(self):
        self.server.state.never_finishes = True; client = self.client(); client.deadline = .5
        started = time.monotonic()
        with self.assertRaises(ComfyWorkflowError): client.run(profile(), {"prompt": "x"}, self.root / "out", should_cancel=lambda: time.monotonic() - started > .1)
        self.assertEqual(len(self.server.state.deletes), 1); self.assertEqual(self.server.state.deletes[0]["delete"], ["job-1"]); self.assertEqual(self.server.state.foreign_queue, [{"prompt": "foreign"}])
        client.uncertain_path.unlink(missing_ok=True)

    def test_rerender_and_failed_publication_preserve_prior_media_and_recipe(self):
        from unittest.mock import patch
        import server.backends.comfy_workflow_backend as adapter
        backend=ComfyWorkflowBackend(profile(),self.server.url,self.root)
        paths=ShotPaths(self.root,self.root/'shot','shot',data_dir=self.root)
        spec=backend.prepare({'id':'s','prompt':'first'}, {},paths)
        self.server.state.files['image.png']=png_bytes()
        first=backend.run(spec,lambda _:None,lambda:False);self.assertEqual(first.exit_code,0,first.error)
        media=(paths.abs_dir/'image.png').read_bytes();recipe=(paths.abs_dir/'recipe.json').read_bytes()
        original=adapter.os.replace
        def fail_recipe(source,target):
            if str(source).endswith('/recipe.json') and str(target)==str(paths.abs_dir/'recipe.json'):raise OSError('simulated recipe publication failure')
            return original(source,target)
        with patch.object(adapter.os,'replace',side_effect=fail_recipe):
            failed=backend.run(spec,lambda _:None,lambda:False)
        self.assertNotEqual(failed.exit_code,0);self.assertEqual((paths.abs_dir/'image.png').read_bytes(),media);self.assertEqual((paths.abs_dir/'recipe.json').read_bytes(),recipe)
        next_run=backend.run(spec,lambda _:None,lambda:False);self.assertEqual(next_run.exit_code,0,next_run.error)
        self.assertTrue(list((paths.abs_dir/'workflow-takes').glob('*/recipe.json')))


if __name__ == "__main__": unittest.main()

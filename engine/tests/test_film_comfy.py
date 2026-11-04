from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import uuid
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from server.film_comfy import ComfyFilmClient, ComfyFilmError, ComfyFilmExecutionError
from server.ltx_media import file_hash
import subprocess
import shutil


class _ComfyStub(BaseHTTPRequestHandler):
    state = {"prompts": [], "uploads": [], "posts": [], "history": {}, "output_root": None}

    def log_message(self, *_args):
        pass

    def _json(self, value, status=200):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/object_info":
            names = {"UNETLoader": ["qwen_image_2.1_turbo_bf16.safetensors"], "CLIPLoader": ["qwen3vl_8b_bf16.safetensors"], "VAELoader": ["qwen_image_2.1_vae_bf16.safetensors"]}
            required = {
                "UNETLoader": {"unet_name": [names["UNETLoader"]]},
                "CLIPLoader": {"clip_name": [names["CLIPLoader"]]},
                "VAELoader": {"vae_name": [names["VAELoader"]]},
            }
            required.update({
                "EmptySD3LatentImage": {"width": ["INT"], "height": ["INT"], "batch_size": ["INT"]},
                "EmptyLatentImage": {"width": ["INT"], "height": ["INT"], "batch_size": ["INT"]},
                "RandomNoise": {"noise_seed": ["INT"]}, "CLIPTextEncode": {"text": ["STRING"]},
                "BetaSamplingScheduler": {"steps": ["INT"], "denoise": ["FLOAT"]},
                "CFGGuider": {"cfg": ["FLOAT"]}, "KSamplerSelect": {"sampler_name": ["STRING"]},
                "ModelSamplingAuraFlow": {"shift": ["FLOAT"]}, "T5TokenizerOptions": {"min_padding": ["INT"], "min_length": ["INT"]},
                "SaveImage": {"filename_prefix": ["STRING"]}, "UNETLoader": {"unet_name": [names["UNETLoader"]], "weight_dtype": ["STRING"]},
                "CLIPLoader": {"clip_name": [names["CLIPLoader"]], "type": ["STRING"], "device": ["STRING"]}, "VAELoader": {"vae_name": [names["VAELoader"]]},
                "LoadImage": {"image": ["STRING"]},
                "LTX25MLXScene": {name: ["ANY"] for name in ("shot_prompts", "width", "height", "first_frames", "continuation_frames", "seed", "low_ram", "resume_run_id", "request_fingerprint", "continuation_image", "image_anchors")},
                "LTX25MLXImageAnchor": {name: ["ANY"] for name in ("image", "clip_index", "frame", "strength", "previous")},
                "LTX25MLXReleaseMemory": {},
                "SaveVideo": {name: ["ANY"] for name in ("video", "filename_prefix", "format")},
                "TextEncodeQwenImage21": {name: ["ANY"] for name in ("clip", "prompt", "negative_prompt", "vae", "resolution")},
                "QwenImage21Cache": {name: ["ANY"] for name in ("model", "device", "dtype")},
                "ManualSigmas": {"sigmas": ["STRING"]},
                "SamplerCustomAdvanced": {name: ["ANY"] for name in ("noise", "guider", "sampler", "sigmas", "latent_image")},
                "VAEDecode": {name: ["ANY"] for name in ("samples", "vae")},
            })
            info = {name: {"input": {"required": required.get(name, {}), "optional": {}}} for name in ("UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "EmptySD3LatentImage", "EmptyLatentImage", "SamplerCustomAdvanced", "RandomNoise", "BetaSamplingScheduler", "T5TokenizerOptions", "ModelSamplingAuraFlow", "CFGGuider", "KSamplerSelect", "VAEDecode", "SaveImage", "LoadImage", "LTX25MLXScene", "LTX25MLXImageAnchor", "LTX25MLXReleaseMemory", "SaveVideo", "TextEncodeQwenImage21", "QwenImage21Cache", "ManualSigmas")}
            return self._json(info)
        if self.path == "/queue":
            return self._json({"queue_running": [], "queue_pending": []})
        if self.path.startswith("/history/"):
            return self._json({self.path.rsplit("/", 1)[-1]: self.state["history"]})
        if self.path.startswith("/view?"):
            raw = b"test-media"
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.path == "/upload/image":
            self.state["uploads"].append(body)
            return self._json({"name": "ref.png", "subfolder": "", "type": "input"})
        if self.path == "/prompt":
            payload = json.loads(body)
            self.state["prompts"].append(payload)
            prompt_id = payload["prompt_id"]
            if any(node.get("class_type") == "SaveImage" for node in payload["prompt"].values()):
                self.state["history"] = {"outputs": {"740": {"images": [{"filename": "character.png", "subfolder": "", "type": "output"}]}}, "status": {"status_str": "success", "completed": True}}
            elif any(node.get("class_type") == "LTX25MLXReleaseMemory" for node in payload["prompt"].values()):
                self.state["history"] = {"outputs": {"release": {"string": ["LTX worker stopped; Comfy memory released."]}}, "status": {"status_str": "success", "completed": True}}
            else:
                self.state["history"] = {"outputs": {}, "status": {"status_str": "success", "completed": True}}
                inputs = payload["prompt"]["200"]["inputs"]
                root = self.state["output_root"] / "ltx25_mlx/scene_runs" / inputs["resume_run_id"]
                root.mkdir(parents=True, exist_ok=True)
                self.state["history"]["outputs"]["200"] = {"ltx_run": [root.name]}
                records = []
                for i, prompt in enumerate(inputs["shot_prompts"].split("\n---\n")):
                    frames = inputs["first_frames"] if i == 0 else inputs["continuation_frames"]
                    video = root / f"shot_{i:04d}.mp4"
                    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=red:size=384x256:rate=24",
                        "-f", "lavfi", "-i", "sine=sample_rate=48000", "-frames:v", str(frames), "-t", str(frames/24),
                        "-c:v", "libx264", "-c:a", "aac", "-ac", "2", str(video)], check=True)
                    wav = video.with_suffix(".wav")
                    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-af",
                        f"apad,atrim=end_sample={frames*2000}", "-c:a", "pcm_s16le", str(wav)], check=True)
                    records.append({"file": video.name, "audio": wav.name, "window": video.name, "frames": frames,
                        "prompt": prompt, "hashes": {"file": file_hash(video), "window": file_hash(video), "audio": file_hash(wav)}})
                from server.ltx_media import concat_media
                concat_media([root / r["file"] for r in records], root / "movie.mp4", frames=[r["frames"] for r in records])
                (root / "manifest.json").write_text(json.dumps({"state": "complete", "segments": records,
                    "movie_hash": file_hash(root / "movie.mp4")}))
                recipe = {**{k: inputs[k] for k in ("width", "height", "first_frames", "continuation_frames", "seed", "low_ram", "continuation_frame", "continuation_strength", "request_fingerprint")},
                          "engine": "ltx-2.5", "installed": {"test": "pinned"}, "prompts": inputs["shot_prompts"].split("\n---\n")}
                recipe["inputs"] = {key: value for key, value in recipe.items() if key not in ("engine", "installed")}
                (root / "recipe.json").write_text(json.dumps(recipe))
            return self._json({"prompt_id": prompt_id, "node_errors": {}})
        if self.path in ("/free", "/interrupt", "/queue"):
            self.state["posts"].append((self.path, json.loads(body or b"{}")))
            return self._json({})
        self.send_error(404)


class FilmComfyClientTests(unittest.TestCase):
    def test_native_recipe_keeps_engine_outside_canonical_inputs(self):
        expected = {"engine": "ltx-2.5", "prompts": ["A robot waves."], "seed": 0, "first_frames": 89}
        recipe = {"engine": "ltx-2.5", "installed": {"test": "pinned"},
                  "inputs": {**{key: value for key, value in expected.items() if key != "engine"},
                             "request_fingerprint": "request"}}
        self.assertTrue(ComfyFilmClient._recipe_matches(recipe, expected, "request", {"test": "pinned"}))
        for wrong in ({**recipe, "engine": "other"}, {**recipe, "installed": {}},
                      {**recipe, "engine": "other", "inputs": {**recipe["inputs"], "engine": "ltx-2.5"}},
                      {**recipe, "inputs": {**recipe["inputs"], "engine": "other"}},
                      {**recipe, "inputs": {**recipe["inputs"], "seed": 1}},
                      {**recipe, "inputs": {**recipe["inputs"], "request_fingerprint": "other"}}):
            self.assertFalse(ComfyFilmClient._recipe_matches(wrong, expected, "request", {"test": "pinned"}))

    def test_jpeg_and_last_anchor_have_a_reproducible_prefix_receipt(self):
        from PIL import Image, PngImagePlugin
        from server.film_comfy import _canonical_image_hash, _canonical_image_content
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("manual.jpg", "metadata.png"):
                image = root / name
                metadata = PngImagePlugin.PngInfo()
                metadata.add_text("note", "A manual upload with metadata")
                Image.new("RGB", (64, 64), "blue").save(image, pnginfo=metadata)
                self.assertNotEqual(file_hash(image), _canonical_image_hash(image))
                client = ComfyFilmClient(self.base, root / name.replace(".", "-"))
                _ComfyStub.state["output_root"] = client.output_dir
                result = client.director_scene(["One", "Two"], [], root / (name + "-scene"),
                    first_frames=25, continuation_frames=8,
                    image_anchors=[[{"path": str(image), "frame": "last", "strength": .65}], []])
                receipt = json.loads((client.output_dir / "ltx25_mlx/scene_runs" /
                                      result["run_id"] / "storyboard-inputs.json").read_text())
                self.assertEqual(receipt["signature"], client._sidecar_signature(receipt))
                self.assertEqual(receipt["image_anchors"][0][0]["frame"], 24)
                self.assertEqual(receipt["image_anchors"][0][0]["hash"], _canonical_image_hash(image))
                self.assertIn(b"Content-Type: image/png", _ComfyStub.state["uploads"][-1])
                self.assertIn(_canonical_image_content(image), _ComfyStub.state["uploads"][-1])

    def test_connection_errors_explain_how_to_start_the_desktop_backend(self):
        client = ComfyFilmClient("http://127.0.0.1:8188")
        for error in (URLError(ConnectionRefusedError("refused")), TimeoutError("timed out"), ConnectionResetError("closed")):
            with self.subTest(error=type(error).__name__), patch('server.film_comfy.urlopen', side_effect=error):
                with self.assertRaisesRegex(ComfyFilmError, 'Comfy Desktop.*LTX-2.5 Q8'):
                    client.preflight()

    @classmethod
    def setUpClass(cls):
        _ComfyStub.state = {"prompts": [], "uploads": [], "posts": [], "history": {}, "output_root": None}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _ComfyStub)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def setUp(self):
        _ComfyStub.state.update(prompts=[], uploads=[], posts=[], history={}, output_root=None)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        patcher = patch("server.film_comfy.runtime_identity", return_value={"test": "pinned"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_preflight_and_qwen_use_real_graph_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ComfyFilmClient(self.base, Path(directory))
            metadata = client.preflight(require_images=True)
            self.assertTrue(metadata["queue_empty"])
            self.assertEqual(metadata["qwen_models"]["unet"], "qwen_image_2.1_turbo_bf16.safetensors")
            result = client.qwen_image("a red kite", Path(directory) / "character.png", width=512, height=512, seed=7)
            self.assertEqual(result.read_bytes(), b"test-media")
            graph = _ComfyStub.state["prompts"][0]["prompt"]
            self.assertEqual(graph["text"]["inputs"]["prompt"], "a red kite")
            self.assertEqual(graph["latent"]["inputs"]["width"], 512)
            self.assertEqual(graph["noise"]["inputs"]["noise_seed"], 7)
            self.assertEqual(graph["sampler"]["inputs"]["sampler_name"], "euler")
            self.assertEqual(graph["guider"]["inputs"]["cfg"], 1.0)
            self.assertEqual(graph["model"]["inputs"]["unet_name"], "qwen_image_2.1_turbo_bf16.safetensors")

    def test_multiple_anchor_graph_reuses_only_matching_content_timing_and_strength(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ComfyStub.state['output_root'] = root
            client = ComfyFilmClient(self.base, root)
            image = root/'anchor.png'
            image.write_bytes(b'image-v1')
            groups = [[{'path': str(image), 'frame': 24, 'strength': .5}],
                      [{'path': str(image), 'frame': 0, 'strength': .3},
                       {'path': str(image), 'frame': 'last', 'strength': .7}]]
            first = client.director_scene(['one', 'two'], [], root/'dest', image_anchors=groups)
            graph = _ComfyStub.state['prompts'][0]['prompt']
            self.assertEqual(graph['200']['inputs']['image_anchors'], ['1005', 0])
            self.assertEqual(graph['1003']['inputs']['previous'], ['1001', 0])
            self.assertEqual(graph['1003']['inputs']['clip_index'], 1)
            self.assertEqual(graph['1003']['inputs']['frame'], 0)
            self.assertEqual(graph['1005']['inputs']['frame'], 95)
            same = client.director_scene(['one', 'two'], [], root/'dest', image_anchors=groups)
            self.assertEqual(first['run_id'], same['run_id'])
            self.assertEqual(len(_ComfyStub.state['prompts']), 1)
            for changes in ({'strength': .4}, {'frame': 8}):
                groups[1][0].update(changes)
                changed = client.director_scene(['one', 'two'], [], root/'dest', image_anchors=groups)
                self.assertNotEqual(changed['run_id'], same['run_id'])
                same = changed
            image.write_bytes(b'image-v2')
            changed = client.director_scene(['one', 'two'], [], root/'dest', image_anchors=groups)
            self.assertNotEqual(changed['run_id'], same['run_id'])
            self.assertTrue((root/'ltx25_mlx/scene_runs'/first['run_id']/'movie.mp4').is_file())

    def test_director_uploads_refs_and_requires_two_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "ltx25_mlx/scene_runs/0123456789abcdef0123456789abcdef"
            run.mkdir(parents=True)
            (run / "manifest.json").write_text(json.dumps({"state": "complete", "segments": [{"file": "shot_0001.mp4", "audio": "shot_0001.wav", "frames": 73}, {"file": "shot_0002.mp4", "audio": "shot_0002.wav", "frames": 96}]}))
            (run / "recipe.json").write_text(json.dumps({"prompts": ["one", "two"], "width": 384, "height": 256, "first_frames": 73, "continuation_frames": 96, "engine": "ltx-2.5", "seed": 0}))
            (run / "movie.mp4").write_bytes(b"movie")
            (run / "shot_0001.mp4").write_bytes(b"shot")
            (run / "shot_0001.wav").write_bytes(b"audio")
            (run / "shot_0002.mp4").write_bytes(b"shot2")
            (run / "shot_0002.wav").write_bytes(b"audio2")
            time.sleep(0.01)
            client = ComfyFilmClient(self.base, root)
            _ComfyStub.state["output_root"] = root
            ref = root / "ref.png"
            ref.write_bytes(b"png")
            result = client.director_scene(["one", "two"], [ref], root / "dest")
            self.assertNotEqual(result["run_id"], run.name)
            self.assertTrue((root / "dest/movie.mp4").is_file())
            self.assertEqual(json.loads((root / "dest/manifest.json").read_text())["segments"][1]["frames"], 96)
            graph = next(item["prompt"] for item in _ComfyStub.state["prompts"] if "LTX25MLXScene" in {node.get("class_type") for node in item["prompt"].values()})
            self.assertEqual(graph["200"]["inputs"]["shot_prompts"], "one\n---\ntwo")
            self.assertEqual(graph["200"]["inputs"]["start_image"], ["100", 0])
            self.assertNotIn("201", graph)
            with self.assertRaisesRegex(ValueError, "ett åpningsbilde"):
                client.director_scene(["one", "two"], [ref, ref], root / "dest2")

    def test_complete_scene_is_revalidated_and_reused_without_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ComfyStub.state["output_root"] = root
            client = ComfyFilmClient(self.base, root)
            first = client.director_scene(["one", "two"], [], root / "dest")
            stamp = first["shots"][0]["video"].stat().st_mtime_ns
            second = client.director_scene(["one", "two"], [], root / "dest")
            self.assertEqual(first["run_id"], second["run_id"])
            self.assertTrue(second["reused"])
            self.assertEqual(len(_ComfyStub.state["prompts"]), 1)
            self.assertEqual(second["shots"][0]["video"].stat().st_mtime_ns, stamp)
            source = root / "ltx25_mlx/scene_runs" / first["run_id"] / first["shots"][0]["video"].name
            source.write_bytes(b"partial")
            client.director_scene(["one", "two"], [], root / "dest")
            self.assertEqual(len(_ComfyStub.state["prompts"]), 2)

    def test_failed_submission_retains_run_id_across_client_restart_and_changes_create_new_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ComfyStub.state["output_root"] = root
            client = ComfyFilmClient(self.base, root)
            dest = root / "dest"
            anchor = root / "anchor.png"
            anchor.write_bytes(b"reference-one")
            with patch.object(client, "_submit", side_effect=ComfyFilmExecutionError("interrupted", {})):
                with self.assertRaises(ComfyFilmExecutionError):
                    client.director_scene(["one", "two"], [anchor], dest, continuation_image=anchor)
            state = json.loads((dest / "scene-state.json").read_text())
            client = ComfyFilmClient(self.base, root)
            resumed = client.director_scene(["one", "two"], [anchor], dest, continuation_image=anchor)
            self.assertEqual(resumed["run_id"], state["run_id"])
            graph = _ComfyStub.state["prompts"][-1]["prompt"]
            self.assertEqual(graph["200"]["inputs"]["continuation_image"], ["102", 0])
            self.assertEqual(graph["200"]["inputs"]["continuation_frame"], 0)
            anchor.write_bytes(b"reference-two")
            changed = client.director_scene(["one", "two"], [anchor], dest, continuation_image=anchor)
            self.assertNotEqual(changed["run_id"], resumed["run_id"])
            with patch("server.film_comfy.runtime_identity", return_value={"test": "new model"}), \
                 patch.object(client, "_submit", side_effect=ComfyFilmExecutionError("stop before inference", {})):
                with self.assertRaises(ComfyFilmExecutionError):
                    client.director_scene(["one", "two"], [anchor], dest, continuation_image=anchor)
            self.assertNotEqual(json.loads((dest / "scene-state.json").read_text())["run_id"], changed["run_id"])
            self.assertTrue(resumed["movie"].is_file())

    def test_timeout_cancels_only_our_prompt(self):
        client = ComfyFilmClient(self.base)
        client.wait_timeout = 0
        with patch.object(client, "_post", return_value={}) as post, \
             patch.object(client, "_cancel") as cancel:
            with self.assertRaises(TimeoutError):
                client._submit({}, None, None, "test")
        own = post.call_args.args[1]["prompt_id"]
        cancel.assert_called_once_with(own)

    @patch("server.film.assert_other_models_idle")
    @patch("server.film.prepare_language_memory")
    def test_desktop_film_restart_reuses_checkpoint_after_project_fingerprints_change(self, *_):
        from server.film import FilmController
        from tests.test_film import FilmTests, FakeComfy
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ctx = FilmTests().context(root)
            _ComfyStub.state["output_root"] = root
            client = ComfyFilmClient(self.base, root)
            client.qwen_image = FakeComfy().qwen_image
            film = FilmController(ctx, client)
            created = film.start({"description": "A robot waves.", "duration": 7, "continuationAnchor": True})
            FilmTests()._finish_checkpoints(ctx, film, created["slug"])
            self.assertEqual(film.status()["phase"], "done", film.status())
            calls = len(_ComfyStub.state["prompts"])
            clip = ctx.store.project_dir(created["slug"]) / "shots/01/clip.mp4"
            stamp = clip.stat().st_mtime_ns
            film = FilmController(ctx, client)
            film.start({"resumeSlug": created["slug"]})
            FilmTests()._finish_checkpoints(ctx, film, created["slug"])
            self.assertEqual(film.status()["phase"], "done", film.status())
            self.assertEqual(len(_ComfyStub.state["prompts"]), calls)
            self.assertEqual(clip.stat().st_mtime_ns, stamp)

    def test_legacy_uuid_scene_requires_original_recipe_model_reference_and_valid_media(self):
        from PIL import Image
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ComfyStub.state["output_root"] = root
            client = ComfyFilmClient(self.base, root)
            result = client.director_scene(["one", "two"], [], root / "old-uuid-scene-001", seed=1)
            old = result["movie"].parent
            source = root / "ltx25_mlx/scene_runs" / result["run_id"]
            model = root / "model"
            model.mkdir()
            (model / "weights").write_bytes(b"fake-weight")
            marker = {"files": {"weights": 11}, "installed_at": 1, "runtime_commit": "pinned"}
            (model / ".ltx-installed.json").write_text(json.dumps(marker))
            anchor = source / "start_image.png"
            Image.new("RGB", (64, 64), "red").save(anchor)
            reference = root / "scene.png"
            shutil.copy2(anchor, reference)
            recipe = json.loads((source / "recipe.json").read_text())
            recipe.update(model=str(model), runtime_commit="pinned", start_image=str(anchor), frame_rate=24,
                          carry_frames=25, stage1_steps=8, stage2_steps=3)
            for folder in (source, old):
                (folder / "recipe.json").write_text(json.dumps(recipe))
                manifest = json.loads((folder / "manifest.json").read_text())
                manifest["started_at"] = time.time()
                (folder / "manifest.json").write_text(json.dumps(manifest))
            with patch("server.film_comfy.MODEL_DIR", model), \
                 patch("server.film_comfy.subprocess.check_output", return_value="pinned"), \
                 patch("server.film_comfy.subprocess.run", return_value=SimpleNamespace(returncode=0)):
                self.assertIsNotNone(client.legacy_scene(old, result["run_id"], ["one", "two"], reference, seed=1))
                self.assertIsNone(client.legacy_scene(old, result["run_id"], ["one", "changed"], reference, seed=1))
                Image.new("RGB", (64, 64), "blue").save(reference)
                self.assertIsNone(client.legacy_scene(old, result["run_id"], ["one", "two"], reference, seed=1))
                shutil.copy2(anchor, reference)
                result["shots"][0]["video"].write_bytes(b"partial")
                self.assertIsNone(client.legacy_scene(old, result["run_id"], ["one", "two"], reference, seed=1))

    def test_missing_qwen_nodes_and_targeted_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ComfyFilmClient(self.base, Path(directory))
            original = _ComfyStub.do_GET
            def missing_model(handler):
                if handler.path == "/object_info":
                    raw = json.dumps({"LoadImage": {"input": {"required": {"image_file": ["STRING"]}}}, "LTX25MLXScene": {"input": {"required": {}}}, "SaveVideo": {"input": {"required": {}}}}).encode()
                    handler.send_response(200); handler.send_header("Content-Length", str(len(raw))); handler.end_headers(); handler.wfile.write(raw); return
                return original(handler)
            _ComfyStub.do_GET = missing_model
            try:
                with self.assertRaises(Exception):
                    client.preflight()
            finally:
                _ComfyStub.do_GET = original
            client._cancel("00000000-0000-4000-8000-000000000001")
            self.assertIn(("/interrupt", {"prompt_id": "00000000-0000-4000-8000-000000000001"}), _ComfyStub.state["posts"])

    def test_preflight_rejects_missing_exact_qwen_weight(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ComfyFilmClient(self.base, Path(directory))
            info = client._get("/object_info")
            info["VAELoader"]["input"]["required"]["vae_name"] = [["other-vae.safetensors"]]
            with patch.object(client, "_get", side_effect=[info, {"queue_running": [], "queue_pending": []}]):
                with self.assertRaisesRegex(Exception, "Qwen vae"):
                    client.preflight(require_images=True)

    def test_release_memory_submits_own_barrier_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ComfyFilmClient(self.base, Path(directory))
            entry = client.release_memory()
            self.assertEqual(entry["status"]["status_str"], "success")
            self.assertEqual(_ComfyStub.state["prompts"][0]["prompt"], {"release": {"class_type": "LTX25MLXReleaseMemory", "inputs": {}}})

    def test_failed_generation_never_adopts_an_existing_run(self):
        client = ComfyFilmClient(self.base)
        error = ComfyFilmExecutionError("failed", {"status": {"status_str": "error", "completed": False}})
        with tempfile.TemporaryDirectory() as directory, patch.object(client, "_submit", side_effect=error):
            with self.assertRaises(ComfyFilmExecutionError):
                client.director_scene(["one"], [], Path(directory) / "dest")
            self.assertTrue((Path(directory) / "dest/scene-state.json").is_file())
            self.assertFalse((Path(directory) / "dest/movie.mp4").exists())

    def test_preflight_rejects_foreign_queue_without_interrupting_it(self):
        client = ComfyFilmClient(self.base)
        info = client._get("/object_info")
        with patch.object(client, "_get", side_effect=[info, {"queue_running": [[0, "foreign"]]}]):
            with self.assertRaisesRegex(Exception, "ikke tom"):
                client.preflight(False)
        self.assertEqual(_ComfyStub.state["posts"], [])

    def test_manifest_rejects_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "ltx25_mlx/scene_runs/0123456789abcdef0123456789abcdef"
            run.mkdir(parents=True)
            (run / "manifest.json").write_text(json.dumps({"state": "complete", "segments": [{"file": "../escape.mp4", "audio": "shot.wav"}, {"file": "shot.mp4", "audio": "shot.wav"}]}))
            (run / "movie.mp4").write_bytes(b"movie")
            ref = root / "ref.png"; ref.write_bytes(b"png")
            with self.assertRaises(Exception):
                ComfyFilmClient(self.base, root)._scene_records(run, 2)


if __name__ == "__main__":
    unittest.main()

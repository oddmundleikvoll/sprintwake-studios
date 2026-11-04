"""CPU-only regression tests for strict cross-run LTX prefix reuse."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.film_comfy import ComfyFilmClient
from server.ltx_media import digest, file_hash, run_ffmpeg

BRIDGE = Path(os.environ.get('RAVEN_LTX_BRIDGE', str(Path(__file__).resolve().parents[3] / "ComfyUI-Film/ComfyUI/custom_nodes/ComfyUI-LTX25-MLX")))
try:
    spec = importlib.util.spec_from_file_location("prefix_render_scene", BRIDGE / "render_scene.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
except (FileNotFoundError, ImportError):
    worker = None


def recipe_for_protocol(config, installed):
    inputs = dict(config)
    for key in ("start_image", "end_image", "continuation_image"):
        if inputs.get(key):
            inputs[key] = file_hash(inputs[key])
    if inputs.get("image_anchors"):
        inputs["image_anchors"] = [[{**a, "path": file_hash(a["path"])} for a in items]
                                    for items in inputs["image_anchors"]]
    return {"inputs": inputs, "installed": installed, "format": 2, "frame_rate": 24,
            "carry_frames": 25, "stage1_steps": 8, "stage2_steps": 3}


class PrefixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runs = self.root / "ltx25_mlx/scene_runs"
        self.old = self.runs / ("a" * 32)
        self.old.mkdir(parents=True)
        self.identity = {"revision": "pinned"}
        self.runtime_patch = patch.object(worker, "runtime_identity", return_value=self.identity) if worker else None
        if self.runtime_patch:
            self.runtime_patch.start()
            self.addCleanup(self.runtime_patch.stop)
        self.config = {"prompts": ["one", "two", "three"], "width": 64, "height": 64,
                       "first_frames": 25, "continuation_frames": 8, "seed": 3,
                       "low_ram": False, "continuation_frame": 0, "continuation_strength": .35,
                       "request_fingerprint": "old-film"}
        self._make_run()
        self.client = ComfyFilmClient(output_dir=self.root)

    def _media(self, path: Path, frames: int):
        run_ffmpeg(("-f", "lavfi", "-i", "color=red:size=64x64:rate=24", "-f", "lavfi",
                      "-i", "sine=sample_rate=48000", "-frames:v", str(frames), "-t", str(frames / 24),
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", path))

    def _make_run(self, state="complete", count=3):
        records = []
        for index in range(count):
            frames = self.config["first_frames"] if index == 0 else self.config["continuation_frames"]
            window_frames = frames + (25 if index else 0)
            clip = self.old / f"shot-{index}.mp4"
            window = self.old / f"window-{index}.mp4"
            self._media(clip, frames)
            self._media(window, window_frames)
            wav = self.old / f"shot-{index}.wav"
            run_ffmpeg(("-i", clip, "-vn", "-af", f"apad,atrim=end_sample={frames * 2000}",
                          "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", wav)
                      )
            records.append({"file": clip.name, "audio": wav.name, "window": window.name,
                            "frames": frames, "prompt": self.config["prompts"][index],
                            "hashes": {key: file_hash(self.old / name) for key, name in
                                       (("file", clip.name), ("audio", wav.name), ("window", window.name))}})
        worker_recipe = recipe_for_protocol(self.config, self.identity)
        worker_signature = digest(worker_recipe)
        (self.old / "recipe.json").write_text(json.dumps({**worker_recipe, "signature": worker_signature}))
        (self.old / "manifest.json").write_text(json.dumps({"state": state, "signature": worker_signature,
                                                               "segments": records, "movie_hash": "unused"}))
        sidecar_config = {k: v for k, v in self.config.items() if k != "request_fingerprint"}
        input_record = {"format": 1, "signature": "old-film", "installed": self.identity,
                        "config": sidecar_config, "provenance": {"scene": {"characterKeys": ["hero"]},
                        "cast": [{"key": "hero", "name": "Hero"}]},
                        "references": {"start_image": None, "end_image": None, "continuation_image": None},
                        "image_anchors": [[], [], []]}
        input_record["provenance_hash"] = digest(input_record["provenance"])
        input_record["signature"] = digest({"config": input_record["config"], "installed": self.identity,
                                             "provenance": input_record["provenance"],
                                             "references": input_record["references"]})
        self.config["request_fingerprint"] = input_record["signature"]
        worker_recipe = recipe_for_protocol(self.config, self.identity)
        (self.old / "recipe.json").write_text(json.dumps({**worker_recipe, "signature": digest(worker_recipe)}))
        (self.old / "storyboard-inputs.json").write_text(json.dumps(input_record))
        self.current = {"format": 1, "signature": "new-film", "installed": self.identity,
                        "config": {**sidecar_config, "prompts": ["one", "two", "changed"]},
                        "provenance": input_record["provenance"], "references": input_record["references"],
                        "image_anchors": [[], [], []]}

    def seed(self, current=None, cancel=None):
        new = self.runs / ("b" * 32)
        copied = self.client._seed_prefix(self.old, new, current or self.current, cancel=cancel)
        return copied, new

    def test_changed_last_prompt_reuses_prefix_and_worker_recipe_matches(self):
        copied, new = self.seed()
        self.assertEqual(copied, 2)
        manifest = json.loads((new / "manifest.json").read_text())
        self.assertEqual(len(manifest["segments"]), 2)
        actual = recipe_for_protocol({**self.config, "prompts": ["one", "two", "changed"],
                                      "request_fingerprint": "new-film"}, self.identity)
        self.assertEqual(manifest["signature"], digest(actual))

    def test_changed_last_anchor_reuses_prefix(self):
        self.current["image_anchors"][2] = [{"hash": "new", "frame": 0, "strength": .4}]
        copied, _ = self.seed()
        self.assertEqual(copied, 2)

    def test_changed_last_png_anchor_recipe_matches_worker_contract(self):
        from PIL import Image
        image = self.root / "anchor.png"
        Image.new("RGB", (64, 64), "blue").save(image)
        from server.film_comfy import _canonical_image_hash
        anchor = {"hash": _canonical_image_hash(image), "frame": 0, "strength": .65}
        self.current["image_anchors"][2] = [anchor]
        copied, new = self.seed()
        self.assertEqual(copied, 2)
        config = {**self.config, "prompts": ["one", "two", "changed"],
                  "request_fingerprint": "new-film",
                  "image_anchors": [[ ], [ ], [{"path": image, "frame": 0, "strength": .65}]]}
        if worker is None:
            self.skipTest("installed LTX worker bridge unavailable")
        actual = worker.recipe_for(config)
        seeded = json.loads((new / "recipe.json").read_text())
        self.assertEqual(seeded["signature"], digest(actual))

    @unittest.skipUnless(worker, "installed LTX worker bridge unavailable")
    def test_uploaded_jpeg_and_metadata_png_match_actual_node_persistence(self):
        import ast
        import hashlib
        import io
        import re
        from types import SimpleNamespace
        import numpy as np
        from PIL import Image, ImageOps, PngImagePlugin
        from server.film_comfy import _canonical_image_hash, _canonical_image_content
        from server.ltx_anchors import clean_anchors
        # Run the installed node's real method, stopping at its worker launch.
        # Extracting this method avoids importing Comfy's live GPU/server state.
        tree = ast.parse((BRIDGE / "nodes.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LTX25MLXScene")
        method = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "_generate")
        class AtWorker(Exception): pass
        def stop_worker(*args, **kwargs): raise AtWorker
        scope = dict(io=io, hashlib=hashlib, Image=Image, np=np, re=re, json=json, Path=Path,
            __file__=str(BRIDGE / "nodes.py"), clean_anchors=clean_anchors,
            comfy=SimpleNamespace(utils=SimpleNamespace(ProgressBar=lambda n: None)),
            mm=SimpleNamespace(throw_exception_if_processing_interrupted=lambda: None),
            release_memory=lambda: None, run_worker=stop_worker, sys=SimpleNamespace(executable="unused"))
        exec(compile(ast.Module(body=[method], type_ignores=[]), "installed-ltx-node", "exec"), scope)
        class Tensor:
            def __init__(self, data): self.data = data
            def __len__(self): return 1
            def __getitem__(self, index): return self
            def cpu(self): return self
            def numpy(self): return self.data
        for name in ("manual.jpg", "metadata.png"):
            image = self.root / name
            metadata = PngImagePlugin.PngInfo(); metadata.add_text("comment", "manual reference")
            Image.new("RGB", (64, 64), "blue").save(image, pnginfo=metadata)
            # Current LoadImage uses PyAV for stills. The upload is normalized
            # PNG so PIL/JPEG decoder differences cannot break worker reuse.
            import av
            with av.open(io.BytesIO(_canonical_image_content(image))) as source:
                frame = next(source.decode(video=0))
                tensor = Tensor(frame.to_ndarray(format="rgb24").astype(np.float32)/255)
            directory = self.root / (name + "-node"); directory.mkdir()
            with self.assertRaises(AtWorker):
                scope["_generate"](None, directory, "one\n---\ntwo\n---\nchanged", 64, 64, 25, 8,
                    3, False, None, None, None, 0, .35, "new-film",
                    [{"image": tensor, "clip_index": 2, "frame": 0, "strength": .65}])
            config = json.loads((directory / "request.json").read_text())
            persisted = config["image_anchors"][2][0]["path"]
            self.assertEqual(file_hash(persisted), _canonical_image_hash(image))
            self.assertNotEqual(file_hash(image), file_hash(persisted))
            self.current["image_anchors"][2] = [{"hash": _canonical_image_hash(image), "frame": 0, "strength": .65}]
            copied, new = self.seed()
            self.assertEqual(copied, 2)
            self.assertEqual(json.loads((new/"recipe.json").read_text())["signature"], digest(worker.recipe_for(config)))

    def test_geometry_cast_and_runtime_changes_forbid_reuse(self):
        for key, value in (("width", 128), ("height", 128)):
            current = json.loads(json.dumps(self.current))
            current["config"][key] = value
            self.assertEqual(self.seed(current)[0], 0)
        current = json.loads(json.dumps(self.current))
        current["provenance"]["cast"][0]["name"] = "Different"
        self.assertEqual(self.seed(current)[0], 0)
        current = json.loads(json.dumps(self.current))
        current["installed"] = {"revision": "new-code-or-model"}
        self.assertEqual(self.seed(current)[0], 0)

    def test_corrupt_window_invalidates_contiguous_prefix(self):
        record = json.loads((self.old / "manifest.json").read_text())["segments"][1]
        (self.old / record["window"]).write_bytes(b"corrupt")
        self.assertEqual(self.seed()[0], 1)

    def test_interrupted_manifest_uses_committed_records_without_complete_state(self):
        self._make_run(state="failed", count=2)
        copied, _ = self.seed()
        self.assertEqual(copied, 2)

    def test_cancellation_during_copy_aborts_without_seeded_manifest(self):
        calls = [0]
        def cancel():
            calls[0] += 1
            return calls[0] > 2
        new = self.runs / ("c" * 32)
        with self.assertRaises(InterruptedError):
            self.client._seed_prefix(self.old, new, self.current, cancel=cancel)
        self.assertFalse((new / "manifest.json").exists())

    def test_edited_sidecar_cannot_override_canonical_recipe(self):
        path = self.old / "storyboard-inputs.json"
        saved = json.loads(path.read_text())
        saved["config"]["prompts"][0] = "forged"
        path.write_text(json.dumps(saved))
        self.assertEqual(self.seed()[0], 0)
        saved = json.loads(path.read_text())
        saved["config"]["prompts"] = ["one", "two", "three"]
        saved["provenance"]["cast"][0]["name"] = "forged"
        saved["provenance_hash"] = digest(saved["provenance"])
        path.write_text(json.dumps(saved))
        self.assertEqual(self.seed()[0], 0)

    def test_nested_scene_shot_details_do_not_invalidate_unchanged_prefix(self):
        path = self.old / "storyboard-inputs.json"
        saved = json.loads(path.read_text())
        saved["provenance"]["scene"]["shots"] = [{"prompt": "one"}, {"prompt": "two"}]
        saved["provenance_hash"] = digest(saved["provenance"])
        path.write_text(json.dumps(saved))
        self.assertEqual(self.seed()[0], 2)


if __name__ == "__main__":
    unittest.main()

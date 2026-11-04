import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from server.backends.base import ShotPaths
from server.backends.ltx_backend import LTXBackend
from server.store import Store, default_board, default_shot, render_fingerprint, stale_reason
from server.speech import prepare_recording
from server.orchestrator import Orchestrator, ShotRun


class LTXBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = ShotPaths(self.root, self.root / "shot", "shot", data_dir=self.root)
        self.backend = LTXBackend(self.root)
        self.backend._info_cache = (time.monotonic(), {})
        self.board = default_board("LTX test")
        self.board["defaults"].update(resolution="96x96", frames=30)
        self.shot = default_shot(self.board["defaults"])
        self.shot.update(prompt="A blue ball rolls across a wooden table.")

    def tearDown(self):
        self.temp.cleanup()

    def test_multiple_anchor_payload_is_validated_before_dispatch(self):
        from PIL import Image
        path = self.root/'chosen.png'
        Image.new('RGB', (16, 16), 'red').save(path)
        self.shot['imageAnchors'] = [{'ref': {'path': path.name}, 'frame': 8, 'strength': .4},
                                     {'ref': {'path': path.name}, 'frame': 'last', 'strength': .7}]
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        expected = [{'path': str(path.resolve()), 'frame': 8, 'strength': .4},
                    {'path': str(path.resolve()), 'frame': 'last', 'strength': .7}]
        self.assertEqual(spec.payload['imageAnchors'], expected)
        with patch.object(self.backend.client, 'director_scene', side_effect=RuntimeError('no inference')) as call:
            self.backend.run(spec, lambda event: None, lambda: False)
        self.assertEqual(call.call_args.kwargs['image_anchors'], [expected])
        self.shot['imageAnchors'][0]['frame'] = 33
        with self.assertRaises(ValueError):
            self.backend.prepare(self.shot, self.board, self.paths)

    def test_sketch_rejects_timed_anchors_instead_of_silently_stretching_them(self):
        self.board['defaults'].update(draft=True, sketch=True)
        self.shot['imageAnchors'] = [{'ref': {'path': 'chosen.png'}, 'frame': 24, 'strength': .5}]
        with self.assertRaisesRegex(ValueError, 'Sketch'):
            self.backend.prepare(self.shot, self.board, self.paths)

    def test_malformed_falsy_anchor_lists_cannot_be_rendered_as_empty(self):
        for value in (None, '', 0, False, [None]):
            self.shot['imageAnchors'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.backend.prepare(self.shot, self.board, self.paths)

    def test_failed_and_cancelled_rerenders_preserve_chain_frames_and_metadata(self):
        store = Store(workspace=self.root, data_dir=self.root / "projects")
        slug, board = store.create("Preserved take")
        board["shots"] = [self.shot]
        self.shot.update(status="done", renderedWith={"engine": "ltx", "steps": 8},
                         renderFingerprint="original-fingerprint", stills={"start": "old.png"},
                         outputs=["/media/accepted.mp4"], validation={"verdict": "done"})
        store.save(slug, board)
        folder = store.data_dir / store.shot_rel_dir(slug, 1)
        frames = folder / "frames"
        frames.mkdir(parents=True)
        anchor = frames / "frame-00000.png"
        anchor.write_bytes(b"accepted anchor")
        clip = folder / "clip.mp4"
        clip.write_bytes(b"accepted clip")
        orch = Orchestrator(self.backend, store, workspace=self.root, data_dir=store.data_dir)
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                orch._cancel.clear()
                orch._prime_batch(slug, [self.shot["id"]])
                def fail(*args, **kwargs):
                    if cancelled:
                        orch._cancel.set()
                    raise RuntimeError("test failed render")
                with patch.object(self.backend.client, "director_scene", side_effect=fail):
                    orch._run_one(slug, self.shot["id"])
                self.assertEqual(anchor.read_bytes(), b"accepted anchor")
                self.assertEqual(clip.read_bytes(), b"accepted clip")
                saved = store.load(slug)["shots"][0]
                self.assertEqual(saved["renderFingerprint"], "original-fingerprint")
                self.assertEqual(saved["renderedWith"], {"engine": "ltx", "steps": 8})
                self.assertEqual(saved["stills"], {"start": "old.png"})
                self.assertEqual(saved["outputs"], ["/media/accepted.mp4"])
                self.assertEqual(saved["validation"], {"verdict": "done"})

    def test_new_projects_and_legacy_shots_use_q8_grid(self):
        self.assertEqual(default_board("New")["defaults"]["model"], "ltx-2.5")
        self.shot["model"] = "ref2va"
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        self.assertEqual(spec.payload["model"], "ltx-2.5")
        self.assertEqual((spec.payload["width"], spec.payload["height"], spec.payload["frames"]), (128, 128, 33))
        self.assertEqual(spec.payload["steps"], 8)

    def test_native_speech_is_spoken_without_claiming_voice_cloning(self):
        self.shot.update(dialogue="Hei!", dialogueSource="native")
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        self.assertTrue(spec.payload["nativeDialogueSpoken"])
        self.assertIn('saying exactly: "Hei!"', spec.payload["prompt"])
        self.assertNotIn("voice identity of the reference", spec.payload["prompt"])

    def test_legacy_take_retains_engine_and_becomes_stale_for_ltx(self):
        store = Store(workspace=self.root, data_dir=self.root / "projects")
        slug, board = store.create("Old film")
        board["defaults"]["model"] = "ref2va"
        shot = default_shot(board["defaults"])
        shot.update(status="done", prompt="A ball rolls", outputs=["/media/old/clip.mp4"],
                    renderedWith={"engine": "mlx", "model": "ref2va", "steps": 17})
        board["shots"] = [shot]
        shot["renderFingerprint"] = render_fingerprint(shot, board)
        (store.project_dir(slug) / "storyboard.json").write_text(json.dumps(board))
        migrated = store.load(slug)
        self.assertEqual(migrated["defaults"]["model"], "ltx-2.5")
        self.assertEqual(migrated["shots"][0]["renderedWith"]["engine"], "mlx")
        self.assertTrue(stale_reason(migrated["shots"][0], migrated))
        self.assertEqual(migrated["shots"][0]["outputs"], ["/media/old/clip.mp4"])

    def test_native_ltx_dialogue_needs_no_tts_recording_before_assembly(self):
        store = Store(workspace=self.root, data_dir=self.root / "projects")
        slug, board = store.create("Spoken film")
        shot = default_shot(board["defaults"])
        shot.update(dialogue="Hei!", dialogueSource="native", status="done")
        board["shots"] = [shot]
        store.save(slug, board)
        ctx = SimpleNamespace(store=store)
        # No tts() or cast voice exists. The native track belongs to the clip.
        prepare_recording(ctx, slug, shot["id"], generate_missing=False)
        prepare_recording(ctx, slug, shot["id"], generate_missing=True)

    def test_retired_krea_engine_is_not_advertised(self):
        self.assertIsNone(self.backend.capability("krea2-still"))

    def test_comfy_stills_do_not_depend_on_missing_video_weights(self):
        from server.backends.base import ModelCapability
        store = Store(workspace=self.root, data_dir=self.root / "projects")
        orch = Orchestrator(backend=self.backend, store=store, workspace=self.root, data_dir=store.data_dir)
        self.board["defaults"]["stillsEngine"] = "qwen-image-2.1-turbo"
        with patch.object(self.backend, "capability", return_value=ModelCapability(
                id="qwen-image-2.1-turbo", label="Qwen", kind="image", engine="comfyui")), \
             patch.object(self.backend, "health", side_effect=AssertionError("video health is irrelevant to stills")):
            self.assertEqual(orch._check_still_engine(self.board), "qwen-image-2.1-turbo")

    def test_missing_chain_and_stale_recording_fail_before_submission(self):
        self.shot["startRef"] = {"kind": "chain"}
        with self.assertRaisesRegex(ValueError, "forrige klipp"):
            self.backend.prepare(self.shot, self.board, self.paths)
        self.shot.update(startRef=None, dialogue="Hei", dialogueSource="recording")
        with self.assertRaisesRegex(ValueError, "dialogopptak"):
            self.backend.prepare(self.shot, self.board, self.paths)

    def test_render_publishes_real_media_and_frame_dumps(self):
        import av
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        def generate(prompts, references, dest, **kwargs):
            dest.mkdir(parents=True)
            video = dest / "shot_0001.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                "testsrc2=size=128x128:rate=24", "-f", "lavfi", "-i", "sine=sample_rate=48000",
                "-frames:v", "33", "-t", str(33/24), "-c:v", "libx264", "-c:a", "aac", str(video)], check=True)
            return {"shots": [{"video": video, "frames": 33}]}
        with patch.object(self.backend.client, "director_scene", side_effect=generate):
            result = self.backend.run(spec, lambda event: None, lambda: False)
        self.assertEqual(result.exit_code, 0, result.error)
        self.assertTrue(self.backend.validate(spec, result).ok)
        with av.open(str(spec.expected_outputs[0])) as source:
            self.assertTrue(source.streams.audio)
            self.assertEqual(len(list(source.decode(video=0))), 33)
        self.assertEqual(len(list(spec.frames_dir.glob("*.png"))), 33)

    def test_cancel_and_failed_generation_preserve_existing_take(self):
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        spec.expected_outputs[0].write_bytes(b"accepted previous take")
        with patch.object(self.backend.client, "director_scene", side_effect=RuntimeError("failed")):
            result = self.backend.run(spec, lambda event: None, lambda: False)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(spec.expected_outputs[0].read_bytes(), b"accepted previous take")
        result = self.backend.run(spec, lambda event: None, lambda: True)
        self.assertTrue(result.cancelled)
        self.assertEqual(spec.expected_outputs[0].read_bytes(), b"accepted previous take")


if __name__ == "__main__":
    unittest.main()

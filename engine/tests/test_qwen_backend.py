"""Source coverage for the native Qwen still backend; no model is loaded."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from server.backends.base import Backend, ModelCapability, ShotPaths
from server.backends.qwen_backend import QWEN_ID, WithQwenImage
from server.store import default_board, default_shot


class _Inner(Backend):
    id = "inner"
    label = "Inner"

    def capabilities(self):
        return [ModelCapability(id="ltx-2.5", label="LTX-2.5 Q8")]

    def prepare(self, shot, project, paths):
        raise AssertionError("Qwen shot must be handled by the wrapper")

    def run(self, spec, on_event, should_cancel):
        raise AssertionError("Qwen run must be handled by the wrapper")


class _FakeClient:
    def __init__(self):
        self.calls = []

    def preflight(self, require_images=False):
        self.calls.append(("preflight", require_images))
        return {"queue_empty": True, "qwen_models": {}}

    def qwen_image(self, prompt, dest, references=None, **kwargs):
        self.calls.append(("qwen_image", prompt, list(references or []), kwargs))
        Image.new("RGB", (kwargs["width"], kwargs["height"]), "red").save(dest)
        return Path(dest)

    def release_memory(self, **kwargs):
        self.calls.append(("release_comfy",))
        return {}


class QwenBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inner = _Inner()
        self.backend = WithQwenImage(self.inner)
        self.client = _FakeClient()
        self.backend.client = self.client
        self.board = default_board("Qwen test")
        self.board["characters"] = [{"id": "robot", "name": "Rob", "description": "Red robot.",
                                     "image": {"path": str(self.root / "robot-card.png"), "role": "character-card"}}]
        Image.new("RGB", (64, 64), "blue").save(self.root / "robot-card.png")
        self.shot = default_shot(self.board["defaults"])
        self.shot.update(id="shot-1", model=QWEN_ID, prompt="A red kite", characterIds=["robot"],
                         resolution="769x513", referenceImages=[])
        self.paths = ShotPaths(self.root, self.root / "shot", "shot")

    def tearDown(self):
        self.temp.cleanup()

    def test_capability_and_preflight_use_qwen_image(self):
        capabilities = self.backend.capabilities()
        self.assertEqual([item.id for item in capabilities], ["ltx-2.5", QWEN_ID])
        self.assertEqual(capabilities[-1].default_steps, 8)
        self.assertEqual(capabilities[-1].max_style_refs, 10)
        self.assertEqual(self.client.calls, [("preflight", True)])

    def test_prepare_aligns_dimensions_and_passes_character_card(self):
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        self.assertEqual(spec.payload["engine"], "qwen-image")
        self.assertEqual((spec.payload["width"], spec.payload["height"]), (800, 544))
        self.assertEqual(len(spec.payload["references"]), 1)
        self.assertIn("8 steps", spec.summary)

    def test_prepare_rejects_more_than_ten_references(self):
        self.shot["referenceImages"] = [{"path": str(self.root / f"ref-{i}.png")} for i in range(10)]
        for i in range(10):
            Image.new("RGB", (8, 8), "green").save(self.root / f"ref-{i}.png")
        with self.assertRaisesRegex(ValueError, "maksimalt 10"):
            self.backend.prepare(self.shot, self.board, self.paths)

    def test_run_releases_fable_and_comfy_before_publishing_verified_image(self):
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        with patch("server.fable.FableLLM") as fable:
            fable.return_value.release_memory.side_effect = lambda **_kwargs: self.client.calls.append(("release_fable",))
            result = self.backend.run(spec, lambda _: None, lambda: False)
        self.assertEqual(result.exit_code, 0, result.error)
        self.assertTrue(spec.expected_outputs[0].is_file())
        with Image.open(spec.expected_outputs[0]) as image:
            image.verify()
        self.assertEqual([call[0] for call in self.client.calls[-3:]],
                         ["release_fable", "release_comfy", "qwen_image"])

    def test_cancel_before_submission_does_not_call_qwen(self):
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        result = self.backend.run(spec, lambda _: None, lambda: True)
        self.assertEqual(result.exit_code, 1)
        self.assertTrue(result.cancelled)
        self.assertFalse(any(call[0] == "qwen_image" for call in self.client.calls))


if __name__ == "__main__":
    unittest.main()

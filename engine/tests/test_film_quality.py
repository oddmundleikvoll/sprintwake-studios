import json
import tempfile
import unittest
from pathlib import Path

from server.film_quality import (ProductionMetrics, clean_policy, continuity_ledger,
                                  load_receipt, parse_verdict, receipt_key,
                                  review_scene_indices, save_receipt, audio_check, motion_check)


class FilmQualityTests(unittest.TestCase):
    def test_policy_caps_budget_and_clamps_anchor(self):
        p = clean_policy({"quality": "thorough", "endAnchorStrength": 4,
                          "speechMode": "recording", "prepareOnly": True})
        self.assertEqual(p["reviewBudgetFraction"], .10)
        self.assertEqual(p["endAnchorStrength"], 1.0)
        self.assertTrue(p["prepareOnly"])

    def test_review_sampling_is_bounded(self):
        self.assertEqual(review_scene_indices(30, {"reviewMode": "balanced"}), [0, 14, 29])
        self.assertEqual(review_scene_indices(30, {"reviewMode": "fast"}), [])

    def test_verdict_allowlists_and_keeps_observation(self):
        verdict = parse_verdict(json.dumps({"verdict": "pause", "observedEndState": {"door": "open"},
                                            "issues": [{"severity": "evil", "action": "execute",
                                                        "issue": "x", "shotIndex": 2}]}))
        self.assertEqual(verdict["issues"][0]["severity"], "warning")
        self.assertEqual(verdict["issues"][0]["action"], "review")
        self.assertEqual(verdict["observedEndState"]["door"], "open")

    def test_receipt_requires_exact_key(self):
        policy = clean_policy()
        key = receipt_key(clip_hashes=["a"], reference_hashes=["b"], prompts=["p"], policy=policy)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "receipt.json"
            save_receipt(path, {"schema": 1, "key": key, "observed": {"x": 1}})
            self.assertEqual(load_receipt(path, key)["observed"]["x"], 1)
            self.assertIsNone(load_receipt(path, key + "x"))

    def test_continuity_does_not_merge_planned_and_observed(self):
        out = continuity_ledger({"scene": 1}, {"scene": 1, "end": "open"}, [{"key": "r"}])
        self.assertEqual(out["planned"], {"scene": 1})
        self.assertEqual(out["observed"]["end"], "open")

    def test_metrics_records_phase(self):
        metrics = ProductionMetrics(); metrics.start("review"); row = metrics.finish("review", reused=True)
        self.assertTrue(row["reused"])
        self.assertIn("review", metrics.as_dict()["phases"])

    def test_invalid_policy_and_unavailable_review_are_not_accepted(self):
        for value in ({"reviewMode": "invented"}, {"endAnchorStrength": float("nan")},
                      {"prepareOnly": "yes"}, {"speechMode": "invented"}):
            with self.assertRaises(ValueError):
                clean_policy(value)
        self.assertIsNone(parse_verdict({"observedEndState": "Invented"})["observedEndState"])
        verdict = parse_verdict({"verdict": "pass", "observedEndState": "x"*9000})
        self.assertLessEqual(len(verdict["observedEndState"]), 4000)

    def test_late_speech_is_audible_and_partial_transcript_is_not_a_match(self):
        import wave
        import numpy as np
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dialogue.wav"
            data = np.concatenate((np.zeros(48000), .1*np.sin(np.arange(48000)*.1)))
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                wav.writeframes((data*32767).astype('<i2').tobytes())
            check = audio_check(path, "Hei, der er du!", transcriber=lambda *a, **kw: "Hei")
            self.assertFalse(check["silent"])
            self.assertFalse(check["transcriptMatch"])
            check = audio_check(path, "Hei, der er du!", transcriber=lambda *a, **kw: "Hei der er du.")
            self.assertTrue(check["transcriptMatch"])

    def test_motion_check_is_advisory_and_hold_suppresses_warning(self):
        import av
        import numpy as np
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "static.mp4"
            with av.open(str(path), "w") as output:
                stream = output.add_stream("mpeg4", rate=8)
                stream.width, stream.height = 32, 32
                stream.pix_fmt = "yuv420p"
                for _ in range(5):
                    frame = av.VideoFrame.from_ndarray(np.zeros((32, 32, 3), dtype=np.uint8), format="rgb24")
                    for packet in stream.encode(frame): output.mux(packet)
                for packet in stream.encode(): output.mux(packet)
            action = motion_check(path, "action")
            hold = motion_check(path, "hold")
            self.assertTrue(action["available"])
            self.assertTrue(action["lowMotion"])
            self.assertFalse(hold["lowMotion"])
            self.assertEqual(action["method"], "PyAV RGB temporal difference advisory")

    def test_motion_check_samples_remainder_of_clip(self):
        import av
        import numpy as np
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "late-motion.mp4"
            with av.open(str(path), "w") as output:
                stream = output.add_stream("mpeg4", rate=8)
                stream.width, stream.height = 32, 32
                stream.pix_fmt = "yuv420p"
                for index in range(32):
                    image = np.zeros((32, 32, 3), dtype=np.uint8)
                    if index >= 8:
                        image[:, min(31, index - 8):min(32, index - 8 + 4)] = 255
                    frame = av.VideoFrame.from_ndarray(image, format="rgb24")
                    for packet in stream.encode(frame): output.mux(packet)
                for packet in stream.encode(): output.mux(packet)
            report = motion_check(path, "action")
            self.assertTrue(report["available"])
            self.assertFalse(report["lowMotion"])

    def test_review_receipt_changes_when_an_unsampled_clip_changes(self):
        from types import SimpleNamespace
        from server.film_review import SceneReviewer
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            opening = root / "opening.png"
            opening.write_bytes(b"reference")
            shots = [{"id": str(i), "title": str(i), "prompt": "same", "dialogue": ""} for i in range(4)]
            for i in range(1, 5):
                directory = root / "shots" / f"{i:02d}"
                directory.mkdir(parents=True)
                (directory / "clip.mp4").write_bytes(b"output")
                (directory / "director-thumb.png").write_bytes(b"frame")
            scene = {"title": "Scene", "shots": [{"end": "done"}]}
            reviewer = SceneReviewer(root, {"scenes": [scene]}, clean_policy({"reviewMode": "fast"}),
                SimpleNamespace(model="test"), release_images=lambda: None, release_language=lambda: None,
                set_phase=lambda **kw: None, cancel=lambda: False)
            first = reviewer.scene(1, scene, shots, {"shots": shots}, opening, [], 100)
            # Visual sampling uses clips 1, 3 and 4. Technical/audio receipts
            # must still be invalidated by a new take of clip 2.
            (root / "shots" / "02" / "clip.mp4").write_bytes(b"new take")
            second = reviewer.scene(1, scene, shots, {"shots": shots}, opening, [], 100)
            self.assertNotEqual(first["key"], second["key"])


if __name__ == "__main__":
    unittest.main()

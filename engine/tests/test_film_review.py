import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from server.film_quality import parse_verdict, review_sample
from server.film_review import SceneReviewer


class FilmReviewContractTests(unittest.TestCase):
    def test_asset_observations_are_bounded_to_known_ids(self):
        result = parse_verdict({
            "verdict": "pass",
            "observedEndState": "The gate is closed.",
            "observedAssetStates": {
                "gate": "closed",
                "invented": "not allowed",
                "nested": {"bad": "shape"},
            },
        }, allowed_asset_ids={"gate"})
        self.assertEqual(result["observedAssetStates"], {"gate": "closed"})
        self.assertEqual(result["observedEndState"], "The gate is closed.")

    def test_unavailable_never_becomes_approval(self):
        result = review_sample(lambda *_: "not json", ["sheet.jpg"], {}, 3)
        self.assertFalse(result["available"])
        self.assertEqual(result["verdict"], "unavailable")

    def test_cancellation_is_not_swallowed(self):
        def cancelled(*_):
            raise InterruptedError("stop")
        with self.assertRaises(InterruptedError):
            review_sample(cancelled, [], {}, 3)

    def test_cold_start_releases_images_before_start_and_language_after(self):
        class Service:
            model = "fake-27b"
            startup_timeout = 720.0
            def __init__(self, events): self.events = events
            def ensure_ready(self, timeout): self.events.append(("start", timeout))
            def complete_with_media(self, *args, **kwargs):
                self.events.append(("infer", kwargs["timeout"]))
                return '{"verdict":"pass","observedEndState":"tail","observedAssetStates":{"garden":"intact"}}'

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); events = []
            opening = root / "opening.png"; Image.new("RGB", (32, 32), "blue").save(opening)
            clip = root / "clip.mp4"; clip.write_bytes(b"neutral")
            shot = {"id": "shot-1", "title": "Beat", "prompt": "walk", "start": "a",
                    "action": "walk", "end": "b", "camera": "wide", "dialogue": ""}
            scene = {"title": "One", "shots": [shot], "locationId": "garden", "stateAfter": "tail"}
            plan = {"scenes": [scene], "filmBible": {"locations": [{"id": "garden"}]}}
            service = Service(events)
            reviewer = SceneReviewer(root, plan, {"reviewMode": "balanced",
                "reviewBudgetFraction": .05, "maxReviewCalls": 3}, service,
                release_images=lambda: events.append("images"),
                release_language=lambda: events.append("language"),
                set_phase=lambda **_: None, cancel=lambda: False)
            with patch("server.film_review.assemble.shot_clip", return_value=clip), \
                 patch("server.film_review.file_hash", return_value="fixed"), \
                 patch("server.film_review.motion_check", return_value={"available": False}), \
                 patch("server.film_review.audio_check", return_value={"available": False}):
                result = reviewer.scene(1, scene, [shot], {"shots": [shot]}, opening, [], 1)
            self.assertTrue(result["available"])
            self.assertEqual(events[0], "images")
            self.assertEqual(events[1], ("start", 720.0))
            self.assertEqual(events[-1], "language")
            self.assertEqual(result["observedAssetStates"], {"garden": "intact"})

    def test_cleanup_failure_is_hard_stop(self):
        class Service:
            model = "fake"
            startup_timeout = 720.0
            def ensure_ready(self, timeout): pass
            def complete_with_media(self, *args, **kwargs): return '{"verdict":"pass"}'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); opening = root / "opening.png"
            Image.new("RGB", (16, 16), "blue").save(opening)
            shot = {"id": "s", "title": "s", "prompt": "p", "dialogue": ""}
            scene = {"title": "s", "shots": [shot]}
            reviewer = SceneReviewer(root, {"scenes": [scene]},
                {"reviewMode": "balanced", "reviewBudgetFraction": .05, "maxReviewCalls": 3}, Service(),
                release_images=lambda: None,
                release_language=lambda: (_ for _ in ()).throw(RuntimeError("release failed")),
                set_phase=lambda **_: None, cancel=lambda: False)
            with patch("server.film_review.assemble.shot_clip", return_value=root / "clip.mp4"), \
                 patch("server.film_review.file_hash", return_value="fixed"), \
                 patch("server.film_review.motion_check", return_value={"available": False}), \
                 patch("server.film_review.audio_check", return_value={"available": False}):
                (root / "clip.mp4").write_bytes(b"neutral")
                with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                    reviewer.scene(1, scene, [shot], {"shots": [shot]}, opening, [], 1)

    def test_cancel_after_startup_cleans_up_without_inference_or_approval(self):
        events, cancelled = [], [False]
        class Service:
            startup_timeout = 8
            def ensure_ready(self, timeout):
                events.append(("startup", timeout)); cancelled[0] = True
            def complete_with_media(self, *args, **kwargs):
                raise AssertionError("Cancelled startup must not infer")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); opening = root / "opening.png"
            Image.new("RGB", (16, 16), "blue").save(opening)
            clip = root / "clip.mp4"; clip.write_bytes(b"fixture")
            shot = {"id":"s", "title":"Shot", "prompt":"Wave", "dialogue":""}
            scene = {"title":"Scene", "shots":[shot]}
            reviewer = SceneReviewer(root, {"scenes":[scene]},
                {"reviewMode":"balanced", "reviewBudgetFraction":.05, "maxReviewCalls":3}, Service(),
                release_images=lambda: events.append("images"),
                prepare_language=lambda: events.append("prepare"),
                release_language=lambda: events.append("language"),
                confirm_language_released=lambda: events.append("empty"),
                set_phase=lambda **_: None, cancel=lambda: cancelled[0])
            with patch("server.film_review.assemble.shot_clip", return_value=clip), \
                 patch("server.film_review.motion_check", return_value={}):
                with self.assertRaises(InterruptedError):
                    reviewer.scene(1, scene, [shot], {"shots":[shot]}, opening, [], 1)
            self.assertEqual(events, ["images", "prepare", ("startup",720.0), "language", "empty"])
            self.assertFalse(list((root / "quality").glob("scene-*.json")))

    def test_next_opening_is_in_contact_sheet_and_pixel_change_invalidates_receipt(self):
        calls = []
        class Service:
            def ensure_ready(self, timeout): pass
            def complete_with_media(self, *args, **kwargs):
                with Image.open(kwargs["images"][0]) as sheet:
                    calls.append(sheet.size)
                    self.asserted = sheet.getpixel((256, 1792))
                return '{"verdict":"pass","observedEndState":"Robot holds cup"}'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); images = []
            for name in ("opening", "portrait1", "portrait2", "next"):
                path = root / (name + ".png"); Image.new("RGB",(32,32),"blue").save(path); images.append(path)
            clip = root / "clip.mp4"; clip.write_bytes(b"fixture")
            shots = [{"id":str(i),"title":"Shot", "prompt":"Wave", "dialogue":""} for i in range(3)]
            for i in range(1,4):
                directory = root / "shots" / f"{i:02d}"
                (directory / "frames").mkdir(parents=True)
                Image.new("RGB",(32,32),"red").save(directory / "director-thumb.png")
                Image.new("RGB",(32,32),"red").save(directory / "frames/frame-0001.png")
            scene = {"title":"Scene", "shots":shots}; service = Service()
            reviewer = SceneReviewer(root, {"scenes":[scene]},
                {"reviewMode":"balanced", "reviewBudgetFraction":.05, "maxReviewCalls":3}, service,
                release_images=lambda: None, release_language=lambda: None,
                set_phase=lambda **_: None, cancel=lambda: False)
            args = (1, scene, shots, {"shots":shots}, images[0], [("A",images[1]),("B",images[2])], 1)
            with patch("server.film_review.assemble.shot_clip", return_value=clip), \
                 patch("server.film_review.motion_check", return_value={}):
                first = reviewer.scene(*args, next_scene={"title":"Next"}, next_opening=images[3])
                self.assertEqual(calls, [(1024,2048)])
                self.assertGreater(service.asserted[2], 180)
                reviewer.scene(*args, next_scene={"title":"Next"}, next_opening=images[3])
                self.assertEqual(len(calls),1)
                Image.new("RGB",(32,32),"green").save(images[3])
                second = reviewer.scene(*args, next_scene={"title":"Next"}, next_opening=images[3])
            self.assertNotEqual(first["key"],second["key"])
            self.assertEqual(len(calls),2)


if __name__ == "__main__":
    unittest.main()

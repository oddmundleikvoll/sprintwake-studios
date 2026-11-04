import tempfile
import unittest
from pathlib import Path

from review_gate import canonical_ref, plan_images, plan_images_complete, receipt_snapshot, review_images, review_images_complete, review_videos, review_videos_complete
from workspace import Workspace


class ReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        self.workspace.ensure_project("film")
        project = self.root / "Projects" / "film"
        (project / "Output" / "Images" / "card.png").write_bytes(b"png")
        (project / "Output" / "Images" / "tail.png").write_bytes(b"png")
        (project / "shots" / "01").mkdir(parents=True)
        (project / "shots" / "01" / "clip.mp4").write_bytes(b"video")

    def tearDown(self):
        self.temp.cleanup()

    def test_real_plan_shapes_and_preview_stills(self):
        plan = {"cards": [{"ref": {"path": "film/Output/Images/card.png"}}], "images": []}
        self.assertEqual(["card.png"], [p.name for p in plan_images(plan, self.workspace)])
        self.assertTrue(plan_images_complete(plan, self.workspace))
        preview = {"tail": {"path": "film/Output/Images/tail.png"}, "nextOpening": None}
        self.assertEqual(["tail.png"], [p.name for p in review_images(preview, self.workspace)])
        self.assertTrue(review_images_complete(preview, self.workspace))

    def test_missing_tail_or_next_opening_is_incomplete(self):
        self.assertFalse(review_images_complete({"tail": {"path": "film/nope.png"}, "nextOpening": None}, self.workspace))
        self.assertFalse(review_images_complete({"tail": {"path": "film/Output/Images/tail.png"}, "nextOpening": {"path": "film/nope.png"}}, self.workspace))

    def test_missing_plan_card_is_incomplete(self):
        self.assertFalse(plan_images_complete({"cards": [{"ref": {"path": "film/Output/Images/card.png"}}, {"ref": {"path": "film/nope.png"}}]}, self.workspace))

    def test_review_videos_follow_shot_ids_and_canonical_fallback(self):
        board = {"shots": [{"id": "s1", "outputs": []}]}
        preview = {"shotIds": ["s1"], "tail": {"path": "film/Output/Images/tail.png"}}
        self.assertEqual(["clip.mp4"], [p.name for p in review_videos(preview, board, self.workspace, "film")])

    def test_empty_media_rejects_by_returning_empty(self):
        self.assertEqual([], plan_images({"cards": [{"ref": {"path": "film/nope.png"}}]}, self.workspace))
        self.assertEqual([], review_videos({"shotIds": ["missing"]}, {"shots": []}, self.workspace, "film"))
        self.assertFalse(review_videos_complete({"shotIds": ["s1", "missing"]}, {"shots": [{"id": "s1", "outputs": []}]}, self.workspace, "film"))

    def test_receipt_changes_when_preview_key_changes(self):
        a = {"project": {"revision": "r1", "reviewPreview": {"key": "a", "shotIds": ["s1"]}}}
        b = {"project": {"revision": "r1", "reviewPreview": {"key": "b", "shotIds": ["s1"]}}}
        self.assertNotEqual(receipt_snapshot(a), receipt_snapshot(b))

    def test_catalogue_refs_share_identity_across_forms(self):
        absolute = self.root / "Projects" / "film" / "Output" / "Images" / "card.png"
        self.assertEqual(canonical_ref(str(absolute), self.workspace), canonical_ref("/media/film/Output/Images/card.png", self.workspace))
        self.assertEqual(canonical_ref("film/Output/Images/card.png", self.workspace), canonical_ref(str(absolute), self.workspace))


if __name__ == "__main__":
    unittest.main()

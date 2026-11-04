import tempfile
import unittest
from pathlib import Path

from production_model import (apply_proposals, apply_view, film_action, next_step,
                               view_project)
from project import Project
from workspace import Workspace


class ProductionModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Path(self.tmp.name) / "Raven Studio")
        self.ws.ensure_project("film")
        root = self.ws.projects_dir / "film"
        (root / "Input" / "hero.png").write_bytes(b"png")
        (root / "Output" / "Video" / "clip.mp4").write_bytes(b"video")
        self.board = {"schema": 1, "name": "Testfilm", "sceneDescription": "En rolig scene", "revision": "r1",
                      "characters": [{"id": "c1", "name": "Ada", "image": "film/Input/hero.png", "private": {"x": 1}}],
                      "shots": [{"id": "s1", "title": "Start", "frames": 121, "start": "Hun står", "action": "Hun ser", "end": "Hun smiler", "camera": "total",
                                  "startRef": "film/Input/hero.png", "outputs": ["film/Output/Video/clip.mp4"], "recipe": {"seed": 4}, "serverMeta": {"keep": True}}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_view_roundtrip_keeps_outputs_and_recipe(self):
        view = view_project("film", self.board, self.ws)
        self.assertEqual(view.shots[0].take.video.endswith("clip.mp4"), True)
        view.shots[0].notes = "Regissørnotat"
        view.shots[0].reviewed = True
        view.shots[0].title = "Ny tittel"
        result = apply_view(self.board, view)
        self.assertEqual(result["shots"][0]["outputs"], self.board["shots"][0]["outputs"])
        self.assertEqual(result["shots"][0]["recipe"], {"seed": 4})
        self.assertEqual(result["shots"][0]["ravenNotes"], "Regissørnotat")
        self.assertTrue(result["shots"][0]["ravenReviewed"])
        self.assertNotIn("reviewed", result["shots"][0])

    def test_draft_does_not_attach_stale_ordinal_video(self):
        stale = self.ws.projects_dir / "film" / "shots" / "01"
        stale.mkdir(parents=True)
        (stale / "clip.mp4").write_bytes(b"old unrelated clip")
        board = {"name": "Reordered", "shots": [{"id": "new-draft", "title": "Nytt", "status": "draft"}]}
        view = view_project("film", board, self.ws)
        self.assertIsNone(view.shots[0].take)

    def test_opening_unreviewed_view_does_not_add_review_flag(self):
        view=view_project("film",self.board,self.ws)
        result=apply_view(self.board,view)
        self.assertNotIn("ravenReviewed",result["shots"][0])
        self.board["shots"][0]["reviewed"]=True
        view=view_project("film",self.board,self.ws)
        view.shots[0].reviewed=False
        self.assertFalse(apply_view(self.board,view)["shots"][0]["ravenReviewed"])

    def test_proposals_require_review_and_known_cast(self):
        with self.assertRaises(ValueError):
            apply_proposals(self.board, [{"operation": "update_shot", "reviewed": True, "id": "s1", "fields": {"outputs": []}}])
        with self.assertRaises(ValueError):
            apply_proposals(self.board, [{"operation": "add_shot", "reviewed": True, "shot": {"title": "X", "characterIds": ["missing"]}}])
        result = apply_proposals(self.board, [{"operation": "add_shot", "reviewed": True, "shot": {"title": "X", "characterIds": ["c1"]}}])
        self.assertEqual(result["shots"][-1]["characterIds"], ["c1"])
        self.assertIn("status", result["shots"][-1])

    def test_film_gate_receipts_and_stale_plan(self):
        context = {"project": {"slug": "film", "isFilm": True, "revision": "rev-7", "canResume": True,
                                "imagePlan": {"digest": "digest-7"}, "imagePlanStale": False,
                                "reviewRequired": True, "reviewPreview": {"url": "review"}}, "anchorRevision": "anchor"}
        self.assertEqual(film_action(context, "approve_images", True)["acceptImagePlan"], "digest-7")
        self.assertEqual(film_action(context, "approve_review", True, "sett")['reviewNote'], "sett")
        self.assertNotIn("acceptReview", film_action(context, "resume"))
        stale = {**context, "project": {**context["project"], "imagePlanStale": True}}
        with self.assertRaises(ValueError): film_action(stale, "approve_images", True)
        with self.assertRaises(ValueError): film_action(context, "approve_review", False)

    def test_anchor_and_character_defaults_are_safe(self):
        context = {"project": {"slug": "film", "isFilm": True, "revision": "rev-7", "continuationAnchor": True}}
        self.assertTrue(film_action(context, "resume")["continuationAnchor"])
        result = apply_proposals(self.board, [{"tool": "add_character", "reviewed": True, "character": {"name": "Ny"}}])
        self.assertTrue(result["characters"][-1]["id"].startswith("c"))
        result = apply_proposals(self.board, [{"tool": "update_shot", "reviewed": True, "shotId": "s1", "fields": {"directorScene": 2}}])
        self.assertEqual(result["shots"][0]["directorScene"], 2)

    def test_film_bible_is_editable_without_server_fields_and_links_shots(self):
        bible = {
            "acts": [{"id": "act-1", "title": "Ankomst", "description": "Ved porten."}],
            "locations": [{"id": "loc-gate", "name": "Porten", "description": "En gammel port.",
                           "referenceImageId": "img-gate"}],
            "props": [{"id": "prop-lantern", "name": "Lykt"}],
        }
        result = apply_proposals(self.board, [{
            "operation": "set_board_fields", "reviewed": True,
            "fields": {"filmBible": bible},
        }, {
            "operation": "add_shot", "reviewed": True,
            "shot": {"title": "Ved porten", "directorScene": 1,
                      "locationId": "loc-gate", "propIds": ["prop-lantern"],
                      "characterIds": ["c1"]},
        }])
        self.assertEqual(result["filmBible"]["locations"][0]["id"], "loc-gate")
        self.assertEqual(result["shots"][-1]["locationId"], "loc-gate")
        self.assertEqual(result["shots"][-1]["propIds"], ["prop-lantern"])
        self.assertEqual(result["shots"][-1]["dialogueSource"], "native")

    def test_film_bible_rejects_runtime_fields_and_unsafe_references(self):
        with self.assertRaises(ValueError):
            apply_proposals(self.board, [{
                "operation": "set_board_fields", "reviewed": True,
                "fields": {"filmBible": {"locations": [{"id": "x", "outputs": ["bad"]}]}},
            }])
        with self.assertRaises(ValueError):
            apply_proposals(self.board, [{
                "operation": "set_board_fields", "reviewed": True,
                "fields": {"filmBible": {"props": [{"id": "x", "referenceImage": {"path": "/etc/passwd"}}]}},
            }])

    def test_onboarding_is_explicit(self):
        self.assertEqual(next_step({}, {})["page"], "brief")
        self.assertEqual(next_step({"sceneDescription": "x"}, {})["page"], "bible")
        scenery = {"sceneDescription": "x", "shots": [{"id": "s", "start": "a", "action": "b", "end": "c", "camera": "d"}]}
        gated = {"project": {"imageApprovalRequired": True, "imagePlan": {"digest": "d"}}}
        self.assertEqual(next_step(scenery, gated)["action"], "approve_images")
        self.assertEqual(next_step(scenery, {"project": {"canProduce": False}})["page"], "production")


if __name__ == "__main__":
    unittest.main()

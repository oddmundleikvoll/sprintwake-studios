import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.backends.render_helpers import _resolved_prompt
from server.film_planning import FilmPlanner, validate_plan
from server.film_storyboard import storyboard_plan
from server.store import Store, default_board, default_shot, render_fingerprint
from server.storyboard_chat import compact_board_context, _clean_fields, SHOT_FIELDS, BOARD_FIELDS, validate_actions
from server.ltx_anchors import image_catalog
from tests import test_film as fixtures
from server.film import FilmController
from server.app import Handler


class FilmContinuityTests(unittest.TestCase):
    def board(self):
        board = default_board("Garden")
        board["filmBible"] = {"locations": [{"id": "garden", "description": "Stone path left, wooden bench right."}],
                             "props": [{"id": "cup", "description": "Small blue enamel cup."}]}
        board["renderStyle"] = "Stop motion"
        shot = default_shot(board["defaults"])
        shot.update(prompt="Old contradictory text.", start="Robot waits beside the path.",
                    action="Robot walks along the path.", end="Robot reaches the bench.",
                    camera="Wide locked shot.", locationId="garden", propIds=["cup"],
                    stateBefore="Cup on bench.", stateAfter="Cup remains on bench.")
        board["shots"] = [shot]
        return board

    def test_structured_context_reaches_images_and_video(self):
        board = self.board()
        plan = storyboard_plan(board, strict=True)
        scene, card = plan["scenes"][0], plan["scenes"][0]["shots"][0]
        self.assertIn("wooden bench", scene["setting"])
        self.assertEqual(scene["propDescriptions"]["cup"], "Small blue enamel cup.")
        self.assertEqual(card["action"], board["shots"][0]["action"])
        prompt = _resolved_prompt(board["shots"][0], board)
        for text in ("wooden bench", "blue enamel", "Robot walks", "Cup remains"):
            self.assertIn(text, prompt)
        self.assertNotIn("Old contradictory", prompt)
        for internal in ("Explicit location", "Explicit props", "START:", "ACTION:", "END:", "Motion intent:"):
            self.assertNotIn(internal, prompt)

    def test_structured_authoring_does_not_require_duplicate_free_text(self):
        board = self.board()
        board["shots"][0]["prompt"] = ""
        plan = storyboard_plan(board, strict=True)
        self.assertEqual(plan["scenes"][0]["shots"][0]["action"], board["shots"][0]["action"])
        board["shots"][0]["action"] = "Walk\n---\nRun"
        with self.assertRaisesRegex(ValueError, "klippskiller"):
            storyboard_plan(board, strict=True)

    def test_legacy_defaults_do_not_stale_existing_fingerprints(self):
        board = self.board()
        shot = board["shots"][0]
        for field in ("start", "action", "end", "camera", "locationId", "stateBefore", "stateAfter", "motionIntent", "propIds", "assetStates"):
            shot.pop(field, None)
        board.pop("filmBible")
        before = render_fingerprint(shot, board)
        prompt = _resolved_prompt(shot, board)
        with tempfile.TemporaryDirectory() as directory:
            Store(Path(directory)).migrate(board)
        self.assertEqual(before, render_fingerprint(board["shots"][0], board))
        self.assertEqual(prompt, _resolved_prompt(board["shots"][0], board))

    def test_each_structured_edit_stales_the_take(self):
        board = self.board()
        baseline = render_fingerprint(board["shots"][0], board)
        for field, value in (("action", "Robot waves."), ("camera", "Close shot."),
                             ("locationId", "other"), ("stateAfter", "Cup is held."),
                             ("motionIntent", "hold"), ("assetStates", {"cup": "held"})):
            changed = copy.deepcopy(board)
            changed["shots"][0][field] = value
            self.assertNotEqual(baseline, render_fingerprint(changed["shots"][0], changed), field)
        board["filmBible"]["locations"][0]["description"] = "Different layout."
        self.assertNotEqual(baseline, render_fingerprint(board["shots"][0], board))

    def test_camera_cut_keeps_explicit_world_identity(self):
        board = self.board()
        second = copy.deepcopy(board["shots"][0])
        second.update(id="next", directorScene=2, camera="Close shot.")
        board["shots"][0]["directorScene"] = 1
        board["shots"].append(second)
        plan = storyboard_plan(board, strict=True)
        self.assertEqual(len(plan["scenes"]), 2)
        self.assertEqual([scene["locationId"] for scene in plan["scenes"]], ["garden", "garden"])
        second["startRef"] = {"kind": "chain", "from": board["shots"][0]["id"]}
        with self.assertRaisesRegex(ValueError, "kjede"):
            storyboard_plan(board, strict=True)

    def test_director_sees_and_can_propose_structured_fields(self):
        board = self.board()
        context = compact_board_context(board)
        self.assertEqual(context["filmBible"], board["filmBible"])
        for field in ("start", "action", "end", "camera", "locationId", "stateBefore", "stateAfter", "motionIntent", "propIds"):
            self.assertEqual(context["shots"][0][field], board["shots"][0][field])
        fields = {key: board["shots"][0][key] for key in ("start", "action", "end", "camera", "locationId", "motionIntent", "propIds", "assetStates")}
        self.assertEqual(_clean_fields(fields, SHOT_FIELDS), fields)
        self.assertEqual(_clean_fields({"filmBible": board["filmBible"]}, BOARD_FIELDS)["filmBible"]["locations"], board["filmBible"]["locations"])

    def test_bible_reference_image_uses_existing_catalog_id_and_retains_on_partial_edit(self):
        board = self.board()
        board["filmBible"]["locations"][0]["referenceImage"] = {"path": "ref.png"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ref.png"
            path.write_bytes(b"not decoded here")
            catalog = image_catalog(board, Path(directory))
            image_id = next(iter(catalog))
            notes = []
            actions = validate_actions(
                [{"tool": "set_board_fields", "fields": {"filmBible": {
                    "locations": [{"id": "garden", "description": "Updated path"}],
                    "props": board["filmBible"]["props"],
                }}}],
                board, notes=notes, data_dir=Path(directory))
            entry = actions[0]["fields"]["filmBible"]["locations"][0]
            self.assertEqual(entry["referenceImage"]["path"], "ref.png")
            self.assertEqual(notes, [])

            chosen = validate_actions(
                [{"tool": "set_board_fields", "fields": {"filmBible": {
                    "locations": [{"id": "garden", "referenceImageId": image_id}]}}}],
                board, notes=notes, data_dir=Path(directory))
            bible = chosen[0]["fields"]["filmBible"]
            self.assertEqual(bible["locations"][0]["referenceImage"]["path"], "ref.png")
            self.assertNotIn("referenceImageId", bible["locations"][0])
            self.assertEqual(bible["props"], board["filmBible"]["props"])

            notes = []
            invalid = validate_actions(
                [{"tool": "set_board_fields", "fields": {"filmBible": {
                    "locations": [{"id": "garden", "referenceImageId": "/tmp/fake.png"}],
                    "props": board["filmBible"]["props"],
                }}}],
                board, notes=notes, data_dir=Path(directory))
            self.assertNotIn("referenceImageId", invalid[0]["fields"]["filmBible"]["locations"][0])
            self.assertEqual(invalid[0]["fields"]["filmBible"]["locations"][0]["referenceImage"]["path"], "ref.png")
            self.assertTrue(notes)

            cleared = validate_actions(
                [{"tool": "set_board_fields", "fields": {"filmBible": {
                    "locations": [{"id": "garden", "referenceImageId": ""}],
                    "props": board["filmBible"]["props"],
                }}}],
                board, notes=[], data_dir=Path(directory))
            self.assertNotIn("referenceImageId", cleared[0]["fields"]["filmBible"]["locations"][0])
            self.assertNotIn("referenceImage", cleared[0]["fields"]["filmBible"]["locations"][0])

    def test_new_scene_map_rejects_overlarge_or_undefined_world_before_cards(self):
        planner = FilmPlanner(None)
        outline = copy.deepcopy(fixtures.PLAN)
        scene = {**outline["scenes"][0], "shotCount": 7}
        with self.assertRaisesRegex(ValueError, "1–6"):
            planner._scene_map([scene], outline, 7)
        scene["shotCount"] = 2
        scene.pop("locationId", None)
        with self.assertRaisesRegex(ValueError, "locationId"):
            planner._scene_map([scene], outline, 2)

    def test_legacy_plan_readable_but_not_new_production(self):
        board = self.board()
        board["shots"][0]["action"] = ""
        self.assertTrue(storyboard_plan(board)["planWarnings"])
        with self.assertRaisesRegex(ValueError, "action"):
            storyboard_plan(board, strict=True)
        legacy = copy.deepcopy(fixtures.PLAN)
        legacy.pop("filmBible", None)
        for scene in legacy["scenes"]:
            scene.pop("locationId", None)
        validate_plan(legacy, 2)
        with self.assertRaises(ValueError):
            validate_plan(legacy, 2, strict=True)

    def test_direct_planner_retries_missing_location_description(self):
        invalid = copy.deepcopy(fixtures.PLAN)
        invalid["filmBible"]["locations"][0].pop("description", None)
        with self.assertRaisesRegex(ValueError, "beskrivelse"):
            validate_plan(invalid, 2, strict=True)
        class Service:
            def __init__(self):
                self.responses = [json.dumps(invalid), json.dumps(fixtures.PLAN)]
            def complete(self, *args, **kwargs):
                return self.responses.pop(0)
        service = Service()
        plan = FilmPlanner(service).plan("Robot walks", 2)
        self.assertEqual(service.responses, [])
        self.assertEqual(plan["scenes"][0]["locationId"], "garden")

    def test_new_plans_cannot_hide_a_camera_cut_inside_native_continuation(self):
        plan = copy.deepcopy(fixtures.PLAN)
        plan["scenes"][0]["shots"][1]["camera"] = "New close-up from the opposite side."
        with self.assertRaisesRegex(ValueError, "camera"):
            validate_plan(plan, 2, strict=True)

    @patch("server.film.assert_other_models_idle")
    @patch("server.film.prepare_language_memory")
    def test_default_off_does_not_remove_existing_automatic_anchors(self, *_):
        with tempfile.TemporaryDirectory() as directory:
            context = fixtures.FilmTests().context(Path(directory))
            client = fixtures.FakeComfy()
            film = FilmController(context, client)
            with patch.object(client, "director_scene", side_effect=AssertionError("No video before approval")):
                result = film.start({"description": "Robot in garden", "duration": 7, "automaticEndAnchors": True})
                context.orch._thread.join(10)
                self.assertEqual(film.status()["phase"], "prepared", film.status())
                before = context.store.load(result["slug"])
                anchors = copy.deepcopy([shot["imageAnchors"] for shot in before["shots"]])
                image_calls = len(client.reference_calls)
                film.start({"resumeSlug": result["slug"], "expectedRevision": before["filmAutomation"]["revision"],
                            "prepareOnly": True, "automaticEndAnchors": False})
                context.orch._thread.join(10)
            self.assertEqual(film.status()["phase"], "prepared", film.status())
            after = context.store.load(result["slug"])
            self.assertEqual(anchors, [shot["imageAnchors"] for shot in after["shots"]])
            self.assertEqual(len(client.reference_calls), image_calls)
            self.assertEqual(before["filmAutomation"]["imagePlan"]["digest"], after["filmAutomation"]["imagePlan"]["digest"])

    @patch("server.film.assert_other_models_idle")
    @patch("server.film.prepare_language_memory")
    def test_page_cannot_forge_approval_and_repairs_survive_preparation(self, *_):
        with tempfile.TemporaryDirectory() as directory:
            context = fixtures.FilmTests().context(Path(directory))
            client = fixtures.FakeComfy()
            film = context.film = FilmController(context, client)
            handler = object.__new__(Handler)
            handler.ctx = context
            with patch.object(client, "director_scene", side_effect=AssertionError("Images only")):
                result = film.start({"description": "Robot in garden", "duration": 7})
                context.orch._thread.join(10)
                slug = result["slug"]
                before = context.store.load(slug)
                forged = copy.deepcopy(before)
                forged["filmAutomation"]["imageApproval"] = {"digest": before["filmAutomation"]["imagePlan"]["digest"]}
                forged["filmAutomation"]["acceptedReviews"] = ["forged"]
                saved = handler._save_from_page(slug, forged)
                self.assertNotIn("imageApproval", saved["filmAutomation"])
                self.assertNotIn("acceptedReviews", saved["filmAutomation"])
                edited = copy.deepcopy(saved)
                edited["shots"][0]["stateAfter"] = "Robot still holds its blue cup."
                edited["filmBible"]["locations"][0]["description"] = "Wooden bench right, stone path left."
                saved = handler._save_from_page(slug, edited)
                self.assertTrue(saved["filmAutomation"]["imagePlanStale"])
                self.assertEqual(saved["filmAutomation"]["planSource"], "storyboard")
                with self.assertRaisesRegex(ValueError, "Bildeplanen er endret"):
                    film.start({"resumeSlug": slug, "expectedRevision": saved["filmAutomation"]["revision"],
                                "acceptImagePlan": before["filmAutomation"]["imagePlan"]["digest"]})
                film.start({"resumeSlug": slug, "expectedRevision": saved["filmAutomation"]["revision"], "prepareOnly": True})
                context.orch._thread.join(10)
            self.assertEqual(film.status()["phase"], "prepared", film.status())
            after = context.store.load(slug)
            self.assertEqual(after["shots"][0]["stateAfter"], edited["shots"][0]["stateAfter"])
            self.assertEqual(after["filmBible"], edited["filmBible"])

    @patch("server.film.assert_other_models_idle")
    @patch("server.film.prepare_language_memory")
    def test_first_sample_requires_human_review_even_for_intentional_hold(self, *_):
        with tempfile.TemporaryDirectory() as directory:
            class Service(fixtures.FakeSplash):
                def complete(self, *args, **kwargs):
                    plan = copy.deepcopy(fixtures.PLAN)
                    for shot in plan["scenes"][0]["shots"]:
                        shot["motionIntent"] = "hold"
                    return json.dumps(plan)
            context = fixtures.FilmTests().context(Path(directory), Service())
            client = fixtures.FakeComfy()
            film = FilmController(context, client)
            result = film.start({"description": "Quiet robot", "duration": 7})
            context.orch._thread.join(10)
            settings = context.store.load(result["slug"])["filmAutomation"]
            film.start({"resumeSlug": result["slug"], "expectedRevision": settings["revision"],
                        "acceptImagePlan": settings["imagePlan"]["digest"]})
            context.orch._thread.join(10)
            self.assertEqual(film.status()["phase"], "needs_review", film.status())
            self.assertFalse((context.store.project_dir(result["slug"]) / "final.mp4").exists())
            images = len(client.reference_calls)
            first_clip = context.store.project_dir(result["slug"]) / "shots/01/clip.mp4"
            stamp = first_clip.stat().st_mtime_ns
            settings = context.store.load(result["slug"])["filmAutomation"]
            film.start({"resumeSlug": result["slug"], "expectedRevision": settings["revision"], "acceptReview": True})
            context.orch._thread.join(10)
            self.assertEqual(film.status()["phase"], "done", film.status())
            self.assertEqual(first_clip.stat().st_mtime_ns, stamp)
            self.assertEqual(len(client.reference_calls), images)


if __name__ == "__main__":
    unittest.main()

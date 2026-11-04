"""Camera-boundary checkpoints on neutral temporary media; no model calls."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from server.film import FilmController
from server.ltx_anchors import image_path
from tests import test_film as fixtures

FakeComfy, FakeSplash, PLAN = fixtures.FakeComfy, fixtures.FakeSplash, fixtures.PLAN


class TwoSetups(FakeSplash):
    def complete(self, *args, **kwargs):
        plan = copy.deepcopy(PLAN)
        scene = plan["scenes"][0]
        first, second = copy.deepcopy(scene), copy.deepcopy(scene)
        first.update(title="First view", shots=[first["shots"][0]], locationId="garden",
                     stateBefore="Robot by the bench", stateAfter="Robot faces flower")
        second.update(title="Reverse view", shots=[second["shots"][1]], locationId="garden",
                      stateBefore="Robot faces flower", stateAfter="Robot has waved")
        plan["scenes"] = [first, second]
        plan["filmBible"] = {"locations": [{"id": "garden", "description": "Stone path left; bench right."}]}
        return json.dumps(plan)


class FilmBoundaryTests(unittest.TestCase):
    def context(self, root):
        return fixtures.FilmTests().context(root, TwoSetups())

    def join(self, ctx):
        ctx.orch._thread.join(15)
        self.assertFalse(ctx.orch._thread.is_alive())

    def resume(self, film, ctx, slug, **payload):
        board = ctx.store.load(slug)
        film.start({"resumeSlug": slug, "expectedRevision": board["filmAutomation"]["revision"], **payload})
        self.join(ctx)
        return ctx.store.load(slug)

    @patch("server.film.prepare_language_memory")
    @patch("server.film.assert_other_models_idle")
    def test_each_setup_requires_its_own_actual_video_boundary_acceptance(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp)); client = FakeComfy(); film = FilmController(ctx, client)
            slug = film.start({"description": "A comet destroys a city after the robot waves", "duration": 7, "reviewMode": "fast"})["slug"]
            self.join(ctx)
            manifest = ctx.store.load(slug)["filmAutomation"]["imagePlan"]
            with patch.object(client, "director_scene", wraps=client.director_scene) as submitted:
                board = self.resume(film, ctx, slug, acceptImagePlan=manifest["digest"])
            prompt = submitted.call_args.args[0][0]
            self.assertIn("Rob turns toward a flower", prompt)
            self.assertIn("Birds and soft wind", prompt)
            self.assertNotIn("comet", prompt)
            self.assertNotIn("START:", prompt)
            self.assertEqual(film.status()["phase"], "needs_review", film.status())
            first = board["filmAutomation"]["reviewRequired"]
            preview = board["filmAutomation"]["reviewPreview"]
            self.assertEqual(preview["scene"], 1)
            self.assertTrue(image_path(preview["tail"], ctx.data_dir, verify=True).is_file())
            self.assertTrue(image_path(preview["nextOpening"], ctx.data_dir, verify=True).is_file())
            self.assertFalse(preview["modelAvailable"])
            self.assertFalse((ctx.store.project_dir(slug) / "final.mp4").exists())
            with self.assertRaisesRegex(ValueError, "gjennomgang"):
                self.resume(film, ctx, slug)
            board = self.resume(film, ctx, slug, acceptReview=True, reviewNote="Robot is still by the bench.")
            self.assertEqual(film.status()["phase"], "needs_review", film.status())
            second = board["filmAutomation"]["reviewRequired"]
            self.assertNotEqual(first, second)
            self.assertEqual(board["filmAutomation"]["reviewPreview"]["scene"], 2)
            self.assertIsNone(board["filmAutomation"]["reviewPreview"]["nextOpening"])
            ack = board["filmAutomation"]["reviewAcknowledgements"][first]
            self.assertEqual(ack["note"], "Robot is still by the bench.")
            self.assertTrue(ack["boundaryConfirmed"])
            board = self.resume(film, ctx, slug, acceptReview=True)
            self.assertEqual(film.status()["phase"], "done", film.status())
            self.assertTrue((ctx.store.project_dir(slug) / "final.mp4").is_file())
            self.assertEqual(len(board["filmAutomation"]["reviewAcknowledgements"]), 2)

    @patch("server.film.prepare_language_memory")
    @patch("server.film.assert_other_models_idle")
    def test_explicit_environment_reference_reaches_scene_composition_and_approval(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp)); slug, board = ctx.store.create("Environment")
            plan = json.loads(TwoSetups().complete())
            # Compile the same authored camera setups through the saved-board route.
            board["filmBible"] = plan["filmBible"]
            board["characters"] = []
            board["shots"] = []
            from server.store import default_shot
            for index, scene in enumerate(plan["scenes"], 1):
                card = scene["shots"][0]
                shot = default_shot(board["defaults"])
                shot.update({key: card[key] for key in ("start", "action", "end", "camera")})
                shot.update(title=scene["title"], directorScene=index, locationId="garden", characterIds=[])
                board["shots"].append(shot)
            target = ctx.store.project_dir(slug) / "refs" / "approved-environment.png"
            Image.new("RGB", (32, 32), "blue").save(target)
            ref = {"path": target.relative_to(ctx.data_dir).as_posix()}
            board["filmBible"]["locations"][0]["referenceImage"] = ref
            board = ctx.store.save(slug, board)
            client = FakeComfy(); film = FilmController(ctx, client)
            from server.ltx_media import digest
            film.start({"storyboardSlug": slug, "storyboardRevision": digest(board), "reviewMode": "fast"})
            self.join(ctx)
            self.assertEqual(film.status()["phase"], "prepared", film.status())
            manifest = ctx.store.load(slug)["filmAutomation"]["imagePlan"]
            self.assertEqual(manifest["assets"][0]["id"], "garden")
            self.assertTrue(client.reference_calls)
            current_ref = ctx.store.load(slug)["filmBible"]["locations"][0]["referenceImage"]
            resolved = image_path(current_ref, ctx.data_dir, verify=True)
            self.assertTrue(all(any(Path(path) == resolved for path, _ in refs)
                                for _, refs, _ in client.reference_calls))

    @patch("server.film.prepare_language_memory")
    @patch("server.film.assert_other_models_idle")
    def test_repaired_take_retains_previous_published_media_and_scene_record(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            ctx = fixtures.FilmTests().context(Path(temp)); client = FakeComfy(); film = FilmController(ctx, client)
            slug = film.start({"description":"Robot waves", "duration":7, "reviewMode":"fast"})["slug"]
            fixtures.FilmTests()._finish_checkpoints(ctx, film, slug)
            self.assertEqual(film.status()["phase"], "done", film.status())
            root = ctx.store.project_dir(slug)
            original = (root / "shots/01/clip.mp4").read_bytes()
            board = ctx.store.load(slug)
            old_scene = copy.deepcopy(board["filmAutomation"]["scenes"][0])
            board["shots"][0]["action"] = "Rob takes one step toward the flower."
            board["filmAutomation"].update(planSource="storyboard", imagePlanStale=True)
            ctx.store.save(slug, board)
            native = client.director_scene
            run = fixtures.subprocess.run
            def changed_take(prompts, refs, destination, **kwargs):
                return native(prompts, refs, destination / "replacement", **kwargs)
            def blue_fixture(command, *args, **kwargs):
                command = [item.replace("color=red:", "color=blue:") if isinstance(item,str) else item for item in command]
                return run(command, *args, **kwargs)
            with patch.object(client,"director_scene",side_effect=changed_take), \
                 patch("tests.test_film.subprocess.run",side_effect=blue_fixture):
                film.start({"resumeSlug":slug, "prepareOnly":True})
                fixtures.FilmTests()._finish_checkpoints(ctx, film, slug)
            self.assertEqual(film.status()["phase"], "done", film.status())
            archived = list((root / "takes/shot-001").glob("*/clip.mp4"))
            self.assertEqual(len(archived),1)
            self.assertEqual(archived[0].read_bytes(), original)
            self.assertNotEqual((root / "shots/01/clip.mp4").read_bytes(), original)
            metadata = json.loads((archived[0].parent / "archive.json").read_text())["metadata"]
            self.assertEqual(metadata["scene"], old_scene)


if __name__ == "__main__":
    unittest.main()

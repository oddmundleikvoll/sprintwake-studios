"""CPU regressions for the resumable long-film production path.

These tests deliberately reuse the small fixtures from ``test_film``.  They
exercise the controller's real worker thread, but never load a model or touch a
user project.
"""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from server.film import FilmController
from server.film_planning import FilmPlanner
from server.film_production import FilmTimings, ReferenceBank, write_animatic
from server.film_comfy import ComfyFilmClient
from server.film_review import SceneReviewer
from server.film_quality import clean_policy
from server.ltx_anchors import image_path
from tests import test_film as _film_fixture

PLAN = _film_fixture.PLAN
FakeComfy = _film_fixture.FakeComfy
FakeSplash = _film_fixture.FakeSplash


class _TwoSceneSplash(FakeSplash):
    def complete(self, system, user, **kwargs):
        plan = copy.deepcopy(PLAN)
        first = copy.deepcopy(plan["scenes"][0])
        second = copy.deepcopy(plan["scenes"][0])
        first["title"], second["title"] = "Hage", "Benken"
        first["shots"] = [first["shots"][0]]
        second["shots"] = [second["shots"][1]]
        second["shots"][0]["dialogue"] = ""
        plan["scenes"] = [first, second]
        plan["filmBible"] = {"locations": [{"id": "garden", "description": "A green garden"}]}
        for scene in plan["scenes"]:
            scene.update(locationId="garden", assetStates={"garden": "intact"},
                         stateBefore="Robot is ready.", stateAfter="Robot has completed the beat.")
        return json.dumps(plan)


class _BlockingPlanner(FakeSplash):
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        self.entered.set()
        self.release.wait(3)
        return super().complete(*args, **kwargs)


class LongFilmTests(unittest.TestCase):
    def context(self, root, service=None):
        # Keep the established fixture in one place without inheriting its
        # broad test class (which would run all of test_film.py again).
        return _film_fixture.FilmTests().context(root, service)

    def _join(self, ctx, seconds=8):
        thread = ctx.orch._thread
        self.assertIsNotNone(thread)
        thread.join(seconds)
        self.assertFalse(thread.is_alive(), "film worker did not exit")

    def _resume_checkpoints(self, ctx, film, slug, *, accept_review=True):
        for _ in range(4):
            board = ctx.store.load(slug)
            settings = board.get("filmAutomation") or {}
            phase = film.status().get("phase")
            if phase == "prepared":
                manifest = settings.get("imagePlan") or {}
                film.start({"resumeSlug": slug, "expectedRevision": settings.get("revision"),
                            "acceptImagePlan": manifest.get("digest")})
            elif phase == "needs_review":
                if not accept_review:
                    return
                film.start({"resumeSlug": slug, "expectedRevision": settings.get("revision"),
                            "acceptReview": True})
            else:
                return
            self._join(ctx)
        self.fail(f"film checkpoints did not finish: {film.status()}")

    def _start(self, ctx, client, payload, *, approve=True, accept_review=True):
        film = FilmController(ctx, client)
        idle_patch = patch("server.film.assert_other_models_idle")
        memory_patch = patch("server.film.prepare_language_memory")
        idle_patch.start()
        memory_patch.start()
        try:
            result = film.start(payload)
            self._join(ctx)
            if approve and not payload.get("prepareOnly") and film.status().get("phase") in {"prepared", "needs_review"}:
                self._resume_checkpoints(ctx, film, result["slug"], accept_review=accept_review)
        finally:
            memory_patch.stop()
            idle_patch.stop()
        return film, result

    def test_first_start_stops_before_video_until_exact_image_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx, client = self.context(Path(temp)), FakeComfy()
            with patch.object(client, "director_scene", side_effect=AssertionError("video requires approval")):
                film, result = self._start(ctx, client, {"description": "Robot", "duration": 7}, approve=False)
            self.assertEqual(film.status()["phase"], "prepared")
            self.assertFalse((ctx.store.project_dir(result["slug"]) / "final.mp4").exists())

    def test_image_approval_digest_is_invalidated_by_anchor_or_forged_page_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx, client = self.context(Path(temp)), FakeComfy()
            film, result = self._start(ctx, client, {"description": "Robot", "duration": 7}, approve=False)
            slug = result["slug"]
            board = ctx.store.load(slug)
            settings = board["filmAutomation"]
            digest = settings["imagePlan"]["digest"]
            revision = settings["revision"]
            board["filmAutomation"]["imageApproval"] = {"digest": digest, "approvedAt": 1}
            board["shots"][0]["imageAnchors"] = [{"ref": board["shots"][0]["startRef"], "frame": 0, "strength": .2}]
            ctx.store.save(slug, board)
            with self.assertRaisesRegex(ValueError, "Bildeplanen er endret"):
                film.start({"resumeSlug": slug, "expectedRevision": revision, "acceptImagePlan": digest})
            self.assertEqual(film.status()["phase"], "prepared")

    def test_timing_estimates_ignore_legacy_and_injected_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "film-timings.json"
            path.write_text(json.dumps({"scenes": [{"frames": 169, "seconds": 1.4,
                "geometry": "384x256", "model": "ltx-2.5-q8"}]}))
            timings = FilmTimings(path)
            fake = FakeComfy()
            movie = root / "movie.mp4"; movie.write_bytes(b"movie")
            video = root / "clip.mp4"; video.write_bytes(b"video")
            audio = root / "clip.wav"; audio.write_bytes(b"audio")
            result = {"movie": movie, "run_id": "a" * 32,
                      "shots": [{"video": video, "audio": audio, "frames": 169}]}
            timings.record(169, 14, result=result, client=fake)
            self.assertIsNone(timings.estimate(169))
            timings.record(169, 14, result=result,
                           client=ComfyFilmClient(output_dir=root))
            self.assertEqual(timings.estimate(169), 14)
            malformed = {**result, "shots": [{**result["shots"][0], "frames": "bad"}]}
            timings.record(169, 14, result=malformed,
                           client=ComfyFilmClient(output_dir=root))
            self.assertEqual(timings.estimate(169), 14)

    def test_timing_estimates_ignore_malformed_qualified_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "film-timings.json"
            qualified = {"geometry": "384x256", "model": "ltx-2.5-q8",
                         "source": "comfy-film-client", "runId": "a" * 32,
                         "frames": 169, "seconds": 14}
            rows = [qualified, {**qualified, "seconds": "bad"}, {**qualified, "seconds": True},
                    {**qualified, "seconds": float("nan")}, {**qualified, "frames": 169.5}]
            path.write_text(json.dumps({"scenes": rows}, allow_nan=True))
            timings = FilmTimings(path)
            self.assertEqual(timings.estimate(169), 14)
            path.write_text(json.dumps([qualified]))
            self.assertIsNone(FilmTimings(path).estimate(169))

    def test_prepare_only_writes_playable_animatic_and_bible_without_director(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx, client = self.context(Path(temp)), FakeComfy()
            with patch.object(client, "director_scene", side_effect=AssertionError("prepare must not render video")):
                film, result = self._start(ctx, client, {"description": "Robot", "duration": 7, "prepareOnly": True})
            root = ctx.store.project_dir(result["slug"])
            board = ctx.store.load(result["slug"])
            self.assertEqual(film.status()["phase"], "prepared", film.status())
            self.assertFalse((root / "final.mp4").exists())
            self.assertTrue((root / "animatic.html").is_file())
            self.assertTrue((root / "animatic.json").is_file())
            self.assertTrue(board["filmAutomation"].get("filmBible") is not None)
            self.assertTrue(list((root / "refs").rglob("*.png")))

    def test_resume_prepared_reuses_plan_and_reference_images(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx, client = self.context(Path(temp), _TwoSceneSplash()), FakeComfy()
            film, result = self._start(ctx, client, {"description": "Robot", "duration": 7, "prepareOnly": True})
            slug = result["slug"]
            before = ctx.store.load(slug)
            refs = {p: p.stat().st_mtime_ns for p in (ctx.store.project_dir(slug) / "refs").rglob("*.png")}
            image_calls = len(client.reference_calls)
            with patch.object(FakeSplash, "complete", side_effect=AssertionError("resume must use saved plan")), \
                 patch("server.film.SceneReviewer.scene", return_value={"verdict":"pass", "available":True,
                       "issues":[], "observedEndState":{"garden":"intact"}, "key":"reviewed"}), \
                 patch("server.film.assert_other_models_idle"), patch("server.film.prepare_language_memory"):
                self._resume_checkpoints(ctx, film, slug)
            self.assertEqual(film.status()["phase"], "done", film.status())
            self.assertEqual([s["id"] for s in before["shots"]], [s["id"] for s in ctx.store.load(slug)["shots"]])
            self.assertTrue(all(p.stat().st_mtime_ns == stamp for p, stamp in refs.items()))
            self.assertEqual(len(client.reference_calls), image_calls)

    def test_planning_interruption_leaves_checkpoint_and_resume_finishes(self):
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "film-planning.json"
            outline = copy.deepcopy(PLAN)
            outline["scenes"][0].pop("shots")
            outline["scenes"][0].update(shotCount=6, synopsis="Rob explores the garden.")
            second = copy.deepcopy(outline["scenes"][0])
            second.update(title="Second beat", shotCount=6)
            third = copy.deepcopy(outline["scenes"][0])
            third.update(title="Final beat", shotCount=1)
            outline["scenes"] = [outline["scenes"][0], second, third]
            batches = []
            for size in (6, 6, 1):
                batch = copy.deepcopy(PLAN)
                batch["scenes"][0]["shots"] = [copy.deepcopy(PLAN["scenes"][0]["shots"][i % 2]) for i in range(size)]
                batches.append(json.dumps(batch))
            class Service:
                def __init__(self): self.responses = [json.dumps(outline), *batches]
                def complete(self, *args, **kwargs): return self.responses.pop(0)
            checks = [0]
            def cancel_after_first_batch():
                checks[0] += 1
                if checks[0] >= 3: raise RuntimeError("stoppet")
            planner = FilmPlanner(Service(), check_cancel=cancel_after_first_batch, checkpoint_path=checkpoint)
            with self.assertRaisesRegex(RuntimeError, "stoppet"):
                planner.plan("Robot", 13)
            self.assertTrue(checkpoint.is_file())
            doc = json.loads(checkpoint.read_text())
            self.assertIn(doc.get("phase"), {"outline", "batches"})
            resumed = FilmPlanner(Service(), checkpoint_path=checkpoint).plan("Robot", 13)
            self.assertEqual(sum(len(s["shots"]) for s in resumed["scenes"]), 13)

    def test_critical_review_pauses_and_accept_review_resumes_without_losing_clips(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx, client = self.context(Path(temp)), FakeComfy()
            review = {"verdict": "pause", "available": True,
                      "issues": [{"severity": "critical", "issue": "wrong prop", "action": "pause"}],
                      "observedEndState": {"bench": "missing"}, "key": "critical-1"}
            with patch("server.film.SceneReviewer.scene", return_value=review):
                film, result = self._start(ctx, client, {"description": "Robot", "duration": 7}, accept_review=False)
            slug = result["slug"]
            board = ctx.store.load(slug)
            self.assertEqual(film.status()["phase"], "needs_review")
            self.assertFalse((ctx.store.project_dir(slug) / "final.mp4").exists())
            self.assertTrue((ctx.store.project_dir(slug) / "shots/01/clip.mp4").is_file())
            self.assertTrue(board["filmAutomation"].get("reviewRequired"))
            with patch("server.film.SceneReviewer.scene", return_value={"verdict": "pass", "available": True, "issues": [], "observedEndState": {}, "key": "reviewed-2"}), \
                 patch("server.film.assert_other_models_idle"), patch("server.film.prepare_language_memory"):
                with self.assertRaisesRegex(ValueError, "kvalitetsrapporten"):
                    film.start({"resumeSlug": slug, "acceptReview": True})
                with self.assertRaisesRegex(ValueError, "endret"):
                    film.start({"resumeSlug": slug, "acceptReview": True, "expectedRevision": "stale"})
                film.start({"resumeSlug": slug, "acceptReview": True,
                            "expectedRevision": board["filmAutomation"]["revision"]})
                self._join(ctx)
                if film.status().get("phase") == "needs_review":
                    self._resume_checkpoints(ctx, film, slug)
            self.assertEqual(film.status()["phase"], "done", film.status())
            self.assertTrue(ctx.store.load(slug).get("finalVideo"))

    def test_unavailable_quality_review_does_not_invent_observed_state(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx, client = self.context(Path(temp)), FakeComfy()
            film, result = self._start(ctx, client, {"description": "Robot", "duration": 7, "reviewMode": "balanced"})
            continuity = json.loads((ctx.store.project_dir(result["slug"]) / "film-continuity.json").read_text())
            self.assertIsNone(continuity["observed"].get("1"))
            self.assertEqual(ctx.store.load(result["slug"])["filmAutomation"]["qualitySummary"]["reviewedScenes"], 0)

    def test_balanced_review_budget_and_receipt_cache_avoid_repeat_call(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx, client = self.context(Path(temp)), FakeComfy()
            film, result = self._start(ctx, client, {"description": "Robot", "duration": 7, "reviewMode": "fast"})
            slug = result["slug"]; root = ctx.store.project_dir(slug)
            board = ctx.store.load(slug); plan = json.loads((root / "film-plan.json").read_text())
            calls = []
            class Reviewer(FakeSplash):
                startup_timeout = 720.0
                def ensure_ready(self, **kwargs):
                    pass
                def complete_with_media(self, *args, **kwargs):
                    calls.append(1)
                    return json.dumps({"verdict": "pass", "observedEndState": {"flower": "open"}, "issues": []})
            reviewer = SceneReviewer(root, plan, clean_policy({"reviewMode": "balanced"}), Reviewer(),
                                     release_images=lambda: None, release_language=lambda: None,
                                     set_phase=lambda **kwargs: None, cancel=lambda: False)
            scene = plan["scenes"][0]
            shots = board["shots"]
            # Resolve the opening from the board receipt.  This remains valid
            # when the implementation changes its generated filename scheme.
            opening = image_path(board["shots"][0]["startRef"], ctx.data_dir, verify=True)
            first = reviewer.scene(1, scene, shots, board, opening, [], 1000)
            second = reviewer.scene(1, scene, shots, board, opening, [], 1000)
            self.assertEqual(len(calls), 1)
            self.assertEqual(first["observedEndState"], second["observedEndState"])

    def test_balanced_review_budget_runs_short_production(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx, client = self.context(Path(temp)), FakeComfy()
            _, result = self._start(ctx, client, {"description": "Robot", "duration": 7, "reviewMode": "fast"})
            slug = result["slug"]; root = ctx.store.project_dir(slug)
            board = ctx.store.load(slug); plan = json.loads((root / "film-plan.json").read_text())
            calls = []
            class Reviewer(FakeSplash):
                startup_timeout = 720.0
                def ensure_ready(self, **kwargs):
                    calls.append("startup")
                def complete_with_media(self, *args, **kwargs):
                    calls.append(1)
                    return json.dumps({"verdict": "pass", "issues": []})
            service = Reviewer()
            reviewer = SceneReviewer(root, plan, clean_policy({"reviewMode": "balanced"}), service,
                                     release_images=lambda: None, release_language=lambda: None,
                                     set_phase=lambda **kwargs: None, cancel=lambda: False)
            scene = plan["scenes"][0]
            opening = image_path(board["shots"][0]["startRef"], ctx.data_dir, verify=True)
            reviewer.scene(1, scene, board["shots"], board, opening, [], 29)
            self.assertIn("startup", calls)

    def test_cold_director_review_is_unavailable_but_not_retried_on_resume(self):
        class ColdReviewer(_TwoSceneSplash):
            def __init__(self):
                self.attempts = []
            def review_ready(self):
                return False
            def ensure_ready(self, **kwargs):
                self.attempts.append(1)
                raise TimeoutError("cold start exceeds review budget")
            def complete_with_media(self, *args, **kwargs):
                raise AssertionError("A cold optional reviewer must not start")
            def release_memory(self, **kwargs):
                return True
        original = SceneReviewer.scene
        def measured_review(reviewer, *args, **kwargs):
            return original(reviewer, *args, **kwargs)
        with tempfile.TemporaryDirectory() as temp:
            service = ColdReviewer()
            ctx, client = self.context(Path(temp), service), FakeComfy()
            with patch.object(SceneReviewer, "scene", measured_review):
                film, result = self._start(ctx, client, {"description": "Robot", "duration": 7,
                                                        "reviewMode": "balanced"})
                self.assertEqual(film.status()["phase"], "done", film.status())
                root = ctx.store.project_dir(result["slug"])
                clips = [(root / "shots" / f"{index:02d}" / "clip.mp4").read_bytes() for index in (1, 2)]
                _, resumed = self._start(ctx, client, {"resumeSlug": result["slug"]})
            self.assertEqual(ctx.store.load(resumed["slug"])["finalVideo"]["missing"], [])
            self.assertGreaterEqual(len(service.attempts), 1)
            self.assertEqual(clips, [(root / "shots" / f"{index:02d}" / "clip.mp4").read_bytes() for index in (1, 2)])
            continuity = json.loads((root / "film-continuity.json").read_text())
            self.assertGreaterEqual(continuity["reviewCalls"], 1)
            self.assertTrue(all(not item["available"] for item in continuity["reviews"]))
            self.assertTrue(all("cold" in item["reason"].lower() for item in continuity["reviews"]))

    def test_warm_review_timeout_is_unavailable_only_after_safe_handoff(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx, client = self.context(Path(temp)), FakeComfy()
            _, result = self._start(ctx, client, {"description": "Robot", "duration": 7, "reviewMode": "fast"})
            root = ctx.store.project_dir(result["slug"])
            board = ctx.store.load(result["slug"])
            plan = json.loads((root / "film-plan.json").read_text())
            calls = []
            class WarmReviewer(FakeSplash):
                startup_timeout = 720.0
                def review_ready(self):
                    return True
                def ensure_ready(self, **kwargs):
                    calls.append("startup")
                def complete_with_media(self, *args, **kwargs):
                    calls.append("review")
                    raise TimeoutError("visual review timed out")
            reviewer = SceneReviewer(root, plan, clean_policy({"reviewMode": "balanced"}), WarmReviewer(),
                release_images=lambda: calls.append("images"), release_language=lambda: calls.append("language"),
                set_phase=lambda **kwargs: None, cancel=lambda: False)
            opening = image_path(board["shots"][0]["startRef"], ctx.data_dir, verify=True)
            verdict = reviewer.scene(1, plan["scenes"][0], board["shots"], board, opening, [], 1000)
            self.assertFalse(verdict["available"])
            self.assertIn("timed out", verdict["error"])
            self.assertEqual(calls, ["images", "startup", "review", "language"])
            reviewer.release_language = lambda: (_ for _ in ()).throw(RuntimeError("handoff unconfirmed"))
            for receipt in (root / "quality").glob("scene-*.json"):
                receipt.unlink()
            with self.assertRaisesRegex(RuntimeError, "handoff unconfirmed"):
                reviewer.scene(1, plan["scenes"][0], board["shots"], board, opening, [], 1000)

    def test_review_cleanup_failure_records_phase_and_preserves_completed_clip(self):
        def failed_review(reviewer, *args, **kwargs):
            reviewer.set_phase(phase="review", detail="Review handoff")
            raise TimeoutError("handoff unconfirmed")
        with tempfile.TemporaryDirectory() as temp:
            ctx, client = self.context(Path(temp), _TwoSceneSplash()), FakeComfy()
            with patch.object(SceneReviewer, "scene", failed_review), self.assertLogs("server.film", level="ERROR") as logs:
                film, result = self._start(ctx, client, {"description": "Robot", "duration": 7, "reviewMode": "fast"})
            self.assertEqual(film.status()["phase"], "failed")
            self.assertEqual(film.status()["failedPhase"], "review")
            self.assertIn("failed during review", logs.output[0])
            self.assertIn("TimeoutError: handoff unconfirmed", logs.output[0])
            root = ctx.store.project_dir(result["slug"])
            original = (root / "shots/01/clip.mp4").read_bytes()
            self.assertEqual(ctx.store.load(result["slug"])["shots"][0]["status"], "done")
            resumed, _ = self._start(ctx, client, {"resumeSlug": result["slug"]})
            self.assertEqual(resumed.status()["phase"], "done")
            self.assertIsNone(resumed.status()["failedPhase"])
            self.assertEqual(original, (root / "shots/01/clip.mp4").read_bytes())

    def test_scene_frame_budget_is_forwarded_for_each_scene(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp), _TwoSceneSplash())
            client = FakeComfy(); calls = []
            original = client.director_scene
            def record(*args, **kwargs):
                calls.append((kwargs.copy(), args[2].name))
                return original(*args, **kwargs)
            client.director_scene = record
            film, result = self._start(ctx, client, {"description": "Robot", "duration": 7})
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual({name for _, name in calls}, {"scene-001", "scene-002"})
            for name in ("scene-001", "scene-002"):
                scene_calls = [kwargs for kwargs, call_name in calls if call_name == name]
                self.assertTrue(all(call["first_frames"] > 0 and call["continuation_frames"] > 0 for call in scene_calls))
                self.assertEqual({(call["first_frames"], call["continuation_frames"], call["seed"])
                                  for call in scene_calls}, {(scene_calls[0]["first_frames"], scene_calls[0]["continuation_frames"], scene_calls[0]["seed"])})
            board = ctx.store.load(result["slug"])
            plan = json.loads((ctx.store.project_dir(result["slug"]) / "film-plan.json").read_text())
            self.assertEqual(film.status()["phase"], "done", film.status())
            self.assertAlmostEqual(board["finalVideo"]["seconds"], plan["timing"]["allocatedFrames"] / 24, places=2)

    def test_reference_bank_reuses_environment_and_props_across_state_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); image = root / "opening.png"
            Image.new("RGB", (32, 32), "red").save(image)
            bank = ReferenceBank(root, "soft stop motion")
            first = {"locationId": "garden", "propIds": ["key"], "assetStates": {"garden": "intact", "key": "held"}}
            second = {"locationId": "garden", "propIds": ["key"], "assetStates": {"garden": "destroyed", "key": "held"}}
            bank.remember(first, image)
            refs = bank.references(second)
            self.assertTrue(refs)
            self.assertTrue(all(not label.startswith("locations:") for _, label in refs))
            self.assertTrue(any(label.startswith("props:") for _, label in refs))
            changed = {"locationId": "garden", "propIds": ["key"],
                       "assetStates": {"garden": "destroyed", "key": "lost"}}
            self.assertEqual(bank.references(changed), [])
            replacement = root / "destroyed.png"
            Image.new("RGB", (32, 32), "blue").save(replacement)
            bank.remember(changed, replacement)
            refs = bank.references(changed)
            self.assertEqual([p for p, _ in refs], [replacement.resolve()])
            self.assertEqual(len(refs), 1)

    def test_observed_tail_is_used_only_for_same_set_and_asset_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); tail = root / "shots" / "tail.png"
            tail.parent.mkdir(parents=True); Image.new("RGB", (32, 32), "green").save(tail)
            bank = ReferenceBank(root, "soft stop motion")
            scene = {"locationId": "garden", "propIds": ["key"],
                     "assetStates": {"garden": "intact", "key": "held"}}
            bank.observe(1, {"key": "held"}, tail, scene)
            self.assertEqual(bank.continuity_reference(scene), tail.resolve())
            self.assertIsNone(bank.continuity_reference({**scene, "locationId": "kitchen"}))
            self.assertIsNone(bank.continuity_reference({**scene, "assetStates": {"garden": "destroyed", "key": "held"}}))
            self.assertIsNone(bank.continuity_reference({"setting": "Unrelated room"}))
            self.assertIsNone(bank.continuity_reference(scene, before_scene=1))
            self.assertEqual(bank.continuity_reference(scene, before_scene=2), tail.resolve())

    def test_scene_images_follow_reviewed_output_without_extra_generation_or_resume_work(self):
        class SameSetSplash(_TwoSceneSplash):
            def complete(self, *args, **kwargs):
                doc = json.loads(super().complete(*args, **kwargs))
                for i in range(1, cast_count):
                    doc["characters"].append({**doc["characters"][0], "key": f"robot{i}", "name": f"Rob {i}"})
                doc["filmBible"] = {"locations": [{"id": "garden", "description": "A green garden"}]}
                for scene in doc["scenes"]:
                    scene.update(locationId="garden", assetStates={"garden": "intact"},
                                 stateBefore="Robot is ready.", stateAfter="Robot has completed the beat.",
                                 characterKeys=[c["key"] for c in doc["characters"]])
                return json.dumps(doc)
        for available, cast_count in ((True, 4), (False, 1)):
            with self.subTest(available=available), tempfile.TemporaryDirectory() as temp:
                ctx, client = self.context(Path(temp), SameSetSplash()), FakeComfy()
                assets = []
                for i in range(5):
                    asset = Path(temp)/f"asset-{i}.png"
                    Image.new("RGB", (32,32), (i*30,0,0)).save(asset)
                    assets.append((asset, f"asset {i}"))
                events = []
                image, video = client.qwen_image, client.director_scene
                def record_image(*args, **kwargs):
                    refs = list(args[2] if len(args)>2 else kwargs.get("references") or [])
                    self.assertLessEqual(len(refs), 10, "Qwen accepts at most ten references")
                    kind = "card" if kwargs.get("width") == 1536 else "image"
                    events.append((kind, kwargs.get("seed"), refs))
                    return image(*args, **kwargs)
                def record_video(*args, **kwargs):
                    events.append(("video", kwargs.get("seed"), []))
                    return video(*args, **kwargs)
                client.qwen_image, client.director_scene = record_image, record_video
                verdict = {"verdict": "pass" if available else "unavailable", "available": available,
                           "issues": [], "observedEndState": {"garden": "intact"} if available else None,
                           "key": "sample"}
                with patch("server.film.SceneReviewer.scene", return_value=verdict), \
                     patch("server.film.ReferenceBank.references", return_value=assets):
                    film, result = self._start(ctx, client, {"description": "Robot", "duration": 7})
                self.assertEqual(film.status()["phase"], "done", film.status())
                self.assertEqual(sum(e[0] in ("image", "card") for e in events), cast_count + 2)
                next_opening = next(e for e in events if e[0] == "image" and e[1] == 2)
                first_video = next(i for i,e in enumerate(events) if e[0] == "video")
                self.assertGreater(first_video, max(i for i, e in enumerate(events) if e[0] in ("image", "card")))
                observed_refs = [(p,label) for p,label in next_opening[2] if "prior scene tail" in label]
                self.assertFalse(observed_refs, "approved image plans must not substitute later scene images from tails")
                before = len(client.reference_calls)
                with patch("server.film.SceneReviewer.scene", return_value=verdict), \
                     patch("server.film.ReferenceBank.references", return_value=assets), \
                     patch("server.film.assert_other_models_idle"), patch("server.film.prepare_language_memory"):
                    self._resume_checkpoints(ctx, film, result["slug"])
                self.assertEqual(film.status()["phase"], "done", film.status())
                self.assertEqual(len(client.reference_calls), before, "resume generated new reference images")
                reel = json.loads((ctx.store.project_dir(result["slug"])/"animatic.json").read_text())
                self.assertTrue(all(card["start"] for card in reel["cards"]))

    def test_animatic_escapes_title_and_dialogue_before_script_embedding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            board = {"name": '</title><script>alert("x")</script>', "shots": [{
                "title": "<img src=x onerror=alert(1)>", "frames": 73,
                "startRef": {"url": "/media/start.png"},
                "imageAnchors": [], "dialogue": '</script><script>alert("d")</script>'
            }]}
            html = write_animatic(root, board, {"title": "Safe"}).read_text()
            self.assertNotIn("<script>alert", html)
            self.assertNotIn("<img src=x", html)
            self.assertIn("\\u003c", html)
            self.assertEqual(html.count("</script>"), 1)


if __name__ == "__main__":
    unittest.main()

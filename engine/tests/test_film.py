"""Film authoring, project publication, cancellation and GPU admission."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from server.film import FilmController, GuardedLLM, clip_count, film_prompt, validate_plan
from server.orchestrator import Orchestrator
from server.store import Store, stale_reason
from server.app import Handler
from server.assemble import assemble, final_stale_reason
from server.backends.base import ModelCapability
from server.update_lifecycle import UpdateLifecycle


PLAN = {"title": "Robot i hagen", "style": "Soft stop-motion animation.",
    "characters": [{"key": "robot", "name": "Rob", "description": "Small red robot with blue eyes.",
                    "imagePrompt": "One red robot, full body on a plain background."}],
    "filmBible": {"locations": [{"id": "garden", "title": "Garden", "description": "A green garden."}], "props": []},
    "scenes": [{"title": "Hage", "setting": "A green garden and a wooden bench.",
        "imagePrompt": "Empty garden and wooden bench.", "characterKeys": ["robot"], "locationId": "garden",
        "stateBefore": "Rob stands beside the bench.", "stateAfter": "Rob faces the flower.",
        "shots": [{"title": "Ser", "start": "Rob stands by the bench.", "action": "Rob turns toward a flower.",
            "end": "Rob faces the flower.", "camera": "Locked medium shot.", "sound": "Birds and soft wind.",
            "dialogue": "", "speakerKey": ""},
            {"title": "Vinker", "start": "Rob faces the flower.", "action": "Rob raises one hand.",
             "end": "Rob holds its hand up.", "camera": "Locked medium shot.", "sound": "Birds and a small servo.",
             "dialogue": "Hei!", "speakerKey": "robot"}]}]}


class FakeSplash:
    def _ready(self):
        return False

    def complete(self, *args, **kwargs):
        return json.dumps(PLAN)

    def release_memory(self, **kwargs):
        return True


class FakeComfy:
    def __init__(self):
        self.refs = []
        self.reference_calls = []

    def preflight(self, **kwargs):
        return {}

    def qwen_image(self, prompt, dest, references=None, **kwargs):
        self.reference_calls.append((prompt, list(references or []), kwargs))
        Image.new("RGB", (kwargs.get("width", 768), kwargs.get("height", 512)), "red").save(dest)
        return dest

    def release_memory(self, **kwargs):
        return {}

    def qwen_model_signature(self):
        return {"fixture": "no model files"}

    def director_scene(self, prompts, references, dest_dir, **kwargs):
        self.refs = references
        dest_dir.mkdir(parents=True, exist_ok=True)
        shots = []
        for index, _ in enumerate(prompts):
            frames = kwargs.get("first_frames", 73) if index == 0 else kwargs.get("continuation_frames", 96)
            video, audio = dest_dir / f"shot{index}.mp4", dest_dir / f"shot{index}.wav"
            if (dest_dir / "movie.mp4").is_file():
                shots.append({"video": video, "audio": audio, "frames": frames})
                continue
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                "color=red:size=64x64:rate=24", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                "-frames:v", str(frames), "-t", str(frames / 24), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ac", "2", str(video)], check=True)
            audio.write_bytes(b"retained waveform")
            shots.append({"video": video, "audio": audio, "frames": frames})
        if (dest_dir / "movie.mp4").is_file():
            return {"shots": shots, "run_id": "own-run", "movie": dest_dir / "movie.mp4"}
        joined = assemble([(str(i), s["video"]) for i, s in enumerate(shots)], dest_dir / "movie.mp4", 64, 64)
        assert joined.ok, joined.error
        return {"shots": shots, "run_id": "own-run", "movie": joined.path}


class FilmTests(unittest.TestCase):
    def _finish_checkpoints(self, ctx, film, slug):
        ctx.orch._thread.join(15)
        for _ in range(4):
            board = ctx.store.load(slug)
            settings = board.get("filmAutomation") or {}
            phase = film.status().get("phase")
            if phase == "prepared":
                film.start({"resumeSlug": slug, "expectedRevision": settings.get("revision"),
                            "acceptImagePlan": (settings.get("imagePlan") or {}).get("digest")})
            elif phase == "needs_review":
                film.start({"resumeSlug": slug, "expectedRevision": settings.get("revision"),
                            "acceptReview": True})
            else:
                return
            ctx.orch._thread.join(15)
        self.fail(film.status())

    def test_plan_rejects_unknown_cast_speaker_counts_and_delimiters(self):
        validate_plan(PLAN, 2)
        for mutate in (lambda p: p["scenes"][0].update(characterKeys=["missing"]),
                       lambda p: p["scenes"][0]["shots"][1].update(speakerKey="missing"),
                       lambda p: p["scenes"][0]["shots"].pop(),
                       lambda p: p["scenes"][0]["shots"][0].update(action="one\n---\ntwo")):
            value = copy.deepcopy(PLAN)
            mutate(value)
            with self.assertRaises(ValueError):
                validate_plan(value, 2)
        for seconds in (False, float("nan"), 0, 7201):
            with self.assertRaises(ValueError):
                clip_count(seconds)
        self.assertEqual(clip_count(7), 2)

    def test_prompt_binds_portrait_environment_and_speaker(self):
        plan = validate_plan(PLAN, 2)
        prompt = film_prompt(plan["style"], plan["characters"], plan["scenes"][0], plan["scenes"][0]["shots"][1])
        for text in ("Rob: Small red robot", "green garden", "Rob faces the flower", "Rob raises one hand", 'Rob says in Norwegian: "Hei!"', "No background music."):
            self.assertIn(text, prompt)
        self.assertNotIn("<Picture", prompt)
        self.assertNotIn("subject_definitions:", prompt)
        card = plan["scenes"][0]["shots"][1]
        card.update(dialogue="Hello!", dialogueLanguage="English")
        self.assertIn('says in English: "Hello!"', film_prompt(plan["style"], plan["characters"], plan["scenes"][0], card))

    def test_long_plan_rejects_overlong_director_scene(self):
        outline = copy.deepcopy(PLAN)
        scene = outline["scenes"][0]
        scene.pop("shots")
        scene.update(shotCount=13, synopsis="Rob turns, walks around the garden, then waves.")
        responses = [json.dumps(outline)]
        for size in (5, 4, 4):
            batch = copy.deepcopy(PLAN)
            batch["scenes"][0]["shots"] = [copy.deepcopy(PLAN["scenes"][0]["shots"][i % 2]) for i in range(size)]
            responses.append(json.dumps(batch))
        class BoundedSplash(FakeSplash):
            def __init__(self):
                self.calls = []
            def complete(self, system, user, **kwargs):
                self.calls.append((system, user, kwargs))
                return responses.pop(0)
        with tempfile.TemporaryDirectory() as temp:
            service = BoundedSplash()
            film = FilmController(self.context(Path(temp)), FakeComfy())
            with self.assertRaisesRegex(ValueError, "(?:maksimalt seks|shotCount)"):
                film._plan(service, "One continuous garden scene", 13)

    def test_long_plan_rejects_wrong_outline_budget_before_writing_cards(self):
        outline = copy.deepcopy(PLAN)
        outline["scenes"][0].update(shotCount=6, synopsis="A robot waves.")
        service = FakeSplash()
        with tempfile.TemporaryDirectory() as temp, patch.object(service, "complete", return_value=json.dumps(outline)) as complete:
            film = FilmController(self.context(Path(temp)), FakeComfy())
            with self.assertRaisesRegex(ValueError, "filmoversikt"):
                film._plan(service, "Robot", 8)
            self.assertEqual(complete.call_count, 2)

    def test_long_plan_cancel_between_batches_prevents_next_request(self):
        outline = copy.deepcopy(PLAN)
        outline["scenes"][0].update(shotCount=6, synopsis="A robot explores the garden.")
        second_scene = copy.deepcopy(outline["scenes"][0])
        second_scene.update(title="Second garden beat", shotCount=2)
        outline["scenes"].append(second_scene)
        cards = copy.deepcopy(PLAN)
        cards["scenes"][0]["shots"] *= 3
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp))
            service = FakeSplash()
            calls = []
            def complete(*args, **kwargs):
                calls.append(args)
                if len(calls) == 1:
                    return json.dumps(outline)
                ctx.orch._cancel.set()
                return json.dumps(cards)
            with patch.object(service, "complete", side_effect=complete):
                with self.assertRaisesRegex(RuntimeError, "stoppet"):
                    FilmController(ctx, FakeComfy())._plan(service, "Robot", 8)
            self.assertEqual(len(calls), 2)

    def context(self, root, service=None):
        store = Store(workspace=root, data_dir=root)
        orch = Orchestrator(backend=SimpleNamespace(), store=store, workspace=root, data_dir=root)
        ctx = SimpleNamespace(store=store, orch=orch, workspace=root, data_dir=root,
            ui_root=Path(__file__).resolve().parents[1], backend=SimpleNamespace(
                capability=lambda model: ModelCapability(id=model, label=model)),
            llm_services=lambda: {"splash-local": service or FakeSplash()})
        ctx.update_lifecycle = UpdateLifecycle(ctx)
        orch.update_lifecycle = ctx.update_lifecycle
        return ctx

    def test_missing_ltx_or_qwen_fails_before_planning_or_project_creation(self):
        for unavailable, engine in (("ltx-2.5", "qwen-image-2.1-turbo"),):
            with tempfile.TemporaryDirectory() as temp:
                ctx = self.context(Path(temp))
                ctx.backend.capability = lambda model: ModelCapability(
                    id=model, label=model, available=model != unavailable, unavailable_reason="missing " + model)
                with patch.object(FakeSplash, "complete", side_effect=AssertionError("no planning before admission")):
                    with self.assertRaisesRegex(RuntimeError, "missing"):
                        FilmController(ctx, FakeComfy()).start({"description": "Robot", "duration": 7, "imageEngine": engine})
                self.assertEqual(ctx.store.list_boards(), [])

    @patch("server.film.assert_other_models_idle")
    @patch("server.film.prepare_language_memory")
    def test_resume_forwards_separate_anchors_for_first_and_continued_clip(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp))
            client = FakeComfy()
            film = FilmController(ctx, client)
            result = film.start({'description': 'A robot waves.', 'duration': 7})
            self._finish_checkpoints(ctx, film, result['slug'])
            self.assertFalse(film.status()['busy'])
            board = ctx.store.load(result['slug'])
            ref = board['shots'][0]['startRef']
            board['shots'][0]['imageAnchors'] = [{'ref': ref, 'frame': 24, 'strength': .6}]
            board['shots'][1]['imageAnchors'] = [{'ref': ref, 'frame': 0, 'strength': .3},
                                                {'ref': ref, 'frame': 'last', 'strength': .7}]
            ctx.store.save(result['slug'], board)
            with patch.object(client, 'director_scene', wraps=client.director_scene) as call:
                film.start({'resumeSlug': result['slug']})
                self._finish_checkpoints(ctx, film, result['slug'])
            self.assertFalse(film.status()['busy'])
            self.assertEqual(film.status()['phase'], 'done', film.status().get('error'))
            groups = call.call_args.kwargs['image_anchors']
            self.assertEqual([a['frame'] for a in groups[0]], [24, 0])
            self.assertEqual([a['frame'] for a in groups[1]], [0, 'last'])
            self.assertEqual([a['strength'] for a in groups[1]], [.3, .7])

    @patch("server.film.assert_other_models_idle")
    @patch("server.film.prepare_language_memory")
    def test_reference_resume_keeps_manual_start_and_end_choices(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp))
            client = FakeComfy()
            film = FilmController(ctx, client)
            created = film.start({"description": "Rob waves.", "duration": 7})
            self._finish_checkpoints(ctx, film, created["slug"])
            slug = created["slug"]
            self.assertEqual(film.status()["phase"], "done", film.status())
            chosen = ctx.store.project_dir(slug) / "refs" / "chosen.png"
            Image.new("RGB", (64, 64), "blue").save(chosen)
            ref = {"kind": "upload", "path": f"{slug}/refs/chosen.png"}
            board = ctx.store.load(slug)
            board["shots"][0]["startRef"] = ref
            board["shots"][1]["endRef"] = ref
            ctx.store.save(slug, board)
            with patch.object(client, "director_scene", wraps=client.director_scene) as render:
                film.start({"resumeSlug": slug})
                self._finish_checkpoints(ctx, film, slug)
            self.assertEqual(film.status()["phase"], "done", film.status())
            self.assertEqual([p.resolve() for p in client.refs], [chosen.resolve()])
            final = ctx.store.load(slug)
            self.assertEqual(final["shots"][0]["startRef"]["path"], ref["path"])
            self.assertEqual(final["shots"][1]["imageAnchors"], [])
            self.assertIn({"path": str(chosen.resolve()), "frame": 95, "strength": 1.0},
                          render.call_args.kwargs["image_anchors"][1])

    @patch("server.film.assert_other_models_idle")
    @patch("server.film.prepare_language_memory")
    def test_complete_film_keeps_existing_project_and_publishes_real_media(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp))
            old_slug, old = ctx.store.create("Existing project")
            client = FakeComfy()
            film = FilmController(ctx, client)
            result = film.start({"description": "A robot waves at a flower.", "duration": 7})
            stale_board = copy.deepcopy(ctx.store.load(result["slug"]))
            self._finish_checkpoints(ctx, film, result["slug"])
            self.assertFalse(film.status()["busy"])
            self.assertEqual(film.status()["phase"], "done", film.status())
            board = ctx.store.load(result["slug"])
            self.assertEqual(len(board["characters"]), 1)
            self.assertEqual(len(client.refs), 1)
            self.assertEqual(client.refs[0].parent.name, "scene-frames")
            self.assertEqual(len(client.reference_calls), 2)
            self.assertEqual([len(call[1]) for call in client.reference_calls], [0, 1])
            self.assertTrue(all(call[1] == [] or call[1][0][1].startswith("Rob") for call in client.reference_calls))
            self.assertEqual((client.reference_calls[0][2]["width"], client.reference_calls[0][2]["height"]), (1536, 1024))
            self.assertTrue(all(not s["imageAnchors"] for s in board["shots"]))
            self.assertEqual(board["filmAutomation"]["engine"], "ltx-2.5")
            self.assertEqual(board["filmAutomation"]["imageEngine"], "qwen-image-2.1-turbo")
            self.assertTrue(board["filmAutomation"]["characterReferences"])
            self.assertEqual([s["frames"] for s in board["shots"]], [97, 96])
            continuation = board["shots"][1]["startRef"]
            self.assertEqual(continuation["kind"], "chain")
            self.assertEqual(continuation["from"], board["shots"][0]["id"])
            self.assertTrue(continuation.get("contentHash"))
            self.assertTrue(continuation.get("resolved", "").endswith("frame-00096.png"))
            self.assertTrue(all(not stale_reason(s, board) for s in board["shots"]))
            self.assertAlmostEqual(board["finalVideo"]["seconds"], 193 / 24, places=1)
            self.assertEqual(final_stale_reason(board, ctx.store.project_dir(result["slug"])), "")
            self.assertEqual(ctx.store.load(old_slug)["name"], old["name"])
            self.assertTrue((ctx.store.project_dir(result["slug"]) / "film-plan.json").is_file())
            ctx.film = film
            handler = object.__new__(Handler)
            handler.ctx = ctx
            with self.assertRaisesRegex(RuntimeError, "på nytt"):
                handler._save_from_page(result["slug"], stale_board)

    @patch("server.film.assert_other_models_idle")
    @patch("server.film.prepare_language_memory")
    def test_resume_uses_saved_plan_and_preserves_portraits(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp))
            film = FilmController(ctx, FakeComfy())
            result = film.start({"description": "Robot", "duration": 7})
            self._finish_checkpoints(ctx, film, result["slug"])
            root = ctx.store.project_dir(result["slug"])
            portrait = ctx.data_dir / ctx.store.load(result["slug"])["characters"][0]["image"]["path"]
            previous_mtime = portrait.stat().st_mtime_ns
            before = ctx.store.load(result["slug"])
            shot_ids = [s["id"] for s in before["shots"]]
            clip = root / "shots/01/clip.mp4"
            previous_clip_mtime = clip.stat().st_mtime_ns
            with patch.object(FakeSplash, "complete", side_effect=AssertionError("must reuse plan")):
                film.start({"resumeSlug": result["slug"]})
                self._finish_checkpoints(ctx, film, result["slug"])
            self.assertEqual(film.status()["phase"], "done", film.status())
            self.assertEqual(portrait.stat().st_mtime_ns, previous_mtime)
            self.assertEqual(len(list((root / "director").iterdir())), 1)
            self.assertEqual(clip.stat().st_mtime_ns, previous_clip_mtime)
            self.assertEqual([s["id"] for s in ctx.store.load(result["slug"])["shots"]], shot_ids)

    @patch("server.film.assert_other_models_idle")
    @patch("server.film.prepare_language_memory")
    def test_reference_upgrade_preserves_legacy_scene_and_creates_new_take(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp))
            client = FakeComfy()
            film = FilmController(ctx, client)
            created = film.start({"description": "Robot", "duration": 7})
            self._finish_checkpoints(ctx, film, created["slug"])
            slug = created["slug"]
            board = ctx.store.load(slug)
            # Resume uses the current reference pipeline, retaining original takes.
            # Its added closing anchors must not reuse the old unanchored recipe.
            board["filmAutomation"]["characterReferences"] = False
            for shot in board["shots"]:
                shot["imageAnchors"] = []
            saved = board["filmAutomation"]["scenes"][0]
            original = ctx.data_dir / saved["movie"]
            legacy = original.parent.with_name("0123456789abcdef0123456789abcdef-scene-001")
            original.parent.rename(legacy)
            saved.update(movie=(legacy / "movie.mp4").relative_to(ctx.data_dir).as_posix())
            saved.pop("contract")
            ctx.store.save(slug, board)
            def verified(directory, run_id, prompts, reference, **kwargs):
                self.assertEqual(directory, legacy)
                return {"movie": legacy / "movie.mp4", "run_id": run_id, "legacy": True,
                        "shots": [{"video": legacy / f"shot{i}.mp4", "audio": legacy / f"shot{i}.wav", "frames": n}
                                  for i, n in enumerate((73, 96))]}
            client.legacy_scene = verified
            with patch.object(client, "director_scene", wraps=client.director_scene) as new_take:
                film.start({"resumeSlug": slug})
                self._finish_checkpoints(ctx, film, slug)
            self.assertEqual(film.status()["phase"], "done", film.status())
            self.assertTrue(new_take.called)
            self.assertTrue(original.parent.exists())
            self.assertTrue((legacy / "movie.mp4").is_file())

    @patch("server.film.assert_other_models_idle")
    @patch("server.film.prepare_language_memory")
    def test_malformed_legacy_metadata_falls_back_to_checkpoint_client(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp))
            client = FakeComfy()
            film = FilmController(ctx, client)
            created = film.start({"description": "Robot", "duration": 7})
            self._finish_checkpoints(ctx, film, created["slug"])
            slug = created["slug"]
            for missing in ("runId", "movie"):
                board = ctx.store.load(slug)
                record = next(s for s in board["filmAutomation"]["scenes"] if isinstance(s, dict) and s.get("index") == 1)
                record.pop("contract", None)
                record.pop(missing, None)
                board["filmAutomation"]["scenes"] = [record, None, {"unrelated": "malformed"}]
                ctx.store.save(slug, board)
                with patch.object(client, "director_scene", wraps=client.director_scene) as render:
                    film.start({"resumeSlug": slug})
                    self._finish_checkpoints(ctx, film, slug)
                self.assertEqual(film.status()["phase"], "done", film.status())
                render.assert_called_once()

    @patch("server.film.prepare_language_memory")
    def test_cancel_during_planning_preserves_new_project_and_cannot_start_twice(self, *_):
        waiting, release = threading.Event(), threading.Event()
        class PausedSplash(FakeSplash):
            def complete(self, *args, **kwargs):
                waiting.set()
                release.wait(5)
                return super().complete(*args, **kwargs)
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp), PausedSplash())
            film = FilmController(ctx, FakeComfy())
            result = film.start({"description": "Robot", "duration": 7})
            self.assertTrue(waiting.wait(2))
            with self.assertRaises(RuntimeError):
                film.start({"description": "Second", "duration": 7})
            film.stop()
            release.set()
            ctx.orch._thread.join(5)
            self.assertEqual(film.status()["phase"], "cancelled")
            self.assertTrue(ctx.store.project_dir(result["slug"]).is_dir())

    def test_llm_and_render_admission_share_one_gate(self):
        entered, release = threading.Event(), threading.Event()
        class WaitingLLM:
            def complete(self, *args, **kwargs):
                entered.set()
                release.wait(5)
                return "ok"
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp))
            service = GuardedLLM(WaitingLLM(), ctx.orch)
            thread = threading.Thread(target=lambda: service.complete("", ""))
            thread.start()
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaisesRegex(RuntimeError, "Språkmodellen"):
                    ctx.orch.start("any")
            finally:
                release.set()
                thread.join()
            self.assertEqual(ctx.orch._llm_active, 0)

    def test_guarded_llm_copy_is_request_local_and_preserves_admission_gate(self):
        inner = SimpleNamespace(thinking='none', label='Inert service', complete=lambda *a, **k: 'ok')
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp))
            guarded = GuardedLLM(inner, ctx.orch)
            cloned = copy.copy(guarded)
            cloned.thinking = 'medium'
            self.assertIsNot(cloned, guarded)
            self.assertIsNot(cloned.inner, inner)
            self.assertIs(cloned.orch, guarded.orch)
            self.assertEqual(inner.thinking, 'none')
            self.assertEqual(cloned.thinking, 'medium')
            self.assertEqual(cloned.complete('', ''), 'ok')
            self.assertEqual(ctx.orch._llm_active, 0)
            ctx.orch._llm_active = 1
            with self.assertRaisesRegex(RuntimeError, 'Storyboard arbeider'):
                cloned.complete('', '')
            self.assertEqual(ctx.orch._llm_active, 1)

    def test_uninitialized_guard_attributes_raise_without_recursive_lookup(self):
        guarded = object.__new__(GuardedLLM)
        with self.assertRaises(AttributeError):
            getattr(guarded, '__setstate__')

    def test_render_waits_for_manual_language_profile_release(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx = self.context(Path(temp))
            ctx.orch.before_gpu_start = lambda: (_ for _ in ()).throw(RuntimeError("release unconfirmed"))
            with self.assertRaisesRegex(RuntimeError, "release unconfirmed"):
                ctx.orch.start("any")
            self.assertFalse(ctx.orch.busy)


if __name__ == "__main__":
    unittest.main()

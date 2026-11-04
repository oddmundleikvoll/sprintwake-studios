import json
import tempfile
import unittest
from pathlib import Path
from server.store import Store, default_board, default_shot

from server.film_planning import FilmPlanner, allocate_timing, clip_count, validate_plan


def card(n=0):
    return {"title": f"Shot {n}", "start": "A robot stands.", "action": "The robot turns and waves.",
            "end": "The robot holds its hand up.", "camera": "Locked medium shot.", "sound": "Wind.",
            "dialogue": "", "speakerKey": ""}


def plan(shots=2):
    return {"title": "Test", "style": "Stop motion.",
            "characters": [{"key": "robot", "name": "Rob", "description": "Small red robot.",
                            "imagePrompt": "Full body red robot."}],
            "filmBible": {"storyArc": "A small discovery.", "theme": "Curiosity.",
                          "acts": [{"id": "act1", "title": "Beginning", "stateBefore": "Unknown", "stateAfter": "Aware"}],
                          "locations": [{"id": "garden", "description": "A garden."}], "props": []},
            "scenes": [{"title": "Garden", "setting": "A garden.", "imagePrompt": "Robot in garden.",
                        "characterKeys": ["robot"], "locationId": "garden",
                        "stateBefore": "Robot waits.", "stateAfter": "Robot learns.",
                        "shots": [card(i) for i in range(shots)]}]}


def structured_scene(**fields):
    scene = {"locationId": "garden", "stateBefore": "Robot waits.", "stateAfter": "Robot learns."}
    scene.update(fields)
    return scene


class Service:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def complete(self, system, user, **kwargs):
        self.calls.append((system, user))
        return self.responses.pop(0)


class FilmPlanningTests(unittest.TestCase):
    def test_validation_allows_single_shot_and_bible(self):
        value = validate_plan(plan(1), 1)
        self.assertEqual(value["filmBible"]["theme"], "Curiosity.")
        self.assertEqual(len(value["scenes"][0]["shots"]), 1)

    def test_strict_validation_requires_location_state_and_short_scene(self):
        value = plan(1)
        value["scenes"][0].pop("locationId")
        with self.assertRaisesRegex(ValueError, "locationId"):
            validate_plan(value, 1, strict=True)
        value = plan(7)
        with self.assertRaisesRegex(ValueError, "seks"):
            validate_plan(value, 7, strict=True)

    def test_duration_cap_and_runtime_count(self):
        for seconds, expected in ((6, 1), (7, 2), (8, 2), (10, 2), (12, 2), (13, 3),
                                  (30, 6), (60, 12), (600, 120), (7200, 1440)):
            with self.subTest(seconds=seconds):
                self.assertEqual(clip_count(seconds), expected)
        with self.assertRaises(ValueError):
            clip_count(120 * 60 + 1)

    def test_new_cards_use_five_seconds_and_old_timing_survives_migration(self):
        board = default_board("New")
        self.assertEqual(default_shot(board["defaults"])["frames"], 121)
        self.assertEqual(default_shot()["frames"], 121)
        with tempfile.TemporaryDirectory() as temp:
            store = Store(workspace=Path(temp), data_dir=Path(temp))
            legacy = store.migrate({"name": "Old", "shots": [{"frames": 73}, {"frames": 192}, {}]})
            self.assertEqual(legacy["defaults"]["frames"], 97)
            self.assertEqual([s["frames"] for s in legacy["shots"]], [73, 192, 97])

    def test_new_allocations_stay_between_four_and_six_seconds(self):
        for seconds in (6, 7, 8, 10, 12, 13, 30, 60, 125):
            count = clip_count(seconds)
            value = plan(1)
            value["scenes"] = []
            for offset in range(0, count, 6):
                value["scenes"].append(plan(min(6, count - offset))["scenes"][0])
            timed = allocate_timing(value, seconds)
            with self.subTest(seconds=seconds):
                self.assertEqual(timed["timing"]["flags"], [])
                self.assertGreaterEqual(timed["timing"]["allocatedFrames"], seconds * 24)
                for scene in timed["scenes"]:
                    self.assertTrue(97 <= scene["firstFrames"] <= 145)
                    self.assertTrue(96 <= scene["continuationFrames"] <= 144)
        timed = allocate_timing(plan(6), 30)
        self.assertEqual((timed["scenes"][0]["firstFrames"], timed["scenes"][0]["continuationFrames"]), (121, 120))

    def test_content_over_six_seconds_requires_split_instead_of_longer_clip(self):
        value = plan(2)
        for shot in value["scenes"][0]["shots"]:
            shot.update(dialogue="one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen",
                        speakerKey="robot")
        timed = allocate_timing(value, 12)
        self.assertEqual((timed["scenes"][0]["firstFrames"], timed["scenes"][0]["continuationFrames"]), (145, 144))
        self.assertTrue(any(flag["reason"] == "dialogue_or_action_needs_split" for flag in timed["timing"]["flags"]))

    def test_timing_uses_dialogue_and_marks_split(self):
        value = plan(2)
        value["scenes"][0]["shots"][0].update(dialogue="one two three four five six seven eight nine ten", speakerKey="robot")
        timed = allocate_timing(value, 6)
        self.assertIn("cardFrames", timed["scenes"][0]["shots"][0])
        self.assertGreaterEqual(timed["scenes"][0]["firstFrames"], 97)
        self.assertTrue(timed["timing"]["allocatedFrames"] >= 144)

    def test_multiscene_runtime_uses_scene_openings_and_uniform_continuations(self):
        value = plan(2)
        value["scenes"].append({"title": "Second", "setting": "A room.", "imagePrompt": "Robot in room.",
                                "characterKeys": ["robot"], "shots": [card(3)]})
        timed = allocate_timing(value, 12)
        total = sum(s["firstFrames"] + (len(s["shots"]) - 1) * s["continuationFrames"] for s in timed["scenes"])
        self.assertEqual(total, timed["timing"]["allocatedFrames"])
        for scene in timed["scenes"]:
            self.assertEqual(scene["firstFrames"] % 8, 1)
            self.assertEqual(scene["continuationFrames"] % 8, 0)
            self.assertTrue(all(shot["cardFrames"] == (scene["firstFrames"] if i == 0 else scene["continuationFrames"])
                                for i, shot in enumerate(scene["shots"])))

    def test_dense_dialogue_cannot_silently_expand_production_runtime(self):
        value = plan(15)
        for shot in value["scenes"][0]["shots"]:
            shot.update(dialogue="one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen", speakerKey="robot")
        timed = allocate_timing(value, 60)
        self.assertTrue(any(flag["reason"] == "runtime_budget_exceeded" for flag in timed["timing"]["flags"]))

    def test_outline_budget_mismatch_fails_before_card_requests(self):
        outline = plan(2)
        outline["scenes"][0].pop("shots")
        outline["scenes"][0]["shotCount"] = 3
        service = Service([json.dumps(outline), json.dumps(outline)])
        with self.assertRaises(ValueError):
            FilmPlanner(service).plan("Explore", 7, system="sys")
        self.assertEqual(len(service.calls), 2)

    def test_long_outline_uses_phase_contract_and_bounded_output_budget(self):
        outline = {"title": "Long", "style": "Style", "characters": plan(1)["characters"],
                   "filmBible": {"storyArc": "Arc"},
                   "scenes": [{"title": "Scene", "setting": "Room", "imagePrompt": "Robot",
                               "characterKeys": ["robot"], "synopsis": "Beat", "shotCount": 7}]}

        class InspectingService(Service):
            def complete(self, system, user, **kwargs):
                self.calls.append((system, user))
                self.kwargs = kwargs
                return json.dumps(outline)

        service = InspectingService([json.dumps(outline), json.dumps(outline)])
        with self.assertRaises(ValueError):
            FilmPlanner(service).plan("Explore", 8, system="global final-plan schema")
        self.assertIn("PHASE: OUTLINE ONLY", service.calls[0][0])
        self.assertLessEqual(service.kwargs["max_tokens"], 6000)

    def test_compact_neutral_31_clip_outline_expands_in_bounded_batches(self):
        budgets = [6, 5, 5, 5, 5, 5]
        outline = {"title": "A Walk", "style": "Documentary", "characters": plan(1)["characters"],
                   "filmBible": plan(1)["filmBible"],
                   "scenes": [{**structured_scene(title=f"Beat {i + 1}", setting="Garden path", imagePrompt="Robot walking",
                               characterKeys=["robot"], synopsis="The robot advances.", shotCount=n)}
                              for i, n in enumerate(budgets)]}

        class BoundedService:
            def __init__(self):
                self.calls = []

            def complete(self, system, user, **kwargs):
                self.calls.append((system, user, kwargs))
                if len(self.calls) == 1:
                    return json.dumps(outline)
                marker = user.rsplit("Write exactly ", 1)[1].split(" shots", 1)[0]
                return json.dumps(plan(int(marker)))

        service = BoundedService()
        result = FilmPlanner(service).plan("A robot walks through a garden.", 31, system="director guidance")
        self.assertEqual(sum(len(scene["shots"]) for scene in result["scenes"]), 31)
        self.assertIn("PHASE: OUTLINE ONLY", service.calls[0][0])
        self.assertLessEqual(service.calls[0][2]["max_tokens"], 6000)

    def test_truncated_outline_retries_with_more_budget(self):
        outline = {"title": "Walk", "style": "Documentary", "characters": plan(1)["characters"],
                   "filmBible": plan(1)["filmBible"],
                   "scenes": [structured_scene(title="Path", setting="Garden", imagePrompt="Robot walking",
                               characterKeys=["robot"], synopsis="The robot advances.", shotCount=6)]}

        class RetryService:
            def __init__(self):
                self.calls = []

            def complete(self, system, user, **kwargs):
                self.calls.append((system, user, kwargs))
                if len(self.calls) == 1:
                    return '{"title":"Walk","scenes":['
                if len(self.calls) == 2:
                    return json.dumps(plan(6))
                return json.dumps(plan(int(user.rsplit("Write exactly ", 1)[1].split(" shots", 1)[0])))

        service = RetryService()
        result = FilmPlanner(service).plan("A robot walks.", 6, system="director guidance")
        self.assertEqual(len(result["scenes"][0]["shots"]), 6)
        self.assertGreaterEqual(service.calls[1][2]["max_tokens"], service.calls[0][2]["max_tokens"])
        self.assertNotIn("unterminated", service.calls[1][1].lower())

    def test_semantic_outline_failure_keeps_normal_retry_budget(self):
        invalid = {"title": "Walk", "style": "Documentary", "characters": plan(1)["characters"],
                   "filmBible": plan(1)["filmBible"],
                   "scenes": [structured_scene(title="Path", setting="Garden", imagePrompt="Robot walking",
                               characterKeys=["robot"], synopsis="The robot advances.", shotCount=6)]}
        valid = json.loads(json.dumps(invalid))
        valid["scenes"][0]["shotCount"] = 7

        class SemanticRetryService:
            def __init__(self):
                self.calls = []

            def complete(self, system, user, **kwargs):
                self.calls.append((system, user, kwargs))
                return json.dumps(invalid if len(self.calls) == 1 else valid)

        service = SemanticRetryService()
        with self.assertRaises(ValueError):
            FilmPlanner(service).plan("A robot walks.", 7, system="director guidance")
        self.assertEqual(service.calls[0][2]["max_tokens"], service.calls[1][2]["max_tokens"])
        self.assertLessEqual(service.calls[1][2]["max_tokens"], 6000)

    def test_invalid_checkpoint_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "checkpoint.json"
            checkpoint.write_text('{"schemaVersion": 99, "signature": "wrong"}')
            service = Service([json.dumps(plan(1))])
            result = FilmPlanner(service, checkpoint_path=checkpoint).plan("Short", 1, system="sys")
            self.assertEqual(len(result["scenes"][0]["shots"]), 1)

    def test_feature_length_uses_bounded_chapters(self):
        class ChapterService:
            def __init__(self): self.calls = []
            def complete(self, system, user, **kwargs):
                self.calls.append(user)
                if len(self.calls) == 1:
                    return json.dumps({"title": "Long", "style": "Style", "characters": plan(1)["characters"],
                        "filmBible": plan(1)["filmBible"], "chapters": [
                            {"id": "c1", "title": "One", "synopsis": "One", "shotCount": 120},
                            {"id": "c2", "title": "Two", "synopsis": "Two", "shotCount": 5}]})
                if len(self.calls) in (2, 3):
                    n = 120 if len(self.calls) == 2 else 5
                    sizes = [6] * (n // 6) + ([n % 6] if n % 6 else [])
                    scenes = [structured_scene(title=f"Scene {index}", setting="Garden", imagePrompt="Robot",
                              characterKeys=["robot"], synopsis="Beat", shotCount=size)
                              for index, size in enumerate(sizes)]
                    return json.dumps({"scenes": scenes})
                marker = user.rsplit("Write exactly ", 1)[1].split(" shots", 1)[0]
                n = int(marker)
                return json.dumps(plan(n))
        service = ChapterService()
        result = FilmPlanner(service).plan("A feature", 125, system="sys")
        self.assertEqual(sum(len(s["shots"]) for s in result["scenes"]), 125)
        self.assertLessEqual(max(len(x) for x in service.calls), 30000)
        self.assertIn("ALL SCENE SUMMARIES", service.calls[3])

    def test_long_planning_is_batched_and_resumable(self):
        outline = plan(2)
        outline["scenes"][0].pop("shots")
        outline["scenes"][0].update(shotCount=4, synopsis="A robot explores.")
        second_scene = structured_scene(title="Second", setting="Garden", imagePrompt="Robot exploring",
                                        characterKeys=["robot"], synopsis="The robot continues.", shotCount=3)
        outline["scenes"].append(second_scene)
        batches = []
        for n in (4, 3):
            batch = plan(n)
            batches.append(json.dumps(batch))
        service = Service([json.dumps(outline), *batches])
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "checkpoint.json"
            result = FilmPlanner(service, checkpoint_path=checkpoint).plan("Explore", 7, system="sys")
            self.assertEqual([len(scene["shots"]) for scene in result["scenes"]], [4, 3])
            self.assertEqual(len(service.calls), 3)
            resumed = FilmPlanner(Service([]), checkpoint_path=checkpoint).plan("Explore", 7, system="sys")
            self.assertEqual([len(scene["shots"]) for scene in resumed["scenes"]], [4, 3])

    def test_many_scene_prompts_keep_global_bible_and_local_state_bounded(self):
        scenes = []
        for index in range(18):
            scenes.append({"title": f"Scene {index}", "setting": "A room.",
                           "imagePrompt": "Robot in room.", "characterKeys": ["robot"], "locationId": "garden",
                           "synopsis": "A beat\\\"\\n" + ("x" * 100000), "shotCount": 1,
                           "stateBefore": f"before-{index}", "stateAfter": f"after-{index}"})
        outline = {"title": "Long", "style": "Style", "theme": "Theme",
                   "characters": plan(1)["characters"], "filmBible": plan(1)["filmBible"],
                   "scenes": scenes}

        class ManySceneService:
            def __init__(self):
                self.calls = []

            def complete(self, system, user, **kwargs):
                self.calls.append(user)
                if len(self.calls) == 1:
                    return json.dumps(outline)
                return json.dumps(plan(1))

        service = ManySceneService()
        result = FilmPlanner(service).plan("A long story", len(scenes), system="sys")
        self.assertEqual(sum(len(scene["shots"]) for scene in result["scenes"]), 18)
        card_prompts = service.calls[1:]
        self.assertLess(max(map(len, card_prompts)), 18000)
        # The local window carries both sides of a hand-off, while distant
        # scene summaries are intentionally omitted.
        middle = card_prompts[9]
        self.assertIn("Scene 7", middle)
        self.assertIn("Scene 11", middle)
        self.assertIn("before-9", middle)
        self.assertNotIn("Scene 17", middle)

    def test_feature_checkpoint_normalizes_missing_chapter_ids_on_resume(self):
        outline = {"title": "Long", "style": "Style", "characters": plan(1)["characters"],
                   "filmBible": plan(1)["filmBible"],
                   "chapters": [{"title": "One", "synopsis": "A", "shotCount": 120},
                                {"title": "Two", "synopsis": "B", "shotCount": 1}]}

        class InterruptingService:
            def __init__(self, resume=False):
                self.resume = resume
                self.calls = []

            def complete(self, system, user, **kwargs):
                self.calls.append(user)
                if not self.resume and len(self.calls) == 1:
                    return json.dumps(outline)
                if not self.resume and len(self.calls) == 2:
                    return json.dumps({"scenes": [structured_scene(title=f"One {index}", setting="Room",
                        imagePrompt="Robot", characterKeys=["robot"], synopsis="A", shotCount=6)
                        for index in range(20)]})
                if not self.resume:
                    raise RuntimeError("interrupt before second chapter")
                if len(self.calls) == 1:
                    return json.dumps({"scenes": [structured_scene(title="Two", setting="Room", imagePrompt="Robot",
                        characterKeys=["robot"], synopsis="B", shotCount=1)]})
                marker = user.rsplit("Write exactly ", 1)[1].split(" shots", 1)[0]
                return json.dumps(plan(int(marker)))

        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "checkpoint.json"
            with self.assertRaises(RuntimeError):
                FilmPlanner(InterruptingService(), checkpoint_path=checkpoint).plan("Feature", 121, system="sys")
            saved = json.loads(checkpoint.read_text())
            self.assertEqual(saved["outline"]["chapters"][0]["id"], "chapter-1")
            self.assertEqual(len(saved["outline"]["scenes"]), 20)
            result = FilmPlanner(InterruptingService(resume=True), checkpoint_path=checkpoint).plan("Feature", 121, system="sys")
            self.assertEqual(sum(len(scene["shots"]) for scene in result["scenes"]), 121)
            self.assertEqual(len(result["scenes"]), 21)
            self.assertEqual([len(scene["shots"]) for scene in result["scenes"][:2]], [6, 6])
            self.assertEqual(len(result["scenes"][-1]["shots"]), 1)


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace

from server.backends.render_helpers import _no_dialogue, _resolved_prompt, estimate_render_seconds


class RenderHelperTests(unittest.TestCase):
    def setUp(self):
        self.board = {"sceneDescription": "A quiet room", "soundscapeInShots": True,
                      "soundscape": "soft room tone", "characters": []}

    def test_recorded_dialogue_is_visual_only(self):
        shot = {"prompt": "A person waves", "dialogue": "Hei", "dialogueSource": "recording"}
        text = _resolved_prompt(shot, self.board, with_audio=True, model="ltx-2.5")
        self.assertIn("dubbed separately", text)
        self.assertNotIn('saying exactly: "Hei"', text)

    def test_native_dialogue_keeps_direction(self):
        board = dict(self.board, characters=[{"id": "c", "name": "Kari", "voiceDescription": "warm alto"}])
        shot = {"prompt": "Kari smiles", "characterIds": ["c"], "speakerId": "c",
                "dialogue": "Hei", "dialogueSource": "native", "dialogueStyle": "quietly"}
        text = _resolved_prompt(shot, board, with_audio=True, model="ltx-2.5")
        self.assertIn('saying exactly: "Hei"', text)
        self.assertIn("quietly", text)

    def test_structured_prompt_is_flowing_prose_without_planning_metadata(self):
        board = {
            **self.board,
            "filmAutomation": {"characterReferences": True},
            "characters": [
                {"id": "c1", "name": "Kari", "description": "red coat"},
                {"id": "c2", "name": "Ola", "description": "blue scarf"},
            ],
            "filmBible": {
                "locations": [{"id": "loc-7", "title": "Garden", "description": "a wet stone garden"}],
                "props": [{"id": "prop-9", "title": "Cup", "description": "a blue enamel cup"}],
            },
        }
        shot = {
            "id": "shot-1", "directorScene": 1, "characterIds": ["c1"],
            "start": "Kari waits beside the bench", "action": "Kari lifts the cup",
            "end": "Kari holds the cup at chest height", "camera": "a slow dolly in",
            "locationId": "loc-7", "propIds": ["prop-9"],
            "stateBefore": "the cup rests on the bench",
            "stateAfter": "the cup is in Kari's hand", "motionIntent": "action",
            "prompt": "This stale text must not win.",
        }
        board["shots"] = [{"directorScene": 1, "characterIds": ["c2"]}]
        text = _resolved_prompt(shot, board, with_audio=False)
        self.assertIn("wet stone garden", text)
        self.assertIn("Kari lifts the cup", text)
        self.assertIn("Visible in this shot: Kari", text)
        self.assertIn("remain off screen", text)
        self.assertNotIn("stale text", text)
        for internal in ("loc-7", "prop-9", "START:", "ACTION:", "END:", "Motion intent:"):
            self.assertNotIn(internal, text)

    def test_no_dialogue_does_not_invent_visible_people(self):
        text = _no_dialogue({"prompt": "An empty room"}, self.board)
        self.assertNotIn("visible", text)
        self.assertIn("no dialogue", text.lower())

    def test_story_brief_stays_out_of_single_shot_and_language_survives(self):
        brief = "The robot crosses the ocean, defeats a dragon, then returns home."
        board = {**self.board, "sceneDescription": brief,
                 "filmAutomation": {"description": brief, "characterReferences": True}}
        shot = {"start": "The robot waits.", "action": "The robot raises its hand.",
                "end": "The robot holds its hand up.", "stateAfter": "The wave is complete.",
                "dialogue": "Hei!", "dialogueSource": "native", "dialogueLanguage": "Norwegian"}
        text = _resolved_prompt(shot, board)
        self.assertNotIn("dragon", text)
        self.assertIn("in Norwegian", text)
        self.assertLess(text.index("raises its hand"), text.index("The wave is complete"))
        self.assertNotIn("..", text)

    def test_timing_uses_model_namespace(self):
        class T:
            def model_key(self, name):
                return "ltx:" + name
            def estimate(self, **kwargs):
                self.kwargs = kwargs
                return 12.5
        timings = T()
        result = estimate_render_seconds({"frames": 25}, {"defaults": {"resolution": "384x256"}}, timings)
        self.assertEqual(result, 12.5)
        self.assertEqual(timings.kwargs["model"], "ltx:ltx-2.5")


if __name__ == "__main__":
    unittest.main()

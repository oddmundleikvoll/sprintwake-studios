"""CPU-only coverage for reference inputs and cache invalidation; no models."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from server.backends.render_helpers import _resolved_prompt
from server.film_comfy import ComfyFilmClient, QWEN_MODELS, QWEN_SIGMAS
from server.film_references import automatic_end, frame_prompt, reference_frame
from server.ltx_anchors import clean_anchors


class ReferenceFrameTests(unittest.TestCase):
    def test_references_reach_qwen_image_slots(self):
        client = ComfyFilmClient()
        client.preflight = Mock(return_value={"qwen_models": QWEN_MODELS})
        client._upload = Mock(side_effect=["robot.png [input]", "orc.png [input]"])
        client._submit = Mock(return_value={"outputs": {"save": {"images": [{"filename": "frame.png"}]}}})
        client._download_result = Mock(return_value=Path("frame.png"))
        refs = [(Path("robot.png"), "Ragnar — identity"), (Path("orc.png"), "Urk — identity")]
        client.qwen_image("Two distinct figures on a rocky plain.", Path("frame.png"), refs)
        graph = client._submit.call_args.args[0]
        self.assertEqual(client._upload.call_args_list[0].args[0], refs[0][0])
        self.assertEqual(client._upload.call_args_list[1].args[0], refs[1][0])
        self.assertEqual(graph["text"]["inputs"]["images.image_1"], ["ref1", 0])
        self.assertEqual(graph["text"]["inputs"]["images.image_2"], ["ref2", 0])
        self.assertEqual(graph["guider"]["inputs"]["cfg"], 1.0)
        self.assertEqual(graph["sigmas"]["inputs"]["sigmas"], QWEN_SIGMAS)

    def test_cache_tracks_portrait_bytes_prompt_and_output_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            portrait = root / "portrait.png"
            Image.new("RGB", (32, 32), "red").save(portrait)
            client = Mock()
            client.qwen_model_signature.return_value = {"fixture": 1}
            client.qwen_image.side_effect = lambda prompt, dest, refs, **kw: Image.new("RGB", (768, 512), "blue").save(dest)
            refs = [(portrait, "Rob — identity")]
            def draw(prompt="Rob stands."):
                return reference_frame(client, prompt, refs, root / "frames", seed=1, cancel=lambda: False)
            first = draw()
            self.assertEqual(first, draw())
            self.assertEqual(client.qwen_image.call_count, 1)
            Image.new("RGB", (32, 32), "green").save(portrait)
            changed = draw()
            self.assertNotEqual(first, changed)
            self.assertTrue(first.is_file())
            self.assertNotEqual(changed, draw("Rob holds one arm up."))
            changed.write_bytes(b"truncated image")
            self.assertEqual(changed, draw())
            self.assertEqual(client.qwen_image.call_count, 4)

    def test_cancel_before_preparation_never_calls_image_client(self):
        client = Mock()
        with self.assertRaisesRegex(RuntimeError, "stoppet"):
            reference_frame(client, "Unused.", [], Path("unused"), seed=1, cancel=lambda: True)
        client.qwen_image.assert_not_called()

    def test_scene_cast_is_retained_when_the_current_shot_selects_only_orc(self):
        robot = {"id": "robot", "name": "Ragnar", "description": "Riveted steel robot with amber optic."}
        orc = {"id": "orc", "name": "Urk", "description": "Green orc carrying a curved axe."}
        shots = [{"id": "one", "directorScene": 1, "characterIds": ["robot"], "prompt": "Ragnar lifts one arm."},
                 {"id": "two", "directorScene": 1, "characterIds": ["orc"], "prompt": "Urk braces his axe.",
                  "startRef": {"kind": "chain", "from": "one"}}]
        board = {"characters": [robot, orc], "shots": shots, "filmAutomation": {"characterReferences": True}}
        prompt = _resolved_prompt(shots[1], board)
        self.assertIn(robot["description"], prompt)
        self.assertIn(orc["description"], prompt)
        self.assertIn("actual preceding video tail", prompt)
        old = copy.deepcopy(board)
        old["filmAutomation"] = {}
        self.assertNotIn(robot["description"], _resolved_prompt(shots[1], old))

    def test_closing_frame_describes_final_pose_and_edited_auto_anchors_are_manual(self):
        cast = [{"key": "robot", "name": "Rob", "description": "Red steel robot."}]
        scene = {"setting": "Garden.", "characterKeys": ["robot"]}
        card = {"start": "Rob sits.", "action": "Rob raises his arm.", "end": "Rob holds his arm up.", "camera": "Locked wide."}
        prompt = frame_prompt("Clay style.", scene, card, cast, closing=True)
        self.assertIn("Show this final state: Rob holds his arm up.", prompt)
        anchor = {"ref": {"role": "film-auto-end"}, "frame": "last", "strength": .65}
        self.assertTrue(automatic_end(anchor))
        self.assertFalse(automatic_end({**anchor, "strength": .8}))
        self.assertFalse(automatic_end({**anchor, "frame": 24}))

    def test_scene_references_explicitly_keep_unselected_cast_off_screen(self):
        cast = [{"id": "robot", "key": "r", "name": "Ragnar", "description": "Steel robot."},
                {"id": "orc", "key": "o", "name": "Urk", "description": "Green orc."}]
        scene = {"setting": "Plain.", "characterKeys": ["r", "o"]}
        card = {"start": "Urk braces his axe.", "camera": "Close-up.", "characterIds": ["orc"]}
        prompt = frame_prompt("Clay.", scene, card, cast, closing=True)
        self.assertIn("Visible cast in this frame: Urk. All other character references remain off screen.", prompt)
        self.assertIn("Ragnar: Steel robot.", prompt)

    def test_32_editor_anchors_leave_room_for_legacy_start_and_end_in_runtime(self):
        explicit = [{"ref": {"path": "frame.png"}, "frame": i+1, "strength": .5} for i in range(32)]
        clean_anchors(explicit, 97)
        with self.assertRaises(ValueError):
            clean_anchors(explicit + [{"ref": {"path": "frame.png"}, "frame": 96, "strength": 1}], 97)
        runtime = [{"path": "frame.png", "frame": a["frame"], "strength": a["strength"]} for a in explicit]
        runtime += [{"path": "start.png", "frame": 0, "strength": 1},
                    {"path": "end.png", "frame": 96, "strength": 1}]
        self.assertEqual(len(clean_anchors(runtime, 97, image_key="path")), 34)

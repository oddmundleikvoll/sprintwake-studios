"""CPU-only checks for bible asset refs and observed continuity state."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from server.film_planning import validate_plan
from server.film_preflight import image_plan
from server.film_production import ReferenceBank, bible_references, validate_bible_references
from server.store import Store, _collect_media_usages


class FilmAssetTests(unittest.TestCase):
    def test_invalid_bible_ref_fails_before_reference_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bible = {"locations": [{"id": "garden", "referenceImage": {"path": "../outside.png"}}]}
            with self.assertRaises(ValueError):
                validate_bible_references(bible, root)

    def test_bible_reference_is_preserved_and_hashed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ref = root / "refs" / "garden.png"
            ref.parent.mkdir()
            Image.new("RGB", (8, 8), "green").save(ref)
            raw = {"title": "Film", "style": "natural", "filmBible": {
                "locations": [{"id": "garden", "title": "Garden", "description": "A garden",
                                "referenceImage": {"path": "refs/garden.png"}}],
                "props": []}, "characters": [], "scenes": [{
                "title": "Garden", "setting": "garden", "imagePrompt": "A garden",
                "locationId": "garden", "stateBefore": "still", "stateAfter": "still",
                "characterKeys": [], "shots": [{"title": "open", "start": "arrive",
                "action": "walk", "end": "stop", "camera": "wide", "sound": "wind"}]}]}
            plan = validate_plan(raw, 1, strict=True)
            self.assertEqual(plan["filmBible"]["locations"][0]["referenceImage"]["path"], "refs/garden.png")
            scene = {"locationId": "garden", "propIds": []}
            self.assertEqual(bible_references(scene, plan["filmBible"], root)[0][0], ref.resolve())
            board = {"shots": [{"id": "s1", "startRef": {"path": "refs/garden.png"}, "frames": 25}],
                     "filmBible": plan["filmBible"]}
            manifest = image_plan(SimpleNamespace(data_dir=root), "film", board,
                                  {"filmBible": plan["filmBible"], "scenes": [{"shots": [{"id": "s1"}]}]})
            self.assertEqual(len(manifest["assets"]), 1)
            Image.new("RGB", (8, 8), "blue").save(ref)
            changed = image_plan(SimpleNamespace(data_dir=root), "film", board,
                                 {"filmBible": plan["filmBible"], "scenes": [{"shots": [{"id": "s1"}]}]})
            self.assertNotEqual(manifest["digest"], changed["digest"])

    def test_observed_state_mismatch_blocks_but_unknown_does_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tail = root / "tail.png"
            Image.new("RGB", (4, 4), "red").save(tail)
            bank = ReferenceBank(root, "style")
            bank.observe(1, "observed narrative", tail,
                         {"locationId": "garden", "propIds": ["gate"]},
                         asset_states={"garden": "wet", "gate": "open"})
            scene = {"locationId": "garden", "propIds": ["gate"],
                     "assetStates": {"garden": "dry", "gate": "open"}}
            self.assertIsNone(bank.continuity_reference(scene, before_scene=2))
            scene["assetStates"] = {"garden": "wet", "gate": "open", "unknown": "new"}
            self.assertEqual(bank.continuity_reference(scene, before_scene=2), tail.resolve())

    def test_planned_state_mismatch_blocks_even_without_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tail = root / "tail.png"
            Image.new("RGB", (4, 4), "red").save(tail)
            bank = ReferenceBank(root, "style")
            bank.observe(1, {"observedEndState": "narrative"}, tail,
                         {"locationId": "garden", "propIds": ["gate"],
                          "assetStates": {"garden": "wet", "gate": "open"}})
            scene = {"locationId": "garden", "propIds": ["gate"],
                     "assetStates": {"garden": "dry", "gate": "open"}}
            self.assertIsNone(bank.continuity_reference(scene, before_scene=2))

    def test_rehome_keeps_bible_image_inside_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "other" / "refs" / "garden.png"
            source.parent.mkdir(parents=True)
            Image.new("RGB", (4, 4), "green").save(source)
            store = Store(root)
            board = {"name": "Mine", "filmBible": {"locations": [{
                "id": "garden", "referenceImage": {"path": "other/refs/garden.png"}}]}, "shots": []}
            moved = store._rehome_media("mine", board)
            self.assertTrue(moved)
            self.assertTrue((root / board["filmBible"]["locations"][0]["referenceImage"]["path"]).is_file())

    def test_bible_refs_are_protected_from_library_delete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ref = root / "film" / "refs" / "garden.png"
            ref.parent.mkdir(parents=True)
            Image.new("RGB", (4, 4), "green").save(ref)
            board = {"filmBible": {"locations": [{"id": "garden",
                "referenceImage": {"path": "film/refs/garden.png"}}]}, "shots": []}
            (root / "film").mkdir(exist_ok=True)
            (root / "film" / "storyboard.json").write_text(__import__("json").dumps(board))
            self.assertIn("film/refs/garden.png", _collect_media_usages(board))
            self.assertRaises(ValueError, Store(root).delete_library_item, "film/refs/garden.png")


if __name__ == "__main__":
    unittest.main()

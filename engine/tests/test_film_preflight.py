import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageOps

from server.film_preflight import approval_matches, image_plan, _decoded_hash


class FilmPreflightTests(unittest.TestCase):
    def test_hash_uses_the_same_exif_orientation_as_native_conditioning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "rotated.jpg"
            image = Image.new("RGB", (8, 12), "blue")
            image.putpixel((0, 0), (255, 0, 0))
            orientation = Image.Exif()
            orientation[274] = 6
            image.save(source, exif=orientation)
            upright = root / "upright.png"
            with Image.open(source) as decoded:
                ImageOps.exif_transpose(decoded).convert("RGB").save(upright)
            self.assertEqual(_decoded_hash(source), _decoded_hash(upright))

    def _board(self, root):
        image = root / "refs" / "opening.png"
        image.parent.mkdir()
        Image.new("RGB", (8, 8), "#467").save(image)
        return {"sceneDescription": "A quiet garden", "renderStyle": "stop motion",
                "soundscape": "wind", "filmBible": {"locations": [{"id": "garden"}]},
                "characters": [{"id": "robot", "name": "Robot", "description": "blue robot",
                                "image": {"path": "refs/opening.png"}}],
                "shots": [{"id": "shot-1", "title": "Arrives", "startRef": {"path": "refs/opening.png"},
                           "imageAnchors": [], "frames": 25, "seed": 4, "characterIds": ["robot"]}]}

    def test_manifest_hashes_decoded_images_and_matches_exact_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = self._board(root)
            plan = {"filmBible": board["filmBible"], "scenes": [{"title": "Garden", "shots": [
                {"id": "shot-1", "start": "arrive", "action": "walk", "end": "stop",
                 "camera": "wide", "locationId": "garden", "motionIntent": "action",
                 "dialogue": "go", "speakerId": "robot"}]}],
                    "characters": [{"id": "robot", "name": "Robot", "description": "blue robot"}]}
            manifest = image_plan(SimpleNamespace(data_dir=root), "demo", board, plan)
            self.assertEqual(manifest["version"], 1)
            self.assertEqual(len(manifest["images"]), 1)
            self.assertEqual(manifest["cards"][0]["name"], "Robot")
            self.assertTrue(approval_matches(manifest, {"imageApproval": {"digest": manifest["digest"]}}))
            self.assertFalse(approval_matches(manifest, {"imageApproval": {"digest": "stale"}}))

    def test_chain_preview_path_does_not_change_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = self._board(root)
            board["shots"].append({"id": "shot-2", "title": "Continues", "startRef": {
                "kind": "chain", "shotId": "shot-1", "frame": "last", "path": "preview-a.png"},
                "imageAnchors": [], "frames": 25, "seed": 5, "characterIds": ["robot"]})
            plan = {"scenes": [{"title": "Garden", "shots": [{"id": "shot-1"}, {"id": "shot-2"}]}]}
            first = image_plan(SimpleNamespace(data_dir=root), "demo", board, plan)
            board["shots"][1]["startRef"]["path"] = "preview-b.png"
            second = image_plan(SimpleNamespace(data_dir=root), "demo", board, plan)
            self.assertEqual(first["digest"], second["digest"])

    def test_missing_first_opening_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = self._board(root)
            board["shots"][0]["startRef"] = None
            with self.assertRaises(ValueError):
                image_plan(SimpleNamespace(data_dir=root), "demo", board,
                           {"scenes": [{"shots": [{"id": "shot-1"}]}]})

    def test_optional_continuation_anchor_is_bound_to_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = self._board(root)
            plan = {"scenes": [{"shots": [{"id": "shot-1"}]}]}
            before = image_plan(SimpleNamespace(data_dir=root), "demo", board, plan)
            board["filmAutomation"] = {"continuationAnchor": True}
            after = image_plan(SimpleNamespace(data_dir=root), "demo", board, plan)
            self.assertNotEqual(before["digest"], after["digest"])

    def test_planner_without_ids_uses_board_order_and_anchor_metadata_is_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = self._board(root)
            board["shots"][0]["imageAnchors"] = [{"ref": {"path": "refs/opening.png"},
                                                    "frame": 0, "strength": 0.4}]
            plan = {"scenes": [{"shots": [{"start": "arrive"}]}]}
            first = image_plan(SimpleNamespace(data_dir=root), "demo", board, plan)
            board["shots"][0]["imageAnchors"][0]["strength"] = 0.8
            second = image_plan(SimpleNamespace(data_dir=root), "demo", board, plan)
            self.assertNotEqual(first["digest"], second["digest"])

    def test_plan_count_mismatch_and_external_character_image_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = self._board(root)
            with self.assertRaises(ValueError):
                image_plan(SimpleNamespace(data_dir=root), "demo", board, {"scenes": [{"shots": []}]})
            board["characters"][0]["image"] = {"path": "/tmp/outside.png"}
            with self.assertRaises(ValueError):
                image_plan(SimpleNamespace(data_dir=root), "demo", board,
                           {"scenes": [{"shots": [{"id": "shot-1"}]}]})

    def test_native_scene_cannot_start_with_chain_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = self._board(root)
            board["shots"][0]["startRef"] = {"kind": "chain", "from": "previous", "frame": "last"}
            with self.assertRaises(ValueError):
                image_plan(SimpleNamespace(data_dir=root), "demo", board,
                           {"scenes": [{"shots": [{"id": "shot-1"}]}]})


if __name__ == "__main__":
    unittest.main()

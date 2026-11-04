import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from workspace import Workspace, new_shot, validate_timing


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Raven Studio"
        self.ws = Workspace(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tree_and_script_do_not_touch_old_workspace(self):
        project = self.ws.ensure_project("demo")
        self.assertTrue((self.root / "Library").is_dir())
        board = {"name": "Lys i rommet", "sceneDescription": "Et rom.", "shots": [{"title": "Åpning", "start": "Døren er lukket.", "action": "Den åpnes.", "end": "Lys faller inn.", "camera": "Nær", "dialogue": "Hei"}]}
        manuscript = self.ws.sync_script("demo", board)
        text = manuscript.read_text()
        self.assertIn("Lys i rommet", text)
        self.assertIn("Døren er lukket", text)
        self.assertEqual(json.loads((project / "Script/manifest.json").read_text())["canonical"], "storyboard.json")

    def test_input_preserves_source_and_is_digest_named(self):
        source = Path(self.tmp.name) / "original.png"
        source.write_bytes(b"original pixels")
        imported = self.ws.import_input("demo", source)
        self.assertNotEqual(source, imported)
        self.assertEqual(source.read_bytes(), b"original pixels")
        self.assertIn("original-", imported.name)
        self.assertEqual(self.ws.import_input("demo", source).resolve(), imported.resolve())

    def test_local_media_is_confined_and_symlinks_are_rejected(self):
        project = self.ws.ensure_project("demo")
        image = project / "Input" / "safe.png"
        image.write_bytes(b"x")
        self.assertEqual(self.ws.local_media("/media/demo/Input/safe.png"), image.resolve())
        self.assertIsNone(self.ws.local_media("/media/../outside.png"))
        link = project / "Input" / "escape.png"
        link.symlink_to(Path(self.tmp.name) / "outside.png")
        self.assertIsNone(self.ws.local_media("/media/demo/Input/escape.png"))

    def test_managed_symlink_ancestors_are_refused(self):
        self.ws.ensure()
        projects = self.root / "Projects"
        projects.rename(self.root / "Projects-real")
        projects.symlink_to(self.root / "Projects-real", target_is_directory=True)
        with self.assertRaises(ValueError):
            self.ws.ensure_project("demo")

        projects.unlink()
        projects.mkdir()
        output = projects / "demo" / "Output"
        output.parent.mkdir()
        output_real = projects / "output-real"
        output_real.mkdir()
        output.symlink_to(output_real, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.ws.ensure_project("demo")

    def test_publish_is_immutable_and_catalogue_skips_work(self):
        source = Path(self.tmp.name) / "take.png"
        source.write_bytes(b"take")
        first = self.ws.publish_image("demo", source)
        second = self.ws.publish_image("demo", source)
        self.assertNotEqual(first, second)
        (self.ws.root / "Projects/demo/image-work").mkdir()
        (self.ws.root / "Projects/demo/image-work/frame.png").write_bytes(b"x")
        paths = {item["path"] for item in self.ws.catalogue("demo")}
        self.assertIn(str(first), paths)
        self.assertNotIn(str(self.ws.root / "Projects/demo/image-work/frame.png"), paths)

    def test_export_and_recoverable_archive(self):
        project = self.ws.ensure_project("demo")
        (project / "storyboard.json").write_text('{"name":"Demo","shots":[]}', encoding="utf-8")
        (project / "recipe.json").write_text("recipe", encoding="utf-8")
        (project / "image-work").mkdir()
        (project / "image-work/cache.png").write_bytes(b"cache")
        archive = self.ws.export("demo")
        with zipfile.ZipFile(archive) as handle:
            names = handle.namelist()
            self.assertIn("demo/storyboard.json", names)
            self.assertIn("demo/recipe.json", names)
            self.assertNotIn("demo/image-work/cache.png", names)
        moved = self.ws.archive("demo")
        self.assertTrue(moved.is_dir())
        self.assertFalse(project.exists())

    def test_trash_roundtrip_preserves_board_and_media(self):
        project = self.ws.ensure_project("demo")
        (project / "storyboard.json").write_text('{"name":"Demo-film","shots":[]}', encoding="utf-8")
        media = project / "Output" / "Images" / "opening.png"
        media.write_bytes(b"real image bytes")

        entry_dir = self.ws.trash("demo")
        self.assertFalse(project.exists())
        self.assertEqual(entry_dir.name, json.loads((entry_dir / "entry.json").read_text())["id"])
        entries = self.ws.trashed_projects()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["slug"], "demo")
        self.assertEqual(entries[0]["name"], "Demo-film")

        self.assertEqual(self.ws.restore_project(entries[0]["id"]), "demo")
        self.assertEqual((project / "storyboard.json").read_text(encoding="utf-8"), '{"name":"Demo-film","shots":[]}')
        self.assertEqual(media.read_bytes(), b"real image bytes")
        self.assertFalse(entry_dir.exists())
        self.assertEqual(self.ws.trashed_projects(), [])

    def test_trash_entries_are_unique_across_recreate_and_restore_refuses_collision(self):
        first = self.ws.ensure_project("demo")
        (first / "storyboard.json").write_text('{"name":"First","shots":[]}', encoding="utf-8")
        first_entry = self.ws.trash("demo")
        second = self.ws.ensure_project("demo")
        (second / "storyboard.json").write_text('{"name":"Second","shots":[]}', encoding="utf-8")
        second_entry = self.ws.trash("demo")
        self.assertNotEqual(first_entry.name, second_entry.name)
        self.assertEqual({entry["slug"] for entry in self.ws.trashed_projects()}, {"demo"})

        self.ws.ensure_project("demo")
        with self.assertRaises(FileExistsError):
            self.ws.restore_project(first_entry.name)
        self.ws.archive("demo")
        self.assertEqual(self.ws.restore_project(first_entry.name), "demo")
        with self.assertRaises(FileExistsError):
            self.ws.restore_project(second_entry.name)

    def test_trash_ignores_missing_corrupt_and_symlink_entries(self):
        self.ws.ensure()
        trash = self.root / "Trash"
        trash.mkdir()
        missing_id = "1" * 32
        (trash / missing_id).mkdir()
        corrupt_id = "2" * 32
        corrupt = trash / corrupt_id
        corrupt.mkdir()
        (corrupt / "entry.json").write_text("not json", encoding="utf-8")
        symlink_id = "3" * 32
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        symlink = trash / symlink_id
        try:
            symlink.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")

        project_link_id = "4" * 32
        project_link_entry = trash / project_link_id
        project_link_entry.mkdir()
        (project_link_entry / "entry.json").write_text(json.dumps({"id": project_link_id, "slug": "demo", "name": "Demo", "deletedAt": 1}), encoding="utf-8")
        (project_link_entry / "Project").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.ws.trashed_projects(), [])
        with self.assertRaises(ValueError):
            self.ws.restore_project(project_link_id)

    def test_trash_rejects_symlink_inside_project(self):
        project = self.ws.ensure_project("demo")
        target = Path(self.tmp.name) / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        try:
            (project / "Input" / "escape.txt").symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaises(ValueError):
            self.ws.trash("demo")
        self.assertTrue(project.is_dir())

    def test_trash_move_failure_keeps_source_and_ignores_incomplete_entry(self):
        project = self.ws.ensure_project("demo")
        (project / "storyboard.json").write_text('{"name":"Demo","shots":[]}', encoding="utf-8")
        with mock.patch("workspace.Path.rename", side_effect=OSError("simulated move failure")):
            with self.assertRaises(OSError):
                self.ws.trash("demo")
        self.assertTrue(project.is_dir())
        self.assertEqual(self.ws.trashed_projects(), [])
        self.assertTrue(any((entry / "entry.json").is_file() for entry in (self.root / "Trash").iterdir()))

    def test_shot_shape_and_timing(self):
        shot = new_shot()
        self.assertEqual(shot["title"], "New shot")
        self.assertEqual(shot["frames"], 121)
        self.assertEqual(shot["dialogueSource"], "recording")
        self.assertTrue(validate_timing(97))
        self.assertTrue(validate_timing(144, continuation=True))
        self.assertFalse(validate_timing(96))
        self.assertFalse(validate_timing(145, continuation=True))
        self.assertFalse(validate_timing(98))
        self.assertFalse(validate_timing(97.0))
        self.assertFalse(validate_timing(True))
        self.assertEqual(new_shot(scene=3)["directorScene"], 3)
        with self.assertRaises(ValueError):
            new_shot(scene=0)


if __name__ == "__main__":
    unittest.main()

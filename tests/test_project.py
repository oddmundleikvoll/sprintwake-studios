import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from project import Project, Shot, Take, import_project, load_working_copy, media_path, save_project


class ProjectTests(unittest.TestCase):
    def test_source_is_unchanged_after_import_edit_save(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "production"
            source.mkdir()
            original = source / "storyboard.json"
            original.write_text(json.dumps({"schema": 1, "name": "Original film", "shots": [
                {"id": "01", "title": "Opening", "numFrames": 121, "prompt": "Look at the flower."}]}))
            before = hashlib.sha256(original.read_bytes()).hexdigest()
            project = import_project(source)
            self.assertEqual(project.title, "Original film")
            project.shots[0].notes = "Ny observasjon"
            target = save_project(project, root / "raven-workspace")
            self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), before)
            self.assertEqual(import_project(target).shots[0].notes, "Ny observasjon")
            self.assertEqual(list(source.iterdir()), [original])

    def test_selected_take_and_review_roundtrip(self):
        project = Project("test", "Test", "", [Shot("01", "Hei", reviewed=True,
            selected_take=1, takes=[Take("a", "A"), Take("b", "B")])])
        with tempfile.TemporaryDirectory() as directory:
            restored = import_project(save_project(project, Path(directory)))
            self.assertTrue(restored.shots[0].reviewed)
            self.assertEqual(restored.shots[0].take.id, "b")

    def test_path_traversal_and_symlink_media_cannot_escape_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "project"
            base.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(b"not needed for this path test")
            (base / "escape.png").symlink_to(outside)
            self.assertEqual(media_path("../outside.png", base), "")
            self.assertEqual(media_path(str(outside), base), "")
            self.assertEqual(media_path("escape.png", base), "")

    def test_board_relative_media_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "image.png"
            media.touch()
            self.assertEqual(media_path(f"/media/{root.name}/image.png", root), str(media.resolve()))

    def test_save_rejects_symlink_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.json"
            original.write_text("preserve me")
            (root / "test.raven.json").symlink_to(original)
            with self.assertRaises(ValueError):
                save_project(Project("test", "Test", "", []), root)
            self.assertEqual(original.read_text(), "preserve me")

    def test_invalid_durations_and_take_are_rejected(self):
        for duration in (float("nan"), float("inf"), -1, 0, 3601):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                Project("test", "Test", "", [Shot("a", "A", duration=duration)]).validate()
        with self.assertRaises(ValueError):
            Project("test", "Test", "", [Shot("a", "A", selected_take=2)]).validate()

    def test_empty_import_stays_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "storyboard.json"
            source.write_text('{"schema":1,"shots":[]}')
            self.assertEqual(import_project(source).shots, [])

    def test_actual_storyboard_fields_and_positional_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "shots/01").mkdir(parents=True)
            (root / "shots/01/clip.mp4").touch()
            (root / "opening.png").touch()
            source = root / "storyboard.json"
            source.write_text(json.dumps({"schema": 1, "name": "Film", "shots": [{
                "id": "s-not-a-folder", "frames": 145, "title": "Hi",
                "start": "Before", "action": "Wave", "end": "After", "camera": "Wide",
                "startRef": {"path": f"{root.name}/opening.png"}, "outputs": [],
            }]}))
            shot = import_project(source).shots[0]
            self.assertEqual(shot.duration, 145 / 24)
            self.assertEqual((shot.start, shot.action, shot.end, shot.camera), ("Before", "Wave", "After", "Wide"))
            self.assertEqual(shot.take.video, str((root / "shots/01/clip.mp4").resolve()))
            self.assertEqual(shot.take.image, str((root / "opening.png").resolve()))

    def test_malformed_project_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "storyboard.json"
            for data in ([], {"schema": 1, "shots": ["bad"]}, {"format": "storyboard-raven", "version": 99}, {"format": "storyboard-raven", "version": 1}):
                with self.subTest(data=data):
                    source.write_text(json.dumps(data))
                    with self.assertRaises(ValueError):
                        import_project(source)

    def test_reopening_original_restores_its_review_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "storyboard.json"
            source.write_text('{"schema":1,"shots":[{"id":"a","title":"Original"}]}')
            project = import_project(source)
            project.shots[0].notes = "Behold dette notatet"
            project.shots[0].reviewed = True
            workspace = root / "copies"
            save_project(project, workspace)
            restored = load_working_copy(source, workspace)
            self.assertEqual(restored.shots[0].notes, "Behold dette notatet")
            self.assertTrue(restored.shots[0].reviewed)
            self.assertNotIn("Behold dette", source.read_text())

    def test_malformed_saved_takes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.raven.json"
            for project in ([], {"shots": [{"takes": ["bad"]}]}, {"shots": [{"takes": "bad"}]}):
                source.write_text(json.dumps({"format": "storyboard-raven", "version": 1, "project": project}))
                with self.subTest(project=project), self.assertRaises(ValueError):
                    import_project(source)

    def test_timeline_boundary_and_end(self):
        project = Project("test", "Test", "", [Shot("a", "A"), Shot("b", "B", 4)])
        self.assertEqual(project.shot_at(5), (1, 0))
        self.assertEqual(project.shot_at(9), (1, 4))
        self.assertEqual(project.shot_at(50), (1, 4))


if __name__ == "__main__":
    unittest.main()

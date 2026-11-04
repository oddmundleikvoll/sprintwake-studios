import copy
import json
from pathlib import Path
import tempfile
import unittest

from cut import CutStore, choose_take, frames, gesture, move, scene_groups, trim, validate_clips
from project import Project, Shot
from workspace import Workspace


class CutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.store = CutStore(self.workspace, "film")
        self.project = Project("film", "Filmen", "", [Shot("a", "A", 5), Shot("b", "B", 5), Shot("c", "C", 5)])
        self.doc = self.store.initialize(self.project)

    def test_trim_order_take_undo_and_redo_survive_reopen(self):
        source = {"id": "7", "label": "Seed 7", "video": "Input/a.mp4", "image": ""}
        doc = self.store.commit(self.doc, choose_take(self.doc["clips"], "a", source, 180), "Take")
        doc = self.store.commit(doc, trim(doc["clips"], "a", 24, 96), "Trim")
        doc = self.store.commit(doc, move(doc["clips"], "a", 2), "Flytt")
        reopened = CutStore(self.workspace, "film")
        self.assertEqual(reopened.load(), doc)
        doc = reopened.history(doc)
        self.assertEqual([c["shotId"] for c in doc["clips"]], ["a", "b", "c"])
        self.assertEqual(doc["clips"][0]["inFrame"], 24)
        doc = reopened.history(doc)
        self.assertEqual(doc["clips"][0]["inFrame"], 0)
        doc = reopened.history(doc, redo=True)
        self.assertEqual(doc["clips"][0]["outFrame"], 96)
        self.assertEqual(doc["clips"][0]["source"], source)

    def test_new_edit_clears_redo_without_forgetting_source(self):
        doc = self.store.commit(self.doc, trim(self.doc["clips"], "a", 12, 100), "Trim")
        doc = self.store.history(doc)
        doc = self.store.commit(doc, move(doc["clips"], "a", 1), "Flytt")
        self.assertFalse(doc["redo"])

    def test_failed_and_stale_edits_preserve_saved_journal(self):
        doc = self.store.commit(self.doc, move(self.doc["clips"], "a", 1), "Flytt")
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "another window"):
            self.store.commit(self.doc, trim(self.doc["clips"], "a", 1, 10), "Gammel")
        with self.assertRaises(ValueError):
            self.store.commit(doc, trim(doc["clips"], "a", 120, 121), "Ugyldig")
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_view_does_not_change_production_or_follow_new_take(self):
        image = self.store.project / "Input/a.png"
        image.write_bytes(b"image")
        video = self.store.project / "Input/a.mp4"
        video.write_bytes(b"old video")
        source = {"id": "7", "label": "Seed 7", "video": "Input/a.mp4", "image": "Input/a.png"}
        doc = self.store.commit(self.doc, choose_take(self.doc["clips"], "a", source, 180), "Take")
        doc = self.store.commit(doc, trim(doc["clips"], "a", 24, 96), "Trim")
        production = copy.deepcopy(self.project)
        edited = self.store.view(doc, self.project)
        self.assertEqual(self.project, production)
        self.assertEqual(edited.shots[0].duration, 3)
        self.assertEqual(edited.shots[0].source_start, 1)
        self.assertEqual(edited.shots[0].take.video, str(video))
        self.assertEqual(edited.shot_at(3), (1, 0))

    def test_missing_media_remains_a_visible_clip_and_refuses_symlink(self):
        source = {"id": "7", "label": "Seed 7", "video": "Input/missing.mp4", "image": ""}
        doc = self.store.commit(self.doc, choose_take(self.doc["clips"], "a", source, 120), "Take")
        self.assertFalse(self.store.view(doc, self.project).shots[0].take.video)
        target = self.store.project / "Input/missing.mp4"
        target.symlink_to(Path(self.temporary.name) / "foreign.mp4")
        with self.assertRaises(ValueError): self.store.view(doc, self.project)

    def test_one_native_gesture_is_one_undo_action(self):
        clips = gesture(self.doc["clips"], {"a": (24, 96), "b": (120, 120), "c": (240, 120)})
        self.assertEqual((clips[0]["inFrame"], clips[0]["outFrame"]), (24, 120))
        doc = self.store.commit(self.doc, clips, "Dra")
        self.assertEqual(len(doc["undo"]), 1)
        self.assertEqual(self.store.history(doc)["clips"], self.doc["clips"])

    def test_native_tail_trim_clamps_to_available_source(self):
        clips = gesture(self.doc["clips"], {"a": (0, 999)})
        self.assertEqual(clips[0]["outFrame"], 120)
        clips = gesture(self.doc["clips"], {"b": (120, 40)})
        self.assertEqual((clips[1]["inFrame"], clips[1]["outFrame"]), (0, 40))

    def test_native_move_reorders_and_closes_gaps(self):
        clips = gesture(self.doc["clips"], {"b": (330, 120)})
        self.assertEqual([c["shotId"] for c in clips], ["a", "c", "b"])
        doc = {**self.doc, "clips": clips}
        view = self.store.view(doc, self.project)
        self.assertEqual(view.shot_at(5), (1, 0))
        self.assertEqual(view.duration, 15)

    def test_cancelled_gesture_is_noop(self):
        clips = gesture(self.doc["clips"], {"a": (0, 120), "b": (120, 120), "c": (240, 120)})
        self.assertEqual(self.store.commit(self.doc, clips, "Avbrutt"), self.doc)

    def test_new_script_cards_survive_undo_and_removed_cards_keep_their_media(self):
        doc = self.store.commit(self.doc, move(self.doc["clips"], "a", 1), "Flytt")
        self.project.shots = [self.project.shots[1], Shot("d", "D", 4)]
        doc = self.store.initialize(self.project)
        self.assertEqual([c["shotId"] for c in doc["clips"]], ["b", "a", "c", "d"])
        doc = self.store.history(doc)
        self.assertEqual([c["shotId"] for c in doc["clips"]], ["a", "b", "c", "d"])

    def test_exclusion_is_reversible_and_does_not_drop_media(self):
        clips = copy.deepcopy(self.doc["clips"])
        clips[0]["included"] = False
        doc = self.store.commit(self.doc, clips, "Utelat")
        self.assertEqual(len(self.store.view(doc, self.project).shots), 2)
        self.assertEqual(len(doc["clips"]), 3)
        self.assertEqual(self.store.history(doc)["clips"], self.doc["clips"])

    def test_invalid_frames_and_unconfined_sources_are_refused(self):
        for start, end in [(True, 10), (10, 10), (-1, 10), (0, 121), (0, float("nan"))]:
            with self.assertRaises(ValueError): trim(self.doc["clips"], "a", start, end)
        for path in ("/tmp/a.mp4", "Input/../a.mp4", "shots/a.mp4"):
            with self.assertRaises(ValueError):
                choose_take(self.doc["clips"], "a", {"id": "1", "label": "take", "video": path, "image": ""}, 120)
        with self.assertRaises(ValueError): frames(float("inf"))

    def test_corrupt_or_symlink_journal_is_not_replaced(self):
        self.store.path.write_text('{"version":999}')
        with self.assertRaises(ValueError): self.store.initialize(self.project)
        self.assertEqual(json.loads(self.store.path.read_text()), {"version": 999})
        self.store.path.unlink()
        self.store.path.symlink_to(self.store.project / "storyboard.json")
        with self.assertRaises(ValueError): self.store.load()

    def test_scene_groups_preserve_camera_boundaries_and_locations(self):
        board = {"filmBible": {"locations": [{"id": "garden", "title": "Hagen"}]}, "shots": [
            {"id": "a", "directorScene": 2, "locationId": "garden"},
            {"id": "b", "directorScene": 2}, {"id": "c", "directorScene": 4}, {"id": "d", "directorScene": 2}]}
        groups = scene_groups(board)
        self.assertEqual([g["scene"] for g in groups], [2, 4, 2])
        self.assertEqual(groups[0]["location"], "Hagen")
        self.assertEqual([i for i, _ in groups[0]["shots"]], [0, 1])

    def test_take_urls_with_cache_timestamps_resolve_locally(self):
        source = self.store.project / "Input" / "take one.mp4"
        source.write_bytes(b"video")
        self.assertEqual(self.workspace.local_media("/media/film/Input/take%20one.mp4?t=123"), source.resolve())
        self.assertIsNone(self.workspace.local_media("https://example.org/media/film/Input/take%20one.mp4"))
        self.assertIsNone(self.workspace.local_media("/media/film/Input/%2e%2e/%2e%2e/%2e%2e/secret.mp4?t=123"))


if __name__ == "__main__": unittest.main()

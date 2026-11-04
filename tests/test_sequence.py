import copy
import tempfile
import unittest
from pathlib import Path

from sequence import (SequenceStore, add_scene, add_sequence, assign_shots,
                      edit_group, order_cut, remove_group, timing, validate_state)
from workspace import Workspace


def clip(sid, start=0, end=24, included=True, source=None):
    return {"shotId": sid, "source": source, "inFrame": start,
            "outFrame": end, "sourceFrames": max(end, 24), "included": included}


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.store = SequenceStore(self.workspace, "film")
        self.board = {"name": "Film", "shots": [
            {"id": "a", "location": "room"}, {"id": "b", "location": "room"},
            {"id": "c", "location": "street"}, {"id": "d", "location": "room"},
        ]}
        self.clips = [clip("a"), clip("b"), clip("c"), clip("d")]
        self.doc = self.store.initialize(self.board, self.clips)

    def test_contiguous_location_groups_camera_setups_without_director_scene(self):
        scenes = self.doc["state"]["sequences"][0]["scenes"]
        self.assertEqual([s["title"] for s in scenes], ["room", "street", "room"])
        self.assertEqual([s["shotIds"] for s in scenes], [["a", "b"], ["c"], ["d"]])

    def test_targets_are_metadata_and_do_not_change_cut(self):
        before = copy.deepcopy(self.clips)
        state = edit_group(self.doc["state"], "film", "film", targetFrames=240)
        sequence = state["sequences"][0]
        state = edit_group(state, "sequence", sequence["id"], targetFrames=120)
        self.assertEqual(timing(state, self.clips)["frames"], 96)
        self.assertEqual(self.clips, before)

    def test_assign_moves_unique_shots_between_scenes(self):
        state = self.doc["state"]
        seq = state["sequences"][0]
        target = seq["scenes"][1]["id"]
        state = assign_shots(state, target, ["a", "a", "b"])
        self.assertEqual(seq["scenes"][0]["shotIds"], ["a", "b"])
        self.assertEqual(state["sequences"][0]["scenes"][0]["shotIds"], [])
        self.assertEqual(state["sequences"][0]["scenes"][1]["shotIds"], ["c", "a", "b"])

    def test_timing_honours_trims_exclusions_unassigned_and_missing(self):
        state = self.doc["state"]
        scene = state["sequences"][0]["scenes"][0]
        state = assign_shots(state, scene["id"], ["a", "ghost"])
        result = timing(state, [clip("a", 5, 15), clip("b", included=False), clip("c", 0, 30)])
        self.assertEqual(result["frames"], 40)
        self.assertEqual(result["unassigned"], [])
        self.assertEqual(result["missing"], ["ghost", "d"])
        self.assertEqual(result["sequences"][0]["scenes"][0]["frames"], 10)

    def test_new_cut_shot_is_added_to_unassigned_and_reopen_survives(self):
        doc = self.store.commit(self.doc, add_sequence(self.doc["state"], "Ekstra"), "Ny sekvens")
        updated = self.store.initialize(self.board, self.clips + [clip("new")])
        self.assertEqual(updated["revision"], doc["revision"] + 1)
        self.assertIn("new", updated["state"]["sequences"][0]["scenes"][-1]["shotIds"])
        self.assertEqual(self.store.load(), updated)
        undone = self.store.history(updated)
        self.assertEqual(undone["state"]["sequences"][-1]["title"], "Filmen" if len(undone["state"]["sequences"]) == 1 else "Ekstra")

    def test_order_cut_is_explicit_and_preserves_clip_dict(self):
        state = self.doc["state"]
        first, second = state["sequences"][0]["scenes"][:2]
        state = assign_shots(state, second["id"], ["a"])
        result = order_cut(state, [self.clips[2], self.clips[0], self.clips[1]])
        self.assertEqual([c["shotId"] for c in result], ["b", "c", "a"])
        self.assertEqual(result[-1], self.clips[0])

    def test_cas_invalid_data_and_symlink_are_refused(self):
        changed = self.store.commit(self.doc, add_sequence(self.doc["state"], "x"), "x")
        with self.assertRaises(ValueError): self.store.commit(self.doc, self.doc["state"], "stale")
        bad = copy.deepcopy(self.doc["state"]); bad["sequences"][0]["scenes"][0]["shotIds"].append("b")
        with self.assertRaises(ValueError): validate_state(bad)
        self.store.path.unlink()
        self.store.path.symlink_to(Path(self.tmp.name) / "outside.json")
        with self.assertRaises(ValueError): self.store.load()

    def test_deleting_all_groups_is_stable_on_reopen(self):
        state = self.doc["state"]
        for sequence in list(state["sequences"]):
            state = remove_group(state, "sequence", sequence["id"])
        doc = self.store.commit(self.doc, state, "Tøm struktur")
        reopened = self.store.initialize(self.board, self.clips)
        self.assertEqual(reopened["state"]["sequences"], [])
        self.assertEqual(reopened, doc)

    def test_opening_second_window_does_not_invalidate_first_window(self):
        second=SequenceStore(self.store.workspace,self.store.slug).initialize(self.board,self.clips)
        self.assertEqual(second,self.doc)
        changed=edit_group(self.doc["state"],"film","film",targetFrames=2400)
        saved=self.store.commit(self.doc,changed,"Mål")
        self.assertEqual(saved["state"]["targetFrames"],2400)

    def test_intentionally_unassigned_shot_is_not_recreated(self):
        state = self.doc["state"]
        scene = state["sequences"][0]["scenes"][0]
        state = remove_group(state, "scene", scene["id"])  # a/b are deliberately unassigned
        doc = self.store.commit(self.doc, state, "Avtildel b")
        reopened = self.store.initialize(self.board, self.clips)
        self.assertNotIn("b", [sid for seq in reopened["state"]["sequences"] for scene in seq["scenes"] for sid in scene["shotIds"]])
        self.assertEqual(reopened["knownShotIds"], ["a", "b", "c", "d"])

    def test_external_card_is_kept_through_undo(self):
        changed = self.store.commit(self.doc, add_sequence(self.doc["state"], "Ekstra"), "Ekstra")
        updated = self.store.initialize(self.board, self.clips + [clip("new")])
        undone = self.store.history(updated)
        self.assertIn("new", [sid for seq in undone["state"]["sequences"] for scene in seq["scenes"] for sid in scene["shotIds"]])

    def test_empty_scenes_do_not_affect_explicit_order(self):
        state = self.doc["state"]
        seq = state["sequences"][0]
        state = remove_group(state, "scene", seq["scenes"][1]["id"])
        state = add_scene(state, seq["id"], "Tom")
        result = order_cut(state, [self.clips[2], self.clips[0], self.clips[1]])
        self.assertEqual([c["shotId"] for c in result], ["a", "b", "c"])


if __name__ == "__main__": unittest.main()

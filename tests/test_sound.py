import tempfile
from pathlib import Path
import unittest

from sound import SoundStore, default_state, remove_clip, update_clip
from workspace import Workspace


class SoundJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SoundStore(Workspace(Path(self.tmp.name)), "film")
        self.doc = self.store.load()

    def tearDown(self):
        self.tmp.cleanup()

    def clip(self, ident="a"):
        return {"id": ident, "track": "dialogue", "label": "A", "path": "Input/a.wav", "sourceFrames": 48,
                "inFrame": 0, "outFrame": 24, "atFrame": 24, "gainDb": 0.0, "fadeIn": 2, "fadeOut": 2, "muted": False}

    def test_commit_undo_redo_and_reopen(self):
        state = default_state(); state["clips"] = [self.clip()]
        changed = self.store.commit(self.doc, state, "Legg til dialog")
        self.assertEqual(self.store.load()["state"]["clips"][0]["atFrame"], 24)
        undone = self.store.history(changed)
        self.assertEqual(undone["state"]["clips"], [])
        redone = self.store.history(undone, redo=True)
        self.assertEqual(redone["state"]["clips"][0]["id"], "a")

    def test_stale_commit_is_rejected_and_edits_are_validated(self):
        state = default_state(); state["clips"] = [self.clip()]
        changed = self.store.commit(self.doc, state, "add")
        with self.assertRaises(ValueError):
            self.store.commit(self.doc, default_state(), "stale")
        with self.assertRaises(ValueError):
            update_clip(changed["state"], "a", atFrame=-1)
        self.assertEqual(remove_clip(changed["state"], "a")["clips"], [])

    def test_rejects_traversal_and_nonfinite_values(self):
        state = default_state(); state["clips"] = [self.clip()]
        state["clips"][0]["path"] = "Input/../outside.wav"
        with self.assertRaises(ValueError):
            self.store.commit(self.doc, state, "bad")
        state = default_state(); state["tracks"][0]["gainDb"] = float("nan")
        with self.assertRaises(ValueError):
            self.store.commit(self.doc, state, "bad")


if __name__ == "__main__":
    unittest.main()

import copy
import unittest

from studio_state import PlaybackReceipt, authoring_digest


class StudioStateTests(unittest.TestCase):
    def test_proposals_survive_server_saves_but_not_authoring_changes(self):
        board = {"name": "Test", "shots": [{"id": "a", "start": "one", "frames": 121}]}
        saved = copy.deepcopy(board)
        saved.update(updatedAt=123, filmAutomation={"phase": "review"})
        saved["shots"][0].update(status="done", outputs=["clip.mp4"])
        self.assertEqual(authoring_digest(board), authoring_digest(saved))
        saved["shots"][0]["start"] = "two"
        self.assertNotEqual(authoring_digest(board), authoring_digest(saved))

    def test_review_requires_full_playback_and_rejects_seek(self):
        receipt = PlaybackReceipt()
        receipt.update(0, 5, True)
        self.assertFalse(receipt.update(5, 5, True))
        receipt = PlaybackReceipt()
        for i in range(101):
            complete = receipt.update(i / 20, 5, True)
        self.assertTrue(complete)

    def test_empty_native_note_default_preserves_authoring_digest(self):
        board = {"name": "Film", "shots": [{"id": "s1", "title": "Åpning"}]}
        saved = copy.deepcopy(board)
        saved["shots"][0].update(ravenNotes="", ravenReviewed=False)
        self.assertEqual(authoring_digest(board), authoring_digest(saved))
        saved["shots"][0]["ravenNotes"] = "En faktisk regiendring"
        self.assertNotEqual(authoring_digest(board), authoring_digest(saved))

    def test_paused_clock_does_not_credit_review(self):
        receipt = PlaybackReceipt()
        for i in range(101):
            complete = receipt.update(i / 20, 5, False)
        self.assertFalse(complete)

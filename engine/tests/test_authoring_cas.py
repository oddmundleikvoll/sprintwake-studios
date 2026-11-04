import copy
import threading
import unittest

from server.app import Handler
from server.authoring import authoring_digest


class _Store:
    def __init__(self, board):
        self.board = copy.deepcopy(board)

    def load(self, slug):
        return copy.deepcopy(self.board)

    def save(self, slug, board):
        self.board = copy.deepcopy(board)
        return copy.deepcopy(self.board)


class _Film:
    def owns(self, slug):
        return False


class _Orch:
    def __init__(self):
        self._start_lock = threading.RLock()
        self.busy = False
        self.stills_busy = False
        self._llm_active = 0


class _Context:
    def __init__(self, board):
        self.store = _Store(board)
        self.film = _Film()
        self.orch = _Orch()


class AuthoringCASTests(unittest.TestCase):
    def setUp(self):
        self.board = {"name": "Film", "updatedAt": 10.0, "anchorsRevision": "a",
                      "shots": [], "characters": [], "filmBible": {}}
        self.handler = object.__new__(Handler)
        self.handler.ctx = _Context(self.board)

    def test_digest_compare_happens_under_save_lock(self):
        expected = authoring_digest(self.board)
        changed = copy.deepcopy(self.board)
        changed["sceneDescription"] = "Brukeren redigerte"
        self.handler.ctx.store.board = changed
        with self.assertRaisesRegex(RuntimeError, "endret"):
            self.handler._save_from_page("film", self.board, expected_digest=expected,
                                         expected_updated_at="10.0")
        self.assertEqual(self.handler.ctx.store.board["sceneDescription"], "Brukeren redigerte")

    def test_updated_at_compare_rejects_external_edit(self):
        with self.assertRaisesRegex(RuntimeError, "oppdatert"):
            self.handler._save_from_page("film", self.board, expected_updated_at="9.0")
        self.assertEqual(self.handler.ctx.store.board["updatedAt"], 10.0)


if __name__ == "__main__":
    unittest.main()

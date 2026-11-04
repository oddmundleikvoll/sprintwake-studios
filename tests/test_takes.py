import tempfile
import unittest
from pathlib import Path

from takes import TakePanelMixin


class FakeBackend:
    def __init__(self):
        self.gets = []
        self.posts = []

    def get(self, path):
        self.gets.append(path)
        return {"takes": [{"seed": 7, "status": "done", "current": True, "clipUrl": "film/Output/Video/take.mp4", "runtimeSeconds": 2.4}]}

    def post(self, path, payload):
        self.posts.append((path, payload))
        return {"board": {"shots": [{"id": "s1", "outputs": ["new.mp4"]}]}}


class FakeStudio(TakePanelMixin):
    def __init__(self):
        self.slug = "film"
        self.epoch = 3
        self.raw_shot = {"id": "s1"}
        self.shot = type("Shot", (), {"id": "s1", "take": None})()
        self.backend = FakeBackend()
        self.workspace = type("Workspace", (), {"local_media": lambda _, ref: Path("/tmp/take.mp4")})()
        self.modal = []
        self.saved = 0
        self.refreshed = 0

    def submit(self, key, operation, done=None, **kwargs):
        if done:
            done(operation())
        return True

    def show_modal(self, *args): self.modal.append(args)
    def modal_close(self): self.modal.append(("closed",))
    def toast(self, *args): self.modal.append(args)
    def save(self, after=None):
        self.saved += 1
        if after: after()
    def refresh_view(self): self.refreshed += 1
    def _busy(self): return False
    def preview(self, path, title): self.modal.append(("preview", str(path), title))


class TakePanelTests(unittest.TestCase):
    def test_history_loads_and_can_preview(self):
        studio = FakeStudio()
        self.assertTrue(studio.take_action("take.history"))
        self.assertEqual(studio.backend.gets, ["/api/boards/film/shots/s1/seed-takes"])
        self.assertTrue(studio.take_action("take.preview.0"))
        self.assertEqual(studio.modal[-1][0], "preview")

    def test_adopt_saves_then_posts_and_refreshes(self):
        studio = FakeStudio()
        studio.show_take_history()
        self.assertTrue(studio.take_action("take.adopt.0"))
        self.assertEqual(studio.saved, 1)
        self.assertEqual(studio.backend.posts[0][0], "/api/boards/film/shots/s1/seed-takes/7/adopt")
        self.assertEqual(studio.refreshed, 1)

    def test_stale_shot_result_is_ignored(self):
        studio = FakeStudio()
        def stale_submit(key, operation, done=None, **kwargs):
            result = operation()
            studio.raw_shot = {"id": "other"}
            if done: done(result)
            return True
        studio.submit = stale_submit
        studio._render_take_history = lambda: (_ for _ in ()).throw(AssertionError("stale result rendered"))
        self.assertTrue(studio.show_take_history())


if __name__ == "__main__":
    unittest.main()

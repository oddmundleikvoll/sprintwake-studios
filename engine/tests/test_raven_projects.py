import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from server.app import Handler
from server.raven_projects import restore_project, trash_project
from server.store import Store


class _Orch:
    busy = False
    stills_busy = False
    _llm_active = 0

    def __init__(self):
        self._start_lock = threading.Lock()


class RavenProjectHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "Raven Studio"
        self.projects = self.root / "Projects"
        self.projects.mkdir(parents=True)
        self.store = Store(workspace=self.root, data_dir=self.projects)
        self.orch = _Orch()
        self.film = SimpleNamespace(status=lambda: {"busy": False}, owns=lambda slug: False)
        self.ctx = SimpleNamespace(store=self.store, data_dir=self.projects, orch=self.orch, film=self.film)

        class TestHandler(Handler):
            ctx = self.ctx

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temp.cleanup()

    def board(self, slug="demo", name="Demo Project"):
        project = self.projects / slug
        project.mkdir()
        (project / "storyboard.json").write_text(json.dumps({"name": name, "shots": []}), encoding="utf-8")
        (project / "Output").mkdir()
        media = project / "Output" / "still.png"
        media.write_bytes(b"pixels")
        return project, media

    def post(self, slug, payload):
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/raven/boards/{slug}/trash",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return urlopen(request)

    def restore(self, entry_id):
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/raven/trash/{entry_id}/restore",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        return urlopen(request)

    def test_exact_confirmation_moves_board_and_media(self):
        project, media = self.board()
        media_bytes = media.read_bytes()
        with self.assertRaises(HTTPError) as failure:
            self.post("demo", {"confirmName": "demo project"})
        self.assertEqual(failure.exception.code, 400)
        self.assertTrue(project.exists())

        with self.post("demo", {"confirmName": "Demo Project"}) as response:
            result = json.load(response)
        self.assertEqual(result["slug"], "demo")
        self.assertEqual(result["name"], "Demo Project")
        entry = Path(result["entry"])
        self.assertEqual(json.loads((entry / "entry.json").read_text())["id"], result["id"])
        self.assertEqual(json.loads((entry / "Project" / "storyboard.json").read_text())["name"], "Demo Project")
        self.assertEqual((entry / "Project" / "Output" / "still.png").read_bytes(), media_bytes)
        self.assertFalse(project.exists())

    def test_busy_rejection_does_not_move_project(self):
        project, _ = self.board()
        self.orch.busy = True
        with self.assertRaises(HTTPError) as failure:
            self.post("demo", {"confirmName": "Demo Project"})
        self.assertEqual(failure.exception.code, 409)
        self.assertTrue(project.exists())

    def test_restore_roundtrip_preserves_media(self):
        project, media = self.board()
        media_bytes = media.read_bytes()
        with self.post("demo", {"confirmName": "Demo Project"}) as response:
            entry_id = json.load(response)["id"]
        with self.restore(entry_id) as response:
            result = json.load(response)
        self.assertEqual(result, {"ok": True, "id": entry_id, "slug": "demo"})
        self.assertEqual((project / "storyboard.json").is_file(), True)
        self.assertEqual(media.read_bytes(), media_bytes)
        self.assertFalse((self.root / "Trash" / entry_id).exists())

    def test_restore_collision_returns_409_without_overwriting_either_project(self):
        original, original_media = self.board()
        original_media.write_bytes(b"original")
        with self.post("demo", {"confirmName": "Demo Project"}) as response:
            entry_id = json.load(response)["id"]
        current, current_media = self.board()
        current_media.write_bytes(b"current")
        with self.assertRaises(HTTPError) as failure:
            self.restore(entry_id)
        self.assertEqual(failure.exception.code, 409)
        self.assertEqual(current_media.read_bytes(), b"current")
        self.assertEqual((self.root / "Trash" / entry_id / "Project" / "Output" / "still.png").read_bytes(), b"original")

    def test_restore_start_lock_serializes_concurrent_requests(self):
        self.board("one", "One")
        self.board("two", "Two")
        with self.post("one", {"confirmName": "One"}) as response:
            one_id = json.load(response)["id"]
        with self.post("two", {"confirmName": "Two"}) as response:
            two_id = json.load(response)["id"]
        entered = threading.Event()
        release = threading.Event()
        calls = []
        original = restore_project

        def blocked(data_dir, entry_id):
            calls.append(entry_id)
            if entry_id == one_id:
                entered.set()
                self.assertTrue(release.wait(3))
            return original(data_dir, entry_id)

        responses = []

        def request(entry_id):
            try:
                with self.restore(entry_id) as response:
                    responses.append(json.load(response))
            except Exception as exc:  # pragma: no cover - assertion below reports it
                responses.append(exc)

        with patch("server.app.restore_project", side_effect=blocked):
            first = threading.Thread(target=request, args=(one_id,))
            second = threading.Thread(target=request, args=(two_id,))
            first.start()
            self.assertTrue(entered.wait(3))
            second.start()
            time.sleep(0.1)
            self.assertEqual(calls, [one_id])
            release.set()
            first.join(3)
            second.join(3)
        self.assertEqual(sorted(item["slug"] for item in responses), ["one", "two"])

    def test_start_lock_serializes_trash_requests(self):
        self.board("one", "One")
        self.board("two", "Two")
        entered = threading.Event()
        release = threading.Event()
        calls = []
        original = trash_project

        def blocked(data_dir, slug):
            calls.append(slug)
            if slug == "one":
                entered.set()
                self.assertTrue(release.wait(3))
            return original(data_dir, slug)

        responses = []

        def request(slug, name):
            try:
                with self.post(slug, {"confirmName": name}) as response:
                    responses.append(json.load(response))
            except Exception as exc:  # pragma: no cover - assertion below reports it
                responses.append(exc)

        with patch("server.app.trash_project", side_effect=blocked):
            first = threading.Thread(target=request, args=("one", "One"))
            second = threading.Thread(target=request, args=("two", "Two"))
            first.start()
            self.assertTrue(entered.wait(3))
            second.start()
            time.sleep(0.1)
            self.assertEqual(calls, ["one"])
            release.set()
            first.join(3)
            second.join(3)
        self.assertEqual(sorted(item["slug"] for item in responses), ["one", "two"])


if __name__ == "__main__":
    unittest.main()

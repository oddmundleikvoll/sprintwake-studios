from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import base64
import os

import sys
sys.path.insert(0, str(Path(__file__).parents[1]))

from importing import import_project
from backend import Backend
from workspace import Workspace


class FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root

    def import_input(self, slug, source):
        destination = self.root / slug / "Input" / Path(source).name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(Path(source).read_bytes())
        return destination


class FakeBackend:
    def __init__(self, root):
        self.data_dir = root / "Projects"
        self.data_dir.mkdir(parents=True)
        self.boards = {}

    def post(self, path, payload):
        slug = payload["name"].lower().replace(" ", "-")
        board = {"schema": 1, "name": payload["name"], "defaults": {"frames": 121}, "shots": []}
        self.boards[slug] = board
        (self.data_dir / slug / "refs").mkdir(parents=True)
        return {"slug": slug, "board": board}

    def upload(self, slug, source):
        target = self.data_dir / slug / "refs" / Path(source).name
        target.write_bytes(Path(source).read_bytes())
        return {"path": f"{slug}/refs/{target.name}", "url": f"/media/{slug}/refs/{target.name}", "label": target.name}

    def put(self, path, board):
        slug = path.rsplit("/", 1)[1]
        self.boards[slug] = board
        (self.data_dir / slug / "storyboard.json").write_text(json.dumps(board))

    def get(self, path):
        return {"boards": [{"slug": slug, "name": board["name"]} for slug, board in self.boards.items()]}


class ImportTests(unittest.TestCase):
    def test_import_rewrites_refs_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "source"
            (source_root / "refs").mkdir(parents=True)
            (source_root / "shots" / "01").mkdir(parents=True)
            (source_root / "refs" / "cast.png").write_bytes(b"png")
            (source_root / "shots" / "01" / "clip.mp4").write_bytes(b"video")
            original = {
                "schema": 1, "name": "Min film", "filmAutomation": {"approved": True},
                "characters": [{"name": "A", "image": {"path": "refs/cast.png"}}],
                "shots": [{"id": "a", "status": "done", "outputs": ["shots/01/clip.mp4"], "startRef": {"path": "refs/cast.png"}}],
            }
            source = source_root / "storyboard.json"
            source.write_text(json.dumps(original))
            backend = FakeBackend(root / "studio")
            slug, listing = import_project(source, backend, FakeWorkspace(root / "studio"))
            board = backend.boards[slug]
            self.assertEqual(slug, "min-film")
            self.assertEqual(board["shots"][0]["status"], "draft")
            self.assertNotIn("filmAutomation", board)
            self.assertTrue((backend.data_dir / slug / "shots" / "01" / "clip.mp4").is_file())
            self.assertEqual(board["shots"][0]["outputs"][0], f"/media/{slug}/shots/01/clip.mp4")
            self.assertEqual(json.loads(source.read_text()), original)
            self.assertEqual(len(listing["boards"]), 1)

    def test_import_uses_real_storyboard_endpoint_without_models(self):
        runtime = Path(__file__).resolve().parents[1] / "runtime/bundle-venv/bin/python"
        if not runtime.is_file():
            self.skipTest("run scripts/setup.sh to install the dedicated CPU runtime")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio_root = root / "studio"
            source_root = root / "source-project"
            (source_root / "refs").mkdir(parents=True)
            (source_root / "shots" / "01").mkdir(parents=True)
            # A real 1x1 PNG is enough for the server's upload/ref contract.
            png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
            (source_root / "refs" / "cast.png").write_bytes(png)
            (source_root / "shots" / "01" / "clip.mp4").write_bytes(b"retained-output")
            (source_root / "final.mp4").write_bytes(b"retained-final")

            backend = Backend(studio_root, runtime_python=runtime, startup_timeout=30)
            workspace = Workspace(studio_root)
            try:
                backend.start()
                template = backend.post("/api/boards", {"name": "Template for import"})
                board = template["board"]
                board["name"] = "Endpoint import"
                board["characters"] = [{"id": "cast-1", "name": "Cast", "image": {"path": "refs/cast.png"}}]
                board["styleRefs"] = [{"path": "refs/cast.png", "label": "style"}]
                board["shots"] = [{"id": "shot-1", "title": "Opening", "frames": 121,
                                    "startRef": "refs/cast.png", "outputs": ["shots/01/clip.mp4"],
                                    "status": "done"}]
                board["finalVideo"] = {"url": "/media/source-project/final.mp4"}
                source = source_root / "storyboard.json"
                source.write_text(json.dumps(board))
                source_hash = source.read_bytes()
                slug, _ = import_project(source, backend, workspace)
                loaded = backend.get(f"/api/boards/{slug}")["board"]
                self.assertEqual(source.read_bytes(), source_hash)
                self.assertEqual(loaded["shots"][0]["status"], "draft")
                self.assertTrue((backend.data_dir / slug / "shots" / "01" / "clip.mp4").is_file())
                self.assertTrue((backend.data_dir / slug / "final.mp4").is_file())
                ref_path = backend.data_dir / slug / "refs"
                self.assertTrue(any(path.suffix == ".png" for path in ref_path.iterdir()))
                self.assertEqual(loaded["shots"][0]["outputs"][0], f"/media/{slug}/shots/01/clip.mp4")
            finally:
                backend.close()

    def test_import_rejects_symlinked_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "source"
            (source_root / "refs").mkdir(parents=True)
            outside = root / "outside.png"
            outside.write_bytes(b"not imported")
            try:
                (source_root / "refs" / "escape.png").symlink_to(outside)
            except OSError:
                self.skipTest("symlinks unavailable")
            source = source_root / "storyboard.json"
            source.write_text(json.dumps({"schema": 1, "name": "Escape", "shots": [], "styleRefs": [{"path": "refs/escape.png"}]}))
            with self.assertRaises(ValueError):
                import_project(source, FakeBackend(root / "studio"), FakeWorkspace(root / "studio"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from library import MediaLibrary
from workspace import Workspace


class UploadBackend:
    def __init__(self):
        self.calls = []

    def upload(self, slug, source):
        self.calls.append((slug, Path(source)))
        return {"canonicalRef": f"/media/{slug}/{Path(source).name}"}


class MediaLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "Raven Studio"
        self.workspace = Workspace(self.root)
        self.library = MediaLibrary(self.workspace)
        self.source = Path(self.temp.name) / "generated"
        self.source.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def _image(self, name="frame.png", data=b"png-bytes"):
        path = self.source / name
        path.write_bytes(data)
        return path

    def test_repeated_import_deduplicates_and_preserves_origins(self):
        image = self._image()
        original = image.read_bytes()
        first = self.library.import_images({"old-output": self.source})
        second = self.library.import_images({"old-output-again": image})
        self.assertEqual(first["imported"], 1)
        self.assertEqual(second["deduplicated"], 1)
        records = self.library.catalogue()
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["origins"]), 2)
        self.assertEqual(image.read_bytes(), original)
        self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), records[0]["sha256"])

    def test_same_filename_distinct_bytes_get_distinct_assets(self):
        first = self._image(data=b"one")
        other = Path(self.temp.name) / "other"
        other.mkdir()
        second = other / first.name
        second.write_bytes(b"two")
        self.library.import_images({"a": first, "b": second})
        records = self.library.catalogue()
        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0]["name"], records[1]["name"])

    def test_hidden_frames_cache_and_recipe_handling(self):
        image = self._image("hero.png")
        image.with_suffix(".json").write_text('{"prompt":"hero"}\n', encoding="utf-8")
        (self.source / ".hidden.png").write_bytes(b"hidden")
        (self.source / "frames001").mkdir()
        (self.source / "frames001" / "skip.png").write_bytes(b"skip")
        (self.source / "cache").mkdir()
        (self.source / "cache" / "skip.png").write_bytes(b"skip")
        self.library.import_images({"generated": self.source})
        record = self.library.catalogue()[0]
        self.assertIn("recipe", record)
        self.assertEqual(Path(record["recipe"]).read_text(encoding="utf-8"), '{"prompt":"hero"}\n')

    def test_source_and_destination_symlink_escape_refused(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "escape.png").write_bytes(b"escape")
        symlink_source = Path(self.temp.name) / "linked"
        symlink_source.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.library.import_images({"escape": symlink_source})
        destination = self.root / "Library" / "escape.png"
        destination.symlink_to(outside / "escape.png")
        with self.assertRaises(ValueError):
            self.library.resolve(destination)

    def test_resolution_rejects_parent_escape_and_tampered_index(self):
        image = self._image()
        self.library.import_images({"generated": image})
        self.assertIsNone(self.library.resolve("../Projects/demo/Input/nope.png"))
        self.library.index_path.write_text(json.dumps([{"id": "bad", "kind": "image", "path": str(self.root / "Projects" / "escape.png"), "name": "escape.png"}]), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.library.catalogue()

    def test_resolution_accepts_canonicalized_library_path(self):
        image = self._image()
        self.library.import_images({"generated": image})
        path = Path(self.library.catalogue()[0]["path"])
        self.assertEqual(self.library.resolve(path.resolve()), path.resolve())

    def test_malformed_index_is_not_silently_overwritten(self):
        self.library.index_path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.library.import_images({"generated": self._image()})

    def test_copy_verification_rejects_wrong_digest_without_destination(self):
        image = self._image()
        destination = self.library.library_dir / "wrong.png"
        with self.assertRaises(ValueError):
            self.library._copy_verified(image, destination, "0" * 64)
        self.assertFalse(destination.exists())

    def test_adopt_imports_into_project_and_uploads_canonical_ref(self):
        image = self._image()
        self.library.import_images({"generated": image})
        asset_id = self.library.catalogue()[0]["id"]
        backend = UploadBackend()
        result = self.library.adopt("demo", asset_id, backend)
        self.assertEqual(result["canonicalRef"].split("/")[1], "media")
        self.assertEqual(len(backend.calls), 1)
        uploaded = backend.calls[0][1]
        self.assertTrue(uploaded.is_file())
        self.assertTrue(str(uploaded).startswith(str(self.root / "Projects" / "demo" / "Input")))
        self.assertEqual(uploaded.read_bytes(), image.read_bytes())


if __name__ == "__main__":
    unittest.main()

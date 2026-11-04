import tempfile
import unittest
from pathlib import Path

from server.film_production import archive_take


class FilmTakeArchiveTests(unittest.TestCase):
    def _take(self, root, recipe=b"recipe", media=b"movie"):
        source = root / "active"
        source.mkdir(parents=True)
        (source / "recipe.json").write_bytes(recipe)
        (source / "manifest.json").write_bytes(b"manifest")
        (source / "movie.mp4").write_bytes(media)
        return source

    def test_archive_is_immutable_and_reused_for_identical_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._take(root)
            archive_root = root / "takes"
            first = archive_take(source, archive_root, {"run": "old"})
            original = self._take(root / "second")
            source.joinpath("movie.mp4").write_bytes(b"changed-active")
            second = archive_take(original, archive_root, {"run": "old"})
            self.assertEqual(first, second)
            self.assertEqual((first / "movie.mp4").read_bytes(), b"movie")

    def test_changed_input_gets_different_archive_and_original_remains(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._take(root)
            first = archive_take(source, root / "takes", {"run": "old"})
            source.joinpath("movie.mp4").write_bytes(b"new")
            second = archive_take(source, root / "takes", {"run": "new"})
            self.assertNotEqual(first, second)
            self.assertEqual((first / "movie.mp4").read_bytes(), b"movie")

    def test_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._take(root)
            outside = root / "outside.bin"
            outside.write_bytes(b"x")
            (source / "link").symlink_to(outside)
            with self.assertRaises(ValueError):
                archive_take(source, root / "takes")

    def test_corrupt_archive_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._take(root)
            archive_root = root / "takes"
            first = archive_take(source, archive_root, {"run": "old"})
            (first / "movie.mp4").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                archive_take(source, archive_root, {"run": "old"})


if __name__ == "__main__":
    unittest.main()

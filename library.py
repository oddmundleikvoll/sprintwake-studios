"""Persistent, safe image library for Raven production workspaces.

The library is deliberately independent of the UI and of ComfyUI.  Imports
are copies (the source tree is never moved or deleted), and the JSON index is
updated atomically so an interrupted migration cannot leave a half-written
catalogue.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

try:  # Running as a module from the Raven directory and as a package both work.
    from .workspace import IMAGE_EXTENSIONS, Workspace
except ImportError:  # pragma: no cover - direct script/test invocation
    from workspace import IMAGE_EXTENSIONS, Workspace


SKIP_DIRS = {"cache", "caches", "__pycache__", ".git", "image-work", "seed-takes", "refine-runs"}
INDEX_NAME = "index.json"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class MediaLibrary:
    """A content-addressed catalogue rooted at ``<workspace>/Library``."""

    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.workspace.ensure()
        self.library_dir = self.workspace.root / "Library"
        self.index_path = self.library_dir / INDEX_NAME
        self._assert_destination(self.library_dir)

    def _assert_destination(self, path: Path) -> None:
        if not _inside(path, self.workspace.root):
            raise ValueError("Biblioteksti utenfor arbeidsområdet")
        current = self.workspace.root
        for part in ("", *path.relative_to(self.workspace.root).parts):
            current = self.workspace.root if not part else current / part
            if current.is_symlink():
                raise ValueError(f"Symlink er ikke tillatt i biblioteket: {current}")

    def _read(self) -> list[dict[str, Any]]:
        if not self.index_path.is_file() or self.index_path.is_symlink():
            return []
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Bibliotekindeksen er ugyldig") from exc
        if not isinstance(value, list):
            raise ValueError("Bibliotekindeksen er ugyldig")
        return value

    def _assert_library_path(self, path: Path) -> Path:
        """Validate both lexical and resolved containment below Library."""
        raw = Path(os.path.normpath(str(path)))
        if not raw.is_absolute():
            raw = Path(os.path.abspath(raw))
        library = self.library_dir.absolute()
        canonical_library = library.resolve(strict=False)
        if _inside(raw, library):
            checked_root = library
        elif _inside(raw, canonical_library):
            # macOS commonly exposes /var as a symlink to /private/var.  A
            # caller may have canonicalised an otherwise valid library path.
            checked_root = canonical_library
        else:
            raise ValueError("Sti utenfor bildebiblioteket")
        current = checked_root
        for part in raw.relative_to(checked_root).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"Symlink er ikke tillatt i bildebiblioteket: {current}")
        resolved = raw.resolve(strict=False)
        if not _inside(resolved, canonical_library):
            raise ValueError("Biblioteksti peker utenfor bildebiblioteket")
        return raw

    @staticmethod
    def _source_safe(path: Path) -> None:
        """Reject a source symlink; directory walking never follows children."""
        path = path.expanduser()
        # macOS has system aliases such as /var -> /private/var.  Rejecting
        # every parent symlink would make ordinary local folders unusable.
        if path.is_symlink():
            raise ValueError(f"Kilde kan ikke være en symlink: {path}")

    def _files(self, source: Path) -> list[Path]:
        if source.is_file():
            if source.suffix.lower() in IMAGE_EXTENSIONS:
                return [source]
            return []
        if not source.is_dir():
            raise ValueError(f"Kilden finnes ikke: {source}")
        result: list[Path] = []
        for directory, dirs, files in os.walk(source, topdown=True, followlinks=False):
            directory_path = Path(directory)
            dirs[:] = [name for name in dirs if not name.startswith(".") and name.lower() not in SKIP_DIRS and not name.startswith("frames")]
            for name in files:
                path = directory_path / name
                if name.startswith(".") or path.is_symlink():
                    continue
                if path.suffix.lower() in IMAGE_EXTENSIONS and path.is_file():
                    result.append(path)
        return sorted(result)

    def _copy_verified(self, source: Path, destination: Path, digest: str) -> None:
        """Copy through a temporary sibling and verify bytes before publish."""
        self._assert_library_path(destination)
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=self.library_dir)
        try:
            with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
                shutil.copyfileobj(input_file, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            temporary_path = Path(temporary)
            if _sha256(temporary_path) != digest:
                raise ValueError(f"Kilden endret seg under import: {source}")
            shutil.copystat(source, temporary_path)
            os.replace(temporary_path, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _filename(source: Path, digest: str) -> str:
        stem = _SAFE_NAME.sub("-", source.stem).strip(".-_") or "image"
        return f"{stem[:80]}-{digest[:16]}{source.suffix.lower()}"

    def import_images(self, sources: dict[str, Path | str]) -> dict[str, Any]:
        """Import image files from labelled files/directories and return a summary."""
        self._assert_destination(self.library_dir)
        records = {record.get("id"): record for record in self._read() if record.get("id")}
        imported = deduplicated = skipped = 0
        skipped_paths: list[str] = []
        for label, raw_source in sources.items():
            source = Path(raw_source).expanduser()
            self._source_safe(source)
            paths = self._files(source)
            for path in paths:
                try:
                    digest = _sha256(path)
                    stat = path.stat()
                except OSError:
                    skipped += 1
                    skipped_paths.append(str(path))
                    continue
                record = records.get(digest)
                origin = {"label": str(label), "source": str(path), "name": path.name, "modifiedAt": stat.st_mtime}
                if record:
                    deduplicated += 1
                    if origin not in record.setdefault("origins", []):
                        record["origins"].append(origin)
                    # A recipe may have been absent during an earlier import.
                    destination = self._assert_library_path(self.library_dir / record["name"])
                    if not destination.is_file() or destination.is_symlink() or _sha256(destination) != digest:
                        # An index may have survived a partial copy.  Repair it
                        # from the unchanged source before writing the index.
                        self._copy_verified(path, destination, digest)
                else:
                    name = self._filename(path, digest)
                    destination = self._assert_library_path(self.library_dir / name)
                    if destination.exists() or destination.is_symlink():
                        if not destination.is_file() or _sha256(destination) != digest:
                            raise ValueError(f"Bibliotekmål finnes allerede med annet innhold: {destination}")
                    else:
                        self._copy_verified(path, destination, digest)
                    record = {"id": digest, "kind": "image", "path": str(destination), "relative": str(destination.relative_to(self.library_dir)), "name": destination.name, "sha256": digest, "origins": [origin], "bytes": stat.st_size, "modifiedAt": destination.stat().st_mtime}
                    records[digest] = record
                    imported += 1
                recipe = path.with_suffix(".json")
                if recipe.is_file() and not recipe.is_symlink():
                    recipe_target = destination.with_suffix(".json")
                    self._assert_destination(recipe_target)
                    if not recipe_target.exists():
                        shutil.copy2(recipe, recipe_target)
                    record["recipe"] = str(recipe_target)
        self._assert_destination(self.index_path)
        _atomic_json(self.index_path, list(records.values()))
        return {"imported": imported, "deduplicated": deduplicated, "skipped": skipped, "skippedPaths": skipped_paths, "total": len(records)}

    def catalogue(self) -> list[dict[str, Any]]:
        records = []
        for record in self._read():
            if record.get("kind") != "image" or not record.get("id"):
                continue
            path = self._assert_library_path(Path(record.get("path", self.library_dir / record.get("relative", ""))))
            if path.is_file() and not path.is_symlink():
                records.append(record)
        return sorted(records, key=lambda item: item.get("name", ""))

    def resolve(self, path_or_id: str | Path) -> Path | None:
        value = str(path_or_id)
        record = next((item for item in self._read() if item.get("id") == value), None)
        if record:
            candidate = Path(record["path"])
        else:
            raw = Path(path_or_id).expanduser()
            candidate = raw if raw.is_absolute() else self.library_dir / raw
        lexical = Path(os.path.normpath(str(candidate)))
        if not lexical.is_absolute():
            lexical = Path(os.path.abspath(lexical))
        library = self.library_dir.absolute()
        canonical_library = library.resolve(strict=False)
        if not (_inside(lexical, library) or _inside(lexical, canonical_library)):
            return None
        candidate = self._assert_library_path(candidate)
        return candidate if candidate.is_file() and not candidate.is_symlink() and candidate.suffix.lower() in IMAGE_EXTENSIONS else None

    def adopt(self, slug: str, asset_id: str, backend: Any) -> Any:
        source = self.resolve(asset_id)
        if source is None:
            raise ValueError("Ukjent eller ugyldig bibliotekbilde")
        copied = self.workspace.import_input(slug, source)
        return backend.upload(slug, copied)

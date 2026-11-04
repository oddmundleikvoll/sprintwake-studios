"""Small, model-free project filesystem operations used by Raven routes."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any


SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
ENTRY_ID_RE = re.compile(r"[0-9a-f]{32}")


def _slug(value: str) -> str:
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        raise ValueError("Ugyldig prosjekt-ID")
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _assert_safe(path: Path, root: Path) -> None:
    if not _inside(path, root):
        raise ValueError("Sti utenfor arbeidsområdet")
    current = root
    for part in ("", *path.relative_to(root).parts):
        current = root if not part else current / part
        if current.is_symlink():
            raise ValueError(f"Symlink er ikke tillatt i arbeidsområdet: {current}")


def _assert_no_symlinks(path: Path, root: Path) -> None:
    _assert_safe(path, root)
    for directory, names, files in os.walk(path, followlinks=False):
        directory_path = Path(directory)
        _assert_safe(directory_path, root)
        for name in (*names, *files):
            candidate = directory_path / name
            if candidate.is_symlink():
                raise ValueError(f"Symlink er ikke tillatt i arbeidsområdet: {candidate}")


def trash_project(data_dir: Path | str, slug: str) -> dict[str, Any]:
    """Atomically move ``Projects/<slug>`` to its recoverable Raven trash.

    ``data_dir`` is the canonical ``Raven Studio/Projects`` directory.  The
    sibling ``Trash`` directory is intentionally outside the project tree.
    Metadata is written before the rename; an interrupted/failed rename is
    therefore visible only as an incomplete entry, never as a false deletion.
    """
    projects = Path(data_dir).expanduser()
    if projects.is_symlink() or not projects.is_dir():
        raise FileNotFoundError(projects)
    projects = projects.absolute()
    root = projects.parent
    _assert_safe(projects, root)
    safe_slug = _slug(slug)
    project = projects / safe_slug
    if project.is_symlink() or not project.is_dir():
        raise FileNotFoundError(project)
    _assert_no_symlinks(project, root)

    name = safe_slug
    board_path = project / "storyboard.json"
    if board_path.is_file() and not board_path.is_symlink():
        try:
            board = json.loads(board_path.read_text(encoding="utf-8"))
            candidate = board.get("name") or board.get("title")
            if isinstance(candidate, str) and candidate.strip():
                name = candidate.strip()
        except (OSError, TypeError, ValueError):
            pass

    trash = root / "Trash"
    if trash.exists() and trash.is_symlink():
        raise ValueError("Trash kan ikke være en symlink")
    trash.mkdir(parents=True, exist_ok=True)
    _assert_safe(trash, root)
    while True:
        entry_id = uuid.uuid4().hex
        entry = trash / entry_id
        if not entry.exists() and not entry.is_symlink():
            break
    entry.mkdir()
    _assert_safe(entry, root)
    metadata = {"id": entry_id, "slug": safe_slug, "name": name, "deletedAt": time.time()}
    metadata_path = entry / "entry.json"
    temporary = entry / ".entry.json.tmp"
    with temporary.open("w",encoding="utf-8") as handle:
        json.dump(metadata,handle,ensure_ascii=False,indent=2)
        handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, metadata_path)
    _assert_safe(metadata_path, root)
    destination = entry / "Project"
    _assert_safe(destination, root)
    project.rename(destination)
    return {**metadata, "entry": str(entry)}


def restore_project(data_dir: Path | str, entry_id: str) -> str:
    """Restore a complete trash entry to its original slug, without clobbering."""
    if not isinstance(entry_id, str) or not ENTRY_ID_RE.fullmatch(entry_id):
        raise ValueError("Ugyldig slettesoppføring")
    projects = Path(data_dir).expanduser()
    if projects.is_symlink() or not projects.is_dir():
        raise FileNotFoundError(projects)
    projects = projects.absolute()
    root = projects.parent
    _assert_safe(projects, root)
    entry = root / "Trash" / entry_id
    if entry.is_symlink() or not entry.is_dir():
        raise FileNotFoundError(entry)
    _assert_safe(entry, root)
    metadata_path = entry / "entry.json"
    project = entry / "Project"
    if metadata_path.is_symlink() or not metadata_path.is_file() or project.is_symlink() or not project.is_dir():
        raise ValueError("Ugyldig slettesoppføring")
    _assert_safe(metadata_path, root)
    _assert_no_symlinks(project, root)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("Ugyldig slettesoppføring") from exc
    if not isinstance(metadata, dict) or metadata.get("id") != entry_id:
        raise ValueError("Ugyldig slettesoppføring")
    slug = _slug(metadata.get("slug"))
    destination = projects / slug
    _assert_safe(destination, root)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    project.rename(destination)
    # A restored project is no longer a trash entry.  Keep this cleanup
    # deliberately narrow: unexpected files are not recursively deleted.
    try:
        metadata_path.unlink()
        entry.rmdir()
    except OSError:
        pass
    return slug

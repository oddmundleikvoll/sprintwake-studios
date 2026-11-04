"""Safe import of a production Storyboard project into Raven's data root."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
from typing import Any

IMAGE_AUDIO = {".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".m4a", ".flac"}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _source_file(value: str, source_root: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value.removeprefix("/media/").removeprefix("media/").removeprefix("projects/")
    candidate = Path(raw)
    if candidate.is_absolute():
        lexical = candidate
        path = candidate.resolve()
    else:
        parts = candidate.parts
        if parts and parts[0] == source_root.name:
            candidate = Path(*parts[1:])
        lexical = source_root / candidate
        path = lexical.resolve()
    if _inside(lexical, source_root):
        current = source_root
        for part in lexical.relative_to(source_root).parts:
            current /= part
            if current.is_symlink():
                raise ValueError(f"Symlinks are not allowed in imported media: {current}")
    if not _inside(path, source_root.resolve()) or path.is_symlink() or not path.is_file():
        return None
    return path


def _copy_file(source: Path, destination: Path, root: Path) -> None:
    source = source.resolve()
    if source.is_symlink() or not source.is_file() or not _inside(source, root.resolve()):
        raise ValueError(f"Import file escapes source project: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.resolve() != source:
        destination.unlink()
    shutil.copy2(source, destination)


def _copy_shots(source_root: Path, destination_root: Path) -> None:
    shots = source_root / "shots"
    if not shots.exists():
        return
    if shots.is_symlink():
        raise ValueError("Source shots directory cannot be a symlink")
    for item in shots.rglob("*"):
        if item.is_symlink():
            raise ValueError(f"Symlinks are not allowed in imported shots: {item}")
        if item.is_file():
            _copy_file(item, destination_root / item.relative_to(source_root), source_root)


def _walk_refs(node: Any, found: dict[str, Path], source_root: Path) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("path", "url") and isinstance(value, str):
                candidate = _source_file(value, source_root)
                if candidate and candidate.suffix.lower() in IMAGE_AUDIO:
                    found[value] = candidate
            else:
                _walk_refs(value, found, source_root)
    elif isinstance(node, list):
        for value in node:
            _walk_refs(value, found, source_root)
    elif isinstance(node, str):
        candidate = _source_file(node, source_root)
        if candidate and candidate.suffix.lower() in IMAGE_AUDIO:
            found[node] = candidate


def _walk_files(node: Any, found: dict[str, Path], source_root: Path) -> None:
    """Find referenced non-reference media such as finalVideo and outputs."""
    if isinstance(node, dict):
        for value in node.values():
            _walk_files(value, found, source_root)
    elif isinstance(node, list):
        for value in node:
            _walk_files(value, found, source_root)
    elif isinstance(node, str):
        candidate = _source_file(node, source_root)
        if candidate and candidate.suffix.lower() not in IMAGE_AUDIO:
            found[node] = candidate


def _rewrite(node: Any, refs: dict[str, Any], files: dict[str, str]) -> Any:
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            if key in ("path", "url") and isinstance(value, str):
                if value in refs:
                    # A complete ref object replaces the original path/url
                    # holder, matching Storyboard's upload contract.
                    return deepcopy(refs[value])
                if value in files:
                    result[key] = files[value]
                    continue
            result[key] = _rewrite(value, refs, files)
        return result
    if isinstance(node, list):
        return [_rewrite(value, refs, files) for value in node]
    if isinstance(node, str):
        if node in refs:
            return deepcopy(refs[node])
        if node in files:
            return files[node]
    return node


def _strip_stale_state(board: dict[str, Any]) -> None:
    board.pop("approval", None)
    board.pop("approvals", None)
    board.pop("filmAutomation", None)
    for shot in board.get("shots") or []:
        if isinstance(shot, dict):
            for key in ("approval", "approvals", "filmAutomation", "validation", "logUrl", "dubUrl", "thumb"):
                shot.pop(key, None)
            shot["status"] = "draft"
            shot["progress"] = 0
            shot["runtimeSeconds"] = None


def import_project(source: Path, backend: Any, workspace: Any) -> tuple[str, dict[str, Any]]:
    """Import *source* as a new server project without modifying its files."""
    source = Path(source).expanduser().resolve()
    if source.is_dir():
        source = source / "storyboard.json"
    if source.is_symlink() or not source.is_file():
        raise ValueError("Choose a regular storyboard.json file")
    source_root = source.parent.resolve()
    original = json.loads(source.read_text())
    if not isinstance(original, dict) or original.get("schema") != 1 or not isinstance(original.get("shots"), list):
        raise ValueError("Choose a Storyboard project with schema 1")
    if original.get("format") == "storyboard-raven":
        raise ValueError("Raven working copies are not production Storyboard projects")

    name = str(original.get("name") or source_root.name or "Imported storyboard")
    created = backend.post("/api/boards", {"name": name})
    slug = created["slug"]
    destination_root = Path(backend.data_dir) / slug
    _copy_shots(source_root, destination_root)

    refs: dict[str, Path] = {}
    _walk_refs(original, refs, source_root)
    uploaded: dict[str, Any] = {}
    files: dict[str, str] = {}
    for old, path in refs.items():
        copied = workspace.import_input(slug, path)
        uploaded[old] = backend.upload(slug, copied)

    # Rewrite all remaining local files, including finalVideo and explicit
    # output paths. Files in canonical shot folders are copied verbatim above.
    other_files: dict[str, Path] = {}
    _walk_files(original, other_files, source_root)
    for value, path in other_files.items():
        try:
            relative = path.relative_to(source_root)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] == "shots":
            rel = relative
        elif path.name == "final.mp4":
            rel = Path("final.mp4")
        else:
            rel = Path("imports") / path.name
        _copy_file(path, destination_root / rel, source_root)
        files[value] = f"/media/{slug}/{rel.as_posix()}"

    board = _rewrite(deepcopy(original), uploaded, files)
    board["name"] = created.get("board", {}).get("name") or name
    board["schema"] = 1
    # Ensure fields introduced by the current server exist, while retaining
    # the source's prompts, recipes, takes and actual output references.
    created_board = created.get("board") or {}
    for key, value in created_board.items():
        board.setdefault(key, deepcopy(value))
    _strip_stale_state(board)
    backend.put(f"/api/boards/{slug}", board)
    if hasattr(workspace, "sync_script"):
        workspace.sync_script(slug, board)
    listing = backend.get("/api/boards")
    return slug, listing

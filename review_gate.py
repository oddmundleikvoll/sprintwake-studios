"""Pure media and receipt helpers for revision-bound production gates."""
from __future__ import annotations

from pathlib import Path
from typing import Any

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def _value(ref: Any) -> str:
    if isinstance(ref, dict):
        return str(ref.get("path") or ref.get("url") or ref.get("ref") or "")
    return str(ref or "")


def _local(ref: Any, workspace) -> Path | None:
    value = _value(ref)
    if not value:
        return None
    path = workspace.local_media(value)
    return Path(path) if path else None


def canonical_ref(ref: Any, workspace) -> str:
    """Normalize absolute, /media/, and project-relative refs to one identity."""
    value = _value(ref)
    path = workspace.local_media(value)
    if not path and value.startswith("/"):
        candidate = Path(value).resolve()
        root = Path(workspace.projects_dir).resolve()
        try:
            if candidate.is_file() and candidate.is_relative_to(root):
                path = candidate
        except (OSError, ValueError):
            path = None
    if path:
        return str(Path(path).resolve().relative_to(Path(workspace.projects_dir).resolve()))
    return value.lstrip("/").removeprefix("media/")


def plan_images(plan: dict[str, Any] | None, workspace) -> list[Path]:
    """Return existing images from the real image-plan card/image shapes."""
    if not isinstance(plan, dict):
        return []
    entries = []
    for key in ("cards", "images"):
        value = plan.get(key)
        if isinstance(value, list):
            entries.extend(value)
    result, seen = [], set()
    for entry in entries:
        ref = entry.get("ref") if isinstance(entry, dict) else entry
        path = _local(ref, workspace)
        if path and path.suffix.lower() in IMAGE_EXTENSIONS and path not in seen:
            seen.add(path); result.append(path)
    return result


def plan_images_complete(plan: dict[str, Any] | None, workspace) -> bool:
    """Require every declared card/image ref to resolve to an actual image."""
    if not isinstance(plan, dict):
        return False
    entries = []
    for key in ("cards", "images"):
        value = plan.get(key)
        if isinstance(value, list):
            entries.extend(value)
    if not entries:
        return False
    for entry in entries:
        ref = entry.get("ref") if isinstance(entry, dict) else entry
        path = _local(ref, workspace)
        if not path or path.suffix.lower() not in IMAGE_EXTENSIONS:
            return False
    return True


def review_videos(preview: dict[str, Any] | None, board: dict[str, Any] | None, workspace, slug: str) -> list[Path]:
    """Resolve the actual videos for reviewPreview.shotIds.

    ``reviewPreview.tail`` and ``nextOpening`` are still-image refs. Video
    review must follow the preview's shot IDs into canonical board outputs,
    falling back to the managed ``shots/NN/clip.mp4`` location.
    """
    if not isinstance(preview, dict) or not isinstance(board, dict):
        return []
    ids = preview.get("shotIds") or []
    shots = [s for s in board.get("shots") or [] if isinstance(s, dict)]
    by_id = {str(s.get("id")): (i, s) for i, s in enumerate(shots)}
    result, seen = [], set()
    for ident in ids:
        pair = by_id.get(str(ident))
        if not pair:
            continue
        index, shot = pair
        refs = shot.get("outputs") or []
        if not isinstance(refs, list):
            refs = [refs]
        candidates = list(refs) + [shot.get("video"), shot.get("clip"), f"{slug}/shots/{index + 1:02d}/clip.mp4"]
        path = next((_local(ref, workspace) for ref in candidates if _local(ref, workspace) and _local(ref, workspace).suffix.lower() in VIDEO_EXTENSIONS), None)
        if path and path not in seen:
            seen.add(path); result.append(path)
    return result


def review_videos_complete(preview: dict[str, Any] | None, board: dict[str, Any] | None, workspace, slug: str) -> bool:
    """True only when every requested shot ID resolves to a real video."""
    if not isinstance(preview, dict) or not isinstance(board, dict):
        return False
    ids = preview.get("shotIds") or []
    return bool(ids) and len(review_videos(preview, board, workspace, slug)) == len(set(map(str, ids)))


def review_images(preview: dict[str, Any] | None, workspace) -> list[Path]:
    """Resolve the visible tail/opening stills, when they are actual files."""
    if not isinstance(preview, dict):
        return []
    result = []
    for key in ("tail", "nextOpening"):
        path = _local(preview.get(key), workspace)
        if path and path.suffix.lower() in IMAGE_EXTENSIONS and path not in result:
            result.append(path)
    return result


def review_images_complete(preview: dict[str, Any] | None, workspace) -> bool:
    """Require every supplied still-image ref; an omitted final opening is valid."""
    if not isinstance(preview, dict):
        return False
    supplied = 0
    for key in ("tail", "nextOpening"):
        if key not in preview or preview.get(key) in (None, ""):
            continue
        supplied += 1
        path = _local(preview.get(key), workspace)
        if not path or path.suffix.lower() not in IMAGE_EXTENSIONS:
            return False
    return supplied > 0


def receipt_snapshot(context: dict[str, Any] | None) -> tuple:
    project = context.get("project") if isinstance(context, dict) and isinstance(context.get("project"), dict) else context or {}
    preview = project.get("reviewPreview") or {}
    plan = project.get("imagePlan") or {}
    return (project.get("revision"), (context or {}).get("anchorRevision"), plan.get("digest"), preview.get("key"), tuple(project.get("reviewPreview", {}).get("shotIds", []) if isinstance(project.get("reviewPreview"), dict) else ()))

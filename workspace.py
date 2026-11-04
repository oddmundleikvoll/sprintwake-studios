"""Safe, human-facing production workspace for Raven.

The Storyboard board remains the source of truth in ``Projects/<slug>``.  This
module only adds a tidy layer around it and deliberately has no model or
network responsibilities.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

MEDIA_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
    ".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv",
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
AUDIO_EXTENSIONS = MEDIA_EXTENSIONS - IMAGE_EXTENSIONS - VIDEO_EXTENSIONS
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
TRASH_ID_RE = re.compile(r"[0-9a-f]{32}")
MAX_INPUT_BYTES = 4 * 1024 * 1024 * 1024
TRANSIENT_DIRS = {"image-work", "seed-takes", "refine-runs", "sound-preview", "editorial-preview", "__pycache__", ".git"}


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _slug(slug: str) -> str:
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise ValueError("Ugyldig prosjekt-ID")
    return slug


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class Workspace:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or (Path.home() / "Movies" / "Raven Studio")).expanduser()
        self.projects_dir = self.root / "Projects"
        self.system_dir = self.root / "System"

    def _assert_managed_path(self, path: Path) -> None:
        """Reject symlinks in every existing component below the Raven root."""
        if not _inside(path, self.root):
            raise ValueError("Sti utenfor arbeidsområdet")
        relative = path.relative_to(self.root)
        current = self.root
        for part in ("", *relative.parts):
            current = self.root if not part else current / part
            if current.is_symlink():
                raise ValueError(f"Symlink er ikke tillatt i arbeidsområdet: {current}")

    def _mkdir(self, path: Path) -> None:
        self._assert_managed_path(path)
        path.mkdir(parents=True, exist_ok=True)
        self._assert_managed_path(path)

    def ensure(self) -> Path:
        """Create only Raven's external tree; never touch ComfyUI or Storyboard."""
        self.root.parent.mkdir(parents=True, exist_ok=True)
        self._assert_managed_path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("Projects", "Library", "Exports", "Tools", "System"):
            self._mkdir(self.root / name)
        return self.root

    def _project(self, slug: str) -> Path:
        self.ensure()
        slug = _slug(slug)
        project = self.projects_dir / slug
        if project.exists() and project.is_symlink():
            raise ValueError("Prosjektet kan ikke være en symlink")
        if not _inside(project, self.projects_dir):
            raise ValueError("Prosjekt utenfor arbeidsområdet")
        return project

    def ensure_project(self, slug: str) -> Path:
        project = self._project(slug)
        self._mkdir(project)
        for relative in ("Script", "Input", "Output/Images", "Output/Video", "Output/Audio", "Output/Exports", "System"):
            directory = project / relative
            self._mkdir(directory)
        return project

    def sync_script(self, slug: str, board: dict[str, Any]) -> Path:
        project = self.ensure_project(slug)
        shots = board.get("shots") or []
        lines = [f"# {board.get('name') or board.get('title') or slug}", "", "## Produksjonsbrief", ""]
        for key, label in (("sceneDescription", "Scene"), ("renderStyle", "Visuell stil"), ("soundscape", "Lydlandskap")):
            if board.get(key):
                lines += [f"**{label}:** {board[key]}", ""]
        bible = board.get("filmBible") or {}
        for key, heading in (("storyArc", "Historiebue"), ("theme", "Tema")):
            if bible.get(key): lines += [f"## {heading}", str(bible[key]), ""]
        acts = bible.get("acts") or []
        if acts:
            lines += ["## Scener og akter", ""]
            for act in acts:
                lines += [f"### {act.get('title') or act.get('name') or 'Akt'}", str(act.get("description") or act.get("summary") or ""), ""]
        lines += ["## Klipp", ""]
        for index, shot in enumerate(shots, 1):
            lines += [f"### {index:02d} · {shot.get('title') or 'Nytt klipp'}"]
            for key, label in (("start", "Start"), ("action", "Handling"), ("end", "Slutt"), ("camera", "Kamera"), ("dialogue", "Dialog")):
                if shot.get(key):
                    lines.append(f"- **{label}:** {shot[key]}")
            if shot.get("soundNote"):
                lines.append(f"- **Lyd:** {shot['soundNote']}")
            if shot.get("prompt"):
                lines.append(f"- **Videobeskrivelse:** {shot['prompt']}")
            lines.append("")
        manuscript = project / "Script" / "manus.md"
        _atomic_text(manuscript, "\n".join(lines).rstrip() + "\n")
        manifest = {"slug": slug, "canonical": "storyboard.json", "directories": {"script": "Script/manus.md", "input": "Input", "images": "Output/Images", "video": "Output/Video", "audio": "Output/Audio", "exports": "Output/Exports"}, "updatedAt": time.time()}
        _atomic_text(project / "Script" / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        return manuscript

    def import_input(self, slug: str, source: Path | str) -> Path:
        project = self.ensure_project(slug)
        source = Path(source).expanduser()
        if source.is_symlink() or not source.is_file() or source.suffix.lower() not in MEDIA_EXTENSIONS:
            raise ValueError("Input må være en vanlig støttet mediefil")
        if source.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("Input-filen er for stor")
        digest = _digest(source)
        target = project / "Input" / f"{source.stem[:80]}-{digest[:12]}{source.suffix.lower()}"
        if target.exists() and _digest(target) == digest:
            return target
        if target.exists() or target.is_symlink():
            target = project / "Input" / f"{source.stem[:70]}-{digest[:20]}-{uuid.uuid4().hex[:6]}{source.suffix.lower()}"
        shutil.copy2(source, target)
        return target

    def local_media(self, ref: str | Path) -> Path | None:
        if not isinstance(ref, (str, Path)):
            return None
        parsed = urlsplit(str(ref))
        if parsed.scheme or parsed.netloc:
            return None
        # Production take URLs carry cache-busting timestamps. Those identify
        # the same local media file, not a filename containing "?t=...".
        value = unquote(parsed.path)
        if value.startswith("/media/"):
            value = value[7:]
        elif value.startswith("media/"):
            value = value[6:]
        elif value.startswith("/" ):
            return None
        candidate = (self.projects_dir / value).resolve()
        if not _inside(candidate, self.projects_dir.resolve()) or candidate.suffix.lower() not in MEDIA_EXTENSIONS:
            return None
        try:
            relative_parts = candidate.relative_to(self.projects_dir.resolve()).parts
            if any((self.projects_dir.joinpath(*relative_parts[:i])).is_symlink() for i in range(1, len(relative_parts))):
                return None
        except ValueError:
            return None
        return candidate if candidate.is_file() and not candidate.is_symlink() else None

    def _files(self, project: Path) -> Iterable[Path]:
        for path in project.rglob("*"):
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            if any(part in TRANSIENT_DIRS or part.startswith("frames") for part in path.relative_to(project).parts):
                continue
            yield path

    def catalogue(self, slug: str) -> list[dict[str, Any]]:
        project = self._project(slug)
        if not project.is_dir():
            return []
        result, seen = [], set()
        for path in self._files(project):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            ext = path.suffix.lower()
            kind = "image" if ext in IMAGE_EXTENSIONS else "video" if ext in VIDEO_EXTENSIONS else "audio"
            result.append({"path": str(path), "relative": str(path.relative_to(project)), "kind": kind, "bytes": path.stat().st_size, "modifiedAt": path.stat().st_mtime})
        return sorted(result, key=lambda item: item["relative"])

    def project_summaries(self) -> list[dict[str, Any]]:
        self.ensure()
        summaries = []
        for project in sorted(self.projects_dir.iterdir()):
            if not project.is_dir() or project.is_symlink():
                continue
            board_path = project / "storyboard.json"
            if not board_path.is_file() or board_path.is_symlink():
                continue
            try:
                board = json.loads(board_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            shots = board.get("shots") or []
            summaries.append({"slug": project.name, "name": board.get("name") or board.get("title") or project.name, "shotCount": len(shots), "renderedCount": sum(bool(s.get("outputs")) for s in shots if isinstance(s, dict)), "updatedAt": board_path.stat().st_mtime})
        return summaries

    def publish(self, slug: str, source: Path | str, kind: str) -> Path:
        project = self.ensure_project(slug)
        source = Path(source).expanduser()
        kind = kind.lower()
        allowed = {"image": IMAGE_EXTENSIONS, "video": VIDEO_EXTENSIONS, "audio": AUDIO_EXTENSIONS}
        if kind not in allowed or source.is_symlink() or not source.is_file() or source.suffix.lower() not in allowed[kind]:
            raise ValueError("Ugyldig leveranse")
        destination_dir = project / "Output" / {"image": "Images", "video": "Video", "audio": "Audio"}[kind]
        digest = _digest(source)
        target = destination_dir / f"{source.stem[:80]}-{digest[:12]}{source.suffix.lower()}"
        if target.exists():
            target = destination_dir / f"{source.stem[:70]}-{digest[:20]}-{uuid.uuid4().hex[:6]}{source.suffix.lower()}"
        shutil.copy2(source, target)
        return target

    def publish_image(self, slug: str, source: Path | str) -> Path:
        return self.publish(slug, source, "image")

    def publish_video(self, slug: str, source: Path | str) -> Path:
        return self.publish(slug, source, "video")

    def snapshots(self, slug: str, board: dict[str, Any]) -> Path:
        project = self.ensure_project(slug)
        stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        target = project / "System" / "snapshots" / f"{stamp}.json"
        _atomic_text(target, json.dumps({"savedAt": time.time(), "slug": slug, "board": board}, ensure_ascii=False, indent=2) + "\n")
        return target

    def export(self, slug: str, include_video: bool = True) -> Path:
        project = self._project(slug)
        if not project.is_dir():
            raise FileNotFoundError(project)
        self._mkdir(self.root / "Exports")
        target = self.root / "Exports" / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        if target.exists():
            target = self.root / "Exports" / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in project.rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(project)
                if any(part in TRANSIENT_DIRS or part.startswith("frames") for part in relative.parts):
                    continue
                if not include_video and path.suffix.lower() in VIDEO_EXTENSIONS:
                    continue
                archive.write(path, Path(slug) / relative)
        return target

    def archive(self, slug: str) -> Path:
        project = self._project(slug)
        if not project.is_dir():
            raise FileNotFoundError(project)
        destination_dir = self.root / "Archive"
        self._mkdir(destination_dir)
        destination = destination_dir / slug
        if destination.exists() or destination.is_symlink():
            destination = destination_dir / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}"
        project.rename(destination)
        return destination

    def trash(self, slug: str) -> Path:
        """Pure storage helper; live UI deletion uses the server's admission lock."""
        self.ensure()
        path=Path(__file__).resolve().parent/"contracts/raven_projects.py"
        spec=importlib.util.spec_from_file_location("raven_project_storage",path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return Path(module.trash_project(self.projects_dir,slug)["entry"])

    def trashed_projects(self) -> list[dict[str, Any]]:
        """Return only complete, validated trash entries."""
        self.ensure()
        trash_dir = self.root / "Trash"
        if trash_dir.exists() and trash_dir.is_symlink():
            raise ValueError("Trash kan ikke være en symlink")
        if not trash_dir.is_dir():
            return []
        self._assert_managed_path(trash_dir)
        result: list[dict[str, Any]] = []
        for entry_dir in trash_dir.iterdir():
            if entry_dir.is_symlink() or not entry_dir.is_dir() or not TRASH_ID_RE.fullmatch(entry_dir.name):
                continue
            try:
                self._assert_managed_path(entry_dir)
                metadata_path = entry_dir / "entry.json"
                project = entry_dir / "Project"
                if metadata_path.is_symlink() or not metadata_path.is_file() or project.is_symlink() or not project.is_dir():
                    continue
                self._assert_managed_path(metadata_path)
                self._assert_managed_path(project)
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if not isinstance(metadata, dict):
                    continue
                if metadata.get("id") != entry_dir.name or not isinstance(metadata.get("slug"), str):
                    continue
                _slug(metadata["slug"])
                if not isinstance(metadata.get("name"), str) or not metadata["name"].strip():
                    continue
                if not isinstance(metadata.get("deletedAt"), (int, float, str)):
                    continue
                result.append({"id": metadata["id"], "slug": metadata["slug"], "name": metadata["name"], "deletedAt": metadata["deletedAt"]})
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sorted(result, key=lambda item: item["deletedAt"], reverse=True)

    def restore_project(self, entry_id: str) -> str:
        """Restore through the shared filesystem contract; live UI uses the API lock."""
        self.ensure()
        path=Path(__file__).resolve().parent/"contracts/raven_projects.py"
        spec=importlib.util.spec_from_file_location("raven_project_storage",path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.restore_project(self.projects_dir,entry_id)


def new_shot(title: str = "Nytt klipp", scene: int = 1) -> dict[str, Any]:
    """Return the current Storyboard default shot shape with Raven defaults."""
    if isinstance(scene, bool) or not isinstance(scene, int) or scene < 1:
        raise ValueError("scene må være et positivt heltall")
    return {
        "id": f"s{uuid.uuid4().hex[:8]}", "title": title, "prompt": "", "start": "", "action": "", "end": "", "camera": "",
        "locationId": "", "stateBefore": "", "stateAfter": "", "motionIntent": "action", "propIds": [], "assetStates": {}, "promptVersions": [],
        "soundNote": "", "characterIds": [], "dialogue": "", "dialogueSource": "recording", "dialogueStyle": "", "dialogueVoice": "", "speakerId": "",
        "dialogueAudioUrl": None, "dubUrl": None, "dubMode": "mix", "startRef": None, "endRef": None, "referenceImages": [], "imageAnchors": [],
        "resolution": "384x256", "frames": 121, "steps": 8, "seed": 0, "status": "draft", "reason": "", "progress": 0,
        "runtimeSeconds": None, "outputs": [], "validation": None, "thumb": None, "logUrl": None, "renderedAs": None, "renderFingerprint": None,
        "dialogueSpokenText": "", "dialogueSpokenStyle": "", "locked": False, "directorScene": scene,
    }


def validate_timing(frames: int, continuation: bool = False) -> bool:
    if isinstance(frames, bool) or not isinstance(frames, int):
        return False
    if continuation:
        return 96 <= frames <= 144 and frames % 8 == 0
    return 97 <= frames <= 145 and (frames - 1) % 8 == 0

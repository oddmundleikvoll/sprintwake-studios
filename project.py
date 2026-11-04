"""Independent review documents. Source projects and media are never written."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parent
SHARED = ROOT.parent.parent / "ComfyUI-Shared/output/storyboard"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}


def media_path(value: object, base: Path, *, external: bool = False) -> str:
    if not isinstance(value, str) or not value:
        return ""
    value = value.removeprefix("/media/").removeprefix("projects/")
    p = Path(value)
    if ".." in p.parts:
        return ""
    if not p.is_absolute() and p.parts and p.parts[0] == base.name:
        p = Path(*p.parts[1:])
    result = (base / p).resolve()
    if not external and not result.is_relative_to(base.resolve()):
        return ""
    if result.suffix.lower() not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS or not result.is_file():
        return ""
    return str(result)


@dataclass
class Take:
    id: str
    label: str
    image: str = ""
    video: str = ""
    provenance: str = "Importert materiale"


@dataclass
class Shot:
    id: str
    title: str
    duration: float = 5.0
    camera: str = "Fast kamera · total"
    start: str = ""
    action: str = ""
    end: str = ""
    notes: str = ""
    reviewed: bool = False
    selected_take: int = 0
    takes: list[Take] = field(default_factory=list)
    source_start: float = 0.0

    @property
    def take(self) -> Take | None:
        return self.takes[self.selected_take] if self.takes else None


@dataclass
class Project:
    id: str
    title: str
    subtitle: str
    shots: list[Shot]
    source: str = ""
    character_image: str = ""
    character_name: str = ""
    character_description: str = ""

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.shots)

    def shot_at(self, seconds: float) -> tuple[int, float]:
        start = 0.0
        for i, shot in enumerate(self.shots):
            if seconds < start + shot.duration or i == len(self.shots) - 1:
                return i, max(0.0, min(shot.duration, seconds - start))
            start += shot.duration
        return 0, 0.0

    def validate(self) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", self.id):
            raise ValueError("Ugyldig prosjekt-ID.")
        if len(self.shots) > 500:
            raise ValueError("Prototypen støtter opptil 500 klipp.")
        if len({s.id for s in self.shots}) != len(self.shots):
            raise ValueError("Duplisert klipp-ID.")
        for s in self.shots:
            if isinstance(s.duration, bool) or not isinstance(s.duration, (int, float)) or not math.isfinite(s.duration) or not 1 / 24 <= s.duration <= 3600:
                raise ValueError("Klippvarighet må være mellom én ramme og én time.")
            if type(s.selected_take) is not int or not 0 <= s.selected_take < max(1, len(s.takes)):
                raise ValueError("Valgt take finnes ikke.")
            if isinstance(s.source_start, bool) or not isinstance(s.source_start, (int, float)) or not math.isfinite(s.source_start) or not 0 <= s.source_start <= 3600:
                raise ValueError("Opptakets innpunkt må være mellom null og én time.")
            if len({t.id for t in s.takes}) != len(s.takes):
                raise ValueError("Duplisert take-ID.")


def demos() -> list[Project]:
    image = lambda n: media_path(f"qwen-image-2.1-turbo_{n:05d}_.png", SHARED)
    takes = [Take(f"qwen-{n}", f"Bilde {i:02}", image(n), provenance="Bevart Qwen-bilde")
             for i, n in enumerate((7, 8, 9), 1)]
    shots = [
        Shot("01", "Et lite møte", start="Rødbert står ved solsikken.",
             action="Han oppdager blomsten og blir stående et øyeblikk.",
             end="Blikket er rettet mot solsikken.", takes=[Take(**asdict(t)) for t in takes]),
        Shot("02", "Tid til å se", start="Rødbert og blomsten deler bildet.",
             action="Et rolig øyeblikk gir plass til nysgjerrighet.",
             end="Rødbert står fortsatt ved potten.", selected_take=1,
             takes=[Take(**asdict(t)) for t in takes]),
        Shot("03", "Et lite hei", start="Rødbert står stødig ved solsikken.",
             action="Han løfter armen og hilser forsiktig.",
             end="Den røde hånden er løftet i en hilsen.", selected_take=2,
             takes=[Take(**asdict(t)) for t in takes]),
    ]
    film = Project("rodbert-studie", "Et lite hei", "En liten historie om å legge merke til noe.", shots,
                   character_image=image(6), character_name="Rødbert",
                   character_description="Rød trefigur · solsikke på brystet")
    video = media_path("probe_flat_dynamic_00001_.mp4", SHARED)
    probe = Project("hagestudie", "Hagestudie", "Bevart prøveklipp · video med original lyd",
                    [Shot("probe", "Robot i hagen", duration=7.291667,
                          camera="Bevart komposisjon", action="Se gjennom faktisk bevegelse og lyd.",
                          takes=[Take("probe-original", "Original", image=str(ROOT / "assets/probe-preview.png"), video=video,
                                      provenance="Bevart Storyboard-prøveklipp · separat fra Rødbert")])])
    return [film, probe]


def import_project(path: Path) -> Project:
    path = path.resolve()
    if path.is_dir():
        path /= "storyboard.json"
    if path.stat().st_size > 8_000_000:
        raise ValueError("Prosjektfilen er for stor for prototypen.")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Prosjektfilen må inneholde et JSON-objekt.")
    if data.get("format") == "storyboard-raven":
        if data.get("version") != 1:
            raise ValueError("Ukjent versjon av Raven-arbeidskopien.")
        return decode(data.get("project"), path.parent)
    if data.get("schema") != 1 or not isinstance(data.get("shots"), list):
        raise ValueError("Velg storyboard.json eller en Raven-arbeidskopi.")
    base = path.parent
    shots = []
    for i, item in enumerate(data["shots"]):
        if not isinstance(item, dict):
            raise ValueError("Et klipp må inneholde et JSON-objekt.")
        sid = str(item.get("id", f"{i + 1:02}"))
        take_image = ""
        stills = item.get("stills") or {}
        if not isinstance(stills, dict):
            stills = {}
        for ref in [item.get("startRef"), item.get("thumb"), item.get("startImage"), *stills.values()]:
            value = ref.get("path", "") if isinstance(ref, dict) else ref
            take_image = media_path(value, base)
            if take_image:
                break
        video = media_path(item.get("video"), base)
        for value in item.get("outputs") or []:
            if not video:
                candidate = media_path(value, base)
                if Path(candidate).suffix.lower() in VIDEO_EXTENSIONS:
                    video = candidate
        # Production Storyboard's canonical per-shot file, only inside this project.
        if not video:
            video = media_path(f"shots/{i + 1:02}/clip.mp4", base)
        frames = item.get("frames", item.get("numFrames", 121))
        duration = float(item.get("duration") or float(frames) / 24)
        takes = [Take("imported", "Original", take_image, video)] if take_image or video else []
        shots.append(Shot(sid, str(item.get("title") or f"Klipp {i + 1:02}"), duration,
                          camera=str(item.get("camera") or "Kamera ikke beskrevet"),
                          start=str(item.get("start") or item.get("startState") or ""),
                          action=str(item.get("action") or item.get("prompt") or ""),
                          end=str(item.get("end") or item.get("endState") or ""), takes=takes))
    project = Project("import-" + hashlib.sha256(str(path).encode()).hexdigest()[:16],
                      str(data.get("name") or data.get("title") or base.name),
                      "Arbeidskopi fra Storyboard", shots, source=str(path))
    cast = data.get("characters") or []
    if cast and isinstance(cast[0], dict):
        character = cast[0]
        ref = character.get("image") or ""
        project.character_image = media_path(ref.get("path", "") if isinstance(ref, dict) else ref, base)
        project.character_name = str(character.get("name") or "")
        project.character_description = str(character.get("description") or "")
    project.validate()
    return project


def decode(data: dict, base: Path) -> Project:
    if not isinstance(data, dict) or not isinstance(data.get("shots"), list):
        raise ValueError("Ugyldig Raven-prosjekt.")
    shots = []
    for item in data["shots"]:
        if not isinstance(item, dict) or not isinstance(item.get("takes", []), list):
            raise ValueError("Ugyldig klipp i Raven-arbeidskopien.")
        item = dict(item)
        if not all(isinstance(t, dict) for t in item.get("takes", [])):
            raise ValueError("Ugyldig take i Raven-arbeidskopien.")
        item["takes"] = [Take(**{**t, "image": media_path(t.get("image"), base, external=True),
                                    "video": media_path(t.get("video"), base, external=True)})
                         for t in item.get("takes", [])]
        shots.append(Shot(**item))
    project = Project(**{**data, "shots": shots})
    project.character_image = media_path(project.character_image, base, external=True)
    project.validate()
    return project


def load_working_copy(path: Path, workspace: Path = ROOT / "workspace") -> Project:
    """Opening a production source returns its existing independent review copy."""
    project = import_project(path)
    if project.source:
        saved = workspace / f"{project.id}.raven.json"
        if saved.is_file():
            working = import_project(saved)
            if working.source == project.source and working.id == project.id:
                return working
    return project


def save_project(project: Project, workspace: Path = ROOT / "workspace") -> Path:
    project.validate()
    if workspace.is_symlink():
        raise ValueError("Arbeidsmappen kan ikke være en symbolsk lenke.")
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / f"{project.id}.raven.json"
    if target.is_symlink():
        raise ValueError("Arbeidskopien kan ikke være en symbolsk lenke.")
    data = {"format": "storyboard-raven", "version": 1, "project": asdict(project)}
    fd, temporary = tempfile.mkstemp(prefix=".save-", dir=workspace)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target

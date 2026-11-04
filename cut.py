"""Frame-based editorial decisions, independent of the production document.

No model calls or canonical board writes. Every edit is one atomic, revision
guarded journal update; undo/redo only replace this journal's clip list.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path

from project import Project, Shot, Take
from workspace import Workspace, _atomic_text

FPS = 24
HISTORY_LIMIT = 50
MAX_CLIPS = 500


def frames(seconds: float) -> int:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        raise ValueError("Tid må være et endelig tall.")
    return round(seconds * FPS)


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 86400 * FPS:
        raise ValueError("Klippet har ugyldige rammetall.")
    return value


def validate_clips(clips):
    if not isinstance(clips, list) or len(clips) > MAX_CLIPS:
        raise ValueError("Klippelaget støtter opptil 500 klipp.")
    ids = set()
    for clip in clips:
        if not isinstance(clip, dict) or set(clip) != {"shotId", "source", "inFrame", "outFrame", "sourceFrames", "included"}:
            raise ValueError("Ugyldig klippelag.")
        sid = clip["shotId"]
        if not isinstance(sid, str) or not sid or sid in ids:
            raise ValueError("Klippelaget har ugyldige eller dupliserte klipp-ID-er.")
        ids.add(sid)
        start, end, total = (_integer(clip[k]) for k in ("inFrame", "outFrame", "sourceFrames"))
        if not 0 <= start < end <= total or type(clip["included"]) is not bool:
            raise ValueError("Inn og ut må avgrense minst én ramme i opptaket.")
        source = clip["source"]
        if source is not None:
            if not isinstance(source, dict) or set(source) != {"id", "label", "video", "image"}:
                raise ValueError("Ugyldig opptak i klippelaget.")
            if any(not isinstance(source[k], str) for k in source):
                raise ValueError("Ugyldig opptaksreferanse.")
            for key in ("video", "image"):
                if source[key]:
                    path = Path(source[key])
                    if path.is_absolute() or ".." in path.parts or path.parts[0] != "Input":
                        raise ValueError("Klippelaget kan bare bruke bevarte medier i Input.")
    return clips


def new_clip(shot):
    total = max(1, frames(shot.duration))
    return {"shotId": shot.id, "source": None, "inFrame": 0, "outFrame": total,
            "sourceFrames": total, "included": True}


def reconcile(clips, project):
    """Keep editorial choices and removed-shot media; append new plan cards."""
    result = copy.deepcopy(clips)
    known = {c["shotId"] for c in result}
    result.extend(new_clip(s) for s in project.shots if s.id not in known)
    return result


def visible_clips(clips):
    return [c for c in clips if c["included"]]


def trim(clips, shot_id, start, end):
    result = copy.deepcopy(clips)
    clip = next(c for c in result if c["shotId"] == shot_id)
    clip.update(inFrame=_integer(start), outFrame=_integer(end))
    validate_clips(result)
    return result


def choose_take(clips, shot_id, source, source_frames):
    result = copy.deepcopy(clips)
    clip = next(c for c in result if c["shotId"] == shot_id)
    clip.update(source=copy.deepcopy(source), sourceFrames=_integer(source_frames, 1),
                inFrame=0, outFrame=source_frames)
    validate_clips(result)
    return result


def move(clips, shot_id, delta):
    result = copy.deepcopy(clips)
    visible = visible_clips(result)
    index = next(i for i, c in enumerate(visible) if c["shotId"] == shot_id)
    target = max(0, min(len(visible) - 1, index + delta))
    a, b = result.index(visible[index]), result.index(visible[target])
    clip = result.pop(a)
    result.insert(b, clip)
    return result


def gesture(clips, changes):
    """Convert one native gesture into ripple trims or insert-style reorder.

    Changes map shot IDs to (timeline start, duration) in frames. The widget
    has free movement; our sequence always closes gaps and keeps sound linked.
    """
    result = copy.deepcopy(clips)
    visible = visible_clips(result)
    positions, cursor = {}, 0
    for clip in visible:
        positions[clip["shotId"]] = cursor
        cursor += clip["outFrame"] - clip["inFrame"]
    moved = {}
    for clip in visible:
        sid = clip["shotId"]
        if sid not in changes:
            continue
        start, length = changes[sid]
        _integer(start); _integer(length, 1)
        old_start = positions[sid]
        old_length = clip["outFrame"] - clip["inFrame"]
        if length != old_length:
            # A left handle moves start while preserving timeline end.
            if start != old_start and start + length == old_start + old_length:
                clip["inFrame"] = max(0, min(clip["outFrame"] - 1, clip["inFrame"] + start - old_start))
            else:
                clip["outFrame"] = max(clip["inFrame"] + 1, min(clip["sourceFrames"], clip["inFrame"] + length))
        elif start != old_start:
            moved[sid] = start
    if moved:
        # Compare a moved clip's leading edge to the other clips' midpoints.
        # Thus dragging B onto C inserts B after C without leaving a gap.
        stationary = [c for c in visible if c["shotId"] not in moved]
        for clip in sorted((c for c in visible if c["shotId"] in moved), key=lambda c: moved[c["shotId"]]):
            at = next((i for i, other in enumerate(stationary)
                       if moved[clip["shotId"]] < positions[other["shotId"]] + (other["outFrame"] - other["inFrame"]) / 2), len(stationary))
            stationary.insert(at, clip)
        iterator = iter(stationary)
        result = [next(iterator) if c["included"] else c for c in result]
    validate_clips(result)
    return result


class CutStore:
    def __init__(self, workspace: Workspace, slug: str):
        self.workspace, self.slug = workspace, slug
        self.project = workspace.ensure_project(slug)
        self.path = self.project / "Script" / "cut.json"
        self.lock_path = self.project / "System" / "cut.lock"

    @contextmanager
    def locked(self):
        self.workspace._assert_managed_path(self.path)
        self.workspace._assert_managed_path(self.lock_path)
        with self.lock_path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def load(self):
        self.workspace._assert_managed_path(self.path)
        if not self.path.exists():
            return {"format": "raven-cut", "version": 1, "fps": FPS, "revision": 0,
                    "clips": [], "undo": [], "redo": []}
        if self.path.stat().st_size > 16_000_000:
            raise ValueError("Klippelaget er for stort.")
        doc = json.loads(self.path.read_text())
        if not isinstance(doc, dict) or doc.get("format") != "raven-cut" or doc.get("version") != 1 or doc.get("fps") != FPS:
            raise ValueError("Ukjent klippelagformat.")
        _integer(doc.get("revision"))
        validate_clips(doc.get("clips"))
        for key in ("undo", "redo"):
            if not isinstance(doc.get(key), list) or len(doc[key]) > HISTORY_LIMIT:
                raise ValueError("Ugyldig angrehistorikk.")
            for item in doc[key]:
                if not isinstance(item, dict) or set(item) != {"label", "clips"} or not isinstance(item["label"], str):
                    raise ValueError("Ugyldig angrehistorikk.")
                validate_clips(item["clips"])
        return doc

    def _write(self, doc):
        _atomic_text(self.path, json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        return doc

    def initialize(self, project):
        with self.locked():
            doc = self.load()
            clips = reconcile(doc["clips"], project)
            if clips != doc["clips"]:
                doc["clips"] = clips
                doc["revision"] += 1
                # New script cards are not an editorial action. Keep them in
                # previous states too, so undo cannot lose a newly added card.
                for state in doc["undo"] + doc["redo"]:
                    state["clips"] = reconcile(state["clips"], project)
                self._write(doc)
            return doc

    def commit(self, doc, clips, label, *, remember=True):
        validate_clips(clips)
        with self.locked():
            actual = self.load()
            if actual["revision"] != doc["revision"]:
                raise ValueError("Klippelaget er endret i et annet vindu. Åpne Klipp på nytt.")
            if clips == actual["clips"]:
                return actual
            if remember:
                actual["undo"] = (actual["undo"] + [{"label": label, "clips": actual["clips"]}])[-HISTORY_LIMIT:]
            actual.update(clips=copy.deepcopy(clips), redo=[], revision=actual["revision"] + 1)
            return self._write(actual)

    def history(self, doc, redo=False):
        with self.locked():
            actual = self.load()
            if actual["revision"] != doc["revision"]:
                raise ValueError("Klippelaget er endret i et annet vindu. Åpne Klipp på nytt.")
            source, dest = ("redo", "undo") if redo else ("undo", "redo")
            if not actual[source]:
                return actual
            state = actual[source].pop()
            actual[dest] = (actual[dest] + [{"label": state["label"], "clips": actual["clips"]}])[-HISTORY_LIMIT:]
            actual.update(clips=state["clips"], revision=actual["revision"] + 1)
            return self._write(actual)

    def view(self, doc, production):
        by_id = {s.id: s for s in production.shots}
        shots = []
        for clip in visible_clips(doc["clips"]):
            shot = copy.deepcopy(by_id.get(clip["shotId"], Shot(clip["shotId"], "Bevart klipp fra tidligere manus")))
            source = clip["source"]
            if source:
                def resolve(key):
                    path = self.project / source[key]
                    self.workspace._assert_managed_path(path)
                    return str(path) if source[key] and path.is_file() else ""
                shot.takes = [Take(source["id"], source["label"], resolve("image"), resolve("video"), "Bevart opptak · klippelag")]
                shot.selected_take = 0
            else:
                # An unbound card is a still placeholder, never an implicit
                # "latest take" which a render could silently replace.
                shot.takes = [Take("placeholder", "Bildeplan", image=shot.take.image, provenance="Bildeplan · video ikke valgt")] if shot.take and shot.take.image else []
                shot.selected_take = 0
            shot.duration = (clip["outFrame"] - clip["inFrame"]) / FPS
            shot.source_start = clip["inFrame"] / FPS if source and source["video"] else 0
            shots.append(shot)
        return Project(production.id, production.title, "Klippelag · bevarte opptak og bildeplan", shots)


def scene_groups(board):
    """Camera groups are not narrative acts; preserve contiguous plan order."""
    locations = {x.get("id"): x.get("title") or x.get("name") or "Sted" for x in (board.get("filmBible") or {}).get("locations", [])}
    result = []
    for index, shot in enumerate(board.get("shots") or []):
        key = shot.get("directorScene", 1)
        if not result or result[-1]["scene"] != key:
            result.append({"scene": key, "location": locations.get(shot.get("locationId"), "Sted ikke valgt"), "shots": []})
        result[-1]["shots"].append((index, shot))
    return result

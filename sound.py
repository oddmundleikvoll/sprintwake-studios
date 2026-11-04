"""Reversible, frame-based sound editorial journal for Raven."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path

from workspace import Workspace, _atomic_text

FPS = 24
HISTORY_LIMIT = 50
MAX_CLIPS = 1000
TRACKS = (
    ("dialogue", "Dialogue"),
    ("music", "Music"),
    ("ambience", "Ambience"),
    ("effects", "Effects"),
)
TRACK_IDS = {item[0] for item in TRACKS}


def _number(value, label="gainDb"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid {label}.")
    return float(value)


def _frame(value, minimum=0):
    if type(value) is not int or value < minimum or value > 86400 * FPS:
        raise ValueError("Invalid audio frame.")
    return value


def _path(value):
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts or not value.startswith("Input/"):
        raise ValueError("Audio can only use preserved media in Input.")
    return value


def default_state():
    return {"tracks": [{"id": ident, "name": name, "gainDb": 0.0, "muted": False, "solo": False} for ident, name in TRACKS], "clips": [], "original": {"enabled": True, "gainDb": 0.0}}


def validate_state(state):
    if not isinstance(state, dict) or set(state) != {"tracks", "clips", "original"}:
        raise ValueError("Invalid sound layer.")
    tracks = state["tracks"]
    if not isinstance(tracks, list) or len(tracks) != len(TRACKS):
        raise ValueError("Sound layer must have four fixed tracks.")
    seen = set()
    for track, (expected, default_name) in zip(tracks, TRACKS):
        if not isinstance(track, dict) or set(track) != {"id", "name", "gainDb", "muted", "solo"}:
            raise ValueError("Invalid audio track.")
        if track["id"] != expected or track["id"] in seen or not isinstance(track["name"], str) or not track["name"]:
            raise ValueError("Invalid fixed audio tracks.")
        if _number(track["gainDb"]) < -120 or _number(track["gainDb"]) > 24 or type(track["muted"]) is not bool or type(track["solo"]) is not bool:
            raise ValueError("Invalid audio track gain.")
        seen.add(track["id"])
    clips = state["clips"]
    if not isinstance(clips, list) or len(clips) > MAX_CLIPS:
        raise ValueError("Sound layer supports up to 1,000 clips.")
    ids = set()
    keys = {"id", "track", "label", "path", "sourceFrames", "inFrame", "outFrame", "atFrame", "gainDb", "fadeIn", "fadeOut", "muted"}
    for clip in clips:
        if not isinstance(clip, dict) or set(clip) != keys or not isinstance(clip["id"], str) or not clip["id"] or clip["id"] in ids:
            raise ValueError("Invalid or duplicate audio clips.")
        if clip["track"] not in TRACK_IDS or not isinstance(clip["label"], str):
            raise ValueError("Invalid audio clip.")
        _path(clip["path"])
        total = _frame(clip["sourceFrames"], 1)
        start, end = _frame(clip["inFrame"]), _frame(clip["outFrame"])
        if not start < end <= total or _frame(clip["atFrame"]) < 0:
            raise ValueError("Invalid audio clip trim or placement.")
        duration = end - start
        if _frame(clip["fadeIn"]) > duration or _frame(clip["fadeOut"]) > duration or type(clip["muted"]) is not bool:
            raise ValueError("Invalid audio clip fade.")
        if _number(clip["gainDb"]) < -120 or _number(clip["gainDb"]) > 24:
            raise ValueError("Invalid audio clip gain.")
        ids.add(clip["id"])
    original = state["original"]
    if not isinstance(original, dict) or set(original) != {"enabled", "gainDb"} or type(original["enabled"]) is not bool or _number(original["gainDb"]) < -120 or _number(original["gainDb"]) > 24:
        raise ValueError("Invalid original audio setting.")
    return state


def update_clip(state, clip_id, **fields):
    result = copy.deepcopy(state)
    allowed = {"track", "label", "path", "sourceFrames", "inFrame", "outFrame", "atFrame", "gainDb", "fadeIn", "fadeOut", "muted"}
    if not fields.keys() <= allowed:
        raise ValueError("Unknown audio clip field.")
    clip = next((item for item in result["clips"] if item["id"] == clip_id), None)
    if clip is None:
        raise KeyError(clip_id)
    clip.update(fields)
    validate_state(result)
    return result


def remove_clip(state, clip_id):
    result = copy.deepcopy(state)
    result["clips"] = [clip for clip in result["clips"] if clip["id"] != clip_id]
    validate_state(result)
    return result


def effective_clips(state):
    validate_state(state)
    solo = {track["id"] for track in state["tracks"] if track["solo"]}
    muted = {track["id"] for track in state["tracks"] if track["muted"]}
    return [clip for clip in state["clips"] if not clip["muted"] and clip["track"] not in muted and (not solo or clip["track"] in solo)]


class SoundStore:
    def __init__(self, workspace: Workspace, slug: str):
        self.workspace, self.slug = workspace, slug
        self.project = workspace.ensure_project(slug)
        self.path = self.project / "Script" / "sound.json"
        self.lock_path = self.project / "System" / "sound.lock"

    @contextmanager
    def locked(self):
        self.workspace._assert_managed_path(self.path)
        self.workspace._assert_managed_path(self.lock_path)
        with self.lock_path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def _default(self):
        return {"format": "raven-sound", "version": 1, "fps": FPS, "revision": 0, "state": default_state(), "undo": [], "redo": []}

    def _validate_doc(self, doc):
        if not isinstance(doc, dict) or set(doc) != {"format", "version", "fps", "revision", "state", "undo", "redo"} or doc["format"] != "raven-sound" or doc["version"] != 1 or doc["fps"] != FPS or type(doc["revision"]) is not int or doc["revision"] < 0:
            raise ValueError("Unknown sound state format.")
        validate_state(doc["state"])
        for key in ("undo", "redo"):
            if not isinstance(doc[key], list) or len(doc[key]) > HISTORY_LIMIT:
                raise ValueError("Invalid sound history.")
            for item in doc[key]:
                if not isinstance(item, dict) or set(item) != {"label", "state"} or not isinstance(item["label"], str):
                    raise ValueError("Invalid sound history.")
                validate_state(item["state"])
        return doc

    def load(self):
        self.workspace._assert_managed_path(self.path)
        if not self.path.exists():
            return self._default()
        if self.path.stat().st_size > 32_000_000:
            raise ValueError("Sound state is too large.")
        return self._validate_doc(json.loads(self.path.read_text(encoding="utf-8")))

    def _write(self, doc):
        self._validate_doc(doc)
        body=json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if len(body.encode())>32_000_000: raise ValueError("Sound layer and history are too large to save.")
        return _atomic_text(self.path, body) or doc

    def commit(self, doc, state, label):
        validate_state(state)
        if not isinstance(label, str) or not label:
            raise ValueError("Sound change must have a label.")
        with self.locked():
            actual = self.load()
            if actual["revision"] != doc["revision"]:
                raise ValueError("Sound layer changed in another window. Reopen Sound.")
            if state == actual["state"]:
                return actual
            actual["undo"] = (actual["undo"] + [{"label": label, "state": copy.deepcopy(actual["state"])}])[-HISTORY_LIMIT:]
            actual.update(state=copy.deepcopy(state), redo=[], revision=actual["revision"] + 1)
            return self._write(actual)

    def history(self, doc, redo=False):
        with self.locked():
            actual = self.load()
            if actual["revision"] != doc["revision"]:
                raise ValueError("Sound layer changed in another window. Reopen Sound.")
            source, target = (("redo", "undo") if redo else ("undo", "redo"))
            if not actual[source]:
                return actual
            item = actual[source].pop()
            actual[target].append({"label": item["label"], "state": copy.deepcopy(actual["state"])})
            actual["state"] = copy.deepcopy(item["state"])
            actual["revision"] += 1
            return self._write(actual)

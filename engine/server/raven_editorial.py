"""Bounded, read-only semantic projection of native v1 editorial journals.

No UI/runtime imports, reconciliation, writes, media paths or undo history.
This reports saved decisions, never media quality or human approval.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
MAX_FILE_BYTES = 16_000_000
MAX_SECTION_BYTES = 48_000
_DAY = 86400 * 24
_TRACKS = ("dialogue", "music", "ambience", "effects")


def _require(condition):
    if not condition:
        raise ValueError("invalid editorial state")


def _text(value):
    _require(isinstance(value, str) and len(value) <= 1000)
    return value


def _frame(value):
    _require(type(value) is int and 0 <= value <= _DAY)
    return value


def _short(value):
    _require(isinstance(value, str) and len(value) <= 300)
    return value


def _title(value):
    _short(value)
    _require(bool(value.strip()))
    return value


def _nonempty(value):
    _text(value)
    _require(bool(value))
    return value


def _number(value, low=-_DAY, high=_DAY):
    _require(type(value) in (int, float) and low <= value <= high and math.isfinite(value))
    return value


def _bool(value):
    _require(type(value) is bool)
    return value


def _rows(value, maximum, ident="id"):
    _require(isinstance(value, list) and len(value) <= maximum)
    seen = set()
    for row in value:
        _require(isinstance(row, dict))
        if ident:
            key = _text(row[ident])
            _require(key and key not in seen)
            seen.add(key)
    return value


def _pick(row, fields, validator):
    return {key: validator(row[key]) for key in fields.split()}


def _keys(row, keys):
    _require(isinstance(row, dict) and set(row) == set(keys.split()))


def _input(value, empty=False):
    _require(isinstance(value, str))
    if empty and not value:
        return
    path = Path(value)
    _require(bool(value) and not path.is_absolute() and ".." not in path.parts and path.parts[0] == "Input")


def _trim(row):
    out = _pick(row, "sourceFrames inFrame outFrame", _frame)
    _require(out["inFrame"] < out["outFrame"] <= out["sourceFrames"])
    return out


def _cut(doc):
    _require(type(doc.get("fps")) is int and doc["fps"] == 24)
    clips = []
    for row in _rows(doc["clips"], 500, "shotId"):
        _keys(row, "shotId source inFrame outFrame sourceFrames included")
        clip = {"shotId": _text(row["shotId"]), **_trim(row), "included": _bool(row["included"])}
        source = row["source"]
        if source is not None:
            _keys(source, "id label video image")
            _input(source["video"], empty=True); _input(source["image"], empty=True)
            clip.update(takeId=_text(source["id"]), takeLabel=_text(source["label"]))
        clips.append(clip)
    return {"fps": 24, "clips": clips, "clipCount": len(clips),
            "runtimeFrames": sum(c["outFrame"] - c["inFrame"] for c in clips if c["included"])}


def _coverage(doc):
    _require(isinstance(doc["plans"], dict) and len(doc["plans"]) <= 30)
    plans = []
    for ident, row in doc["plans"].items():
        _keys(row, "title locationId width depth height aspect axis side actors cameras")
        _require(isinstance(ident, str) and 0 < len(ident) <= 100)
        plan = {"id": ident, **_pick(row, "title locationId", _short),
                "width": _number(row["width"], 2, 40), "depth": _number(row["depth"], 2, 40),
                "height": _number(row["height"], 2, 8), "aspect": _number(row["aspect"], .5, 2)}
        _require(type(row["side"]) is int and row["side"] in (-1, 1))
        plan["side"] = row["side"]
        actors = []
        for actor in _rows(row["actors"], 8):
            _keys(actor, "id characterId name x y height lookAt")
            actors.append({**_pick(actor, "id characterId name lookAt", _short),
                           "x": _number(actor["x"], .1, plan["width"] - .1),
                           "y": _number(actor["y"], .1, plan["depth"] - .1),
                           "height": _number(actor["height"], .5, min(2.5, plan["height"]))})
        actor_ids = {a["id"] for a in actors}
        _require(all(not a["lookAt"] or a["lookAt"] in actor_ids - {a["id"]} for a in actors))
        axis = row["axis"]
        _require(isinstance(axis, list) and len(axis) <= 2 and all(isinstance(a, str) and a in actor_ids for a in axis))
        _require(len(set(axis)) == len(axis) and (len(actors) < 2 or len(axis) == 2))
        cameras = []
        for camera in _rows(row["cameras"], 12):
            _keys(camera, "id name kind x y z lens aimIds aim selected")
            aim_ids = camera["aimIds"]
            _require(isinstance(aim_ids, list) and len(aim_ids) <= 8 and all(isinstance(a, str) and a in actor_ids for a in aim_ids))
            _require(len(set(aim_ids)) == len(aim_ids))
            aim = camera["aim"]
            _require(isinstance(aim, list) and len(aim) == 3)
            _require(all(isinstance(camera[k], str) and 0 < len(camera[k]) <= 300 for k in ("id", "name", "kind")))
            cameras.append({**_pick(camera, "id name kind", _short),
                            "x": _number(camera["x"], .1, plan["width"] - .1),
                            "y": _number(camera["y"], .1, plan["depth"] - .1),
                            "z": _number(camera["z"], .3, plan["height"] - .1),
                            "lens": _number(camera["lens"], 18, 135), "aimIds": aim_ids,
                            "aim": [_number(v, 0, high) for v, high in zip(aim, (plan["width"], plan["depth"], plan["height"]))],
                            "selected": _bool(camera["selected"])})
        plans.append({**plan, "axis": axis, "actors": actors, "cameras": cameras})
    return {"plans": plans, "planCount": len(plans)}


def _structure(doc):
    state = doc["state"]
    _keys(state, "targetFrames sequences")
    sequences, group_ids, shot_ids = [], set(), set()
    for row in _rows(state["sequences"], 500):
        _keys(row, "id title targetFrames scenes")
        sequence = {"id": _text(row["id"]), "title": _title(row["title"]), "targetFrames": _frame(row["targetFrames"]), "scenes": []}
        _require(sequence["id"] not in group_ids)
        group_ids.add(sequence["id"])
        for scene in _rows(row["scenes"], 500):
            _keys(scene, "id title targetFrames shotIds")
            ids = scene["shotIds"]
            _require(isinstance(ids, list) and len(ids) <= 5000)
            _require(all(isinstance(sid, str) and 0 < len(sid) <= 1000 for sid in ids))
            _require(len(set(ids)) == len(ids) and not shot_ids.intersection(ids))
            shot_ids.update(ids)
            item = {"id": _text(scene["id"]), "title": _title(scene["title"]), "targetFrames": _frame(scene["targetFrames"]), "shotIds": ids}
            _require(item["id"] not in group_ids)
            group_ids.add(item["id"])
            sequence["scenes"].append(item)
        sequences.append(sequence)
    return {"targetFrames": _frame(state["targetFrames"]), "sequences": sequences,
            "sequenceCount": len(sequences), "sceneCount": sum(len(s["scenes"]) for s in sequences)}


def _sound(doc):
    state = doc["state"]
    _keys(state, "tracks clips original")
    _require(type(doc.get("fps")) is int and doc["fps"] == 24)
    tracks = []
    for row in _rows(state["tracks"], 4):
        _keys(row, "id name gainDb muted solo")
        tracks.append({"id": _text(row["id"]), "name": _nonempty(row["name"]), "gainDb": _number(row["gainDb"], -120, 24),
                       **_pick(row, "muted solo", _bool)})
    _require(tuple(t["id"] for t in tracks) == _TRACKS)
    clips = []
    for row in _rows(state["clips"], 1000):
        _keys(row, "id track label path sourceFrames inFrame outFrame atFrame gainDb fadeIn fadeOut muted")
        _input(row["path"])
        clip = {**_pick(row, "id track label", _text), **_trim(row),
                **_pick(row, "atFrame fadeIn fadeOut", _frame), "gainDb": _number(row["gainDb"], -120, 24),
                "muted": _bool(row["muted"])}
        _require(clip["track"] in _TRACKS and max(clip["fadeIn"], clip["fadeOut"]) <= clip["outFrame"] - clip["inFrame"])
        clips.append(clip)
    original = state["original"]
    _keys(original, "enabled gainDb")
    return {"fps": 24, "sampleRate": 48000, "tracks": tracks, "clips": clips, "clipCount": len(clips),
            "original": {"enabled": _bool(original["enabled"]), "gainDb": _number(original["gainDb"], -120, 24)}}


_PROJECTORS = {"cut": _cut, "coverage": _coverage, "structure": _structure, "sound": _sound}


def _validate_history(doc, section):
    if section == "sound":
        _keys(doc, "format version fps revision state undo redo")
    if section == "structure":
        _require(set(doc) <= set("format version revision state undo redo knownShotIds".split()))
        known = doc.get("knownShotIds", [])
        _require(isinstance(known, list) and all(isinstance(k, str) and k for k in known) and len(set(known)) == len(known))
    for key in ("undo", "redo"):
        history = doc[key]
        _require(isinstance(history, list) and len(history) <= (30 if section == "coverage" else 50))
        for entry in history:
            if section == "coverage":
                _coverage({"plans": entry})
            else:
                field = "clips" if section == "cut" else "state"
                _keys(entry, "label " + field)
                (_text if section == "cut" else _short)(entry["label"])
                _PROJECTORS[section]({field: entry[field], "fps": 24})


def _load(root, slug, section):
    try:
        _require(isinstance(slug, str) and _SLUG.fullmatch(slug))
        project = root / slug
        path = project / "Script" / (section + ".json")
        _require(not project.is_symlink() and not (project / "Script").is_symlink())
        _require(path.resolve().is_relative_to(project.resolve()))
        if not path.exists():
            return {"status": "absent", "revision": 0}
        _require(path.is_file())
        limit = min(MAX_FILE_BYTES, 8_000_000 if section == "coverage" else MAX_FILE_BYTES)
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            return {"status": "truncated", "reason": "file_limit"}
        doc = json.loads(raw)
        _require(isinstance(doc, dict) and doc.get("format") == "raven-" + section)
        if type(doc.get("version")) is not int or doc["version"] != 1:
            return {"status": "unsupported"}
        _require(type(doc.get("revision")) is int and doc["revision"] >= 0)
        _validate_history(doc, section)
        data = _PROJECTORS[section](doc)
        result = {"status": "ok", "revision": doc["revision"], **data}
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_SECTION_BYTES:
            return {"status": "truncated", "revision": doc["revision"], "reason": "context_limit",
                    **{k: v for k, v in data.items() if k.endswith("Count") or k in {"runtimeFrames", "targetFrames"}}}
        return result
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, RuntimeError):
        return {"status": "invalid", "reason": "unreadable_or_invalid"}


def editorial_context(projects_dir, slug, board=None):
    """Read a bounded snapshot; never initialise/reconcile native journals."""
    root = Path(projects_dir).resolve()
    return {"projectSlug": slug, "editorialVersion": 1,
            **{name: _load(root, slug, name) for name in _PROJECTORS}}

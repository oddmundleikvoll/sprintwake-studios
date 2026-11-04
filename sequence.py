"""Narrative sequence and scene structure, independent of picture editing.

This layer stores editorial grouping only.  It never changes the canonical
board or the cut's trim/take data; ``order_cut`` is an explicit projection.
"""
from __future__ import annotations

import copy
import fcntl
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

from workspace import Workspace, _atomic_text

HISTORY_LIMIT = 50
MAX_GROUPS = 500
MAX_SHOTS = 5000


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _frames(value, *, name="frame count"):
    if type(value) is not int or value < 0 or value > 86400 * 24:
        raise ValueError(f"Invalid {name}.")
    return value


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise ValueError("Invalid group title.")
    return value.strip()


def validate_state(state):
    if not isinstance(state, dict) or set(state) != {"targetFrames", "sequences"}:
        raise ValueError("Invalid sequence structure.")
    _frames(state["targetFrames"], name="target frames")
    sequences = state["sequences"]
    if not isinstance(sequences, list) or len(sequences) > MAX_GROUPS:
        raise ValueError("Too many sequences.")
    ids, shots = set(), set()
    for sequence in sequences:
        if not isinstance(sequence, dict) or set(sequence) != {"id", "title", "targetFrames", "scenes"}:
            raise ValueError("Invalid sequence.")
        if not isinstance(sequence["id"], str) or not sequence["id"] or sequence["id"] in ids:
            raise ValueError("Duplicate or invalid sequence ID.")
        ids.add(sequence["id"])
        _title(sequence["title"]); _frames(sequence["targetFrames"], name="target frames")
        scenes = sequence["scenes"]
        if not isinstance(scenes, list) or len(scenes) > MAX_GROUPS:
            raise ValueError("Too many scenes.")
        for scene in scenes:
            if not isinstance(scene, dict) or set(scene) != {"id", "title", "targetFrames", "shotIds"}:
                raise ValueError("Invalid scene.")
            if not isinstance(scene["id"], str) or not scene["id"] or scene["id"] in ids:
                raise ValueError("Duplicate or invalid scene ID.")
            ids.add(scene["id"])
            _title(scene["title"]); _frames(scene["targetFrames"], name="target frames")
            shot_ids = scene["shotIds"]
            if not isinstance(shot_ids, list) or len(shot_ids) > MAX_SHOTS:
                raise ValueError("Too many shots in scene.")
            for shot_id in shot_ids:
                if not isinstance(shot_id, str) or not shot_id or shot_id in shots:
                    raise ValueError("A shot can only be assigned to one scene.")
                shots.add(shot_id)
    return state


def _empty_state():
    return {"targetFrames": 0, "sequences": []}


def _find(state, kind, ident):
    if kind == "sequence":
        for sequence in state["sequences"]:
            if sequence["id"] == ident:
                return sequence, None
    elif kind == "scene":
        for sequence in state["sequences"]:
            for scene in sequence["scenes"]:
                if scene["id"] == ident:
                    return scene, sequence
    raise ValueError("Group not found.")


def add_sequence(state, title="New sequence"):
    validate_state(state); result = copy.deepcopy(state)
    result["sequences"].append({"id": _id("sequence"), "title": _title(title), "targetFrames": 0, "scenes": []})
    validate_state(result); return result


def add_scene(state, sequence_id, title="New scene"):
    validate_state(state); result = copy.deepcopy(state)
    sequence, _ = _find(result, "sequence", sequence_id)
    sequence["scenes"].append({"id": _id("scene"), "title": _title(title), "targetFrames": 0, "shotIds": []})
    validate_state(result); return result


def edit_group(state, kind, ident, *, title=None, targetFrames=None):
    validate_state(state); result = copy.deepcopy(state)
    if kind == "film":
        if title is not None: raise ValueError("The film has no title here.")
        if targetFrames is not None: result["targetFrames"] = _frames(targetFrames, name="target frames")
    else:
        group, _ = _find(result, kind, ident)
        if title is not None: group["title"] = _title(title)
        if targetFrames is not None: group["targetFrames"] = _frames(targetFrames, name="target frames")
    validate_state(result); return result


def assign_shots(state, scene_id, shot_ids):
    validate_state(state)
    if not isinstance(shot_ids, list) or any(not isinstance(s, str) or not s for s in shot_ids):
        raise ValueError("Invalid shot IDs.")
    result = copy.deepcopy(state); target, _ = _find(result, "scene", scene_id)
    wanted, seen = [], set()
    for shot_id in shot_ids:
        if shot_id not in seen: wanted.append(shot_id); seen.add(shot_id)
    for sequence in result["sequences"]:
        for scene in sequence["scenes"]:
            scene["shotIds"] = [s for s in scene["shotIds"] if s not in seen]
    target["shotIds"].extend(wanted)
    validate_state(result); return result


def move_group(state, kind, ident, delta):
    validate_state(state)
    if type(delta) is not int: raise ValueError("Invalid move.")
    result = copy.deepcopy(state)
    if kind == "sequence":
        groups, index = result["sequences"], next((i for i, x in enumerate(result["sequences"]) if x["id"] == ident), None)
    elif kind == "scene":
        owner = _find(result, "scene", ident)[1]; groups, index = owner["scenes"], next(i for i, x in enumerate(owner["scenes"]) if x["id"] == ident)
    else: raise ValueError("Only sequences or scenes can be moved.")
    if index is None: raise ValueError("Group not found.")
    item = groups.pop(index); groups.insert(max(0, min(len(groups), index + delta)), item)
    return result


def remove_group(state, kind, ident):
    validate_state(state); result = copy.deepcopy(state)
    if kind == "sequence":
        result["sequences"] = [s for s in result["sequences"] if s["id"] != ident]
    elif kind == "scene":
        owner = _find(result, "scene", ident)[1]
        owner["scenes"] = [s for s in owner["scenes"] if s["id"] != ident]
    else: raise ValueError("Film cannot be deleted.")
    validate_state(result); return result


def _clip_map(cut_clips):
    if not isinstance(cut_clips, list): raise ValueError("Invalid cut layer.")
    result = {}
    for clip in cut_clips:
        if not isinstance(clip, dict) or not isinstance(clip.get("shotId"), str): raise ValueError("Invalid cut layer.")
        if clip["shotId"] in result: raise ValueError("Duplicate shot ID.")
        result[clip["shotId"]] = clip
    return result


def timing(state, cut_clips):
    validate_state(state); by_id = _clip_map(cut_clips)
    included = {sid: max(0, c.get("outFrame", 0) - c.get("inFrame", 0)) for sid, c in by_id.items() if c.get("included") is True}
    assigned_order = [sid for s in state["sequences"] for g in s["scenes"] for sid in g["shotIds"]]
    assigned = set(assigned_order)
    sequences, total = [], 0
    for sequence in state["sequences"]:
        scenes, seq_frames = [], 0
        for scene in sequence["scenes"]:
            frames = sum(included.get(sid, 0) for sid in scene["shotIds"])
            seq_frames += frames
            scenes.append({"id": scene["id"], "title": scene["title"], "frames": frames, "targetFrames": scene["targetFrames"], "shotIds": list(scene["shotIds"])})
        total += seq_frames
        sequences.append({"id": sequence["id"], "title": sequence["title"], "frames": seq_frames, "targetFrames": sequence["targetFrames"], "scenes": scenes})
    return {"frames": sum(included.values()), "targetFrames": state["targetFrames"], "actualShotCount": len(included), "sequences": sequences, "unassigned": [sid for sid in by_id if sid not in assigned and by_id[sid].get("included") is True], "missing": [sid for sid in assigned_order if sid not in by_id]}


def order_cut(state, cut_clips):
    validate_state(state); _clip_map(cut_clips)
    ordered = [sid for sequence in state["sequences"] for scene in sequence["scenes"] for sid in scene["shotIds"]]
    rank = {sid: i for i, sid in enumerate(ordered)}
    return sorted(copy.deepcopy(cut_clips), key=lambda c: rank.get(c["shotId"], len(ordered) + cut_clips.index(c)))


def _location(shot, board):
    value = shot.get("location") or shot.get("locationId") or shot.get("place") or ""
    bible = board.get("filmBible") or {}
    for item in bible.get("locations") or []:
        if item.get("id") == value: return item.get("title") or item.get("name") or value
    return str(value)


class SequenceStore:
    def __init__(self, workspace: Workspace, slug: str):
        self.workspace, self.slug = workspace, slug
        self.project = workspace.ensure_project(slug)
        self.path, self.lock_path = self.project / "Script" / "structure.json", self.project / "System" / "structure.lock"

    @contextmanager
    def locked(self):
        self.workspace._assert_managed_path(self.path); self.workspace._assert_managed_path(self.lock_path)
        with self.lock_path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX); yield

    def _empty_doc(self): return {"format": "raven-structure", "version": 1, "revision": 0, "state": _empty_state(), "undo": [], "redo": []}

    def load(self):
        self.workspace._assert_managed_path(self.path)
        if not self.path.exists(): return self._empty_doc()
        if self.path.stat().st_size > 16_000_000: raise ValueError("Sequence structure is too large.")
        doc = json.loads(self.path.read_text())
        if not isinstance(doc, dict) or not set(doc).issubset({"format", "version", "revision", "state", "undo", "redo", "knownShotIds"}) or not {"format", "version", "revision", "state", "undo", "redo"}.issubset(doc) or doc["format"] != "raven-structure" or doc["version"] != 1 or type(doc["revision"]) is not int or doc["revision"] < 0: raise ValueError("Unknown sequence format.")
        known = doc.setdefault("knownShotIds", [])
        if not isinstance(known, list) or any(not isinstance(s, str) or not s for s in known) or len(set(known)) != len(known): raise ValueError("Invalid known-shot register.")
        validate_state(doc["state"])
        for key in ("undo", "redo"):
            if not isinstance(doc[key], list) or len(doc[key]) > HISTORY_LIMIT: raise ValueError("Invalid sequence history.")
            for item in doc[key]:
                if not isinstance(item, dict) or set(item) != {"label", "state"} or not isinstance(item["label"], str): raise ValueError("Invalid sequence history.")
                validate_state(item["state"])
        return doc

    def _write(self, doc):
        body=json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if len(body.encode())>16_000_000: raise ValueError("Sequence structure and history are too large to save.")
        return _atomic_text(self.path, body) or doc

    def initialize(self, board, cut_clips):
        with self.locked():
            existed = self.path.exists()
            doc = self.load(); state = copy.deepcopy(doc["state"])
            ids = [c["shotId"] for c in cut_clips if isinstance(c, dict) and isinstance(c.get("shotId"), str)]
            known = set(doc.get("knownShotIds", []))
            incoming = list(dict.fromkeys(sid for sid in ids if sid not in known))
            if not state["sequences"] and not existed:
                sequence = {"id": _id("sequence"), "title": "The film", "targetFrames": 0, "scenes": []}
                last, scene = object(), None
                shots = {s.get("id"): s for s in (board.get("shots") or []) if isinstance(s, dict)}
                for sid in ids:
                    location = _location(shots.get(sid, {}), board)
                    if scene is None or location != last:
                        scene = {"id": _id("scene"), "title": location or "Scene", "targetFrames": 0, "shotIds": []}; sequence["scenes"].append(scene); last = location
                    scene["shotIds"].append(sid)
                state["sequences"] = [sequence]
            else:
                if not state["sequences"] and incoming:
                    state["sequences"] = [{"id": _id("sequence"), "title": "The film", "targetFrames": 0, "scenes": []}]
                if not state["sequences"]: incoming = []
                sequence = state["sequences"][0] if state["sequences"] else None
                unassigned = next((s for s in sequence["scenes"] if s["title"] == "Unassigned"), None) if sequence else None
                new = incoming
                if new:
                    if unassigned is None:
                        unassigned = {"id": _id("scene"), "title": "Unassigned", "targetFrames": 0, "shotIds": []}; sequence["scenes"].append(unassigned)
                    unassigned["shotIds"].extend(dict.fromkeys(new))
            # The register is durable, so an intentionally unassigned card is
            # not mistaken for a new card on the next open.  Historical states
            # also learn incoming cards, preventing undo from losing them.
            doc["knownShotIds"] = list(dict.fromkeys([*doc.get("knownShotIds", []), *ids]))
            for historical in doc["undo"] + doc["redo"]:
                hstate = historical["state"]
                if incoming:
                    if not hstate["sequences"]:
                        hstate["sequences"] = [{"id": _id("sequence"), "title": "The film", "targetFrames": 0, "scenes": []}]
                    hseq = hstate["sequences"][0]
                    hscene = next((s for s in hseq["scenes"] if s["title"] == "Unassigned"), None)
                    if hscene is None:
                        hscene = {"id": _id("scene"), "title": "Unassigned", "targetFrames": 0, "shotIds": []}; hseq["scenes"].append(hscene)
                    assigned = {sid for hs in hstate["sequences"] for hg in hs["scenes"] for sid in hg["shotIds"]}
                    hscene["shotIds"].extend(sid for sid in incoming if sid not in assigned)
                validate_state(hstate)
            validate_state(state)
            if state != doc["state"] or incoming or set(doc.get("knownShotIds",[])) != known:
                doc["state"] = state; doc["revision"] += 1; self._write(doc)
            return doc

    def commit(self, doc, state, label):
        validate_state(state)
        if not isinstance(label, str) or len(label) > 300: raise ValueError("Invalid history label.")
        with self.locked():
            actual = self.load()
            if actual["revision"] != doc["revision"]: raise ValueError("Sequence structure changed in another window.")
            if state == actual["state"]: return actual
            actual["undo"] = (actual["undo"] + [{"label": label, "state": actual["state"]}])[-HISTORY_LIMIT:]
            actual.update(state=copy.deepcopy(state), redo=[], revision=actual["revision"] + 1); self._write(actual); return actual

    def history(self, doc, redo=False):
        with self.locked():
            actual = self.load()
            if actual["revision"] != doc["revision"]: raise ValueError("Sequence structure changed in another window.")
            source, dest = ("redo", "undo") if redo else ("undo", "redo")
            if not actual[source]: return actual
            state = actual[source].pop(); actual[dest] = (actual[dest] + [{"label": state["label"], "state": actual["state"]}])[-HISTORY_LIMIT:]
            actual.update(state=state["state"], revision=actual["revision"] + 1); self._write(actual); return actual

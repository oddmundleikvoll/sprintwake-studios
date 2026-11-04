"""Bounded, resumable planning primitives for long films.

This module deliberately contains no model or media calls.  ``FilmPlanner``
only asks the injected language service for an outline and small card batches;
timing and validation are deterministic so a long request does not acquire
extra inference passes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

from .length_estimate import dialogue_seconds
from .ltx_media import atomic_json

FPS = 24
CLIP_MIN_SECONDS = 4
CLIP_TARGET_SECONDS = 5
CLIP_MAX_SECONDS = 6
FIRST_DEFAULT = CLIP_TARGET_SECONDS * FPS + 1  # 8k+1, about five seconds
CONT_DEFAULT = CLIP_TARGET_SECONDS * FPS      # 8k
FIRST_MIN = CLIP_MIN_SECONDS * FPS + 1
CONT_MIN = CLIP_MIN_SECONDS * FPS
FIRST_PLAN_MAX = CLIP_MAX_SECONDS * FPS + 1
CONT_PLAN_MAX = CLIP_MAX_SECONDS * FPS
# Native limits remain wider for saved plans and explicit manual timing.
FIRST_MAX = 481
CONT_MAX = 480
MAX_SECONDS = 120 * 60

# Prompt budgets are deliberately independent of the requested film length.
# A feature may contain hundreds of scenes, but each card request should carry
# the same small global bible plus a local continuity window.
GLOBAL_CONTEXT_CHARS = 9000
CHAPTER_CONTEXT_CHARS = 3500
LOCAL_CONTEXT_CHARS = 6500
SCENE_CONTEXT_CHARS = 4200
PRIOR_CONTEXT_CHARS = 1200
SYNOPSIS_CONTEXT_CHARS = 1200


def _phase_system(base: str, phase: str) -> str:
    """Give each bounded planning call its own output contract.

    ``film.py`` supplies a broad authoring prompt for the final plan.  Reusing
    that prompt for an outline or card batch made the model emit full nested
    scenes in a phase that only has room for metadata, which is especially
    damaging on long films.  Keep the supplied director guidance, but put the
    phase contract last so it is the operative schema for this request.
    """
    contracts = {
        "direct": (
            "PHASE: COMPLETE SHORT PLAN. Return the final validated schema with "
            "scenes[].shots[]. Do not omit shot cards."
        ),
        "outline": (
            "PHASE: OUTLINE ONLY. Return one compact JSON object containing title, "
            "style, theme, filmBible, characters, and either chapters (for a "
            "feature) or scene metadata. Do not emit shots, prompts for every "
            "clip, or any full scene card. Keep prose concise: titles and state "
            "fields <=120 characters, synopsis <=280 characters, and no more "
            "than 24 scenes or 24 chapters. Each director scene has at most six "
            "clips, an explicit known locationId, stateBefore and stateAfter."
        ),
        "chapter": (
            "PHASE: CHAPTER SCENE MAP ONLY. Return JSON {scenes:[...]}; each item "
            "has title, setting, imagePrompt, characterKeys, synopsis, locationId, "
            "stateBefore, stateAfter and shotCount (1..6). Do not emit shots. Keep each text field concise (<=180 "
            "characters) and use only the requested chapter budget."
        ),
        "batch": (
            "PHASE: SHOT CARD BATCH ONLY. Return JSON {scenes:[{shots:[...]}]} "
            "with exactly the requested number of shots. Every shot must include "
            "explicit start, action, end and camera strings, motionIntent "
            "(action|hold), and locationId when a film bible location exists; "
            "include stateBefore/stateAfter, propIds and assetStates when they "
            "change. Do not include film bible, characters, chapters, or extra prose."
        ),
    }
    return (base or "").rstrip() + "\n\n" + contracts[phase]


def _bounded_json(value: Any, limit: int) -> str:
    """Serialize context deterministically while shrinking long prose fields.

    The planner still sends every character and bible entry, but progressively
    shortens their prose when a caller supplies unusually verbose material. It
    never drops neighbouring scene records or state fields, and adds no model
    calls. The normal validated plan is already well below these limits.
    """
    def shrink(obj: Any, cap: int) -> Any:
        if isinstance(obj, str):
            return obj if len(obj) <= cap else obj[:max(16, cap - 1)].rstrip() + "…"
        if isinstance(obj, list):
            return [shrink(item, cap) for item in obj]
        if isinstance(obj, dict):
            return {key: shrink(item, cap) for key, item in obj.items()}
        return obj

    current = value
    for cap in (1200, 800, 500, 320, 220, 140, 90, 60, 40):
        encoded = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) <= limit:
            return encoded
        current = shrink(value, cap)
    # This fallback only applies to pathological user supplied metadata. Keep
    # shrinking the encoded JSON itself so escaping cannot exceed the budget.
    text = str(value)
    while len(json.dumps(text, ensure_ascii=False)) > limit and len(text) > 16:
        text = text[:max(16, int(len(text) * 0.8))]
    return json.dumps(text, ensure_ascii=False)


def _global_context(outline: dict) -> dict:
    """Return the stable, allowlisted part of the film bible and cast."""
    return {key: outline[key] for key in
            ("title", "style", "theme", "filmBible", "characters") if key in outline}


def _scene_summary(scene: dict) -> dict:
    return {key: scene.get(key, "") for key in
            ("title", "synopsis", "shotCount", "stateBefore", "stateAfter", "plannedStateBefore", "plannedStateAfter",
             "locationId", "propIds", "assetStates")}


def _text(obj: dict, key: str, limit: int = 4000, optional: bool = False) -> str:
    value = obj.get(key, "" if optional else None)
    if not isinstance(value, str) or len(value) > limit or (not optional and not value.strip()):
        raise ValueError(f"Ugyldig eller manglende {key}")
    if re.search(r"(?m)^\s*---\s*$", value):
        raise ValueError("Planen inneholder en reservert klippskiller")
    return value.strip()


def clip_count(seconds: float) -> int:
    if isinstance(seconds, bool):
        raise ValueError("Varighet må være et tall")
    seconds = float(seconds)
    if not math.isfinite(seconds) or not 6 <= seconds <= MAX_SECONDS:
        raise ValueError("Varighet må være mellom 6 og 7200 sekunder")
    # Aim for five seconds, allowing four to six instead of adding a new card
    # for every small remainder. The extra opening frame belongs to the grid.
    return max(1, math.floor(seconds / CLIP_TARGET_SECONDS + 0.5),
               math.ceil((seconds * FPS - 1) / CONT_PLAN_MAX))


def _validate_bible(raw: dict, out: dict) -> None:
    """Copy a small, allowlisted film bible without accepting executable data."""
    bible = raw.get("filmBible", raw.get("bible", {}))
    if bible in (None, {}):
        return
    if not isinstance(bible, dict):
        raise ValueError("Filmbibelen må være et JSON-objekt")
    out["filmBible"] = {}
    for key, limit in (("storyArc", 8000), ("theme", 2000)):
        if key in bible:
            out["filmBible"][key] = _text(bible, key, limit, True)
    ids: set[str] = set()
    for key in ("acts", "locations", "props"):
        value = bible.get(key, [])
        if not isinstance(value, list) or len(value) > 32:
            raise ValueError(f"Filmbibelens {key} er ugyldig")
        clean = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError(f"Filmbibelens {key} er ugyldig")
            entry = {}
            for field, limit in (("id", 80), ("title", 160), ("description", 3000),
                                 ("motivation", 2000), ("stateBefore", 3000), ("stateAfter", 3000)):
                if field in item:
                    entry[field] = _text(item, field, limit, True)
            if "referenceImage" in item:
                ref = item["referenceImage"]
                if not isinstance(ref, dict):
                    raise ValueError(f"Filmbibelens {key} har et ugyldig referenceImage")
                clean_ref = {field: ref[field] for field in
                             ("kind", "path", "url", "role", "tag", "label")
                             if isinstance(ref.get(field), str) and len(ref[field]) <= 1000}
                if not (clean_ref.get("path") or clean_ref.get("url")):
                    raise ValueError(f"Filmbibelens {key} trenger en lokal referenceImage")
                entry["referenceImage"] = clean_ref
            if "referenceState" in item:
                entry["referenceState"] = _text(item, "referenceState", 100, True) or "base"
            if "id" not in entry:
                raise ValueError(f"Filmbibelens {key} mangler id")
            if entry["id"] in ids:
                raise ValueError("Filmbibelens id-er må være unike")
            ids.add(entry["id"])
            clean.append(entry)
        out["filmBible"][key] = clean


def validate_plan(raw: dict, count: int, *, strict: bool = False) -> dict:
    """Validate authoring JSON, allowing one-shot scenes and film-bible fields."""
    if not isinstance(raw, dict):
        raise ValueError("Filmplanen må være et JSON-objekt")
    if type(count) is not int or count < 1:
        raise ValueError("Ugyldig klippantall")
    plan = {"title": _text(raw, "title", 160), "style": _text(raw, "style", 2000),
            "characters": [], "scenes": []}
    if "theme" in raw:
        plan["theme"] = _text(raw, "theme", 2000, True)
    _validate_bible(raw, plan)
    if strict and not plan.get("filmBible", {}).get("locations"):
        raise ValueError("Nye filmplaner trenger minst én filmbibel-location")
    chars = raw.get("characters", [])
    if not isinstance(chars, list) or len(chars) > 4:
        raise ValueError("Filmplanen kan ha inntil fire hovedkarakterer")
    keys: set[str] = set()
    for item in chars:
        if not isinstance(item, dict):
            raise ValueError("Ugyldig karakter")
        key = _text(item, "key", 40)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in keys:
            raise ValueError("Karakterene må ha unike enkle nøkler")
        keys.add(key)
        chars_out = {"key": key, "name": _text(item, "name", 100),
                     "description": _text(item, "description"),
                     "imagePrompt": _text(item, "imagePrompt")}
        if "motivation" in item:
            chars_out["motivation"] = _text(item, "motivation", 2000, True)
        plan["characters"].append(chars_out)
    scenes = raw.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= count:
        raise ValueError("Ugyldig scenetall")
    actual = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            raise ValueError("Ugyldig scene")
        cast = scene.get("characterKeys", [])
        shots = scene.get("shots")
        if not isinstance(cast, list) or any(not isinstance(k, str) for k in cast):
            raise ValueError("Scenens rolleliste må være en liste med tekstnøkler")
        if len(set(cast)) != len(cast) or any(k not in keys for k in cast):
            raise ValueError("Scenen viser til ukjente eller gjentatte karakterer")
        if not isinstance(shots, list) or not 1 <= len(shots) <= count:
            raise ValueError("Hver scene må ha minst ett klipp")
        if strict and len(shots) > 6:
            raise ValueError("Nye scener kan ha maksimalt seks klipp; lag et nytt kamerakutt")
        clean = {"title": _text(scene, "title", 160), "setting": _text(scene, "setting"),
                 "imagePrompt": _text(scene, "imagePrompt"), "characterKeys": cast, "shots": []}
        for key in ("stateBefore", "stateAfter", "locationId", "propIds", "actId", "assetStates"):
            if key in scene:
                value = scene[key]
                if key == "assetStates":
                    if not isinstance(value, dict) or len(value) > 32 or any(not isinstance(k,str) or not isinstance(v,str) or len(v)>1000 for k,v in value.items()):
                        raise ValueError("Ugyldig assetStates")
                elif key == "propIds":
                    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                        raise ValueError("Ugyldige rekvisitter")
                elif not isinstance(value, str):
                    raise ValueError(f"Ugyldig {key}")
                else:
                    value = value[:3000]
                if key == "locationId" and "filmBible" in plan:
                    known = {x["id"] for x in plan["filmBible"].get("locations", [])}
                    if value not in known:
                        raise ValueError("Scenen viser til ukjent locationId")
                if key == "actId" and "filmBible" in plan:
                    known = {x["id"] for x in plan["filmBible"].get("acts", [])}
                    if value not in known:
                        raise ValueError("Scenen viser til ukjent actId")
                clean[key] = value
            if "propIds" in clean and "filmBible" in plan:
                known = {x["id"] for x in plan["filmBible"].get("props", [])}
                if any(x not in known for x in clean["propIds"]):
                    raise ValueError("Scenen viser til ukjent propId")
        if strict:
            location_id = clean.get("locationId", "")
            known_locations = {x["id"] for x in plan["filmBible"].get("locations", []) if x.get("description", "").strip()}
            if location_id not in known_locations:
                raise ValueError("Nye scener trenger en kjent locationId med beskrivelse")
            if not clean.get("stateBefore") or not clean.get("stateAfter"):
                raise ValueError("Nye scener trenger stateBefore og stateAfter")
        for shot in shots:
            if not isinstance(shot, dict):
                raise ValueError("Ugyldig klipp")
            entry = {k: _text(shot, k, 6000 if k == "action" else 2000)
                     for k in ("title", "start", "action", "end", "camera", "sound")}
            for key in ("locationId", "stateBefore", "stateAfter"):
                if key in shot:
                    entry[key] = _text(shot, key, 3000, True)
            motion = shot.get("motionIntent", "action")
            if motion not in ("action", "hold"):
                raise ValueError("motionIntent må være action eller hold")
            entry["motionIntent"] = motion
            if "propIds" in shot:
                props = shot["propIds"]
                if not isinstance(props, list) or len(props) > 32 or any(not isinstance(v, str) or not v.strip() for v in props):
                    raise ValueError("Ugyldige rekvisitter")
                entry["propIds"] = list(dict.fromkeys(props))
            if "assetStates" in shot:
                states = shot["assetStates"]
                if (not isinstance(states, dict) or len(states) > 32
                        or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 1000
                               for k, v in states.items())):
                    raise ValueError("Ugyldig assetStates")
                entry["assetStates"] = dict(states)
            if "locationId" in entry and "filmBible" in plan:
                known = {x["id"] for x in plan["filmBible"].get("locations", [])}
                if entry["locationId"] and entry["locationId"] not in known:
                    raise ValueError("Klippet viser til ukjent locationId")
                if strict and entry["locationId"] and entry["locationId"] != clean["locationId"]:
                    raise ValueError("Et stedsskifte trenger ny kamerascene")
            entry["dialogue"] = _text(shot, "dialogue", 300, True)
            entry["speakerKey"] = _text(shot, "speakerKey", 40, True)
            entry["dialogueLanguage"] = _text(shot, "dialogueLanguage", 60, True) or "Norwegian"
            if not re.fullmatch(r"[A-Za-z][A-Za-z -]*", entry["dialogueLanguage"]):
                raise ValueError("Dialogspråket må ha et enkelt engelsk språknavn")
            if entry["dialogue"] and entry["speakerKey"] not in cast:
                raise ValueError("Dialogen trenger en taler i scenens rolleliste")
            if "cardFrames" in shot:
                frames = shot["cardFrames"]
                if type(frames) is not int or frames < 1:
                    raise ValueError("Ugyldige klippbilder")
                if frames > FIRST_MAX or frames % 8 not in (0, 1):
                    raise ValueError("Klippbilder må følge LTX-rammeregelen")
                entry["cardFrames"] = frames
            if "timingSeconds" in shot:
                if not isinstance(shot["timingSeconds"], (int, float)) or not math.isfinite(float(shot["timingSeconds"])):
                    raise ValueError("Ugyldig klipptid")
                entry["timingSeconds"] = float(shot["timingSeconds"])
            clean["shots"].append(entry)
        if strict and len({entry["camera"] for entry in clean["shots"]}) > 1:
            raise ValueError("Samme kamerascene må beholde identisk camera-felt; et nytt utsnitt trenger egen scene")
        actual += len(shots)
        plan["scenes"].append(clean)
        for key, low, high, modulus in (("firstFrames", 25, FIRST_MAX, 8), ("continuationFrames", 8, CONT_MAX, 8)):
            if key in scene:
                value = scene[key]
                if type(value) is not int or not low <= value <= high or (value - (1 if key == "firstFrames" else 0)) % modulus:
                    raise ValueError(f"Ugyldige {key}")
                clean[key] = value
    if actual != count:
        raise ValueError(f"Forventet {count} klipp, fikk {actual}")
    if "timing" in raw:
        timing = raw["timing"]
        if not isinstance(timing, dict) or not isinstance(timing.get("requestedSeconds"), (int, float)):
            raise ValueError("Ugyldig timing")
        if not isinstance(timing.get("allocatedFrames"), int) or timing["allocatedFrames"] < 1:
            raise ValueError("Ugyldig timingbudsjett")
        if not isinstance(timing.get("flags", []), list):
            raise ValueError("Ugyldige timingflagg")
        plan["timing"] = {"requestedSeconds": float(timing["requestedSeconds"]),
                           "allocatedFrames": timing["allocatedFrames"], "flags": timing.get("flags", [])}
    return plan


def _action_floor(shot: dict) -> float:
    text = str(shot.get("action", ""))
    # Conservative deterministic proxy for action beats; no extra LLM call.
    verbs = re.findall(r"\b(?:turn|walk|run|reach|grab|open|close|sit|stand|look|raise|lower|pick|put|move|wave|nod|speak|enter|leave|cross|hold)\w*\b", text.lower())
    return max(1.0, 0.65 + 0.65 * len(verbs))


def allocate_timing(plan: dict, requested_seconds: float) -> dict:
    """Allocate new four-to-six-second cards; flag content that needs splitting."""
    requested = float(requested_seconds)
    if not math.isfinite(requested) or not 6 <= requested <= MAX_SECONDS:
        raise ValueError("Varighet må være mellom 6 og 7200 sekunder")
    out = json.loads(json.dumps(plan))
    scenes = out.get("scenes", [])
    cards = [shot for scene in scenes for shot in scene.get("shots", [])]
    if not cards or not scenes:
        return out
    mins = []
    for shot in cards:
        talk, _ = dialogue_seconds(shot, None)
        mins.append(max(_action_floor(shot), talk + 1.0 if talk else 0.0))
    target = math.ceil(requested * FPS)
    flags = []
    cursor = 0
    allocations = []
    for scene in scenes:
        count = len(scene.get("shots", []))
        scene_mins = mins[cursor:cursor + count]
        first_need = math.ceil(scene_mins[0] * FPS)
        first = max(FIRST_MIN, 1 + 8 * math.ceil(max(0, first_need - 1) / 8))
        cont_need = max((math.ceil(x * FPS) for x in scene_mins[1:]), default=CONT_MIN)
        cont = max(CONT_MIN, 8 * math.ceil(cont_need / 8))
        if first > FIRST_PLAN_MAX:
            flags.append({"scene": len(allocations), "reason": "first_card_requires_split", "minimumFrames": first})
            first = FIRST_PLAN_MAX
        if cont > CONT_PLAN_MAX:
            flags.append({"scene": len(allocations), "reason": "continuation_requires_split", "minimumFrames": cont})
            cont = CONT_PLAN_MAX
        allocations.append([first, cont, count])
        cursor += count
    # Grow the shortest windows first, keeping one continuation size per setup.
    # A final uniform group can cross the target slightly; filling only the
    # remainder with opening frames would stretch openings before that group.
    def total():
        return sum(f + (n - 1) * c for f, c, n in allocations)
    while total() < target:
        actions = []
        for i, (first, cont, n) in enumerate(allocations):
            if first < FIRST_PLAN_MAX:
                actions.append((i, 0, 8))
            if n > 1 and cont < CONT_PLAN_MAX:
                actions.append((i, 1, 8*(n-1)))
        if not actions:
            flags.append({"reason": "requested_duration_exceeds_clip_budget", "requestedFrames": target})
            break
        i, field, _ = min(actions,
            key=lambda a: (allocations[a[0]][a[1]] - (1 if a[1] == 0 else 0), a[2], a[0]))
        allocations[i][field] += 8
    cursor = 0
    for scene, (first, cont, count) in zip(scenes, allocations):
        scene["firstFrames"], scene["continuationFrames"] = first, cont
        for local, shot in enumerate(scene.get("shots", [])):
            frame_count = first if local == 0 else cont
            shot["cardFrames"] = frame_count
            shot["timingSeconds"] = round(frame_count / FPS, 2)
            if frame_count < math.ceil(mins[cursor] * FPS):
                flags.append({"index": cursor, "reason": "dialogue_or_action_needs_split", "minimumFrames": math.ceil(mins[cursor] * FPS)})
            cursor += 1
    # Do not silently turn a dense script into a much longer, costlier film.
    # Small clips retain two seconds of grid/default-length tolerance.
    if total() > math.ceil(max(requested * 1.10, requested + 2) * FPS):
        flags.append({"reason": "runtime_budget_exceeded", "allocatedFrames": total(), "requestedFrames": target})
    out["timing"] = {"requestedSeconds": requested, "allocatedFrames": total(), "flags": flags}
    return out


class FilmPlanner:
    """Bounded planner with a durable, signature-checked checkpoint."""
    def __init__(self, service, *, check_cancel: Callable[[], None] | None = None,
                 on_progress: Callable[..., None] | None = None,
                 checkpoint_path: str | Path | None = None):
        self.service = service
        self.check_cancel = check_cancel or (lambda: None)
        self.on_progress = on_progress or (lambda **kwargs: None)
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None

    def _save(self, doc):
        if self.checkpoint_path:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(self.checkpoint_path, doc)

    def _scene_map(self, scenes, outline, count):
        if not isinstance(scenes, list) or not scenes or len(scenes) > 24:
            raise ValueError("Oversikten trenger 1–24 kamerascener")
        budgets = [scene.get("shotCount") for scene in scenes if isinstance(scene, dict)]
        if (len(budgets) != len(scenes) or any(type(value) is not int or not 1 <= value <= 6 for value in budgets)
                or sum(budgets) != count):
            raise ValueError(f"Hver kamerascene trenger shotCount 1–6; summen må være {count}")
        bible = {}
        _validate_bible(outline, bible)
        locations = {entry["id"] for entry in bible.get("filmBible", {}).get("locations", []) if entry.get("description")}
        for scene in scenes:
            if scene.get("locationId") not in locations:
                raise ValueError("Hver kamerascene trenger locationId fra filmbibelens beskrevne steder")
            for field in ("stateBefore", "stateAfter"):
                _text(scene, field, 3000)

    def _load(self, signature):
        if not self.checkpoint_path or not self.checkpoint_path.is_file():
            return None
        try:
            doc = json.loads(self.checkpoint_path.read_text())
            if doc.get("schemaVersion") != 1 or doc.get("signature") != signature:
                return None
            if doc.get("phase") == "done":
                doc["plan"] = validate_plan(doc.get("plan"), sum(len(s.get("shots", [])) for s in doc["plan"].get("scenes", [])))
            return doc
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            return None

    def plan(self, description: str, count: int, *, system: str = "", settings: dict | None = None) -> dict:
        if type(count) is not int or count < 1:
            raise ValueError("Ugyldig klippantall")
        signature = hashlib.sha256(json.dumps({"description": description, "count": count,
            "system": system, "settings": settings or {}}, sort_keys=True).encode()).hexdigest()
        saved = self._load(signature)
        if saved and saved.get("plan") and saved.get("phase") == "done":
            return saved["plan"]
        if saved:
            outline = saved.get("outline")
            completed = saved.get("completed", {})
        else:
            if count <= 6:
                prompt = f"Return one complete JSON film plan with EXACTLY {count} shots. Film brief:\n{description}"
                last_error = None
                for attempt in range(2):
                    self.check_cancel()
                    raw = self.service.complete(_phase_system(system, "direct"), prompt, timeout=600,
                                                 max_tokens=min(12000, 1800 + count * 700))
                    try:
                        direct = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
                        plan = validate_plan(direct, count, strict=True)
                        self._save({"schemaVersion": 1, "signature": signature, "phase": "done", "plan": plan,
                                    "outline": direct, "completed": {}})
                        return plan
                    except (ValueError, TypeError, json.JSONDecodeError) as exc:
                        last_error = exc
                        prompt += f"\nCorrect the complete JSON plan: {exc}"
                raise ValueError(f"Regissørmodellen laget ikke en gyldig filmplan: {last_error}")
            self.check_cancel()
            outline = None
            if count > 120:
                outline_prompt = (f"Return JSON only. For exactly {count} shots, produce a compact global film outline "
                                  "with title, style, theme, filmBible, characters, and chapters. Each chapter has id, title, "
                                  "synopsis, plannedStateBefore, plannedStateAfter and shotCount; use at most 24 chapters, "
                                  "each shotCount <=120, and sums exactly to the requested total. Do not emit shot cards or scenes. "
                                  "Keep the bible and character descriptions concise; each synopsis/state field is <=280 characters. "
                                  f"Film brief:\n{description}")
            else:
                outline_prompt = (f"Return JSON only. For exactly {count} shots, produce title, style, characters, "
                                  "filmBible, and scenes with title, setting, imagePrompt, characterKeys, synopsis, locationId, stateBefore, stateAfter and shotCount. "
                                  "Each scene is one camera setup with shotCount 1..6; counts sum exactly to the requested total. "
                                  "Use at most 24 scenes and keep every text field <=180 characters; do not emit shots. "
                                  f"Film brief:\n{description}")
            last_error = None
            larger_retry = False
            for attempt in range(2):
                self.check_cancel()
                outline_budget = min(12000 if larger_retry else 6000,
                                     (4800 if larger_retry else 2400) + min(count, 120) * 60)
                raw = self.service.complete(_phase_system(system, "outline"), outline_prompt, timeout=600,
                                             max_tokens=outline_budget)
                try:
                    outline = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
                    scenes = outline.get("scenes") if isinstance(outline, dict) else None
                    if count > 120:
                        chapters = outline.get("chapters") if isinstance(outline, dict) else None
                        budgets = [c.get("shotCount") for c in chapters] if isinstance(chapters, list) else []
                        if (not chapters or len(chapters) > 24
                                or any(not isinstance(c, dict) for c in chapters)
                                or any(type(x) is not int or not 1 <= x <= 120 for x in budgets)
                                or sum(budgets) != count):
                            raise ValueError(f"chapters må være inntil 24, hvert shotCount 1–120 og summere til {count}")
                        # Older or hand-authored outlines may omit IDs. Assign
                        # deterministic IDs before the first durable save so a
                        # resume can recognize already expanded chapters.
                        seen_ids = set()
                        for chapter_index, chapter in enumerate(chapters):
                            chapter_id = chapter.get("id")
                            if not isinstance(chapter_id, str) or not chapter_id.strip():
                                chapter_id = f"chapter-{chapter_index + 1}"
                            if chapter_id in seen_ids:
                                raise ValueError("kapitlene må ha unike id-er")
                            seen_ids.add(chapter_id)
                            chapter["id"] = chapter_id
                        outline["chapters"] = chapters
                        break
                    self._scene_map(scenes, outline, count)
                    break
                except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                    last_error = exc
                    larger_retry = isinstance(exc, json.JSONDecodeError)
                    outline_prompt += f"\nCorrect the complete outline: {exc}"
                    outline = None
            if outline is None:
                raise ValueError(f"Regissørmodellen laget ikke en gyldig filmoversikt: {last_error}")
            completed = {}
            self._save({"schemaVersion": 1, "signature": signature, "phase": "outline", "outline": outline, "completed": completed})
        if count <= 6 and isinstance(outline.get("scenes"), list) and outline["scenes"] and "shots" in outline["scenes"][0]:
            plan = validate_plan(outline, count, strict=True)
            self._save({"schemaVersion": 1, "signature": signature, "phase": "done", "plan": plan, "outline": outline, "completed": completed})
            return plan
        # Feature-length requests are expanded one bounded chapter at a time.
        # This keeps prompts well below context limits while retaining one
        # global film bible and stable character keys.
        if count > 120 and "chapters" in outline:
            outline.setdefault("scenes", [])
            chapters = outline.get("chapters", [])
            # Normalize checkpoints created by older planner versions too.
            seen_ids = set()
            for chapter_index, chapter in enumerate(chapters):
                chapter_id = chapter.get("id")
                if not isinstance(chapter_id, str) or not chapter_id.strip():
                    chapter_id = f"chapter-{chapter_index + 1}"
                if chapter_id in seen_ids:
                    raise ValueError("kapitlene må ha unike id-er")
                seen_ids.add(chapter_id)
                chapter["id"] = chapter_id
            for chapter_index, chapter in enumerate(chapters):
                if any(s.get("chapterId") == chapter.get("id") for s in outline["scenes"]):
                    continue
                chapter_data = {key: chapter.get(key, "") for key in
                                ("id", "title", "synopsis", "plannedStateBefore", "plannedStateAfter", "shotCount")}
                chapter_prompt = ("Return JSON only with scenes for exactly this chapter budget. "
                                  "Each scene has title, setting, imagePrompt, characterKeys, synopsis, locationId, stateBefore, stateAfter and shotCount; "
                                  "shotCounts must sum to the chapter budget and each camera scene has 1..6 shots. "
                                  f"GLOBAL FILM BIBLE AND CAST: {_bounded_json(_global_context(outline), GLOBAL_CONTEXT_CHARS)}\n"
                                  f"CHAPTER: {_bounded_json(chapter_data, CHAPTER_CONTEXT_CHARS)}\n"
                                  "Keep character keys unchanged and describe planned state, not observed state.")
                expanded = None
                last_error = None
                larger_retry = False
                for attempt in range(2):
                    self.check_cancel()
                    chapter_budget = min(12000 if larger_retry else 6000,
                                         (5200 if larger_retry else 2600) + min(int(chapter["shotCount"]), 120) * 32)
                    raw = self.service.complete(_phase_system(system, "chapter"), chapter_prompt, timeout=600,
                                                 max_tokens=chapter_budget)
                    try:
                        expanded = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
                        chapter_scenes = expanded.get("scenes")
                        budget = int(chapter["shotCount"])
                        self._scene_map(chapter_scenes, outline, budget)
                        break
                    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                        last_error = exc
                        larger_retry = isinstance(exc, json.JSONDecodeError)
                        chapter_prompt += f"\nCorrect the complete chapter outline: {exc}"
                        expanded = None
                if expanded is None:
                    raise ValueError(f"Kapittel {chapter_index + 1} mangler gyldig sceneoversikt: {last_error}")
                for scene in expanded["scenes"]:
                    scene["chapterId"] = chapter.get("id", f"chapter-{chapter_index + 1}")
                    scene.setdefault("plannedStateBefore", chapter.get("plannedStateBefore", ""))
                    scene.setdefault("plannedStateAfter", chapter.get("plannedStateAfter", ""))
                outline["scenes"].extend(expanded["scenes"])
                self._save({"schemaVersion": 1, "signature": signature, "phase": "chapters", "outline": outline,
                            "chapters": chapters, "completed": {"chapters": chapter_index + 1}})
            outline.pop("chapters", None)
        scenes = outline.get("scenes", [])
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("Filmoversikten mangler scener")
        if sum(int(s.get("shotCount", len(s.get("shots", [])))) for s in scenes) != count:
            raise ValueError("Filmoversikten har feil klippbudsjett")
        # Only neighbouring scenes are relevant for a card batch. Keeping a
        # two-scene look-behind/look-ahead preserves hand-offs without making
        # every feature-length prompt grow with the complete film.
        for si, scene in enumerate(scenes):
            budget = int(scene.get("shotCount", len(scene.get("shots", []))))
            scene.setdefault("shots", [])
            synopsis = scene.get("synopsis", "")
            remaining = budget - len(scene["shots"])
            batch_count = max(1, math.ceil(budget / 6))
            base, remainder = divmod(budget, batch_count)
            sizes = [base + (1 if i < remainder else 0) for i in range(batch_count)]
            offset = len(scene["shots"])
            completed_to_skip = offset
            for batch_index, planned_size in enumerate(sizes):
                if offset >= budget:
                    break
                if completed_to_skip:
                    skipped = min(completed_to_skip, planned_size)
                    completed_to_skip -= skipped
                    planned_size -= skipped
                    if planned_size <= 0:
                        continue
                self.check_cancel()
                size = min(planned_size, budget - offset)
                prior = (scene["shots"][-1].get("end", "Opening of this scene.") if scene["shots"] else
                         scene.get("stateBefore") or (scenes[si-1].get("stateAfter") or scenes[si-1]["shots"][-1]["end"] if si else "Opening of this scene."))
                compact = _global_context(outline)
                nearby = [_scene_summary(scenes[index]) for index in
                           range(max(0, si - 2), min(len(scenes), si + 3))]
                prompt = (f"GLOBAL OUTLINE (fixed): {_bounded_json(compact, GLOBAL_CONTEXT_CHARS)}\n"
                          f"ALL SCENE SUMMARIES (nearby fixed): {_bounded_json(nearby, LOCAL_CONTEXT_CHARS)}\n"
                          f"Scene: {_bounded_json({k: v for k, v in scene.items() if k != 'shots'}, SCENE_CONTEXT_CHARS)}\n"
                          f"Prior planned story state: {_bounded_json(str(prior), PRIOR_CONTEXT_CHARS)}\n"
                          f"Beats: {_bounded_json(str(synopsis), SYNOPSIS_CONTEXT_CHARS)}\nWrite exactly {size} shots.")
                cards = None
                last_error = None
                for attempt in range(2):
                    self.check_cancel()
                    raw = self.service.complete(_phase_system(system, "batch"), prompt, timeout=600,
                                                 max_tokens=min(9000, 1800 + size * 520))
                    try:
                        doc = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
                        cards = validate_plan({**outline, "scenes": [{**scene, "shots": doc["scenes"][0]["shots"]}]}, size, strict=True)["scenes"][0]["shots"]
                        break
                    except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError) as exc:
                        last_error = exc
                        prompt += f"\nCorrect this batch and return exactly {size} shots: {exc}"
                if cards is None:
                    raise ValueError(f"Regissørmodellen laget ikke gyldige klippkort: {last_error}")
                if scene["shots"]:
                    cards[0]["start"] = prior
                scene["shots"].extend(cards)
                offset += len(cards)
                completed[str(si)] = len(scene["shots"])
                self._save({"schemaVersion": 1, "signature": signature, "phase": "batches", "outline": outline, "completed": completed})
                self.on_progress(scene=si + 1, completed=len(scene["shots"]), total=budget)
        plan = validate_plan(outline, count, strict=True)
        self._save({"schemaVersion": 1, "signature": signature, "phase": "done", "plan": plan, "outline": outline, "completed": completed})
        return plan

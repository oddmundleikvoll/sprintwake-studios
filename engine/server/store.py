"""Storyboard persistence.

A storyboard is one JSON file, ``storyboard.json``, living in the same folder
as the shots it produced::

    <projects-folder>/<slug>/
        storyboard.json          <- the whole board: scene, refs, shots
        refs/                    <- uploaded reference images
        shots/01/shot.vpipeline  <- generated per render
        shots/01/clip.mp4
        shots/01/frames/*.png

Keeping the board next to its output means a project folder is self-contained
— copy or zip the folder and you have the board *and* everything it made.
That also makes save/load/export the same operation on the same file rather
than three formats.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .soundtrack.board import no_music_in_shots

BOARD_FILE = "storyboard.json"
SCHEMA = 1
IMAGE_MANIFEST_FILE = "images.json"


def last_saved_frame(frames_dir: Path, expected_frames: int = 0) -> Path | None:
    """Return the last frame belonging to the current requested length.

    Re-rendering into ``frame-%04d.png`` does not remove files from a longer
    previous take. Choosing ``frames[-1]`` therefore made a chained shot use
    an old frame whenever the new take was shorter. Prefer the highest frame
    index below the current request; if a short/sketch render has fewer files,
    its actual last file remains the correct fallback.
    """
    frames = sorted(frames_dir.glob("*.png")) if frames_dir.exists() else []
    if not frames:
        return None
    if expected_frames > 0:
        usable = []
        for frame in frames:
            match = re.search(r"-(\d+)$", frame.stem)
            if match and int(match.group(1)) < expected_frames:
                usable.append(frame)
        if usable:
            return usable[-1]
    return frames[-1]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s[:60] or "untitled"


def artifact_filename(board_name: str, scene_number: int, kind: str, suffix: str) -> str:
    """Readable, filesystem-safe filename for a generated scene artifact."""
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", (board_name or "storyboard").strip())
    label = re.sub(r"\s+", " ", label).strip(" .-_")[:100] or "storyboard"
    return f"{label} - scene {scene_number:02d} - {kind}{suffix}"


def _rewrite_slug(node: Any, old: str, new: str) -> Any:
    """Repoint every stored path from one project slug to another.

    Two forms occur: browser URLs (``/media/<slug>/...``) and paths relative
    to the selected projects folder (``<slug>/...``). Handle the historical
    ``projects/<slug>/...`` forms too, so an old board can still be renamed
    before its load-time path migration is saved.
    """
    if isinstance(node, str):
        for prefix in ("/media/projects/", "projects/", "/media/", ""):
            old_head = f"{prefix}{old}/"
            if node.startswith(old_head):
                return f"{prefix}{new}/{node[len(old_head):]}"
        return node
    if isinstance(node, list):
        return [_rewrite_slug(v, old, new) for v in node]
    if isinstance(node, dict):
        return {k: _rewrite_slug(v, old, new) for k, v in node.items()}
    return node


def _normalize_media_paths(node: Any) -> Any:
    """Remove the obsolete internal ``projects/`` storage segment."""
    if isinstance(node, str):
        if node.startswith("/media/projects/"):
            return "/media/" + node[len("/media/projects/"):]
        if node.startswith("projects/"):
            return node[len("projects/"):]
        return node
    if isinstance(node, list):
        return [_normalize_media_paths(v) for v in node]
    if isinstance(node, dict):
        return {k: _normalize_media_paths(v) for k, v in node.items()}
    return node


def _media_rel(value: str) -> str:
    if value.startswith("/media/"):
        value = value[len("/media/"):]
    if value.startswith("projects/"):
        value = value[len("projects/"):]
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        return ""
    return str(path)


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ref_path(ref: Any) -> str:
    if isinstance(ref, str):
        return _media_rel(ref)
    if not isinstance(ref, dict):
        return ""
    value = ref.get("path") or ref.get("url") or ref.get("resolved") or ""
    return _media_rel(value) if isinstance(value, str) else ""


def _usage_label(index: int, shot: dict[str, Any] | None, label: str) -> str:
    if shot:
        title = (shot.get("title") or "").strip()
        head = f"Scene {index}"
        if title:
            head += f" - {title}"
        return f"{head} - {label}"
    return label


def _add_usage(
    usages: dict[str, list[str]],
    ref: Any,
    label: str,
    *,
    index: int | None = None,
    shot: dict[str, Any] | None = None,
) -> None:
    rel = _ref_path(ref)
    if not rel:
        return
    name = _usage_label(index or 0, shot, label) if shot else label
    bucket = usages.setdefault(rel, [])
    if name not in bucket:
        bucket.append(name)


def _collect_media_usages(board: dict[str, Any]) -> dict[str, list[str]]:
    usages: dict[str, list[str]] = {}
    shots = board.get("shots") or []
    characters = {
        c.get("id"): c
        for c in (board.get("characters") or [])
        if c.get("id")
    }

    _add_usage(usages, (board.get("assembly") or {}).get("backgroundAudio"), "Soundtrack audio file")
    _add_usage(usages, (board.get("soundtrack") or {}).get("reference"), "Soundtrack reference")
    for i, ref in enumerate(board.get("styleRefs") or [], start=1):
        _add_usage(usages, ref, f"Style reference {i}")

    bible = board.get("filmBible") if isinstance(board.get("filmBible"), dict) else {}
    for group in ("locations", "props"):
        for entry in bible.get(group) or []:
            if isinstance(entry, dict):
                ident = entry.get("id") or entry.get("title") or group[:-1]
                _add_usage(usages, entry.get("referenceImage"),
                           f"Film bible {group[:-1]} {ident}")

    for idx, shot in enumerate(shots, start=1):
        for output in shot.get("outputs") or []:
            _add_usage(usages, output, "Rendered output", index=idx, shot=shot)
        for phase, still in (shot.get("stills") or {}).items():
            _add_usage(usages, still, f"Legacy still ({phase})", index=idx, shot=shot)
        _add_usage(usages, shot.get("startRef"), "Start", index=idx, shot=shot)
        _add_usage(usages, shot.get("endRef"), "End", index=idx, shot=shot)
        for ref_idx, ref in enumerate(shot.get("referenceImages") or [], start=1):
            _add_usage(usages, ref, f"Shot reference {ref_idx}",
                       index=idx, shot=shot)
        from .ltx_anchors import readable_anchors
        for ref_idx, anchor in enumerate(readable_anchors(shot), start=1):
            _add_usage(usages, anchor.get("ref"), f"Image anchor {ref_idx}", index=idx, shot=shot)
        for cid in shot.get("characterIds") or []:
            ch = characters.get(cid)
            if not ch:
                continue
            name = (ch.get("name") or "Character").strip()
            _add_usage(usages, ch.get("image"), f"Character {name}", index=idx, shot=shot)

    used_in_shots = {
        cid
        for shot in shots
        for cid in (shot.get("characterIds") or [])
    }
    for ch in characters.values():
        if ch.get("id") in used_in_shots:
            continue
        name = (ch.get("name") or "Character").strip()
        _add_usage(usages, ch.get("image"), f"Cast - {name}")

    return usages


def new_id(prefix: str = "s") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# What a render was of
# ---------------------------------------------------------------------------

def _ref_key(ref: Any) -> str | None:
    """A reference reduced to what identifies it, and nothing that moves.

    A chain's ``resolved`` frame is included because it is the actual image
    consumed by the render. The resolver keeps it stable when the source take
    is unchanged, but changing from a stale longer-take tail to the current
    tail must invalidate the dependent shot.
    """
    if not ref:
        return None
    if isinstance(ref, str):
        return ref
    if not isinstance(ref, dict):
        return None
    if ref.get("kind") == "chain":
        return (f"chain:{ref.get('from') or ''}:{ref.get('resolved') or ''}"
                + (":" + ref["contentHash"] if ref.get("contentHash") else ""))
    key = ref.get("path") or ref.get("url") or ""
    key = key if isinstance(key, str) else ""
    return key + (":" + ref["contentHash"] if isinstance(ref.get("contentHash"), str) else "")


def speech_fingerprint(shot: dict, board: dict) -> str:
    from .dubbing import speaker_for
    speaker = speaker_for(shot, board) or {}
    payload = [shot.get("dialogue", "").strip(), shot.get("dialogueStyle", "").strip(),
               speaker.get("id"), _ref_key(speaker.get("voice")), speaker.get("voiceText"),
               shot.get("dialogueVoice"), (board.get("defaults") or {}).get("tts")]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _speaker_voice_description(shot: dict[str, Any], board: dict[str, Any]) -> dict[str, str]:
    """The speaking character's voice description, for the fingerprint.

    Empty -> no key at all, so boards from before the field existed keep the
    fingerprints they were rendered under.
    """
    if not (shot.get("dialogue") or "").strip():
        return {}
    from .dubbing import speaker_for

    voice = ((speaker_for(shot, board) or {}).get("voiceDescription") or "").strip()
    return {"voiceDescription": voice} if voice else {}


def render_fingerprint(shot: dict[str, Any], board: dict[str, Any], *,
                       _library_refs: list | None = None) -> str:
    """Hash of everything that decides what a render of *shot* produces.

    Recorded on a shot when it renders, so "has this been rendered?" can be
    answered as "rendered from *what the board says now*?" — a distinction
    that cost a whole run: every shot was marked done, so a rewritten prompt
    was skipped and the batch cheerfully re-reported clips of the old words.

    Only conditioning and geometry count. ``dialogue`` is included because it
    reaches native LTX speech or guides mouth movement for a separate recording.

    The project's style-reference *library* (``board["styleRefs"]``) is not
    an input: a shot only sends a style reference that is also in its own
    reference images. Legacy image/voice fields are retained in this hash so
    existing render records remain interpretable; those references are
    counted below. Counting the library too meant that adding an image to
    one shot, which also files it in the library, marked every rendered shot
    on the board as changed. ``_library_refs`` rebuilds that old formula, so
    fingerprints recorded under it can be recognised (see
    ``matches_pre_library_fix``); nothing else should pass it.
    """
    defaults = board.get("defaults") or {}
    external_model = (shot.get("modelProvider") or defaults.get("modelProvider")) == "comfy-workflow"
    legacy = _library_refs is not None
    library = list(_library_refs or [])
    wanted = set(shot.get("characterIds") or [])
    cast = [
        {
            "name": (c.get("name") or "").strip(),
            "description": (c.get("description") or "").strip(),
            "image": _ref_key(c.get("image")),
        }
        for c in (board.get("characters") or [])
        if c.get("id") in wanted
    ]
    payload = {
        # Preserve the legacy fingerprint shape. Migrated boards request
        # LTX explicitly, which makes previous-engine takes stale.
        "layeringVersion": 6,
        "speechInputs": speech_fingerprint(shot, board),
        "recording": shot.get("dialogueAudioUrl") if shot.get("dialogueSource") == "recording" else None,
        "dialogueSource": shot.get("dialogueSource", "auto"),
        "dialogueStyle": shot.get("dialogueStyle", ""),
        **_speaker_voice_description(shot, board),
        "speakerId": shot.get("speakerId", ""),
        "dialogueVoice": shot.get("dialogueVoice", ""),
        "dubMode": shot.get("dubMode", "mix"),
        "voices": [{"id": c.get("id"), "voice": _ref_key(c.get("voice")), "voiceText": c.get("voiceText", "")} for c in board.get("characters", []) if c.get("id") in wanted or c.get("id") == shot.get("speakerId")],
        "referenceMetadata": [{"tag": r.get("tag", ""), "role": r.get("role", "")} for r in ([shot.get("startRef"), shot.get("endRef")] + (shot.get("referenceImages") or []) + library + [c.get("image") for c in board.get("characters", []) if c.get("id") in wanted]) if isinstance(r, dict)],
        "prompt": (shot.get("prompt") or "").strip(),
        "dialogue": (shot.get("dialogue") or "").strip(),
        "soundNote": (shot.get("soundNote") or "").strip(),
        "scene": (board.get("sceneDescription") or "").strip(),
        "soundscape": (board.get("soundscape") or "").strip()
        if board.get("soundscapeInShots", True) else "",
        "soundscapeInShots": bool(board.get("soundscapeInShots", True)),
        "cast": cast,
        "startRef": _ref_key(shot.get("startRef")),
        "endRef": _ref_key(shot.get("endRef")),
        **({"imageAnchors": ([{"ref": _ref_key(a.get("ref")), "frame": a.get("frame"),
                              "strength": a.get("strength")} if isinstance(a, dict) else {"invalid": a}
                             for a in shot["imageAnchors"]] if isinstance(shot["imageAnchors"], list)
                             else {"invalid": shot["imageAnchors"]})}
           if "imageAnchors" in shot and shot["imageAnchors"] != [] else {}),
        "referenceImages": [
            _ref_key(r) for r in (shot.get("referenceImages") or [])
        ],
        "model": (requested_model := shot.get("model") or defaults.get("model") or "ref2va"),
        # Historical IDs remain readable for old render records; migrated
        # projects explicitly select ltx-2.5 before fingerprinting new takes.
        "effectiveModel": (
            requested_model if external_model or requested_model in ("qwen-image-2.1-turbo", "krea2-still", "wan-i2v", "ltx-2.5")
            else "ref2va"
        ),
        # Frame size is a project setting with a per-shot fallback, in the same
        # order the backend resolves it.
        "resolution": (shot.get("resolution") or defaults.get("resolution") or "") if external_model else (defaults.get("resolution") or shot.get("resolution") or ""),
        "frames": int(shot.get("frames") or 0),
        "steps": int(shot.get("steps") or 0),
        "seed": int(shot.get("seed") or 0),
        "draft": bool(defaults.get("draft")),
        "sketch": bool(defaults.get("draft")) and bool(defaults.get("sketch")),
    }
    if external_model:
        payload["modelProvider"] = "comfy-workflow"
        payload["modelProfileDigest"] = shot.get("modelProfileDigest") or defaults.get("modelProfileDigest")
    structured = {
        key: shot.get(key)
        for key in ("start", "action", "end", "camera", "locationId", "stateBefore", "stateAfter", "motionIntent")
        if isinstance(shot.get(key), str) and shot.get(key).strip()
        and not (key == "motionIntent" and shot[key] == "action")
    }
    prop_ids = shot.get("propIds")
    if isinstance(prop_ids, list) and prop_ids:
        structured["propIds"] = [value for value in prop_ids if isinstance(value, str)]
    asset_states = shot.get("assetStates")
    if isinstance(asset_states, dict) and asset_states:
        structured["assetStates"] = {key: value for key, value in asset_states.items()
                                      if isinstance(key, str) and isinstance(value, str)}
    if structured:
        payload["structuredContinuity"] = structured
    if shot.get("dialogueLanguage"):
        payload["dialogueLanguage"] = shot["dialogueLanguage"]
    bible = board.get("filmBible")
    if isinstance(bible, dict) and any(bible.values()):
        payload["filmBible"] = bible
    if payload["draft"]:
        # Drafts used to cap steps at 8 and now keep the shot's own count. A
        # shot at 8 steps or fewer renders exactly as before, so it keeps the
        # old profile (and its recorded fingerprint still matches); one set
        # higher was drafted at fewer steps than it now asks for.
        payload["draftProfile"] = (
            "384-long-edge-8-step-with-audio" if 0 < payload["steps"] <= 8
            else "384-long-edge-shot-steps-with-audio"
        )
    if payload["sketch"]:
        payload["sketchProfile"] = "min-frames-stretched-silent-pencil-sketch"
    if payload["effectiveModel"] == "ref2va":
        payload["ref2vaProfile"] = "ordered-reference-set-v4"
    # Both only present when set, so clips rendered before these options
    # existed keep their fingerprint; setting one changes the prompt, and says so.
    render_style = (board.get("renderStyle") or "").strip()
    if render_style:
        payload["renderStyle"] = render_style
    if no_music_in_shots(board):
        payload["noMusicInShots"] = True
    if legacy:
        payload["styleRefs"] = [_ref_key(r) for r in library]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def matches_pre_library_fix(recorded: str | None, shot: dict[str, Any],
                            board: dict[str, Any]) -> bool:
    """Was *recorded* made by the old formula, which also hashed the style
    library, from inputs that otherwise match this shot now?

    The library only grows by appending (adding a shot reference files it
    there), so the library as it was at render time is one of its prefixes;
    trying each covers "images were added since", which is exactly the case
    that marked unchanged shots CHANGED. A shot whose own inputs changed
    matches none of them and stays stale, as it should.
    """
    if not recorded:
        return False
    library = board.get("styleRefs") or []
    return any(recorded == render_fingerprint(shot, board, _library_refs=library[:k])
               for k in range(len(library), -1, -1))


def stale_reason(shot: dict[str, Any], board: dict[str, Any]) -> str:
    """Why this shot's clip is not a render of what the board now says.

    Empty string when the clip is current — or when there is no clip at all,
    since ``status`` already says that and calling it stale as well would
    report the same thing twice.
    """
    if not shot.get("outputs"):
        return ""
    if shot.get("locked"):
        # Locked is the user saying "this clip is final" -- a project setting
        # changed since does not make it a candidate for re-rendering.
        return ""
    recorded = shot.get("renderFingerprint")
    if not recorded:
        # Rendered by a version that kept no record of its inputs. It may well
        # be current, but nothing here can show that it is, and claiming so is
        # how a stale clip reaches the final cut.
        return "rendered before the board started tracking changes, so it cannot be shown to match"
    if recorded != render_fingerprint(shot, board):
        # Recorded before the style library stopped counting: current if
        # nothing but the library has moved since (load() also restamps
        # these, but an open tab's autosave can hand the old value back).
        if matches_pre_library_fix(recorded, shot, board):
            return ""
        return "the prompt, references or clip settings changed since this was rendered"
    return ""


def default_shot(defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    d = defaults or {}
    return {
        "id": new_id(),
        "title": "New shot",
        "prompt": "",
        "start": "",
        "action": "",
        "end": "",
        "camera": "",
        "locationId": "",
        "stateBefore": "",
        "stateAfter": "",
        "motionIntent": "action",
        "propIds": [],
        "assetStates": {},
        # Earlier wordings of the prompt, oldest first: {id, at (ms), rating
        # 0-3, values: {"prompt": ...}, renderedAt?}. Kept and recalled by the
        # editor only; never an input to a render or its fingerprint.
        "promptVersions": [],
        "soundNote": "",
        "characterIds": [],
        "dialogue": "",           # the spoken line, synthesised separately
        "dialogueSource": "recording", # use the approved Dialogue-window take
        "dialogueStyle": "",      # delivery notes for TTS, not spoken text
        "dialogueVoice": "",      # engine voice id, or "" for the default
        "speakerId": "",          # which cast member says it; "" = infer
        "dialogueAudioUrl": None, # the spoken line on its own, for preview
        "dubUrl": None,           # the clip with speech muxed over it
        # "mix": lay the spoken line over the clip's own generated audio, so
        # ambience (engine hum, wind) survives under it. "replace": the
        # dubbed clip carries only the spoken line — for when the video model
        # ignored the "no voice" prompt instruction and generated its own
        # mumbled dialogue, which "mix" would otherwise leave audible under
        # the real line as a second, overlapping voice.
        "dubMode": "mix",
        "startRef": None,
        "endRef": None,
        "referenceImages": [],
        "imageAnchors": [],
        # No per-shot "model" field -- the video engine is a project-wide
        # choice (board.defaults.model). LTX Start/End images are anchors.
        # A chained start uses the prior shot's final rendered image.
        # See migrate()'s note on why this key must stay absent.
        "resolution": d.get("resolution", "384x256"),
        "frames": d.get("frames", 121),
        "steps": d.get("steps", 8),
        "seed": 0,
        "status": "draft",
        "reason": "",
        "progress": 0,
        "runtimeSeconds": None,
        "outputs": [],
        "validation": None,
        "thumb": None,
        "logUrl": None,
        "renderedAs": None,   # "draft" | "final"
        # What the last render was of, so a board edit can be told from a
        # board that has simply not been rendered. See render_fingerprint.
        "renderFingerprint": None,
        # The words the stored dialogue.wav actually says. A line edited after
        # it was spoken must not be muxed from the old take.
        "dialogueSpokenText": "",
        "dialogueSpokenStyle": "",
        # Frozen: no edit, render, dub or still touches this shot, whatever
        # the project settings do, until it is unlocked. Only the lock
        # endpoint changes it (see app.py _keep_locked_shots).
        "locked": False,
    }


def default_character(name: str = "", description: str = "") -> dict[str, Any]:
    """One member of the cast.

    ``name`` and ``description`` are required — the name is how a shot prompt
    refers to them, and the description is what actually conditions the model.
    An image supplies identity to Qwen character cards and scene composition.
    LTX receives complete scene frames as anchors; voice clips belong to the
    separate speech recording path.
    """
    return {
        "id": new_id("c"),
        "name": name,
        "description": description,
        "image": None,   # {path, url, label}
        "voice": None,   # {path, url, label}
        # What the reference clip says. Qwen3-TTS's voice cloning conditions on
        # the transcript as well as the audio, so a clip alone is not enough;
        # the service can derive this itself via /transcribe.
        "voiceText": "",
    }


def default_board(name: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "name": name or "Untitled storyboard",
        "sceneDescription": "",
        "renderStyle": "",
        "soundscape": "",
        "filmBible": {"acts": [], "locations": [], "props": []},
        "characters": [],
        "styleRefs": [],
        "defaults": {
            "model": "ltx-2.5",
            "resolution": "384x256",
            "frames": 121,
            "steps": 8,
            "draft": False,
            "sketch": False,
            "stillsSize": "small",
            "stillsStyle": "global",
            "stillsEngine": "qwen-image-2.1-turbo",
            "stillsSteps": 0,
            "stillsSeed": 0,
            "tts": "none",
            "llm": "",       # "" means: use the server's default service
            "adSpeakerId": "",   # cast member whose voice the AD borrows to speak
        },
        "shots": [],
        "finalVideo": None,
        "outputMuted": False,
        "createdAt": time.time(),
        "updatedAt": time.time(),
    }


@dataclass
class Store:
    """Project folders directly under the selected ``data_dir``.

    Two roots, deliberately separate:

    ``workspace``
        Where vpipe is launched from. Not ours — vpipe resolves ``models/``
        and its LMDB model registry relative to it, so it is dictated by
        where the models were prepared. Read only, for offering existing
        renders to pick from.

    ``data_dir``
        Where the storyboards and their uploads live. Ours entirely, and by
        default the workspace so nothing moves for an existing install — but
        it can be anywhere, which is the point: a storyboard and its
        references are the user's documents and should not have to live inside
        another tool's runtime directory to be usable.
    """

    workspace: Path
    data_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.data_dir is None:
            self.data_dir = self.workspace

    @property
    def root(self) -> Path:
        return self.data_dir

    # -- locations ------------------------------------------------------- #

    def project_dir(self, slug: str) -> Path:
        safe = slugify(slug)
        return self.root / safe

    def board_path(self, slug: str) -> Path:
        return self.project_dir(slug) / BOARD_FILE

    def refs_dir(self, slug: str) -> Path:
        return self.project_dir(slug) / "refs"

    def image_manifest_path(self, slug: str) -> Path:
        """Durable direct-image history for a project.

        It deliberately lives beside (rather than inside) ``storyboard.json``:
        page autosaves can therefore never replace an image recipe or result.
        """
        return self.project_dir(slug) / IMAGE_MANIFEST_FILE

    def image_history(self, slug: str) -> list[dict[str, Any]]:
        path = self.image_manifest_path(slug)
        if not path.is_file():
            return []
        try:
            value = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        records = value if isinstance(value, list) else value.get("images", []) if isinstance(value, dict) else []
        return [dict(item) for item in records if isinstance(item, dict)]

    def save_image_record(self, slug: str, record: dict[str, Any]) -> dict[str, Any]:
        """Append/update one immutable image result atomically."""
        d = self.project_dir(slug)
        d.mkdir(parents=True, exist_ok=True)
        records = self.image_history(slug)
        rid = record.get("id")
        replaced = False
        for i, old in enumerate(records):
            if rid and old.get("id") == rid:
                records[i] = dict(record)
                replaced = True
                break
        if not replaced:
            records.append(dict(record))
        path = self.image_manifest_path(slug)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(records, indent=2) + "\n")
        tmp.replace(path)
        return dict(record)

    def update_image_records(self, slug: str, updates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        records = self.image_history(slug)
        changed = False
        for i, old in enumerate(records):
            if old.get("id") in updates:
                records[i] = {**old, **updates[old["id"]]}
                changed = True
        if changed:
            path = self.image_manifest_path(slug)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(records, indent=2) + "\n")
            tmp.replace(path)
        return records

    def locked_dirs(self, slug: str, board: dict[str, Any]) -> dict[str, str]:
        """Shot folders holding a locked shot's clip -> that shot's id.

        Shot folders are numbered by position, so a shot rendered at a
        position a locked shot once held would write over its clip.
        """
        out: dict[str, str] = {}
        for i, shot in enumerate(board.get("shots") or []):
            if not shot.get("locked"):
                continue
            out[self.shot_rel_dir(slug, i + 1)] = shot["id"]
            for url in shot.get("outputs") or []:
                if isinstance(url, str) and url.startswith("/media/"):
                    out[str(Path(url[len("/media/"):]).parent)] = shot["id"]
        return out

    def shot_rel_dir(self, slug: str, index: int) -> str:
        """A shot's directory, relative to ``data_dir``.

        This is the form stored in a board and served under ``/media/``. It
        stays relative so a board keeps working when the data directory moves,
        which is what makes a project folder portable.
        """
        return f"{slugify(slug)}/shots/{index:02d}"

    # -- listing --------------------------------------------------------- #

    def list_boards(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.root.exists():
            return out
        for d in sorted(self.root.iterdir()):
            bp = d / BOARD_FILE
            if not bp.is_file():
                continue
            try:
                board = json.loads(bp.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            shots = board.get("shots") or []
            out.append(
                {
                    "slug": d.name,
                    "name": board.get("name", d.name),
                    "shots": len(shots),
                    # What the folder actually holds, so two boards with the
                    # same name are distinguishable: an imported copy carries
                    # the shot list but none of the renders, and picking the
                    # wrong one is how you lose track of your work.
                    "rendered": sum(1 for sh in shots if sh.get("outputs")),
                    "configPath": str(bp),
                    "updatedAt": board.get("updatedAt") or bp.stat().st_mtime,
                }
            )
        out.sort(key=lambda b: b["updatedAt"], reverse=True)
        return out

    #: What the picker can offer, by kind. Audio is here because a voice clip
    #: already uploaded to a project was otherwise unreachable: the only way to
    #: attach one was to upload it again from the filesystem, so a clip sitting
    #: in a project's refs/ could not be linked to a character at all.
    LIBRARY_EXTS = {
        "image": {".png", ".jpg", ".jpeg", ".webp"},
        "audio": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"},
    }

    #: Folders inside a project that hold generated work, not material to pick
    #: from: every auto-refine attempt keeps eight review stills and every seed
    #: comparison keeps its takes, and left in they bury the references.
    LIBRARY_SKIP_DIRS = {"refine-runs", "seed-takes", "image-work"}

    def library(self, limit: int = 300, kind: str = "image") -> list[dict[str, Any]]:
        """Files already in the projects tree, for picking without re-uploading.

        Covers uploaded refs, loose files dropped into a project folder by
        hand, and rendered stills. Per-frame directories are skipped
        deliberately: a handful of clips is thousands of PNGs, and chaining to
        a previous shot's last frame is already a dedicated control rather
        than something you hunt for in a grid. So are the working folders of
        auto-refine and seed comparison (``LIBRARY_SKIP_DIRS``).
        """
        exts = self.LIBRARY_EXTS.get(kind) or self.LIBRARY_EXTS["image"]
        usages = self.media_usages()
        delete_index = self._library_delete_index() if kind == "image" else {}
        out: list[dict[str, Any]] = []
        if not self.root.exists():
            return out
        for path in sorted(self.root.rglob("*")):
            if len(out) >= limit:
                break
            if not path.is_file() or path.suffix.lower() not in exts:
                continue
            # Any per-frame directory, however it is named — "frames",
            # "frames-h3", "frames-ref" all exist in practice, and each holds
            # a whole clip's worth of PNGs that would bury everything else.
            parts = path.relative_to(self.root).parts
            if any(part.startswith("frames") for part in parts[:-1]):
                continue
            if any(part in self.LIBRARY_SKIP_DIRS for part in parts[1:-1]):
                continue
            rel = path.relative_to(self.data_dir)
            rel_name = str(rel).replace("\\", "/")
            blockers, owners = delete_index.get(rel_name, (usages.get(rel_name, []), []))
            uses = blockers if kind == "image" else usages.get(rel_name, [])
            project = path.relative_to(self.root).parts[0]
            out.append(
                {
                    "path": rel_name,
                    "url": "/media/" + rel_name,
                    "label": path.name,
                    "project": project,
                    "kind": kind,
                    "used": bool(uses),
                    "uses": uses,
                    "canDelete": not blockers,
                    "deleteBlockedReason": ("Still used: " + "; ".join(blockers)) if blockers else "",
                    "digest": _file_digest(path) if kind == "image" else "",
                    "bytes": path.stat().st_size,
                    "modifiedAt": path.stat().st_mtime,
                }
            )
        out.sort(key=lambda i: i["modifiedAt"], reverse=True)
        return out

    def used_media_paths(self) -> set[str]:
        """Media paths still referenced by any storyboard."""
        return set(self.media_usages())

    def media_usages(self) -> dict[str, list[str]]:
        """Media paths and human-readable places that still reference them."""
        out: dict[str, list[str]] = {}
        if not self.root.exists():
            return out
        for bp in self.root.glob(f"*/{BOARD_FILE}"):
            try:
                board = json.loads(bp.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            project = (board.get("name") or bp.parent.name).strip()
            for rel, labels in _collect_media_usages(board).items():
                bucket = out.setdefault(rel, [])
                for label in labels:
                    full = f"{project} - {label}"
                    if full not in bucket:
                        bucket.append(full)
        # Standalone Images results are server-owned and are not copied into
        # mutable storyboard.json. Scan manifests independently so a deleted
        # board (whose media folder is retained) still protects its files.
        for manifest in self.root.glob(f"*/{IMAGE_MANIFEST_FILE}"):
            try:
                records = json.loads(manifest.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(records, dict):
                records = records.get("images", [])
            project = manifest.parent.name
            for record in records if isinstance(records, list) else []:
                if not isinstance(record, dict):
                    continue
                if record.get("status") == "deleted":
                    continue
                image_id = record.get("id") or "image"
                _add_usage(out, record.get("ref"), f"{project} - image {image_id}")
                for i, ref in enumerate(record.get("references") or [], 1):
                    _add_usage(out, ref, f"{project} - image {image_id} reference {i}")
        return out

    def _library_delete_index(self) -> dict[str, tuple[list[str], list[dict[str, Any]]]]:
        index: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
        for bp in self.root.glob(f"*/{BOARD_FILE}"):
            try:
                board = json.loads(bp.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            project = (board.get("name") or bp.parent.name).strip()
            for rel, labels in _collect_media_usages(board).items():
                blockers, owners = index.setdefault(rel, ([], []))
                for label in labels:
                    value = f"{project} - {label}"
                    if value not in blockers:
                        blockers.append(value)
        for manifest in self.root.glob(f"*/{IMAGE_MANIFEST_FILE}"):
            try:
                records = json.loads(manifest.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(records, dict):
                records = records.get("images", [])
            for record in records if isinstance(records, list) else []:
                if not isinstance(record, dict) or record.get("status") == "deleted":
                    continue
                image_id = record.get("id") or "image"
                output = _ref_path(record.get("ref"))
                if output:
                    index.setdefault(output, ([], []))[1].append({"manifest": manifest, "record": record})
                for i, ref in enumerate(record.get("references") or [], 1):
                    rel = _ref_path(ref)
                    if rel:
                        blockers = index.setdefault(rel, ([], []))[0]
                        blockers.append(f"{manifest.parent.name} - image {image_id} reference {i}")
        return {rel: (sorted(set(blockers)), owners) for rel, (blockers, owners) in index.items()}

    def delete_library_item(self, rel_path: str, *, kind: str = "image") -> dict[str, Any]:
        """Remove a library file, tombstoning owned image results."""
        exts = self.LIBRARY_EXTS.get(kind) or self.LIBRARY_EXTS["image"]
        data_dir = self.data_dir.resolve()
        root = self.root.resolve()
        path = (data_dir / rel_path).resolve()
        path.relative_to(data_dir)
        path.relative_to(root)
        if not path.is_file():
            raise FileNotFoundError(f"no such file: {rel_path}")
        if path.suffix.lower() not in exts:
            raise ValueError(f"not a {kind} library file: {rel_path}")

        rel_name = str(path.relative_to(data_dir)).replace("\\", "/")
        delete_index = self._library_delete_index() if kind == "image" else {}
        if kind == "image":
            blockers, owners = delete_index.get(rel_name, ([], []))
            if rel_name not in delete_index:
                blockers = self.media_usages().get(rel_name, [])
        else:
            blockers, owners = self.media_usages().get(rel_name, []), []
        if blockers:
            raise ValueError("that file is still used: " + "; ".join(blockers))

        temp_path = path.with_name(f".{path.name}.delete-{uuid.uuid4().hex}")
        snapshots: dict[Path, bytes] = {}
        for owner in owners:
            manifest = owner["manifest"]
            if manifest not in snapshots:
                snapshots[manifest] = manifest.read_bytes()
        try:
            path.rename(temp_path)
            for owner in owners:
                record = dict(owner["record"])
                record["status"] = "deleted"
                record["deletedAt"] = time.time()
                record["deletedPath"] = rel_name
                record["ref"] = None
                record["path"] = None
                record["url"] = None
                record["recipeAvailable"] = False
                manifest = owner["manifest"]
                self.save_image_record(manifest.parent.name, record)
            temp_path.unlink()
        except Exception:
            for manifest, content in snapshots.items():
                try:
                    manifest.write_bytes(content)
                except OSError:
                    pass
            if temp_path.exists() and not path.exists():
                temp_path.rename(path)
            raise
        return {"ok": True, "path": rel_name, "deletedResults": len(owners)}

    # -- read / write ---------------------------------------------------- #

    def load(self, slug: str) -> dict[str, Any]:
        bp = self.board_path(slug)
        if not bp.is_file():
            raise FileNotFoundError(f"no storyboard at {bp}")
        board = json.loads(bp.read_text())
        before_migrate = json.dumps(board, sort_keys=True, separators=(",", ":"))
        board = _normalize_media_paths(board)
        board = self.migrate(board)
        migrated = json.dumps(board, sort_keys=True, separators=(",", ":")) != before_migrate
        # Self-heal on the way out: a board saved before rehoming existed,
        # or one hand-copied from another project's folder, can still point
        # at media that lives elsewhere. Every read is a chance to notice
        # and fix that before it's shown to anyone, not just the moment of
        # import. Same idea for a chain ref whose source already has frames
        # but has never had its preview resolved.
        healed = migrated
        healed = self._rehome_media(slug, board) or healed
        healed = self._resolve_chain_previews(slug, board) or healed
        # Older boards keep voice clips under their download names; save()
        # files them as <name>-voice.wav.
        healed = self.voices_need_filing(slug, board) or healed
        audio_refs = [(c.get("voice") or {}) for c in board.get("characters", [])]
        audio_refs.append((board.get("assembly") or {}).get("backgroundAudio") or {})
        for voice in audio_refs:
            path = self.data_dir / voice.get("path", "")
            if path.is_file() and path.resolve().is_relative_to(self.data_dir.resolve()):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if voice.get("contentHash") != digest:
                    voice["contentHash"] = digest
                    healed = True
        # Upgrade matching legacy fingerprints when first adding content hashes.
        # Once upgraded, later changes at the same path invalidate the take.
        legacy = copy.deepcopy(board)
        for character in legacy.get("characters", []):
            (character.get("voice") or {}).pop("contentHash", None)
        for scene in legacy.get("shots", []):
            for key in ("startRef", "endRef"):
                if isinstance(scene.get(key), dict):
                    scene[key].pop("contentHash", None)
        for scene, old in zip(board.get("shots", []), legacy.get("shots", [])):
            if scene.get("speechFingerprint") == speech_fingerprint(old, legacy):
                fingerprint = speech_fingerprint(scene, board)
                if scene["speechFingerprint"] != fingerprint:
                    scene["speechFingerprint"] = fingerprint
                    healed = True
            recorded = scene.get("renderFingerprint")
            if recorded and (recorded == render_fingerprint(old, legacy)
                             or matches_pre_library_fix(recorded, old, legacy)
                             or matches_pre_library_fix(recorded, scene, board)):
                fingerprint = render_fingerprint(scene, board)
                if recorded != fingerprint:
                    scene["renderFingerprint"] = fingerprint
                    healed = True
        from .ltx_anchors import anchor_error
        if healed and not any(anchor_error(shot) for shot in board.get("shots", [])):
            board = self.save(slug, board)
        return board

    def save(self, slug: str, board: dict[str, Any]) -> dict[str, Any]:
        board = self.migrate(board)
        from .ltx_anchors import clean_anchors
        for shot in board.get("shots", []):
            shot["imageAnchors"] = clean_anchors(shot.get("imageAnchors", []), int(shot["frames"]))
        self._canonical_voices(slug, board)
        board["updatedAt"] = time.time()
        d = self.project_dir(slug)
        d.mkdir(parents=True, exist_ok=True)
        (d / "refs").mkdir(exist_ok=True)
        bp = d / BOARD_FILE
        # write via a temp file so an interrupted save cannot truncate the board
        tmp = bp.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(board, indent=2) + "\n")
        tmp.replace(bp)
        return board

    # -- one voice file per character ------------------------------------- #

    def voice_stems(self, board: dict[str, Any]) -> dict[str, str]:
        """Character id -> the file stem their voice is kept under.

        ``<name>-voice``, with the id added only if two cast members share a
        name, so one can never overwrite the other's voice.
        """
        stems: dict[str, str] = {}
        for c in board.get("characters") or []:
            base = slugify(c.get("name") or "")
            if not base or base == "untitled" or not c.get("id"):
                continue
            stem = f"{base}-voice"
            if stem in stems.values():
                stem = f"{base}-{slugify(c['id'])}-voice"
            stems[c["id"]] = stem
        return stems

    def _canonical_voices(self, slug: str, board: dict[str, Any]) -> bool:
        """File each cast member's voice clip as ``refs/<name>-voice.wav``.

        Exactly one voice file per character: a new clip replaces the old
        one under the same name rather than piling up beside it, whatever
        its format (everything becomes WAV, which also drops cover art a
        clip can carry and be misread as an image). The clip it came from is
        removed if it was this project's own and nothing else uses it.

        Done on save, so picking a clip changes nothing until the Cast editor
        is saved. A render recorded from the same audio under the old name is
        re-stamped, so a rename alone never marks a scene CHANGED; a new clip
        does, as it should. Returns whether anything changed.
        """
        if not shutil.which("ffmpeg"):
            return False
        refs = self.refs_dir(slug)
        before = copy.deepcopy(board)
        replaced: list[Path] = []
        changed = False
        for c in board.get("characters") or []:
            voice = c.get("voice")
            stem = self.voice_stems(board).get(c.get("id"))
            if not (isinstance(voice, dict) and voice.get("path") and stem):
                continue
            dest = refs / f"{stem}.wav"
            rel = str(dest.relative_to(self.data_dir)).replace("\\", "/")
            src = self.data_dir / voice["path"]
            if voice["path"] == rel and dest.is_file():
                continue
            if src.is_file():
                refs.mkdir(parents=True, exist_ok=True)
                tmp = refs / f".{stem}.tmp.wav"
                done = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(src),
                     "-vn", "-map_metadata", "-1", "-c:a", "pcm_s16le", str(tmp)],
                    capture_output=True, text=True,
                )
                if done.returncode != 0 or not tmp.is_file():
                    tmp.unlink(missing_ok=True)
                    continue   # unreadable clip: leave it as it was
                tmp.replace(dest)
                if src.resolve() != dest.resolve():
                    replaced.append(src)
            elif not dest.is_file():
                continue       # nothing to file, and nothing filed yet
            # (src gone but the named file exists: an old tab still holding
            # the pre-rename path -- point it at the named file.)
            digest = _file_digest(dest)
            c["voice"] = {
                **voice, "kind": "upload", "path": rel, "label": dest.name,
                "url": f"/media/{rel}?v={digest[:12]}", "contentHash": digest,
                "originalName": voice.get("originalName") or voice.get("label") or src.name,
            }
            changed = True
        if not changed:
            return False

        # Same audio under a new name is the same render.
        for shot, old in zip(board.get("shots") or [], before.get("shots") or []):
            if shot.get("renderFingerprint") and \
                    shot["renderFingerprint"] == render_fingerprint(old, before):
                shot["renderFingerprint"] = render_fingerprint(shot, board)
            if shot.get("speechFingerprint") and \
                    shot["speechFingerprint"] == speech_fingerprint(old, before):
                shot["speechFingerprint"] = speech_fingerprint(shot, board)

        # Drop the clips that were replaced, if they were this project's own
        # and no board points at them -- this one, or another project's, which
        # can link straight into this folder (an older board did).
        if replaced:
            still_used = [json.dumps(board)] + [
                bp.read_text(errors="replace")
                for bp in self.root.glob(f"*/{BOARD_FILE}")
                if bp.parent.resolve() != self.project_dir(slug).resolve()
            ]
        for src in replaced:
            try:
                src.resolve().relative_to(refs.resolve())
            except ValueError:
                continue
            rel_src = str(src.relative_to(self.data_dir)).replace("\\", "/")
            if not any(rel_src in text for text in still_used):
                src.unlink(missing_ok=True)
        return True

    def voices_need_filing(self, slug: str, board: dict[str, Any]) -> bool:
        stems = self.voice_stems(board)
        refs = self.refs_dir(slug)

        def filed(stem: str) -> str:
            return str((refs / f"{stem}.wav").relative_to(self.data_dir)).replace("\\", "/")

        return any(
            isinstance(c.get("voice"), dict) and c["voice"].get("path") and c.get("id") in stems
            and c["voice"]["path"] != filed(stems[c["id"]])
            for c in board.get("characters") or []
        )

    def create(self, name: str) -> tuple[str, dict[str, Any]]:
        slug = slugify(name)
        base, n = slug, 2
        while self.board_path(slug).exists():
            slug = f"{base}-{n}"
            n += 1
        board = default_board(name)
        self.save(slug, board)
        return slug, board

    def rename(self, slug: str, new_name: str) -> tuple[str, dict[str, Any]]:
        """Rename a project: its display name **and** its folder.

        The folder is the slug, and the slug is baked into every path the
        board has recorded — thumbnails, run logs, outputs, character
        portraits, style references. So renaming the display name alone would
        leave the folder on disk saying something else, and moving the folder
        alone would break every one of those paths. This does both, and
        rewrites the paths to match.

        The rewrite walks the whole board rather than naming the fields it
        knows about, because the fields holding paths have grown over time and
        a rename that silently missed one would cost the user a thumbnail or a
        cast portrait with nothing to explain why.

        Returns the new slug, which the caller must use from here on.
        """
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("a project needs a name")

        old_slug = slugify(slug)
        board = self.load(old_slug)          # raises if there is no such board

        new_slug = slugify(new_name)
        if new_slug != old_slug:
            base, n = new_slug, 2
            while self.project_dir(new_slug).exists():
                new_slug = f"{base}-{n}"
                n += 1

            # Move first: if this fails, nothing has been changed at all.
            self.project_dir(old_slug).rename(self.project_dir(new_slug))
            board = _rewrite_slug(board, old_slug, new_slug)
            # Direct-image history is a separate server-owned manifest; keep
            # its media refs valid when the project folder moves.
            manifest = self.image_manifest_path(new_slug)
            if manifest.is_file():
                try:
                    records = _rewrite_slug(json.loads(manifest.read_text()), old_slug, new_slug)
                    tmp = manifest.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(records, indent=2) + "\n")
                    tmp.replace(manifest)
                except (OSError, json.JSONDecodeError):
                    pass

        board["name"] = new_name
        board = self.save(new_slug, board)
        return new_slug, board

    def delete(self, slug: str, *, keep_outputs: bool = True) -> None:
        d = self.project_dir(slug)
        if not d.exists():
            return
        if d.is_symlink():
            raise ValueError("cannot delete a symlinked project")
        root = self.root.resolve()
        target = d.resolve()
        target.relative_to(root)
        if target == root:
            raise ValueError("invalid project path")
        if not keep_outputs:
            prefix = f"{slugify(slug)}/"
            blockers: list[str] = []
            for other in self.root.glob(f"*/{BOARD_FILE}"):
                if other.parent.name == slugify(slug):
                    continue
                try:
                    board = json.loads(other.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                for rel, labels in _collect_media_usages(board).items():
                    if rel.startswith(prefix):
                        blockers.extend(f"{other.parent.name} - {label}" for label in labels)
            for manifest in self.root.glob(f"*/{IMAGE_MANIFEST_FILE}"):
                if manifest.parent.name == slugify(slug):
                    continue
                for record in self.image_history(manifest.parent.name):
                    if record.get("status") == "deleted":
                        continue
                    refs = [record.get("ref"), *(record.get("references") or [])]
                    if any(_ref_path(ref).startswith(prefix) for ref in refs):
                        blockers.append(f"{manifest.parent.name} - image {record.get('id') or 'image'}")
            if blockers:
                raise ValueError("project media is used by another project: " + "; ".join(sorted(set(blockers))))
        if keep_outputs:
            # Only remove the board; generated media can be expensive to
            # recreate, so deleting a board never throws away renders.
            self.board_path(slug).unlink(missing_ok=True)
            # If nothing was left worth keeping, do not leave an empty shell
            # of a folder behind either. "Nothing" means literally no files
            # anywhere under it — anything at all, and the folder stays.
            try:
                if not any(p.is_file() for p in d.rglob("*")):
                    shutil.rmtree(d)
            except OSError:
                pass
        else:
            shutil.rmtree(d)

    def adopt(self, slug: str, rel_path: str, name: str = "") -> dict[str, Any]:
        """Copy an existing file from the projects tree into this project's ``refs/``.

        Picking an image from another project would otherwise leave this board
        pointing into that project's folder: deleting the other project breaks
        this one, and an exported board refers to a file its own folder does
        not contain. Copying keeps the promise the layout is built on — a
        project folder holds everything that project needs.

        *name* (a file stem, e.g. "alex-voice") files the copy under that
        name instead, even from this project's own refs/ — the original stays
        where it is, since something else may use it. An identical file
        already under that name is reused rather than copied again.
        """
        src = (self.data_dir / rel_path).resolve()
        # never let a crafted path reach outside the data directory
        src.relative_to(self.data_dir.resolve())
        if not src.is_file():
            raise FileNotFoundError(f"no such file: {rel_path}")

        refs = self.refs_dir(slug)
        refs.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", name or "")[:48].strip("._")
        if stem:
            dest = refs / f"{stem}{src.suffix.lower()}"
            n = 2
            while dest.exists() and dest.resolve() != src and \
                    _file_digest(dest) != _file_digest(src):
                dest = refs / f"{stem}-{n}{src.suffix.lower()}"
                n += 1
            if not dest.exists():
                shutil.copy2(src, dest)
        elif (refs / src.name).resolve() == src:
            # already this project's own ref — nothing to copy
            dest = refs / src.name
        else:
            dest = refs / src.name
            n = 2
            while dest.exists() and _file_digest(dest) != _file_digest(src):
                dest = refs / f"{src.stem}-{n}{src.suffix}"
                n += 1
            if not dest.exists():
                shutil.copy2(src, dest)

        rel = dest.relative_to(self.data_dir)
        return {
            "path": str(rel).replace("\\", "/"),
            "url": "/media/" + str(rel).replace("\\", "/"),
            "label": dest.name,
        }

    def _rehome_ref(self, slug: str, ref: Any) -> Any:
        """``adopt`` a single reference into *slug*, in place.

        A ref that carries no real file (a chain reference, an empty slot),
        or that already lives under *slug*'s own ``refs/``, is returned
        completely untouched — same object, so a caller can tell by identity
        whether anything needed fixing at all. Only a path pointing at some
        other project's folder is actually copied in. One whose file no
        longer exists under ``data_dir`` at all — an import from another
        machine — is dropped rather than kept pointing at a path this
        project does not have, which would just move the "points outside
        the project" bug from a resolvable case to an unresolvable one.
        """
        rel = _ref_path(ref)
        if not rel or rel.startswith(f"{slug}/"):
            return ref
        try:
            adopted = self.adopt(slug, rel)
        except FileNotFoundError:
            return None
        return {**ref, **adopted} if isinstance(ref, dict) else adopted

    def _resolve_chain_previews(self, slug: str, board: dict[str, Any]) -> bool:
        """Point every chain ref at its source shot's actual last frame.

        The orchestrator also does this (``Orchestrator._resolve_chain``),
        but only right before *this* shot next renders — so a board whose
        source shot already has frames, rendered before that shot ever ran
        or before this preview existed at all, would otherwise show the
        Frame anchors panel's "⛓ chained" placeholder forever, with no
        picture, despite the frame already sitting on disk. Every load is a
        chance to catch it up, the same way ``_rehome_media`` self-heals
        stale references.
        """
        changed = False
        shots = board.get("shots") or []
        index_by_id = {s.get("id"): i for i, s in enumerate(shots)}
        for shot in shots:
            for key in ("startRef", "endRef"):
                ref = shot.get(key)
                if not isinstance(ref, dict) or ref.get("kind") != "chain":
                    continue
                src_idx = index_by_id.get(ref.get("from"))
                if src_idx is None:
                    continue
                frames_dir = (
                    self.data_dir / self.shot_rel_dir(slug, src_idx + 1) / "frames"
                )
                frame = last_saved_frame(
                    frames_dir, int(shots[src_idx].get("frames") or 0)
                )
                if frame is None:
                    continue
                resolved = str(frame.relative_to(self.data_dir)).replace("\\", "/")
                content_hash = hashlib.sha256(frame.read_bytes()).hexdigest()
                if ref.get("contentHash") != content_hash:
                    ref["contentHash"] = content_hash
                    changed = True
                if ref.get("resolved") != resolved:
                    ref["resolved"] = resolved
                    changed = True
        return changed

    def _rehome_media(self, slug: str, board: dict[str, Any]) -> bool:
        """Copy every reference image a board points at into *slug*'s own refs/, in place.

        A board can carry ``styleRefs``, cast portraits and shot references
        whose ``path``/``url`` point into a *different* project's folder —
        from an import, a hand-copied project directory, or a board edited
        outside the app. Left alone, this project silently depends on that
        other project's folder: deleting or renaming it breaks references
        here, and two projects end up appearing to share what look like each
        other's style references or cast portraits. This gives every
        reference in the board the same treatment ``adopt`` already gives a
        single picked image, and reports whether anything actually moved so
        the caller knows whether the board needs re-saving.
        """
        slug = slugify(slug)
        changed = False

        def rehome_list(refs: list[Any]) -> list[Any]:
            nonlocal changed
            out = []
            for ref in refs:
                new = self._rehome_ref(slug, ref)
                if new is not ref:
                    changed = True
                if new is not None:
                    out.append(new)
            return out

        board["styleRefs"] = rehome_list(board.get("styleRefs") or [])
        for ch in board.get("characters") or []:
            if ch.get("image"):
                new = self._rehome_ref(slug, ch["image"])
                changed = changed or new is not ch["image"]
                ch["image"] = new
        for shot in board.get("shots") or []:
            for key in ("startRef", "endRef"):
                if shot.get(key):
                    new = self._rehome_ref(slug, shot[key])
                    changed = changed or new is not shot[key]
                    shot[key] = new
            if isinstance(shot.get("referenceImages"), list):
                shot["referenceImages"] = rehome_list(shot["referenceImages"])
            from .ltx_anchors import image_path, readable_anchors
            for anchor in readable_anchors(shot):
                ref = anchor.get("ref")
                new = self._rehome_ref(slug, ref)
                if new is not None and new is not ref:
                    anchor["ref"] = new
                    changed = True
                try:
                    content_hash = _file_digest(image_path(anchor.get("ref"), self.data_dir))
                    if anchor["ref"].get("contentHash") != content_hash:
                        anchor["ref"]["contentHash"] = content_hash
                        changed = True
                except (ValueError, OSError):
                    pass  # Keep broken choices visible; render validation rejects them.
        bible = board.get("filmBible") if isinstance(board.get("filmBible"), dict) else {}
        for group in ("locations", "props"):
            for entry in bible.get(group) or []:
                if not isinstance(entry, dict) or not entry.get("referenceImage"):
                    continue
                new = self._rehome_ref(slug, entry["referenceImage"])
                if new is not entry["referenceImage"]:
                    entry["referenceImage"] = new
                    changed = True
        return changed

    # -- import / export -------------------------------------------------- #

    def export(self, slug: str) -> str:
        """The board as a JSON string, suitable for downloading."""
        board = self.load(slug)
        board = dict(board)
        board.pop("_slug", None)
        return json.dumps(board, indent=2) + "\n"

    # Rendered output, left out of a folder export: every video file, and
    # the per-shot folders a render writes its frames and preview stills to.
    EXPORT_SKIP_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
    EXPORT_SKIP_DIRS = {"frames", "stills", "sketch-refs"}

    def export_zip(self, slug: str, out, *, include_video: bool = False) -> str:
        """Write the project folder as a ZIP to the binary file *out*.

        The default is the setup: everything a board is made of —
        storyboard.json, reference images and voice clips, prompts, logs and
        job files — but none of the rendered video, which is most of a
        project's size and can be rendered again. A rendered frame the board
        itself points at (a chained Start frame) is kept, since the board
        needs it to re-render. With *include_video* the rendered clips, the
        final video and the soundtrack come too (the per-frame preview dumps
        are still left out: they are regenerated and would dwarf the rest).
        Entries sit under ``<slug>/`` so the archive unpacks into a folder
        that can be dropped straight into a projects folder.

        Returns the download name: ``storyboard - <project name>.zip``, or
        ``storyboard - <project name> (with video).zip``.
        """
        import zipfile

        board = self.load(slug)
        folder = self.project_dir(slug)
        safe = folder.name
        root = folder.resolve()
        referenced: set[Path] = set()

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)
            elif isinstance(value, str) and value.startswith(safe + "/"):
                path = (self.root / value.split("?", 1)[0]).resolve()
                if path.is_file():
                    referenced.add(path)

        walk(board)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(folder.rglob("*")):
                if not path.is_file() or path.name == ".DS_Store":
                    continue
                rel = path.relative_to(folder)
                is_video = path.suffix.lower() in self.EXPORT_SKIP_EXTS
                if path.resolve() not in referenced and (
                    (is_video and not include_video)
                    or any(part in self.EXPORT_SKIP_DIRS for part in rel.parts[:-1])
                ):
                    continue
                # Video is already compressed: storing it is far quicker and no bigger.
                zf.write(path, f"{safe}/{rel.as_posix()}",
                         compress_type=zipfile.ZIP_STORED if is_video else zipfile.ZIP_DEFLATED)
        # Only what a filename cannot hold on macOS/Windows is replaced.
        name = re.sub(r'[\\/:*?"<>|]+', "-", (board.get("name") or safe)).strip() or safe
        return f"storyboard - {name}{' (with video)' if include_video else ''}.zip"

    def import_board(self, raw: str | dict, *, name: str | None = None) -> tuple[str, dict]:
        board = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if not isinstance(board.get("shots"), list):
            raise ValueError("not a storyboard: no 'shots' array")
        board = self.migrate(board)
        board["name"] = name or board.get("name") or "Imported storyboard"
        # An imported board's shots have not been rendered *here*, and their
        # recorded outputs point at another machine's folders. Reset run state
        # rather than show a green board with nothing behind it.
        for shot in board["shots"]:
            shot.update(
                status="draft", progress=0, runtimeSeconds=None,
                outputs=[], validation=None, thumb=None, logUrl=None,
                dubUrl=None,
            )
        slug = slugify(board["name"])
        base, n = slug, 2
        while self.board_path(slug).exists():
            slug = f"{base}-{n}"
            n += 1
        self._rehome_media(slug, board)
        self.save(slug, board)
        return slug, board

    def reconcile_startup(self) -> list[str]:
        """Demote run states that cannot still be true.

        A shot left "running" or "queued" when the process exited has no one
        watching it any more, so it would sit there forever claiming to be in
        progress. Called once at startup, before any render can be active —
        doing it in migrate() would corrupt the state of a live run, since the
        orchestrator re-reads the board between shots.
        """
        touched: list[str] = []
        for entry in self.list_boards():
            slug = entry["slug"]
            try:
                board = self.load(slug)
            except (OSError, ValueError):
                continue
            changed = False
            for shot in board.get("shots") or []:
                if shot.get("status") == "running":
                    shot["status"] = "interrupted"
                    changed = True
                    touched.append(f"{slug}/{shot.get('title') or shot['id']}")
                elif shot.get("status") == "queued":
                    shot["status"] = "draft"
                    changed = True
            if changed:
                self.save(slug, board)
        return touched

    # -- schema ----------------------------------------------------------- #

    def migrate(self, board: dict[str, Any]) -> dict[str, Any]:
        """Fill in anything a board is missing.

        Boards are hand-editable files that outlive the code that wrote them,
        so read defensively: never KeyError on a board written by an older
        version or edited by hand.
        """
        board.setdefault("schema", SCHEMA)
        board.setdefault("name", "Untitled storyboard")
        board.setdefault("sceneDescription", "")
        board.setdefault("renderStyle", "")
        board.setdefault("soundscape", "")
        board.setdefault("filmBible", {})
        if not isinstance(board.get("filmBible"), dict):
            board["filmBible"] = {}
        for group in ("acts", "locations", "props"):
            entries = board["filmBible"].get(group, [])
            if not isinstance(entries, list):
                board["filmBible"][group] = []
            else:
                clean_entries = []
                for entry in entries[:32]:
                    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                        continue
                    clean = {key: value.strip() for key, value in entry.items()
                             if isinstance(key, str) and isinstance(value, str)}
                    # Keep only the opaque local reference fields accepted by
                    # image_path; never persist arbitrary nested/external data.
                    ref = entry.get("referenceImage")
                    if isinstance(ref, dict):
                        clean_ref = {key: ref[key] for key in
                                     ("kind", "path", "url", "role", "tag", "label")
                                     if isinstance(ref.get(key), str)}
                        if clean_ref.get("path") or clean_ref.get("url"):
                            clean["referenceImage"] = clean_ref
                    if isinstance(entry.get("referenceState"), str):
                        clean["referenceState"] = entry["referenceState"].strip()[:100] or "base"
                    clean["id"] = entry["id"].strip()[:80]
                    if clean["id"]:
                        clean_entries.append(clean)
                board["filmBible"][group] = clean_entries
        board.setdefault("soundscapeInShots", True)
        board.setdefault("characters", [])
        board.setdefault("styleRefs", [])
        board.setdefault("createdAt", time.time())
        board.setdefault("updatedAt", time.time())
        # The assembled cut: {url, builtAt, parts, missing, seconds}. None
        # until the shots have been concatenated at least once.
        board.setdefault("finalVideo", None)
        # Output playback preference. It belongs to the project so switching
        # boards does not unexpectedly turn sound back on (or off).
        board.setdefault("outputMuted", False)

        defaults = board.setdefault("defaults", {})
        previous_video_model = defaults.get("model")
        def external_binding(fields):
            if fields.get("modelProvider") != "comfy-workflow":return False
            if not isinstance(fields.get("model"),str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',fields["model"]) or not isinstance(fields.get("modelProfileDigest"),str) or not re.fullmatch(r'[a-f0-9]{64}',fields["modelProfileDigest"]):
                raise ValueError("Invalid explicit Comfy model/profile binding")
            return True
        external_defaults = external_binding(defaults)
        retired_video_model = previous_video_model if not external_defaults and previous_video_model not in (None, "", "ltx-2.5") else None
        # Keep old takes and their engine metadata; changing the requested
        # model makes their old fingerprint stale instead of relabelling them.
        if not external_defaults:defaults["model"] = "ltx-2.5"
        defaults.setdefault("resolution", "384x256")
        defaults.setdefault("frames", 97)  # Preserve the legacy fallback on existing boards.
        defaults.setdefault("steps", 8)
        defaults.setdefault("draft", False)
        defaults.setdefault("sketch", False)
        # Briefly a toggle; drafts now always keep each shot's step count.
        defaults.pop("draftFullSteps", None)
        defaults.setdefault("stillsSize", "small")
        defaults.setdefault("stillsStyle", "global")
        defaults["stillsEngine"] = "qwen-image-2.1-turbo"
        defaults.setdefault("stillsSteps", 0)
        defaults.setdefault("stillsSeed", 0)
        defaults.setdefault("tts", "none")
        defaults.setdefault("llm", "")
        # Which cast member's reference voice the Storyboard AD borrows when
        # reading its replies aloud — "" means the TTS engine's own default
        # voice, since the AD is not itself a character with a recorded clip.
        defaults.setdefault("adSpeakerId", "")

        for ch in board.get("characters") or []:
            ch.setdefault("id", new_id("c"))
            ch.setdefault("name", "")
            ch.setdefault("description", "")
            ch.setdefault("image", None)
            ch.setdefault("voice", None)
            ch.setdefault("voiceText", "")

        shots = board.setdefault("shots", [])
        seen: set[str] = set()
        for shot in shots:
            shot.setdefault("id", new_id())
            while shot["id"] in seen:
                shot["id"] = new_id()
            seen.add(shot["id"])
            shot.setdefault("title", "Untitled shot")
            shot.setdefault("prompt", "")
            for key in ("start", "action", "end", "camera", "locationId", "stateBefore", "stateAfter"):
                if not isinstance(shot.get(key), str):
                    shot[key] = ""
            shot.setdefault("motionIntent", "action")
            if shot.get("motionIntent") not in ("action", "hold"):
                shot["motionIntent"] = "action"
            if not isinstance(shot.get("propIds"), list):
                shot["propIds"] = []
            else:
                shot["propIds"] = [value for value in shot["propIds"] if isinstance(value, str)]
            if not isinstance(shot.get("assetStates"), dict):
                shot["assetStates"] = {}
            else:
                shot["assetStates"] = {key: value for key, value in shot["assetStates"].items()
                                        if isinstance(key, str) and isinstance(value, str)}
            if not isinstance(shot.get("promptVersions"), list):
                shot["promptVersions"] = []
            shot.setdefault("soundNote", "")
            shot.setdefault("characterIds", [])
            shot.setdefault("speakerId", "")
            shot.setdefault("dialogueAudioUrl", None)
            shot.setdefault("dialogue", "")
            shot.setdefault("dialogueStyle", "")
            shot.setdefault("dialogueVoice", "")
            shot.setdefault("dubUrl", None)
            shot.setdefault("dubMode", "mix")
            # continuityRef is retired: it was always a chain-sourced "soft"
            # reference to the previous shot's last frame, and startRef (chain
            # or manual) now covers exactly that as an ordered Ref2VA
            # reference -- there is no second mechanism left for it to feed.
            # _effective_video_model has always hard-errored on a shot with
            # both set, so no real board should reach here with both -- but
            # read defensively anyway, since boards are hand-editable files
            # that outlive the code that wrote them.
            continuity = shot.pop("continuityRef", None)
            if isinstance(continuity, dict):
                continuity.pop("mode", None)
            if continuity and not shot.get("startRef"):
                shot["startRef"] = continuity
            shot.setdefault("startRef", None)
            shot.setdefault("endRef", None)
            shot.setdefault("referenceImages", [])
            shot.setdefault("imageAnchors", [])
            # Local/legacy models use the project-wide default. Explicit
            # workflow overrides carry their provider and profile digest;
            # unmarked old fields must not shadow the migrated local model.
            if retired_video_model and shot.get("outputs"):
                shot.setdefault("renderedWith", {"engine": "retired-h3", "model": retired_video_model,
                                                  "steps": shot.get("steps", 0)})
            # Explicit portable workflow bindings are per-shot overrides;
            # unmarked legacy fields retain the original LTX migration rule.
            if not external_binding(shot):shot.pop("model", None)
            shot.setdefault("resolution", defaults["resolution"])
            shot.setdefault("frames", defaults["frames"])
            shot.setdefault("steps", defaults["steps"])
            shot.setdefault("seed", 0)
            shot.setdefault("status", "draft")
            shot.setdefault("reason", "")
            shot.setdefault("progress", 0)
            shot.setdefault("runtimeSeconds", None)
            shot.setdefault("outputs", [])
            shot.setdefault("validation", None)
            shot.setdefault("thumb", None)
            shot.setdefault("logUrl", None)
            shot.setdefault("renderedAs", None)
            shot.setdefault("renderFingerprint", None)
            shot.setdefault("dialogueSpokenText", "")
            shot.setdefault("dialogueSpokenStyle", "")
        return board

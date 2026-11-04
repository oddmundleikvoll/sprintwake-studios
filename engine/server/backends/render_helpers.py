"""Shared helpers for the active LTX video and still-image backends.

This module intentionally contains no legacy video-engine or reference-list
logic.  LTX receives prose, cast descriptions and optional start/end anchors;
the still engines use the same prose and geometry helpers.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import FrameRule, ShotPaths
from ..dubbing import speaker_for
from ..length_estimate import FPS, dialogue_seconds
from ..soundtrack.board import no_music_in_shots

LTX_FRAME_RULE = FrameRule(kind="affine", step=8, offset=1, minimum=25,
                            note="LTX-2.5 uses 8k+1 frames at 24 fps.")
LTX_SIZE_ALIGN = 64
# No local LTX render has been used to qualify a resolution yet.
LTX_TESTED: list[str] = []
ASPECT_TABLE = {
    "21:9": ["1792x768", "1344x576", "1120x480"],
    "16:9": ["1344x768", "960x544", "832x480"],
    "4:3": ["1024x768", "768x576", "640x480"],
    "1:1": ["768x768", "576x576", "480x480"],
    "3:4": ["768x1024", "576x768", "480x640"],
    "9:16": ["768x1344", "544x960", "480x832"],
}
ALL_RESOLUTIONS = [r for group in ASPECT_TABLE.values() for r in group]
SKETCH_STYLE_PREFIX = (
    "STYLE: black-and-white hand-drawn pencil storyboard sketch — heavy, dark "
    "graphite contour lines on plain white paper, loose scratchy linework, "
    "flat monotone, absolutely no color, no photographic detail, no rendered lighting."
)


def _align_up(value: int, align: int) -> int:
    return value if align <= 1 else ((value + align - 1) // align) * align


def _draft_geometry(w: int, h: int, steps: int, align: int = 16) -> tuple[int, int, int]:
    scale = min(0.5, 384.0 / max(w, h))
    return (_align_up(max(align, round(w * scale)), align),
            _align_up(max(align, round(h * scale)), align),
            min(steps, 8) if steps > 0 else 8)


def _wh(res: str) -> tuple[int, int]:
    try:
        w, h = str(res).lower().split("x")
        return int(w), int(h)
    except Exception:
        return 960, 544


def _has_downstream_chain(shot: dict, project: dict) -> bool:
    shot_id = shot.get("id")
    return bool(shot_id and any(
        isinstance(other.get(key), dict)
        and other[key].get("kind") == "chain"
        and other[key].get("from") == shot_id
        for other in project.get("shots") or [] for key in ("startRef", "endRef")
    ))


def _ref_source(ref: Any, paths: ShotPaths) -> str | None:
    if not ref:
        return None
    rel = ref if isinstance(ref, str) else ref.get("resolved") or ref.get("path") or ""
    if not rel:
        return None
    return rel if Path(rel).is_absolute() else paths.pipe_path(rel)


def _shot_characters(shot: dict, project: dict) -> list[dict]:
    wanted = set(shot.get("characterIds") or [])
    return [c for c in project.get("characters") or [] if c.get("id") in wanted]


def _character_description(name: str, description: str) -> str:
    name, description = name.strip(), description.strip()
    if not description:
        return ""
    return description if description.lower().startswith(name.lower() + ":") else f"{name}: {description}" if name else description


def _film_bible_entry(project: dict, group: str, entry_id: str) -> dict:
    bible = project.get("filmBible") or {}
    for entry in bible.get(group) or []:
        if isinstance(entry, dict) and entry.get("id") == entry_id:
            return entry
    return {}


def _structured_beats(shot: dict, project: dict) -> list[str]:
    location_id = (shot.get("locationId") or "").strip()
    location = _film_bible_entry(project, "locations", location_id) if location_id else {}
    parts: list[str] = []
    if location_id:
        description = (location.get("description") or location.get("title") or "").strip()
        # IDs are authoritative in the plan, but are not useful conditioning
        # tokens for LTX.  Only expose the human-readable bible description.
        if description:
            parts.append(f"The scene takes place in {description}")
    prop_ids = shot.get("propIds") or []
    props = []
    for prop_id in prop_ids:
        entry = _film_bible_entry(project, "props", prop_id)
        description = (entry.get("description") or entry.get("title") or "").strip()
        if description:
            props.append(description)
    if props:
        parts.append("The recurring physical props are " + "; ".join(props))
    states = shot.get("assetStates") or {}
    if states:
        for asset_id, value in sorted(states.items()):
            value = str(value).strip()
            if not value or value.lower() in {"base", "unchanged"} or re.fullmatch(r"v\d+", value, re.I):
                continue
            entry = location if asset_id == location_id else _film_bible_entry(project, "props", asset_id)
            subject = (entry.get("title") or entry.get("description") or "").strip().rstrip(".")
            if subject:
                parts.append(f"{subject} is {value}")
    if (shot.get("camera") or "").strip():
        parts.append(shot["camera"].strip())
    beats = [shot.get(key, "").strip() for key in ("start", "action", "end")]
    before = (shot.get("stateBefore") or "").strip()
    after = (shot.get("stateAfter") or "").strip()
    if before and before != beats[0]:
        parts.append("At the beginning, " + before)
    parts.extend(value for value in beats[:2] if value)
    if beats[2]:
        parts.append("The action ends with " + beats[2])
    if after and after != beats[2]:
        parts.append("By the end, " + after)
    if shot.get("motionIntent") == "hold":
        parts.append("Keep the movement deliberately quiet and observational")
    return parts


def _speaker_name(shot: dict, project: dict) -> str:
    speaker = speaker_for(shot, project)
    return (speaker or {}).get("name", "The visible character").strip() or "The visible character"


def _dialogue_visual_cue(shot: dict, project: dict, clones_voice: bool=False, speaks_aloud: bool=False) -> str:
    line = ' '.join((shot.get('dialogue') or '').split())
    if not line:
        return ''
    speaker = _speaker_name(shot, project)
    if speaks_aloud:
        language = str(shot.get("dialogueLanguage") or "").strip()
        language = f" in {language}" if language else ""
        return f'{speaker} speaks the line aloud{language}, in a voice that fits the character and scene, saying exactly: "{line}"'
    return f'{speaker} speaks the line with natural jaw and lip movement: "{line}"'

def _voice_direction(shot: dict, project: dict, clones_voice: bool) -> str:
    style = ' '.join((shot.get('dialogueStyle') or '').split()).rstrip(' .')
    who = speaker_for(shot, project) or {}
    voice = ' '.join((who.get('voiceDescription') or '').split()).rstrip(' .')
    if voice and style:
        style = f'{voice}; in this scene: {style}'
    elif voice:
        style = voice
    if not style:
        return ''
    speaker = _speaker_name(shot, project)
    authority = ' How the line sounds comes only from this direction: any wording about the voice in the scene action above describes the action, not the delivery.'
    override = ''
    return f"Voice direction for {speaker}'s line: {style}.{authority}{override}"

def _no_dialogue(shot: dict, project: dict) -> str:
    text = "There is no dialogue in this clip: nobody speaks, with no spoken words, no voices, no murmuring and no vocal sounds of any kind, only the scene's sound effects and ambience."
    start = shot.get('startRef')
    if isinstance(start, dict) and start.get('kind') == 'chain':
        text += " The previous scene's dialogue has already ended: this clip opens with no speech at all."
    names = [(c.get('name') or '').strip() for c in _shot_characters(shot, project) if (c.get('name') or '').strip()]
    if names:
        who = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
        text += f" {who} {('is' if len(names) == 1 else 'are')} on screen but silent for the whole clip: mouth closed."
    return text

def _only_this_line(shot: dict, project: dict, clones_voice: bool) -> str:
    from ..length_estimate import FPS, dialogue_seconds
    speaker = _speaker_name(shot, project)
    text = f'{speaker} speaks only this one line, exactly once, and says nothing else: no extra words, no repeats, no ad-libbing, and no other voices.'
    start = shot.get('startRef')
    if isinstance(start, dict) and start.get('kind') == 'chain':
        text += f" The previous scene's dialogue has already ended: this clip opens with no speech at all, and nobody talks until {speaker} starts the line."
    speaker_obj = speaker_for(shot, project)
    silent = [(c.get('name') or '').strip() for c in _shot_characters(shot, project) if c is not speaker_obj and (c.get('name') or '').strip()]
    if silent:
        who = silent[0] if len(silent) == 1 else ', '.join(silent[:-1]) + ' and ' + silent[-1]
        text += f" {who} {('is' if len(silent) == 1 else 'are')} on screen but silent for the whole clip: mouth closed, no speech, no murmuring, no vocal sounds of any kind."
    clip = int(shot.get('frames') or 0) / FPS
    (speech, _) = dialogue_seconds(shot, None)
    if clip and speech:
        text += f" The line takes only about {speech:.0f} seconds of this {clip:.0f}-second clip; before and after it there is no speech at all, only the scene's sound effects and ambience." if clip - speech > 1.5 else " Apart from the line there is no speech, only the scene's sound effects and ambience."
    return text

def _sentence(text: str) -> str:
    text = text.strip()
    if text[-1:] in '.!?;:,':
        return text
    if len(text) >= 2 and text[-1] in '"”\'' and (text[-2] in '.!?;:,'):
        return text
    return text + '.'


def _resolved_prompt(shot: dict, project: dict, with_audio: bool = True, model: str = "") -> str:
    """Compose LTX prose while keeping recording and native speech separate."""
    automation = project.get("filmAutomation") or {}
    reference_film = automation.get("characterReferences", False)
    shared = (project.get("sceneDescription") or "").strip()
    # Film-from-brief stores the entire story in sceneDescription. Keep that
    # story in the planner; it must not compete with this shot's single action.
    full_beats = all(isinstance(shot.get(key), str) and shot[key].strip() for key in ("start", "action", "end"))
    parts = [] if reference_film and full_beats and shared == automation.get("description") else [shared]
    if project.get("renderStyle"):
        parts.append(str(project["renderStyle"]).strip())
    cast = _shot_characters(shot, project)
    if reference_film:
        parts.append("Keep each character's identity, body design, materials, colours and costume consistent with the image anchors. "
                     "The scene cast below includes off-screen characters; keep their identities distinct when they return. "
                     "Shot instructions specify framing and action, not a replacement character.")
        scene = shot.get("directorScene")
        wanted = {cid for card in project.get("shots", []) if scene is not None and card.get("directorScene") == scene
                  for cid in card.get("characterIds", [])}
        wanted.update(c.get("id") for c in cast)
        cast = [c for c in project.get("characters", []) if c.get("id") in wanted]
    else:
        parts.append("Shared scene and Cast details are defaults. Explicit shot instructions take priority for actions, setting, and appearance changes.")
    visible_ids = {c.get("id") for c in _shot_characters(shot, project)}
    visible = [c.get("name", "").strip() for c in cast if c.get("id") in visible_ids and c.get("name", "").strip()]
    offscreen = [c.get("name", "").strip() for c in cast if c.get("id") not in visible_ids and c.get("name", "").strip()]
    if visible:
        parts.append("Visible in this shot: " + ", ".join(visible))
    if offscreen:
        parts.append("The other established scene characters remain off screen and keep their identities for later shots: " + ", ".join(offscreen))
    for character in cast:
        text = _character_description(character.get("name") or "", character.get("description") or "")
        if text:
            parts.append(text)
    parts.extend(_structured_beats(shot, project))
    if reference_film and (shot.get("startRef") or {}).get("kind") == "chain":
        parts.append("Continue the actual preceding video tail and its character identities, positions and camera naturally.")
    elif shot.get("startRef"):
        parts.append("Use the supplied Start image as the opening composition; develop the action naturally from it.")
    if shot.get("endRef"):
        parts.append("Develop the action toward the supplied End image and closing composition.")
    beats_complete = all(isinstance(shot.get(key), str) and shot.get(key).strip() for key in ("start", "action", "end"))
    if not beats_complete:
        parts.append((shot.get("prompt") or "").strip())
    parts = [re.sub(r"@([A-Za-z0-9_-]+)", lambda m: m.group(1).replace("-", " ").replace("_", " "), part) for part in parts]
    line = (shot.get("dialogue") or "").strip()
    native = bool(line) and shot.get("dialogueSource") == "native"
    if with_audio:
        cue = _dialogue_visual_cue(shot, project, speaks_aloud=native)
        if cue:
            parts.append(cue)
        if project.get("soundscapeInShots", True):
            parts.append((project.get("soundscape") or "").strip())
        parts.append((shot.get("soundNote") or "").strip())
        if no_music_in_shots(project):
            parts.append("No music: sound effects and ambience only, the score is added separately.")
        if native:
            parts.append("Dialogue priority: the selected character must speak the specified line aloud in a voice that fits the character and scene; any 'no speech' instruction applies only to background or unrelated voices.")
            parts.append(_only_this_line(shot, project, False))
            parts.append(_voice_direction(shot, project, False))
        elif line:
            parts.append("Generated audio contains ambient sound only: no spoken words, no voice, and no intelligible dialogue; the voice line is dubbed separately.")
        else:
            parts.append(_no_dialogue(shot, project))
    return " ".join(_sentence(part) for part in parts if part)


def _stretch_clip(path: Path, factor: float, log: list[tuple[str, str]]) -> None:
    if not path.exists():
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.append(("WARN", "ffmpeg not found; sketch clip was not stretched"))
        return
    tmp = path.with_suffix(".stretch.mp4")
    try:
        subprocess.run([ffmpeg, "-y", "-i", str(path), "-vf", f"setpts={factor:.6f}*PTS", "-r", "24", "-an", str(tmp)], check=True, capture_output=True, text=True, timeout=120)
        tmp.replace(path)
        log.append(("INFO", f"sketch clip held {factor:.2f}x"))
    except Exception as exc:
        log.append(("WARN", f"could not stretch sketch clip: {exc}"))
        tmp.unlink(missing_ok=True)


_MODEL_GEOMETRY = {"ltx-2.5": (LTX_FRAME_RULE, LTX_SIZE_ALIGN)}


def estimate_render_seconds(shot: dict, project: dict, timings=None) -> float | None:
    if timings is None:
        return None
    defaults = project.get("defaults") or {}
    if (shot.get("model") or defaults.get("model")) in ("krea2-still", "qwen-image-2.1-turbo"):
        return None
    w, h = _wh(defaults.get("resolution") or shot.get("resolution") or "384x256")
    frames = LTX_FRAME_RULE.snap(int(shot.get("frames") or defaults.get("frames") or 97))
    steps = 8
    if defaults.get("draft"):
        w, h, _ = _draft_geometry(w, h, steps, LTX_SIZE_ALIGN)
        if defaults.get("sketch"):
            frames = LTX_FRAME_RULE.minimum
    model_key = getattr(timings, "model_key", lambda model: model)("ltx-2.5")
    return timings.estimate(model=model_key, width=_align_up(w, LTX_SIZE_ALIGN), height=_align_up(h, LTX_SIZE_ALIGN), frames=frames, steps=steps)

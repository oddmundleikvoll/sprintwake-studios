"""Canonical digest for user-authored storyboard fields."""
from __future__ import annotations

import hashlib
import json


def authoring_digest(board: dict) -> str:
    board_fields = ("name", "sceneDescription", "renderStyle", "soundscape", "soundtrackPrompt",
                    "defaults", "filmBible", "characters", "styleRefs", "ravenSettings")
    shot_fields = ("id", "title", "prompt", "start", "action", "end", "camera", "locationId",
                   "stateBefore", "stateAfter", "motionIntent", "propIds", "assetStates", "soundNote",
                   "characterIds", "dialogue", "dialogueSource", "dialogueStyle", "dialogueVoice",
                   "speakerId", "dubMode", "referenceImages", "imageAnchors", "resolution", "frames",
                   "steps", "seed", "startRef", "endRef", "directorScene", "ravenNotes")
    value = {key: board.get(key) for key in board_fields}
    value["shots"] = [{key: (shot.get(key) or None) if key == "ravenNotes" else shot.get(key)
                       for key in shot_fields} for shot in board.get("shots", [])]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

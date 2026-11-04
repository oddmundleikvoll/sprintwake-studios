"""Small pure state rules shared by the native studio and its tests."""
from __future__ import annotations

import hashlib
import json
import time


def authoring_digest(board):
    """Server progress and save timestamps do not invalidate an editing proposal."""
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


class PlaybackReceipt:
    """Credit continuous normal playback, never a seek straight to the end."""
    def __init__(self):
        self.previous = None
        self.watched = 0.0
        self.previous_wall = None

    def update(self, current, duration, playing):
        now = time.monotonic()
        if playing and self.previous is not None:
            delta = current - self.previous
            elapsed = now - self.previous_wall if self.previous_wall is not None else 0
            if 0 < delta <= elapsed + .25:
                self.watched += delta
        self.previous = current
        self.previous_wall = now
        return bool(duration > 0 and current >= duration - .15 and self.watched >= duration * .95)

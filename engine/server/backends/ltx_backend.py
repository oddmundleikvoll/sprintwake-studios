"""Installed LTX-2.5 Q8 video through the existing Comfy queue."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from .base import Backend, FrameRule, JobSpec, ModelCapability, ProgressEvent, RunResult
from .render_helpers import (_align_up, _draft_geometry, _has_downstream_chain,
                             _ref_source, _resolved_prompt, _stretch_clip, _wh, SKETCH_STYLE_PREFIX)
from ..film_comfy import ComfyFilmClient, ComfyFilmError
from ..ltx_anchors import shot_anchors

MODEL_DIR = Path(os.environ.get("RAVEN_LTX_MODEL_DIR",str(Path.home()/"ComfyUI-Shared/models/ltx/ltx-2.5-mlx-q8")))
FRAME_RULE = FrameRule(kind="affine", step=8, offset=1, minimum=25,
                       note="LTX-2.5 uses 8k+1 frames at 24 fps.")


def _publish_take(video, destination, frames):
    """Commit the clip and its chain frames together, restoring on I/O error."""
    backup = destination.parent / (".ltx-previous-" + uuid.uuid4().hex)
    backup.mkdir()
    previous, published = [], []
    targets = [(video, destination), (frames, destination.parent / "frames"),
               (None, destination.parent / "stills")]
    try:
        for index, (source, target) in enumerate(targets):
            if target.exists():
                saved = backup / str(index)
                target.rename(saved)
                previous.append((saved, target))
            if source is not None:
                source.rename(target)
                published.append(target)
    except BaseException:
        for target in reversed(published):
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        for saved, target in reversed(previous):
            saved.rename(target)
        backup.rmdir()
        raise
    shutil.rmtree(backup)


class LTXBackend(Backend):
    id = "ltx"
    label = "LTX-2.5 Q8 — MLX / ComfyUI"

    def __init__(self, workspace, base_url="http://127.0.0.1:8188"):
        self.workspace = Path(workspace)
        self.client = ComfyFilmClient(base_url)
        self._cancelled = threading.Event()
        self._info_cache = (0.0, {})

    def _node_info(self):
        checked, info = self._info_cache
        if time.monotonic() - checked > 5:
            try:
                info = self.client._get("/object_info")
            except (OSError, ComfyFilmError, ValueError):
                info = {}
            self._info_cache = (time.monotonic(), info)
        return info

    def health(self):
        if not (MODEL_DIR / ".ltx-installed.json").is_file():
            return False, "LTX-2.5 Q8-modellen må installeres med godkjent Hugging Face-tilgang."
        try:
            self.client.preflight()
            return True, "Installert LTX-2.5 Q8, fast 8+3 steg og 48 kHz lyd."
        except Exception as error:
            return False, str(error)

    def capabilities(self):
        available = (MODEL_DIR / ".ltx-installed.json").is_file()
        return [ModelCapability(id="ltx-2.5", label="LTX-2.5 Q8 — video + audio",
                    supports_start_anchor=True, supports_end_anchor=True, supports_audio=True,
                    frame_rule=FRAME_RULE, size_align=64, default_steps=8, engine="ltx",
                    resolutions=["384x256", "512x320", "768x448", "1024x576", "1280x704"],
                    available=available, unavailable_reason="LTX-2.5 Q8-pakken er ikke ferdig installert.")]

    def resolved_prompt(self, shot, project, paths=None, *, with_audio=True, sketch=False):
        prompt = _resolved_prompt(shot, project, with_audio=with_audio, model="ltx-2.5")
        return f"{SKETCH_STYLE_PREFIX} {prompt}" if sketch else prompt

    def prepare(self, shot, project, paths):
        paths.abs_dir.mkdir(parents=True, exist_ok=True)
        defaults = project.get("defaults") or {}
        cap = self.capability("ltx-2.5")
        width, height = _wh(defaults.get("resolution") or shot.get("resolution") or cap.resolutions[0])
        draft = bool(defaults.get("draft"))
        sketch = draft and bool(defaults.get("sketch"))
        if draft:
            width, height, _ = _draft_geometry(width, height, 8, cap.size_align)
        width, height = _align_up(width, cap.size_align), _align_up(height, cap.size_align)
        frames = FRAME_RULE.snap(shot.get("frames") or defaults.get("frames") or 97)
        if frames > 481:
            raise ValueError("LTX-klipp er begrenset til 481 bilder (ca. 20 sekunder); del handlingen i flere klipp.")
        render_frames = 25 if sketch else frames
        with_audio = not sketch
        if shot.get("dialogueSource") == "recording" and shot.get("dialogue"):
            from ..store import speech_fingerprint
            if (not (paths.abs_dir / "dialogue.wav").is_file()
                    or shot.get("speechFingerprint") != speech_fingerprint(shot, project)):
                raise ValueError("Lag et oppdatert dialogopptak før klippet rendres.")
        anchors = {}
        for key in ("startRef", "endRef"):
            ref = shot.get(key)
            if ref:
                source = _ref_source(ref, paths)
                if not source or not Path(source).is_file():
                    raise ValueError("Åpnings-/sluttbildet mangler. Lag forrige klipp først ved kjeding.")
                anchors[key] = str(source)
        if sketch and shot.get("imageAnchors"):
            raise ValueError("Bildeankre støttes i normal rendering, ikke i Sketch-utkast som strekkes i tid. Slå av Sketch eller tøm ankrene.")
        timed_anchors = shot_anchors(shot, render_frames, paths.data_dir)
        prompt = self.resolved_prompt(shot, project, paths, with_audio=with_audio, sketch=sketch)
        data = {"engine": "ltx", "model": "ltx-2.5", "timingModel": "ltx:ltx-2.5",
                "prompt": prompt, "width": width, "height": height, "frames": render_frames,
                "steps": 8, "seed": int(shot.get("seed") or 0), "anchors": anchors,
                "imageAnchors": timed_anchors,
                "with_audio": with_audio, "draft": draft, "sketch": sketch,
                "sketchStretchFactor": frames / render_frames if sketch else 1,
                "nativeDialogueSpoken": with_audio and shot.get("dialogueSource") == "native" and bool(shot.get("dialogue"))}
        frames_dir = paths.abs_dir / "frames" if not draft or _has_downstream_chain(shot, project) else None
        if frames_dir:
            frames_dir.mkdir(parents=True, exist_ok=True)
        (paths.abs_dir / "shot.ltx.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return JobSpec(shot_id=shot["id"], expected_outputs=[paths.abs_dir / "clip.mp4"],
                       frames_dir=frames_dir, expected_frames=frames if frames_dir else 0, payload=data,
                       summary=f"{cap.label} · {width}×{height} · {frames} frames · 8+3 steps")

    def run(self, spec, on_event, should_cancel):
        self._cancelled.clear()
        result = RunResult(started_at=time.time())
        spec.started_at = result.started_at
        data = spec.payload
        cancel = lambda: self._cancelled.is_set() or should_cancel()
        progress = lambda phase, detail, percent: on_event(ProgressEvent(phase=phase, detail=detail, percent=percent))
        destination = spec.expected_outputs[0]
        temporary = destination.with_name(destination.stem + ".pending" + destination.suffix)
        pending_frames = destination.parent / (".ltx-frames-" + uuid.uuid4().hex)
        try:
            if cancel():
                raise InterruptedError("Kjøringen ble stoppet")
            anchors = data["anchors"]
            output = self.client.director_scene([data["prompt"]],
                    [Path(anchors["startRef"])] if anchors.get("startRef") else [],
                    destination.parent / "ltx-runs" / uuid.uuid4().hex,
                    width=data["width"], height=data["height"], first_frames=data["frames"],
                    seed=data["seed"], end_image=Path(anchors["endRef"]) if anchors.get("endRef") else None,
                    image_anchors=[data["imageAnchors"]],
                    cancel=cancel, on_progress=progress)
            shutil.copy2(output["shots"][0]["video"], temporary)
            if not data["with_audio"]:
                silent = temporary.with_name("silent.pending.mp4")
                subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(temporary),
                                "-an", "-c:v", "copy", str(silent)], check=True)
                silent.replace(temporary)
            if data["sketchStretchFactor"] != 1:
                _stretch_clip(temporary, data["sketchStretchFactor"], result.log)
            if spec.frames_dir:
                import av
                pending_frames.mkdir()
                with av.open(str(temporary)) as source:
                    for index, frame in enumerate(source.decode(video=0)):
                        if cancel():
                            raise InterruptedError("Kjøringen ble stoppet")
                        frame.to_image().save(pending_frames / f"frame-{index:05d}.png")
                if len(list(pending_frames.glob("*.png"))) != spec.expected_frames:
                    raise ValueError("LTX leverte feil bildeantall; forrige klipp er beholdt.")
            if cancel():
                raise InterruptedError("Kjøringen ble stoppet")
            _publish_take(temporary, destination, pending_frames if pending_frames.exists() else None)
            result.exit_code = 0
        except Exception as error:
            result.cancelled, result.error, result.exit_code = cancel(), str(error), 1
            result.log.append(("ERROR", str(error)))
        finally:
            temporary.unlink(missing_ok=True)
            if pending_frames.exists():
                shutil.rmtree(pending_frames)
            result.ended_at = time.time()
        return result

    def cancel(self):
        self._cancelled.set()

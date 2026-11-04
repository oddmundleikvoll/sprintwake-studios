"""Native Qwen-Image-2.1-Turbo stills through the existing Comfy queue."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .base import Backend, JobSpec, ModelCapability, ProgressEvent, RunResult, ShotPaths
from .render_helpers import _align_up, _ref_source, _resolved_prompt, _shot_characters, _wh
from ..film_comfy import ComfyFilmClient, ComfyFilmError


QWEN_ID = "qwen-image-2.1-turbo"
QWEN_SIZE_ALIGN = 32
QWEN_DEFAULT_SIZE = (768, 512)
QWEN_MAX_REFERENCES = 10


def _qwen_references(shot: dict, project: dict, paths: ShotPaths) -> list[tuple[Path, str]]:
    """Resolve every selected cast/reference image, failing rather than dropping it."""
    refs: list[tuple[Path, str]] = []
    def add(ref, label: str) -> None:
        source = _ref_source(ref, paths)
        if not source or not Path(source).is_file():
            raise ValueError(f"Qwen-referanse mangler: {label}")
        refs.append((Path(source).resolve(), label))

    start = shot.get("startRef")
    if start and not (isinstance(start, dict) and start.get("kind") == "chain"):
        add(start, "startbildet")

    for character in _shot_characters(shot, project):
        image = character.get("image")
        if not image:
            raise ValueError(f"Karakteren {character.get('name') or character.get('id') or 'ukjent'} mangler karakterkort")
        add(image, f"{character.get('name') or 'karakter'} (karakterkort)")

    for index, ref in enumerate(shot.get("referenceImages") or [], start=1):
        add(ref, f"referansebilde {index}")

    if len(refs) > QWEN_MAX_REFERENCES:
        raise ValueError(f"Qwen-bildeflyten støtter maksimalt {QWEN_MAX_REFERENCES} referansebilder; valgte {len(refs)}")
    return refs


class WithQwenImage(Backend):
    def __init__(self, inner, base_url="http://127.0.0.1:8188"):
        self.inner, self.client = inner, ComfyFilmClient(base_url)
        self.id, self.label = inner.id, inner.label
        self._cap_cache = (0.0, False, "Åpne Comfy Desktop med LTX-2.5 Q8")

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def health(self):
        try:
            healthy, message = self.inner.health() if self.inner.id != 'none' else (True,'')
            if not healthy:
                return False, message
            self.client.preflight(require_images=True)
            return True, "Installert LTX-2.5 Q8 og Qwen-Image-2.1-Turbo."
        except Exception as exc:
            return False, str(exc)

    def capabilities(self):
        checked, ok, message = self._cap_cache
        if time.monotonic() - checked > 5:
            try:
                self.client.preflight(require_images=True)
                ok, message = True, ""
            except Exception as exc:
                ok, message = False, str(exc)
            self._cap_cache = time.monotonic(), ok, message
        return self.inner.capabilities() + [ModelCapability(
            id=QWEN_ID, label="Qwen Image 2.1 Turbo — ComfyUI",
            kind="image", supports_style_refs=True, max_style_refs=QWEN_MAX_REFERENCES,
            size_align=QWEN_SIZE_ALIGN, default_steps=8,
            resolutions=["768x512", "1024x768", "1024x1024", "2048x2048"],
            available=ok, unavailable_reason=message, engine="comfyui")]

    def prepare(self, shot, project, paths):
        if shot.get("model") != QWEN_ID:
            return self.inner.prepare(shot, project, paths)
        paths.ensure()
        defaults = project.get("defaults") or {}
        width, height = _wh(shot.get("resolution") or defaults.get("resolution") or "768x512")
        width, height = _align_up(width, QWEN_SIZE_ALIGN), _align_up(height, QWEN_SIZE_ALIGN)
        if min(width, height) < 64:
            raise ValueError("Qwen-bildestørrelsen må være minst 64 piksler")
        refs = _qwen_references(shot, project, paths)
        prompt = _resolved_prompt(shot, project, with_audio=False, model=QWEN_ID)
        return JobSpec(
            shot_id=shot["id"], expected_outputs=[paths.abs_dir / "image.png"],
            payload={"engine": "qwen-image", "prompt": prompt, "width": width, "height": height,
                     "seed": int(shot.get("seed") or 0), "references": refs, "steps": 8},
            summary=f"Qwen Image 2.1 Turbo · {width}×{height} · 8 steps · {len(refs)} references")

    def run(self, spec, on_event, should_cancel):
        if spec.payload.get("engine") != "qwen-image":
            return self.inner.run(spec, on_event, should_cancel)
        result = RunResult(started_at=time.time())
        spec.started_at = result.started_at
        temporary = spec.expected_outputs[0].with_name("image.pending.png")
        cancel = lambda: should_cancel()
        try:
            if cancel():
                raise InterruptedError("Kjøringen ble stoppet")
            from ..fable import FableLLM
            FableLLM().release_memory(cancel=cancel)
            self.client.release_memory(cancel=cancel)
            data = spec.payload
            self.client.qwen_image(data["prompt"], temporary, references=data["references"],
                width=data["width"], height=data["height"], seed=data["seed"],
                cancel=cancel, on_progress=lambda phase, detail, percent:
                    on_event(ProgressEvent(phase=phase, detail=detail, percent=percent)))
            if cancel():
                raise InterruptedError("Kjøringen ble stoppet")
            from PIL import Image
            with Image.open(temporary) as image:
                image.verify()
            with Image.open(temporary) as image:
                image.convert("RGB").load()
            temporary.replace(spec.expected_outputs[0])
            result.exit_code = 0
        except Exception as exc:
            result.exit_code, result.error = 1, str(exc)
            result.cancelled = cancel()
        finally:
            temporary.unlink(missing_ok=True)
            result.ended_at = time.time()
        return result

    def cancel(self):
        self.inner.cancel()

    def validate(self, spec, result):
        if spec.payload.get("engine") == "qwen-image":
            return super().validate(spec, result)
        return self.inner.validate(spec, result)

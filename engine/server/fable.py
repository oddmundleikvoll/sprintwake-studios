"""Local MLX director, retaining saved projects' profile/service identity.

oMLX owns Splash and its model process. GPU handoff waits for idle-only eviction.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .splash import SplashLLM
from .director_omlx import MODEL, MODEL_DIR, CONTEXT, OUTPUT, validate_checkpoint

PROFILE = "qwen38-fable"
URL = "http://127.0.0.1:8002"
OMLX_CLI = Path.home() / ".omlx/bin/omlx"


class FableLLM(SplashLLM):
    # Config is re-read per request; the lock must span client instances.
    _lifecycle = threading.RLock()
    managed_memory = True
    startup_timeout = 720.0

    def __init__(self, sid="splash-local", label="Qwen3.8 Uncensored — lokal regissør (256K)",
                 url=URL, model=MODEL, **kwargs):
        kwargs.setdefault("profile", PROFILE)
        kwargs.setdefault("cli_path", OMLX_CLI)
        kwargs.setdefault("cache_dir", MODEL_DIR)
        super().__init__(sid, label, url, model, **kwargs)
        self.kind = "fable"

    def _status(self, timeout=4.0):
        self._validate_models(timeout)
        return {"model": self.model}

    def _validate_models(self, timeout=4.0):
        models = self._json_response(f"{self.api}/models", timeout=timeout)
        model = next((item for item in models.get("data", []) if item.get("id") == self.model), None)
        if model is None:
            raise RuntimeError(f"{self.label}: serveren tilbyr ikke {self.model!r}")
        if int(model.get("context_length", 0)) < CONTEXT:
            raise RuntimeError(f"{self.label}: krever minst {CONTEXT} tokens kontekst")
        if model.get("vision") is not True:
            raise RuntimeError(f"{self.label}: synsmodulen er ikke lastet")

    def health(self):
        try:
            validate_checkpoint(self.cache_dir)
            return super().health()
        except (OSError, ValueError, RuntimeError) as exc:
            return False, str(exc)

    def _dumps(self, body):
        # Do not inherit the generic client's removal of output limits when
        # thinking is enabled. This template otherwise defaults to xhigh.
        body.update(temperature=1.0, top_p=0.95, top_k=20, min_p=0.0,
                    repetition_penalty=1.0)
        body["reasoning_effort"] = "xhigh" if self.thinking == "high" else self.thinking
        body.setdefault("max_tokens", OUTPUT)
        return json.dumps(body)

    def _request(self, method, *args, **kwargs):
        # Keep the frontend/cache warm across language calls. Video admission
        # explicitly releases this model; oMLX also evicts idle weights at 5m.
        with self._lifecycle:
            try:
                return method(*args, **kwargs)
            except BaseException:
                self.release_memory()
                raise

    def complete(self, *args, **kwargs):
        return self._request(super().complete, *args, **kwargs)

    def complete_with_image(self, *args, **kwargs):
        return self._request(super().complete_with_image, *args, **kwargs)

    def complete_with_media(self, *args, **kwargs):
        # This model understands pictures, not input_audio. Preserve its
        # useful visual review when a character also has a voice reference.
        kwargs.pop("audio", None)
        return self._request(super().complete_with_media, *args, **kwargs)

    def review_ready(self):
        try:
            return self._json_response(f"{self.url}/review-ready", timeout=2).get("ready") is True
        except (OSError, ValueError):
            return False

    def release_memory(self, cancel=None, timeout=960.0):
        # Cleanup must finish even after cancellation. A disconnected socket
        # alone does not establish that GPU buffers have been released.
        with self._lifecycle:
            deadline = time.monotonic() + timeout
            def remaining():
                seconds = deadline - time.monotonic()
                if seconds <= 0:
                    raise TimeoutError("handoff deadline reached")
                return seconds
            try:
                status = self._profile_status(self._controller_status(timeout=remaining()))
                if status is None:
                    raise RuntimeError(f"{self.label}: Switchboard-profilen mangler")
                if status.get("request_model") != self.model:
                    raise RuntimeError(f"{self.label}: ukjent eierskap til modellprosessen")
                self._json_response(f"{self.controller_url}/api/stop", method="POST",
                                    payload={"profile": self.profile}, timeout=remaining())
                while True:
                    status = self._profile_status(self._controller_status(timeout=remaining()))
                    if status and status.get("running") is False and not self._ready(timeout=min(4.0, remaining())):
                        return True
                    time.sleep(min(0.1, remaining()))
            except TimeoutError as exc:
                raise TimeoutError(f"{self.label}: minnefrigjøring ble ikke bekreftet innen {timeout:.0f}s; "
                                   "bilde/video ble ikke startet") from exc

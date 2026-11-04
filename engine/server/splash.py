"""On-demand client for the locally managed Splash model.

Splash is owned by Model Switchboard.  This client talks to the Splash
frontend directly once it is ready, and asks the controller to start the
*named* profile when it is stopped.  In particular, it does not use the
desktop proxy: probing that proxy can switch another managed model.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .llm import OpenAICompatLLM


DEFAULT_MODEL = "incoai/Qwen3.8-27B-Splash"
DEFAULT_URL = "http://127.0.0.1:8001"
DEFAULT_CONTROLLER = "http://127.0.0.1:8877"
DEFAULT_PROFILE = "qwen38-splash"
DEFAULT_CLI = Path(os.environ.get("RAVEN_SPLASH_CLI", str(Path.home() / ".local/bin/splash-m1"))).expanduser()
DEFAULT_CACHE = Path.home() / ".cache" / "huggingface" / "hub" / (
    "models--incoai--Qwen3.8-27B-Splash"
)


def _splash_idle(status: dict[str, Any]) -> bool:
    """Return true only when Splash itself reports a safe idle release state."""
    weights = status.get("weights") or {}
    scheduler = status.get("scheduler") or {}
    frontend = status.get("frontend") or {}
    transport = status.get("transport") or {}
    return (
        status.get("ready") is True
        and weights.get("released") is True
        and weights.get("idle_release_seconds", 0) > 0
        and all(scheduler.get(key) == 0 for key in (
            "queued", "waiting_resources", "waiting_prefix", "prefilling",
            "decoding", "waiting_mask"))
        and frontend.get("active") == 0
        and frontend.get("waiting") == 0
        and transport.get("pending") == 0
        and transport.get("recovering") is False
    )


class SplashLLM(OpenAICompatLLM):
    """OpenAI-compatible Splash service, started through Model Switchboard."""

    profile = DEFAULT_PROFILE
    startup_timeout = 120.0

    def __init__(
        self,
        sid: str = "splash-local",
        label: str = "Splash (this machine)",
        url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        *,
        controller_url: str = DEFAULT_CONTROLLER,
        profile: str = DEFAULT_PROFILE,
        cli_path: Path | str = DEFAULT_CLI,
        cache_dir: Path | str = DEFAULT_CACHE,
    ):
        super().__init__(sid, label, url, model, kind="openai")
        self.controller_url = controller_url.rstrip("/")
        self.profile = profile
        self.cli_path = Path(cli_path)
        self.cache_dir = Path(cache_dir)

    @staticmethod
    def _json_response(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None,
                       timeout: float = 4.0) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Splash returned a non-object response from {url}")
        return value

    def _ready(self, timeout: float = 4.0) -> bool:
        request = urllib.request.Request(f"{self.url}/ready")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status == 200
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            return False

    def _status(self, timeout: float = 4.0) -> dict[str, Any]:
        return self._json_response(f"{self.url}/status", timeout=timeout)

    def _controller_status(self, timeout: float = 4.0) -> dict[str, Any]:
        return self._json_response(f"{self.controller_url}/api/status", timeout=timeout)

    def _profile_status(self, controller: dict[str, Any]) -> dict[str, Any] | None:
        """Find this exact profile; never infer ownership from another entry."""
        for item in controller.get("statuses") or []:
            if isinstance(item, dict) and item.get("profile") == self.profile:
                return item
        return None

    def _validate_models(self, timeout: float = 4.0) -> None:
        """Validate the live frontend's model list after it is ready."""
        doc = self._json_response(f"{self.api}/models", timeout=timeout)
        ids = {item.get("id") for item in doc.get("data") or []
               if isinstance(item, dict)}
        if self.model not in ids and f"models/{self.model}" not in ids:
            raise RuntimeError(
                f"{self.label} is ready but does not serve {self.model!r}")

    def health(self) -> tuple[bool, str]:
        """Check readiness without touching ``/v1/models`` or starting Splash."""
        if not self.url or not self.model:
            return False, f"{self.id}: no Splash URL or model configured"
        if self._ready():
            try:
                status = self._status()
            except (OSError, ValueError, urllib.error.URLError) as exc:
                return False, f"{self.label}: cannot read Splash status ({exc})"
            actual = status.get("model") or status.get("model_id")
            if actual and actual != self.model:
                return False, f"{self.label} is serving {actual!r}, not {self.model!r}"
            return True, ""

        # Offline readiness is deliberately passive.  A configured local
        # profile with its binary/cache present is usable on demand, provided
        # the controller is reachable; neither probe starts or loads a model.
        if not self.cli_path.exists() or not self.cache_dir.exists():
            return False, f"{self.label}: Splash CLI or cached model is missing"
        try:
            controller = self._controller_status()
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return False, f"{self.label}: Model Switchboard is unavailable ({exc})"
        profile = self._profile_status(controller)
        if profile is None:
            return False, f"{self.label}: profile {self.profile!r} is not registered"
        missing = profile.get("missing_artifacts") or profile.get("missingArtifacts") or []
        request_model = (profile.get("request_model") or profile.get("requestModel")
                         or profile.get("server_model_id") or profile.get("serverModelId"))
        if missing or (request_model and request_model != self.model):
            return False, f"{self.label}: profile is missing local artifacts"
        return True, "Starts on demand through Model Switchboard"

    def ensure_ready(self, *, timeout: float | None = None) -> None:
        """Start only this profile and wait for its own readiness endpoint."""
        if self._ready():
            self._validate_models()
            return
        wait_seconds = self.startup_timeout if timeout is None else timeout
        deadline = time.monotonic() + wait_seconds
        remaining = max(1.0, deadline - time.monotonic())
        # A running frontend can still be loading.  Wait for it rather than
        # asking Switchboard to start a duplicate instance.
        try:
            controller = self._controller_status(timeout=min(4.0, remaining))
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
            controller = {}
        profile = self._profile_status(controller)
        if not (profile and profile.get("running") is True):
            try:
                self._json_response(
                    f"{self.controller_url}/api/start",
                    method="POST",
                    payload={"profile": self.profile},
                    timeout=remaining,
                )
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc:
                raise RuntimeError(f"{self.label}: could not start profile {self.profile!r} ({exc})") from exc
        while not self._ready():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Model Switchboard did not ready {self.profile!r} within {wait_seconds:.0f}s")
            time.sleep(0.25)
        self._validate_models()

    def _dumps(self, body: dict[str, Any]) -> str:
        # Splash accepts OpenAI's reasoning field but ignores the generic
        # chat_template_kwargs extension used by other local servers.
        encoded = json.loads(super()._dumps(body))
        encoded.pop("chat_template_kwargs", None)
        encoded["reasoning_effort"] = self.thinking if self.thinking != "none" else "none"
        return json.dumps(encoded)

    def complete(self, system: str, user: str, *, timeout: float = 300.0,
                 max_tokens: int | None = None) -> str:
        self.ensure_ready()
        return super().complete(system, user, timeout=timeout, max_tokens=max_tokens)

    def complete_with_image(self, system: str, user: str, image: Path, *,
                            timeout: float = 300.0) -> str:
        self.ensure_ready()
        return super().complete_with_image(system, user, image, timeout=timeout)

    def complete_with_media(self, system: str, user: str, *, images: list[Path] | None = None,
                            audio: Path | None = None, timeout: float = 300.0) -> str:
        self.ensure_ready()
        return super().complete_with_media(system, user, images=images, audio=audio, timeout=timeout)

    def release_memory(self, cancel: Callable[[], bool] | None = None,
                       timeout: float = 90.0) -> bool:
        """Wait for Splash's own idle-release timer; never stop a process here."""
        deadline = time.monotonic() + timeout
        while True:
            if cancel and cancel():
                raise RuntimeError(f"{self.label}: memory release cancelled")
            try:
                if _splash_idle(self._status()):
                    return True
            except (OSError, ValueError, urllib.error.URLError):
                # The desktop monitor may stop its own idle Splash instance
                # immediately after release.  Accept that only with explicit
                # controller evidence for this profile; unreachable controller
                # state is never evidence of a safe release.
                try:
                    controller = self._controller_status()
                    profile = self._profile_status(controller)
                    if profile and profile.get("running") is False:
                        return True
                except (OSError, ValueError, urllib.error.URLError):
                    pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{self.label}: weights did not release within {timeout:.0f}s")
            time.sleep(0.25)

"""Process-wide admission gate used by desktop update handoff.

The gate deliberately does not stop work.  A desktop may reserve it only after
all producers are idle; once reserved, every new producer is rejected until
the desktop releases the lease or the process exits.
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
from typing import Any


class UpdateBusy(RuntimeError):
    """The server cannot be quiesced, or is already reserved for updating."""


class UpdateLifecycle:
    def __init__(self, ctx: Any):
        self.ctx = ctx
        self._lease_token: str | None = None
        self._lease_pid: int | None = None
        self._capability = os.environ.get("SPRINTWAKE_UPDATE_TOKEN", "")
        self._http_mutations = 0

    @property
    def _lock(self):
        return self.ctx.orch._start_lock

    def _backend_reason_locked(self) -> str | None:
        backend = getattr(self.ctx, "backend", None)
        if backend is None:
            return "backend-status-uncertain"
        checker = getattr(backend, "admission_ready", None)
        if checker is None:
            return "backend-status-uncertain"
        try:
            result = checker()
        except Exception as exc:  # an unreachable/malformed admission is unsafe
            return f"ComfyUI-admission-uncertain: {type(exc).__name__}"
        if result is False:
            return "ComfyUI-admission-rejected"
        return None

    def _snapshot_locked(self, *, include_backend: bool = True) -> dict[str, Any]:
        orch = self.ctx.orch
        reasons: list[str] = []
        active: dict[str, Any] = {}
        checks = (
            ("render", "busy", "render-active"),
            ("stills", "stills_busy", "stills-active"),
            ("images", "image_busy", "images-active"),
            ("projectBatch", "_project_batch_active", "project-batch-active"),
        )
        for key, attr, reason in checks:
            value = getattr(orch, attr, None)
            if not isinstance(value, bool):
                active[key] = "unknown"
                reasons.append(f"{key}-status-uncertain")
            else:
                active[key] = value
                if value:
                    reasons.append(reason)
        language = getattr(orch, "_llm_active", None)
        if type(language) is not int or language < 0:
            active["language"] = "unknown"
            reasons.append("language-status-uncertain")
        else:
            active["language"] = language > 0
            if language:
                reasons.append("language-active")
        try:
            film = self.ctx.film.status()
            if not isinstance(film, dict) or not isinstance(film.get("busy", False), bool):
                raise ValueError("malformed film status")
            active["film"] = bool(film["busy"])
            if film["busy"]:
                reasons.append("film-active")
        except Exception:
            active["film"] = "unknown"
            reasons.append("film-status-uncertain")
        if include_backend:
            backend_reason = self._backend_reason_locked()
            active["comfyAdmission"] = backend_reason is None
            if backend_reason:
                reasons.append(backend_reason)
        else:
            active["comfyAdmission"] = "unchecked"
        closing = getattr(orch, "_closing", None)
        if not isinstance(closing, bool):
            reasons.append("server-status-uncertain")
        elif closing:
            reasons.append("server-closing")
        in_flight = self._http_mutations
        active["httpMutations"] = in_flight
        if in_flight:
            reasons.append("http-mutation-active")
        return {
            "ready": not reasons and self._lease_token is None,
            "quiesced": self._lease_token is not None,
            "blockedReasons": reasons,
            "active": active,
            "desktopPid": os.getpid(),
            "pid": os.getpid(),
            "desktopOwned": bool(getattr(self.ctx, "desktop_owned", False)),
            "leasePid": self._lease_pid,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def assert_open(self, producer: str = "operation") -> None:
        """Reject a new direct GPU/LLM/film admission after reservation."""
        with self._lock:
            if self._lease_token is not None:
                raise UpdateBusy(f"Oppdatering er reservert; {producer} er blokkert")

    def assert_mutation_allowed(self, path: str) -> None:
        self.assert_open(f"HTTP {path}")

    def enter_http_mutation(self, path: str) -> None:
        with self._lock:
            if self._lease_token is not None:
                raise UpdateBusy(f"Oppdatering er reservert; HTTP {path} er blokkert")
            self._http_mutations += 1

    def exit_http_mutation(self) -> None:
        with self._lock:
            self._http_mutations = max(0, self._http_mutations - 1)

    def _auth(self, capability: str | None) -> None:
        if not self._capability or not isinstance(capability, str) or not hmac.compare_digest(capability, self._capability):
            raise UpdateBusy("Ugyldig desktop-oppdateringskapabilitet")

    def quiesce(self, capability: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._auth(capability)
            if self._lease_token is not None:
                if self._lease_pid == os.getpid():
                    return {**self._snapshot_locked(include_backend=False), "leaseToken": self._lease_token}
                raise UpdateBusy("Oppdatering er allerede reservert")
            snapshot = self._snapshot_locked()
            if not snapshot["ready"]:
                raise UpdateBusy("Serveren er ikke i ro: " + ", ".join(snapshot["blockedReasons"]))
            self._lease_token = secrets.token_urlsafe(32)
            self._lease_pid = os.getpid()
            snapshot = self._snapshot_locked(include_backend=False)
            snapshot["ready"] = True
            snapshot["leaseToken"] = self._lease_token
            return snapshot

    def resume(self, token: str | None, capability: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._auth(capability)
            if not isinstance(token, str) or not self._lease_token or not hmac.compare_digest(token, self._lease_token):
                raise UpdateBusy("Ugyldig oppdateringslås")
            self._lease_token = None
            self._lease_pid = None
            return self._snapshot_locked()

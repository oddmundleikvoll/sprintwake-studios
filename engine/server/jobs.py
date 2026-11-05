"""Small, provider-agnostic asynchronous job scheduler for Raven replacement.

SPDX-License-Identifier: MIT
Copyright (c) 2026 Oddmund Leikvoll
"""
from __future__ import annotations

from contextlib import nullcontext
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: str
    slug: str
    kind: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    status: str = "queued"
    phase: str = "queued"
    completed: int = 0
    total: int = 0
    result: Any = None
    error: str | None = None
    thread: threading.Thread | None = None


class Scheduler:
    """One admission lock and one active job per project.

    Cancellation is cooperative: ``stop`` marks the matching job and returns;
    the admission slot is released only after the worker callable returns.
    """
    def __init__(self, max_workers: int = 1):
        self.admission_lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._threads: dict[str, threading.Thread] = {}
        # The model backend is a single global GPU resource.  Keep this one
        # even if an older caller passes a larger value.
        self._max_workers = 1
        self._quiesced = False
        self._cv = threading.Condition(self.admission_lock)

    def status(self, slug: str) -> Job | None:
        with self.admission_lock:
            return self._jobs.get(slug)

    @property
    def quiesced(self) -> bool:
        with self.admission_lock:
            return self._quiesced

    def quiesce(self) -> None:
        """Close admission; existing work continues until drained."""
        with self.admission_lock:
            self._quiesced = True

    def resume(self) -> None:
        with self.admission_lock:
            self._quiesced = False

    def active(self) -> Job | None:
        with self.admission_lock:
            return next((j for j in self._jobs.values()
                         if j.status in {"queued", "running", "cancelling"}), None)

    def snapshot(self) -> dict[str, Any]:
        with self.admission_lock:
            active = self.active()
            return {"quiesced": self._quiesced,
                    "busy": active is not None,
                    "jobId": active.id if active else None,
                    "slug": active.slug if active else None,
                    "status": active.status if active else "idle",
                    "phase": active.phase if active else "idle"}

    def submit(self, slug: str, kind: str, worker: Callable[[Job], Any], *, before_start: Callable[[], bool] | None = None) -> Job:
        with self.admission_lock:
            if self._quiesced:
                raise RuntimeError("production admission is quiesced")
            guard = before_start or getattr(self, "before_start", None)
            if guard is not None and guard() is False:
                raise RuntimeError("production start guard refused admission")
            current = self._jobs.get(slug)
            if current and current.status in {"queued", "running", "cancelling"}:
                raise RuntimeError("project already has an active production job")
            if sum(t.is_alive() for t in self._threads.values()) >= self._max_workers:
                raise RuntimeError("production worker capacity is full")
            job = Job(uuid.uuid4().hex, str(slug), str(kind))
            self._jobs[slug] = job

            def run() -> None:
                with self.admission_lock:
                    job.status = "running"
                    job.phase = "running"
                try:
                    with getattr(self, 'resource_context', nullcontext)():
                        job.result = worker(job)
                    with self.admission_lock:
                        if job.status == "cancelling":
                            job.status = "cancelled"
                        elif job.status not in {"cancelled", "failed"}:
                            job.status = "completed"
                            job.phase = "complete"
                except Exception as exc:  # worker failures are durable via controller
                    with self.admission_lock:
                        job.error = str(exc)
                        job.status = "cancelled" if job.cancel_event.is_set() else "failed"
                        job.phase = "error"
                finally:
                    with self.admission_lock:
                        self._cv.notify_all()

            thread = threading.Thread(target=run, name=f"raven-{job.id}", daemon=True)
            job.thread = thread
            self._threads[job.id] = thread
            thread.start()
            return job

    def stop(self, slug: str, job_id: str | None = None) -> bool:
        with self.admission_lock:
            job = self._jobs.get(slug)
            if not job or not job_id or job.id != job_id or job.status not in {"queued", "running"}:
                return False
            job.cancel_event.set()
            job.status = "cancelling"
            job.phase = "cancelling"
            return True

    def drain(self, slug: str, job_id: str, timeout: float | None = None) -> Job | None:
        """Cancel and join the matching job, retaining global busy until return."""
        with self.admission_lock:
            job = self._jobs.get(slug)
            if not job or job.id != job_id:
                return None
            if job.status in {"queued", "running"}:
                job.cancel_event.set()
                job.status = "cancelling"
                job.phase = "cancelling"
            thread = job.thread
        if thread:
            thread.join(timeout)
        return job

    def wait(self, slug: str, timeout: float | None = None) -> Job | None:
        job = self.status(slug)
        if job and job.thread:
            job.thread.join(timeout)
        return job

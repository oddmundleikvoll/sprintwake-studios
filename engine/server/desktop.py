"""An app-owned editor drains its own work when its parent's pipe closes."""
from __future__ import annotations

import sys
import threading
import time


class DesktopLifetime:
    def __init__(self, ctx, httpd):
        self.ctx = ctx
        self.httpd = httpd
        self.stopping = threading.Event()
        self._lock = threading.Lock()

    def request_stop(self):
        with self._lock:
            if self.stopping.is_set():
                return
            self.stopping.set()
        # shutdown must never run in serve_forever's thread (including signals).
        threading.Thread(target=self._stop, name="desktop-stop", daemon=True).start()

    def _stop(self):
        with self.ctx.orch._start_lock:
            self.ctx.orch._closing = True
            self.ctx.orch._cancel.set()
            image_cancel = getattr(self.ctx.orch, "_image_cancel", None)
            if image_cancel is not None:
                image_cancel.set()
        self.httpd.shutdown()

    def watch(self, owner_input):
        def read_owner():
            try:
                while owner_input.read(1):
                    pass
            finally:
                self.request_stop()
        threading.Thread(target=read_owner, name="desktop-owner", daemon=True).start()


def stop_owned_work(ctx, timeout=90.0):
    """Bounded cleanup; never stop a shared language server or foreign prompt."""
    deadline = time.monotonic() + timeout
    orch = ctx.orch
    with orch._start_lock:
        orch._closing = True
        orch._cancel.set()
        workers = [t for t in (getattr(orch, "_thread", None),
                               getattr(orch, "_stills_thread", None),
                               getattr(orch, "_image_thread", None)) if t is not None]

    def cancel():
        try:
            ctx.film.stop()
        finally:
            orch.stop()
    cancellation = threading.Thread(target=cancel, name="desktop-cancel", daemon=True)
    cancellation.start()
    for worker in [cancellation, *workers]:
        worker.join(max(0, deadline - time.monotonic()))
    drained = not any(t.is_alive() for t in [cancellation, *workers])
    if not drained:
        print("Storyboard shutdown timed out; completed checkpoints are preserved.", file=sys.stderr)
    return drained

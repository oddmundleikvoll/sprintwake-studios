"""Cross-process lifetime and update handoff locks.

The code-reader lock is held for the complete engine lifetime.  Sparkle (or
another installer) can therefore replace a bundle only after every old engine
has exited, including engines using another project/config directory.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path


class AppAlreadyRunning(RuntimeError):
    pass


class UpdatePending(RuntimeError):
    pass


class AppLifetime:
    def __init__(self, root=None, bundle=None, build=None):
        self.root = Path(root or os.environ.get(
            "SPRINTWAKE_UPDATE_LOCK_ROOT",
            Path.home() / "Library/Application Support/Raven Studio/Updates",
        )).expanduser().resolve()
        self.bundle = str(Path(bundle).expanduser().resolve()) if bundle else ""
        self.build = int(build or 0)
        self._instance = None
        self._reader = None
        self._startup = None
        self._reserved = False

    def _open(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path.open("a+")

    def _pending_path(self):
        return self.root / "pending-update.json"

    def _pending(self):
        try:
            path = self._pending_path()
            if not path.exists():
                return None
            value = json.loads(path.read_text())
            if not isinstance(value, dict) or not isinstance(value.get("bundle"), str):
                raise UpdatePending("Malformed update handoff marker")
            try:
                int(value.get("build"))
            except (TypeError, ValueError) as exc:
                raise UpdatePending("Malformed update handoff marker") from exc
            return value
        except UpdatePending:
            raise
        except (OSError, ValueError) as exc:
            raise UpdatePending("Unreadable update handoff marker") from exc

    def _clear_matching_pending(self):
        pending = self._pending()
        if not pending or str(pending.get("bundle")) != self.bundle:
            return
        try:
            target = int(pending.get("build"))
        except (TypeError, ValueError):
            return
        if self.build < target:
            return
        # We are still under startup mutex and code is exclusively probed by
        # the caller, so deleting this marker cannot race a live old reader.
        try:
            self._pending_path().unlink()
        except FileNotFoundError:
            pass

    def enter_app(self):
        if self._reader is not None:
            return self
        self._instance = self._open("app-instance.lock")
        try:
            fcntl.flock(self._instance, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._instance.close(); self._instance = None
            raise AppAlreadyRunning("Sprintwake Studios is already running") from exc
        self._startup = self._open("startup.lock")
        fcntl.flock(self._startup, fcntl.LOCK_EX)
        try:
            self._reader = self._open("code-reader.lock")
            pending = self._pending()
            if pending and not (str(pending.get("bundle")) == self.bundle and self.build >= int(pending.get("build", 0))):
                raise UpdatePending("An update is pending; restart with the updated application")
            if pending:
                fcntl.flock(self._reader, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._clear_matching_pending()
                fcntl.flock(self._reader, fcntl.LOCK_SH)
            else:
                fcntl.flock(self._reader, fcntl.LOCK_SH)
            return self
        except Exception:
            self.close(); raise
        finally:
            fcntl.flock(self._startup, fcntl.LOCK_UN)
            self._startup.close(); self._startup = None

    def enter_engine(self):
        if self._reader is not None:
            return self
        self._startup = self._open("startup.lock")
        fcntl.flock(self._startup, fcntl.LOCK_EX)
        try:
            if self._pending():
                raise UpdatePending("An update is pending; restart with the updated application")
            self._reader = self._open("code-reader.lock")
            fcntl.flock(self._reader, fcntl.LOCK_SH)
            return self
        except Exception:
            self.close()
            raise
        finally:
            fcntl.flock(self._startup, fcntl.LOCK_UN)
            self._startup.close(); self._startup = None

    def reserve(self, target_build):
        if self._reader is None:
            raise RuntimeError("application lifetime is not entered")
        self._startup = self._open("startup.lock")
        fcntl.flock(self._startup, fcntl.LOCK_EX)
        try:
            marker = {"bundle": self.bundle, "build": int(target_build), "pid": os.getpid()}
            path = self._pending_path()
            fd, temp = tempfile.mkstemp(prefix=".pending-", dir=self.root)
            try:
                with os.fdopen(fd, "w") as out:
                    json.dump(marker, out); out.write("\n"); out.flush(); os.fsync(out.fileno())
                os.chmod(temp, 0o600)
                os.replace(temp, path)
            finally:
                if os.path.exists(temp): os.unlink(temp)
            self._reserved = True
            return marker
        finally:
            fcntl.flock(self._startup, fcntl.LOCK_UN); self._startup.close(); self._startup = None

    def writer_ready(self):
        if self._reader is None:
            return False
        try:
            fcntl.flock(self._reader, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def cancel_update(self):
        if self._reader is not None:
            fcntl.flock(self._reader, fcntl.LOCK_SH)
        if self._startup is None:
            self._startup = self._open("startup.lock"); fcntl.flock(self._startup, fcntl.LOCK_EX)
        try:
            pending = self._pending()
            if pending and pending.get("pid") == os.getpid():
                try: self._pending_path().unlink()
                except FileNotFoundError: pass
            self._reserved = False
        finally:
            fcntl.flock(self._startup, fcntl.LOCK_UN); self._startup.close(); self._startup = None

    def close(self):
        for attr in ("_reader", "_instance"):
            handle = getattr(self, attr)
            if handle is not None:
                try: fcntl.flock(handle, fcntl.LOCK_UN)
                finally: handle.close(); setattr(self, attr, None)

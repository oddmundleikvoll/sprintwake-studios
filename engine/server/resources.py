# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Cross-process admission for the studio's explicitly owned model operations."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
from .store import Conflict, safe_path

class Resources:
    def __init__(self, path, provider):
        self.path=Path(os.environ.get('RAVEN_GPU_LOCK',str(path)))
        self.provider=provider
        self.language_journal=provider.state_dir/'language-lifecycle.json'
    @contextmanager
    def lease(self):
        safe_path(self.path.parent,self.path.name,create_parent=True)
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode): raise ValueError('Unsafe model admission lock')
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as error: raise Conflict('Another studio process still owns model resources') from error
            from .language_lifecycle import assert_drained
            assert_drained(self.language_journal)
            check=getattr(self.provider,'has_unresolved',None)
            if check and check(): self.provider.recover()
            if check and check(): raise Conflict('A previous Comfy prompt has unconfirmed completion; inspect its provider journal before starting another model')
            check=getattr(self.provider,'assert_ltx_drained',None)
            if check: check()
            check=getattr(self.provider,'ensure_memory_drained',None)
            if check: check()
            yield
        finally:
            fcntl.flock(fd,fcntl.LOCK_UN); os.close(fd)

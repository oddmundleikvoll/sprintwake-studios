# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Reader leases and update handoff, interoperable with installed Raven builds."""
from pathlib import Path
import fcntl
import json
import os
import stat
import tempfile
import time
import uuid

class AppLifetime:
    def __init__(self,root=None,bundle=None,build=None):
        self.root=Path(root or os.environ.get('SPRINTWAKE_UPDATE_LOCK_ROOT') or Path.home()/'Library/Application Support/Raven Studio/Updates')
        self.bundle=str(bundle) if bundle is not None else None
        self.build=int(build) if isinstance(build,str) and build.isdigit() else build
        self._handle=self._instance=self._startup=None
        self.mode=self._token=None
    def _open(self,name):
        if self.root.is_symlink(): raise RuntimeError('Unsafe lifetime directory')
        self.root.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.root/name,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd); raise RuntimeError('Unsafe lifetime lock')
        return os.fdopen(fd,'r+b')
    def _pending(self):
        path=self.root/'pending-update.json'
        if path.is_symlink(): raise RuntimeError('Unsafe update marker')
        if not path.exists(): return None
        if not path.is_file() or path.stat().st_size>4096: raise RuntimeError('Uncertain update marker')
        try: value=json.loads(path.read_text())
        except (ValueError,OSError) as error: raise RuntimeError('Uncertain update marker') from error
        if not isinstance(value,dict) or type(value.get('build')) is not int or not isinstance(value.get('bundle'),str) or type(value.get('pid')) is not int:
            raise RuntimeError('Uncertain update marker')
        # Published builds before 0.11 used exactly these three protocol fields.
        if 'token' not in value and set(value)!={'build','bundle','pid'}: raise RuntimeError('Unknown legacy update marker')
        if 'token' in value and (not isinstance(value['token'],str) or not value['token']): raise RuntimeError('Uncertain update ownership')
        return value
    @staticmethod
    def _lock(handle,mode):
        try: fcntl.flock(handle,mode|fcntl.LOCK_NB)
        except BlockingIOError as error: raise RuntimeError('Application update ownership is busy') from error
    @staticmethod
    def _close(handle):
        if handle:
            fcntl.flock(handle,fcntl.LOCK_UN); handle.close()
    def enter_engine(self):
        fence=self._open('startup.lock')
        try:
            self._lock(fence,fcntl.LOCK_SH)
            if self._pending() is not None: raise RuntimeError('Pending update blocks engine startup')
            self._handle=self._open('code-reader.lock'); self._lock(self._handle,fcntl.LOCK_SH)
            self.mode='reader'; return self
        except Exception:
            self.close(); raise
        finally: self._close(fence)
    def enter_app(self,*,on_update_handoff=None):
        fence=self._open('startup.lock')
        try:
            self._lock(fence,fcntl.LOCK_SH)
            pending=self._pending()
            self._instance=self._open('app-instance.lock'); self._lock(self._instance,fcntl.LOCK_EX)
            self._handle=self._open('code-reader.lock')
            if pending is not None:
                if pending['build']!=self.build or pending['bundle']!=self.bundle: raise RuntimeError('Pending update requires its exact matching application')
                self._lock(fence,fcntl.LOCK_EX); self._lock(self._handle,fcntl.LOCK_EX)
                if self._pending()!=pending: raise RuntimeError('Update marker changed during handoff')
                # Finish update-only maintenance before consuming its durable
                # intent. Failure leaves the marker for the matching app to retry.
                if on_update_handoff is not None:on_update_handoff()
                (self.root/'pending-update.json').unlink(); self._sync_directory()
            self._lock(self._handle,fcntl.LOCK_SH); self.mode='reader'; return self
        except Exception:
            self.close(); raise
        finally: self._close(fence)
    def _sync_directory(self):
        fd=os.open(self.root,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    def reserve(self,build=None):
        if not self._instance or not self._handle: raise RuntimeError('Only an owning application can reserve an update')
        if self._pending() is not None: raise RuntimeError('An update is already pending')
        target=build if build is not None else self.build
        if type(target) is not int or not self.bundle: raise RuntimeError('An exact target build and bundle are required')
        fence=self._open('startup.lock')
        try:
            self._lock(fence,fcntl.LOCK_EX)
            if self._pending() is not None: raise RuntimeError('An update is already pending')
            token=uuid.uuid4().hex
            data={'build':target,'bundle':self.bundle,'pid':os.getpid(),'token':token,'reservedAt':time.time()}
            fd=os.open(self.root/'pending-update.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:
                json.dump(data,stream); stream.flush(); os.fsync(stream.fileno())
            self._sync_directory(); self._startup=fence; self._token=token
        except Exception:
            self._close(fence); raise
    def writer_ready(self):
        if not self._handle or not self._instance or not self._startup or not self._token: return False
        pending=self._pending()
        if pending is None or pending.get('token')!=self._token: raise RuntimeError('Update ownership changed')
        try:
            self._lock(self._startup,fcntl.LOCK_EX); self._lock(self._handle,fcntl.LOCK_EX)
            self.mode='writer'; return True
        except RuntimeError:
            # Restore our reader after a failed conversion; the startup fence
            # remains exclusive while existing peer readers drain.
            self._lock(self._handle,fcntl.LOCK_SH); self.mode='reader'; return False
    def cancel_update(self):
        if not self._handle or not self._instance or not self._token: raise RuntimeError('This application does not own the update marker')
        pending=self._pending()
        if pending is None or pending.get('token')!=self._token: raise RuntimeError('Update ownership changed')
        (self.root/'pending-update.json').unlink(); self._sync_directory()
        self._lock(self._handle,fcntl.LOCK_SH); self.mode='reader'
        self._close(self._startup); self._startup=None; self._token=None
    def close(self):
        # Keep a reserved marker through exit; the matching new app clears it.
        for name in ('_startup','_instance','_handle'):
            self._close(getattr(self,name)); setattr(self,name,None)
        self.mode=None

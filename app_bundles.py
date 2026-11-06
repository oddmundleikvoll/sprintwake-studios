# MIT License
"""Small, conservative LaunchServices/app-bundle cleanup primitives.

This module deliberately operates only on Sprintwake's bundle identifiers and
on application bundles that are already in the user's Trash.  It never resets
the LaunchServices database or empties Trash.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import fcntl
import json
import hashlib
import os
import plistlib
import re
import shutil
import stat
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from typing import Iterable

BUNDLE_ID = "no.oddmund.storyboard.raven"
APP_NAME = "Sprintwake Studios.app"
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
_QA_RE = re.compile(r"^" + re.escape(BUNDLE_ID) + r"\.qa\.[0-9a-f]{32}$")


def _allowed_id(value: str | None) -> bool:
    return value == BUNDLE_ID or bool(value and _QA_RE.fullmatch(value))


def _canonical() -> Path:
    return Path.home() / "Applications" / APP_NAME


def _cleanup_root() -> Path:
    return Path.home()/'Library/Application Support/Raven Studio/AppCleanup'


@contextmanager
def _cleanup_lock():
    root=_cleanup_root()
    if root.is_symlink() or _protected(_resolved(root),(_canonical(),)):
        raise ValueError('App cleanup state cannot point into an application')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(root/'cleanup.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('App cleanup lock is not a regular file')
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:os.close(fd)


def _serialized(function):
    @wraps(function)
    def run(*args,**kwargs):
        with _cleanup_lock():return function(*args,**kwargs)
    return run


def _resolved(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _protected(path: Path, targets: Iterable[Path]) -> bool:
    path = _resolved(path)
    return any(path == target or target in path.parents or path in target.parents
               for target in (_resolved(item) for item in targets))


def bundle_id(path: Path) -> str | None:
    """Read a bundle identifier without following a bundle-root symlink."""
    path = Path(path)
    try:
        if not path.is_dir() or path.is_symlink():
            return None
        contents = path / "Contents"
        info = contents / "Info.plist"
        if contents.is_symlink() or info.is_symlink():
            return None
        data = plistlib.loads(info.read_bytes())
        if not isinstance(data,dict):return None
        value = data.get("CFBundleIdentifier")
        return value if isinstance(value, str) else None
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        return None


def _reject_path(path: Path) -> None:
    original = Path(path).expanduser()
    path = _resolved(original)
    if _protected(path, (_canonical(),)):
        raise ValueError("The canonical Sprintwake application is protected")
    if original.is_symlink():
        raise ValueError("Refusing to move a symlink bundle")
    contents = path / "Contents"
    info = contents / "Info.plist"
    if contents.is_symlink() or info.is_symlink():
        raise ValueError("Refusing a bundle with a symlinked Contents or Info.plist")


def _running_process(path: Path) -> bool:
    path = _resolved(path)
    prefix = str(path / "Contents") + os.sep
    try:
        result = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        raise RuntimeError("Unable to verify whether the Sprintwake application is running")
    own = str(os.getpid())
    parsed=[]
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields)!=2 or not fields[0].isdigit():
            raise RuntimeError('Process inventory is incomplete')
        parsed.append(fields)
    if not any(fields[0]==own for fields in parsed):
        raise RuntimeError('Process inventory is incomplete')
    for fields in parsed:
        if fields[0] != own and str(_resolved(Path(fields[1]))).startswith(prefix):
            return True
    return False


def assert_bundle_idle(path: Path) -> None:
    """Check the original bundle path before any installer rename."""
    if _running_process(path):
        raise RuntimeError('The Sprintwake application or a bundled worker is still running')


def validate_build_output(path: Path) -> Path:
    """Managed builds stay below a real, unindexed staging directory."""
    selected=_resolved(Path(path).expanduser())
    if not selected.name.endswith('.app') or not any(p.name.endswith('.noindex') for p in selected.parents):
        raise ValueError('Build output must be inside a resolved .noindex directory')
    if _protected(selected,(_canonical(),)):
        raise ValueError('Build output cannot replace the installed application')
    return selected


def _helper_path() -> Path:
    return Path(__file__).resolve().parent / "scripts" / "trash-app.swift"


def _trash_with_helper(source: Path, receipt: Path) -> Path:
    command = ["xcrun", "swift", str(_helper_path()), "--source", str(source),
               "--receipt", str(receipt), "--expected-id", BUNDLE_ID,
               "--canonical", str(_canonical())]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Unable to move application to Trash").strip())
    try:
        payload = json.loads(result.stdout)
        actual = Path(payload["trash"]).absolute()
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Trash helper returned no resultingItemURL") from exc
    trash = _resolved(Path.home() / ".Trash")
    if trash not in actual.parents or not actual.exists():
        raise RuntimeError("Trash helper returned an invalid resultingItemURL")
    return actual


def _dump_records() -> list[tuple[Path, str | None]]:
    result = subprocess.run([LSREGISTER, "-dump"], capture_output=True, text=True, check=True)
    records: list[tuple[Path, str | None]] = []
    current: Path | None = None
    current_id: str | None = None
    seen: set[Path] = set()
    for line in result.stdout.splitlines() + [""]:
        stripped = line.strip()
        if stripped.startswith("path:"):
            if current is not None:
                key = _resolved(current)
                if key not in seen:
                    records.append((current, current_id)); seen.add(key)
            value = stripped.split(":", 1)[1].strip()
            value = re.sub(r"\s+\(0x[0-9a-fA-F]+\)$", "", value)
            current = Path(value)
            current_id = None
        elif stripped.startswith("identifier:"):
            current_id = stripped.split(":", 1)[1].strip()
        elif not stripped and current is not None:
            key = _resolved(current)
            if key not in seen:
                records.append((current, current_id)); seen.add(key)
            current = None
            current_id = None
    return records


def _dump_paths(identifier: str | None) -> list[Path]:
    return [path for path, current_id in _dump_records()
            if identifier is None or current_id == identifier]


def _unregister(path: Path) -> None:
    _unregister_many((path,))


def _unregister_many(paths: Iterable[Path]) -> None:
    paths=tuple(paths)
    if not paths:return
    selected={_resolved(path) for path in paths}
    # Finder's move notification can arrive after the first unregister. Require
    # repeated fresh confirmation for Trash paths and retry late registrations.
    required = 2 if any(path.is_relative_to(_resolved(Path.home()/'.Trash')) for path in selected) else 1
    absent = 0
    for _ in range(4):
        for path in paths:
            subprocess.run([LSREGISTER, "-u", str(path)], capture_output=True, text=True)
        try:
            still = bool(selected.intersection(_resolved(candidate) for candidate in _dump_paths(None)))
        except (OSError, subprocess.CalledProcessError):
            raise RuntimeError("Unable to verify obsolete application registrations")
        absent = 0 if still else absent+1
        if absent >= required:return
        time.sleep(.25)
    raise RuntimeError("LaunchServices could not unregister obsolete applications")


def _bundle_entries(source: Path) -> dict:
    entries={}
    for path in (source,*source.rglob('*')):
        value=path.lstat();mode=value.st_mode
        entry={'mode':mode,'size':value.st_size,'mtime':value.st_mtime_ns}
        if stat.S_ISLNK(mode):entry['link']=os.readlink(path)
        elif not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError('Application backup contains an unsupported file type')
        entries[path.relative_to(source).as_posix()]=entry
    return entries


def _write_receipt(path: Path, value: dict) -> None:
    temporary=path.with_name(path.name+'.tmp')
    with temporary.open('w') as handle:
        json.dump(value,handle,indent=2);handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def _archive_trash_bundle(source: Path, receipt: Path) -> tuple[Path,dict]:
    """Keep all bytes/links in a ZIP that macOS cannot rediscover as an app."""
    _reject_path(source)
    source=_resolved(source)
    if not source.is_relative_to(_resolved(Path.home()/'.Trash')) or not _allowed_id(bundle_id(source)):
        raise ValueError('Recovery conversion requires an owned app in Trash')
    assert_bundle_idle(source)
    entries=_bundle_entries(source)
    archive=source.with_name(source.name+'.recovery-'+uuid.uuid4().hex+'.zip')
    subprocess.run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent','--zlibCompressionLevel','1',str(source),str(archive)],check=True,capture_output=True)
    try:
        with zipfile.ZipFile(archive) as backup:
            for relative,entry in entries.items():
                name=source.name+('/'+relative if relative!='.' else '')
                if stat.S_ISDIR(entry['mode']):name+='/'
                member=backup.getinfo(name);mode=member.external_attr>>16
                if stat.S_IFMT(mode)!=stat.S_IFMT(entry['mode']) or stat.S_IMODE(mode)!=stat.S_IMODE(entry['mode']):
                    raise RuntimeError('Recovery archive did not preserve application entry types and permissions')
                if stat.S_ISLNK(entry['mode']):
                    if backup.read(member).decode('utf-8')!=entry['link']:
                        raise RuntimeError('Recovery archive did not preserve an application symlink')
                elif stat.S_ISREG(entry['mode']):
                    with (source/relative).open('rb') as original,backup.open(member) as stored:
                        if hashlib.file_digest(original,'sha256').digest()!=hashlib.file_digest(stored,'sha256').digest():
                            raise RuntimeError('Recovery archive bytes differ from the application')
    except (zipfile.BadZipFile,KeyError) as error:
        raise RuntimeError('Recovery archive is incomplete; the original application was preserved') from error
    if entries!=_bundle_entries(source):raise RuntimeError('Application changed while its recovery archive was prepared')
    with archive.open('rb') as handle:os.fsync(handle.fileno())
    fd=os.open(archive.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    value=json.loads(receipt.read_text()) if receipt.exists() else {'source':str(source),'trash':str(source),'bundleIdentifier':bundle_id(source)}
    value.update(backup=str(archive),restoreName=APP_NAME,state='archive-verified')
    _write_receipt(receipt,value)
    return archive,entries


def _remove_archived_bundle(source: Path, entries: dict) -> None:
    assert_bundle_idle(source)
    if _bundle_entries(source)!=entries:raise RuntimeError('Application changed before recovery conversion completed')
    # Only obsolete directory permissions change; never follow links or alter
    # file/hardlink modes. The verified archive retains the original permissions.
    for folder,_,_ in os.walk(source,followlinks=False):
        directory=Path(folder);directory.chmod(stat.S_IMODE(directory.stat().st_mode)|stat.S_IWUSR)
    shutil.rmtree(source)


@_serialized
def trash_bundle(path: Path, *, keep: Iterable[Path] = (), receipt_dir: Path) -> Path:
    raw_source = Path(path).expanduser()
    _reject_path(raw_source)
    source = _resolved(raw_source)
    ident = bundle_id(source)
    if not _allowed_id(ident):
        raise ValueError("Refusing a non-Sprintwake application bundle")
    if _running_process(source):
        raise RuntimeError("Refusing to move a running Sprintwake application")
    if _protected(source, tuple(keep) + (_canonical(),)):
        raise ValueError("The requested bundle is protected")
    receipt_dir = Path(receipt_dir)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = receipt_dir / f"trash-{uuid.uuid4().hex}.json"
    _unregister(source)
    actual = _trash_with_helper(source, receipt)
    backup,entries=_archive_trash_bundle(actual,receipt)
    # The helper writes the receipt before this call; unregister the resulting
    # Trash URL, since moving can cause LaunchServices to register it again.
    _unregister(actual)
    _remove_archived_bundle(actual,entries)
    _unregister(actual)
    return backup

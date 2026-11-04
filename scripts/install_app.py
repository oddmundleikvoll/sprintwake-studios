"""Keep exactly one discoverable Raven application bundle on this Mac."""
from __future__ import annotations

from pathlib import Path
import os
import sys
import plistlib
import shutil
import subprocess
import uuid

BUNDLE_ID = "no.oddmund.storyboard.raven"
APP_NAME = "Sprintwake Studios.app"
# Only this known Raven bundle may be migrated. A generic Storyboard.app is
# outside the product's ownership and must never be touched by installation.
LEGACY_NAMES = ("Storyboard Raven.app",)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from contracts.app_lifetime import AppLifetime


def _ours(path: Path) -> bool:
    try:
        return plistlib.loads((path / "Contents/Info.plist").read_bytes()).get("CFBundleIdentifier") == BUNDLE_ID
    except (OSError, ValueError):
        return False


def prepare_destination(root: Path, target: Path) -> None:
    """Replace the old shortcut only when it points at our own legacy bundle."""
    legacy = next((root / name for name in LEGACY_NAMES if (root / name).exists()), root / APP_NAME)
    if target.is_symlink():
        if target.resolve() != legacy.resolve() or not _ours(legacy):
            raise ValueError("Applications-oppføringen peker på en ukjent app")
        target.unlink()
    if target.exists() and (not target.is_dir() or not _ours(target)):
        raise ValueError("Installeringsmålet tilhører en annen app")
    target.parent.mkdir(parents=True, exist_ok=True)


def finalize_install(root: Path, target: Path, *, register=True) -> Path | None:
    """Retain the old launcher outside .app indexing after the new app is signed."""
    if target.is_symlink() or not _ours(target):
        raise ValueError("Den ferdige Raven-appen mangler")
    legacy = next((root / name for name in LEGACY_NAMES if (root / name).exists() and (root / name).resolve() != target.resolve()), root / APP_NAME)
    retired = None
    registrar = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
    if register:
        subprocess.run([registrar, "-f", str(target)], check=True, capture_output=True)
    if legacy.exists() and legacy.resolve() != target.resolve():
        if legacy.is_symlink() or not _ours(legacy):
            raise ValueError("Den gamle appbanen tilhører en ukjent app")
        if register:
            subprocess.run([registrar, "-u", str(legacy)], check=True, capture_output=True)
        retired = root / "qa/retired-apps.noindex" / uuid.uuid4().hex
        retired.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(retired))
    return retired


def _verify_signature(bundle: Path) -> None:
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)],
                   check=True, capture_output=True)


def transactional_install(root: Path, bundle: Path, target: Path, *, register=False) -> Path | None:
    """Verify and stage on the target volume, then swap under the lifetime lock."""
    root = Path(root).expanduser().absolute()
    bundle = Path(bundle).expanduser().resolve()
    target = Path(target).expanduser().absolute()
    if not _ours(bundle):
        raise ValueError("Den nye Sprintwake-bunten mangler eller har feil identitet")
    if target.exists() and (target.is_symlink() or not _ours(target)):
        raise ValueError("Installeringsmålet tilhører en annen app")
    executable_paths = [target / "Contents/MacOS/Sprintwake Studios",
                        root / "Storyboard Raven.app/Contents/MacOS/Storyboard Raven"]
    processes = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=True).stdout
    for executable in executable_paths:
        if any(str(executable) in line and not line.lstrip().startswith(str(os.getpid()) + " ") for line in processes.splitlines()):
            raise RuntimeError("Raven kjører fortsatt; oppdateringen er utsatt")
    try:
        build = int(plistlib.loads((bundle / "Contents/Info.plist").read_bytes())["CFBundleVersion"])
        if build <= 0: raise ValueError()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("Den nye Sprintwake-bunten mangler gyldig buildnummer") from exc
    lifetime = AppLifetime(bundle=str(target), build=build).enter_app()
    staged = target.parent / ("." + target.name + ".staged-" + uuid.uuid4().hex)
    previous = None
    replaced = False
    reserved = False
    installed = False
    try:
        lifetime.reserve(build); reserved = True
        if not lifetime.writer_ready():
            raise RuntimeError("Raven har fortsatt en aktiv leser; oppdateringen er utsatt")
        _verify_signature(bundle)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundle, staged, symlinks=True)
        _verify_signature(staged)
        if not _ours(staged):
            raise ValueError("Den kopierte Sprintwake-bunten mangler identitet")
        if target.exists():
            previous = target.parent / (target.name + ".previous-" + uuid.uuid4().hex)
            target.rename(previous)
        staged.rename(target); replaced = True
        retired = finalize_install(root, target, register=register)
    except Exception:
        if replaced and target.exists():
            shutil.rmtree(target)
        if previous and previous.exists():
            previous.rename(target)
        raise
    else:
        if previous and previous.exists():
            retired_previous = root / "qa/retired-apps.noindex" / uuid.uuid4().hex
            retired_previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(previous), str(retired_previous))
        installed = True
        return retired
    finally:
        try:
            if staged.exists(): shutil.rmtree(staged)
        finally:
            try:
                if reserved and not installed: lifetime.cancel_update()
            finally:
                lifetime.close()


if __name__ == "__main__":
    import sys
    stage, *arguments = sys.argv[1:]
    if stage == "prepare":
        source, destination = arguments
        prepare_destination(Path(source), Path(destination))
    elif stage == "finalize":
        source, destination = arguments
        finalize_install(Path(source), Path(destination))
    elif stage == "install":
        root, bundle, target = arguments
        transactional_install(Path(root), Path(bundle), Path(target), register=True)
    else:
        raise SystemExit("Unknown installation stage")

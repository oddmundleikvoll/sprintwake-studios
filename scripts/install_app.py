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
from app_bundles import assert_bundle_idle,bundle_id,trash_bundle


def _ours(path: Path) -> bool:
    return bundle_id(path)==BUNDLE_ID


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


def _legacy_bundle(root: Path, target: Path) -> Path | None:
    for name in LEGACY_NAMES:
        legacy = root / name
        if legacy.exists() and legacy.absolute() != target.absolute():
            if legacy.is_symlink() or not _ours(legacy):
                raise ValueError("The old app path belongs to an unknown application")
            return legacy
    return None


def finalize_install(root: Path, target: Path, *, register=True) -> Path | None:
    """Register only the installed app; return a validated obsolete launcher."""
    if target.is_symlink() or not _ours(target):
        raise ValueError("Den ferdige Raven-appen mangler")
    legacy = _legacy_bundle(root, target)
    registrar = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
    if register:
        subprocess.run([registrar, "-f", str(target)], check=True, capture_output=True)
    return legacy


def _verify_signature(bundle: Path) -> None:
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)],
                   check=True, capture_output=True)


def transactional_install(root: Path, bundle: Path, target: Path, *, register=False) -> Path | None:
    """Verify and stage on the target volume, then swap under the lifetime lock."""
    root = Path(root).expanduser().absolute()
    bundle = Path(bundle).expanduser().absolute()
    target = Path(target).expanduser().absolute()
    if bundle.is_symlink() or not _ours(bundle):
        raise ValueError("Den nye Sprintwake-bunten mangler eller har feil identitet")
    bundle = bundle.resolve()
    if bundle == target.resolve() or target.resolve().is_relative_to(bundle) or bundle.is_relative_to(target.resolve()):
        raise ValueError("Build source and installed application must be separate bundles")
    if target.exists() and (target.is_symlink() or not _ours(target)):
        raise ValueError("Installeringsmålet tilhører en annen app")
    for candidate in (target,root/'Storyboard Raven.app',bundle):
        assert_bundle_idle(candidate)
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
        # The verified replacement is committed. A cleanup failure must not undo
        # that replacement or cancel its handoff marker. Receipts retain the
        # actual Trash URL so registration cleanup can be retried safely.
        installed = True
        receipt_dir = root / "qa/app-cleanup.noindex"
        result = None
        for obsolete in (previous, retired, bundle):
            if obsolete is not None and obsolete.exists():
                try:
                    moved = trash_bundle(obsolete, keep=(target,), receipt_dir=receipt_dir)
                    if obsolete == retired:
                        result = moved
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                    raise RuntimeError("The new app is installed; obsolete app cleanup is incomplete. "
                                       f"Recovery receipts: {receipt_dir}") from error
        return result
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
        obsolete = finalize_install(Path(source), Path(destination))
        if obsolete is not None:
            trash_bundle(obsolete, keep=(Path(destination),), receipt_dir=Path(source)/"qa/app-cleanup.noindex")
    elif stage == "install":
        root, bundle, target = arguments
        transactional_install(Path(root), Path(bundle), Path(target), register=True)
    else:
        raise SystemExit("Unknown installation stage")

"""Build a relocatable Sprintwake Studios.app staging bundle.

The bundle contains the Raven sources, the companion Storyboard server, the
native runtime and an isolated Python distribution. User projects, models,
Keychain entries and per-machine settings stay outside the bundle.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

BUNDLE_ID = "no.oddmund.storyboard.raven"
APP_NAME = "Sprintwake Studios.app"
VERSION = "0.10.1"
BUILD = "15"


def _copytree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", "qa", "workspace"))


def _copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target, follow_symlinks=False)


def _write_plist(path: Path, *, release: dict) -> None:
    data = {
        "CFBundleExecutable": "Sprintwake Studios",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "Sprintwake Studios",
        "CFBundleDisplayName": "Sprintwake Studios",
        "CFBundlePackageType": "APPL",
        "CFBundleDevelopmentRegion": "en",
        "CFBundleLocalizations": ["en"],
        "CFBundleShortVersionString": str(release["version"]),
        "CFBundleVersion": str(release["build"]),
        "CFBundleIconFile": "AppIcon",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "14.0",
    }
    if release.get("feedURL"):
        data.update({"SUFeedURL": release["feedURL"],
                     "SUEnableAutomaticChecks": bool(release.get("automaticChecks", False)),
                     "SUAutomaticallyUpdate": False,
                     "SUAllowsAutomaticUpdates": False,
                     "SUVerifyUpdateBeforeExtraction": True,
                     "SURequireSignedFeed": True})
    if release.get("publicKey"):
        data["SUPublicEDKey"] = release["publicKey"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML, sort_keys=False))


def _copy_python(runtime: Path, destination: Path) -> None:
    runtime = runtime.expanduser().resolve()
    executable = runtime / "bin/python3.12"
    if not executable.exists():
        executable = runtime / "bin/python"
    if not executable.exists():
        raise ValueError(f"Python 3.12 runtime is missing bin/python3.12: {runtime}")
    # A uv/venv is only a thin wrapper around a base interpreter. Copy the
    # resolved base distribution and then layer the venv's site-packages over
    # it, avoiding absolute symlinks back into the developer's home directory.
    base = executable.resolve().parent.parent
    _copytree(base, destination)
    # Keep only interpreters needed for subprocess execution. Console scripts
    # (pip/idle/pydoc) are development tooling and can make ad-hoc signing fail
    # because they are unsigned text launchers inside the app framework.
    bin_dir = destination / "bin"
    keep = {"python", "python3", "python3.12"}
    if bin_dir.is_dir():
        for child in bin_dir.iterdir():
            if child.name not in keep:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    # Derive the ABI directory from the copied base, rather than from the
    # interpreter running the builder (which may be a different Python).
    lib_dirs = sorted((base / "lib").glob("python3.*"))
    version = lib_dirs[0].name if lib_dirs else "python3.12"
    packages = runtime / "lib" / version / "site-packages"
    if packages.is_dir():
        _copytree(packages, destination / "lib" / version / "site-packages")


def _write_python_manifest(studio: Path, python_root: Path) -> None:
    """Describe the relocated interpreter relative to Resources/Studio."""
    lib_dirs = sorted((python_root / "lib").glob("python3.*"))
    version = lib_dirs[0].name if lib_dirs else "python3.12"
    libraries = sorted((python_root / "lib").glob("libpython*.dylib"))
    library = f"../../Frameworks/Python/lib/{libraries[0].name}" if libraries else ""
    manifest = {
        "base": "../Python",
        "executable": "../Python/bin/python3.12",
        "library": library.replace("../../Frameworks/Python", "../Python"),
        "version": version,
    }
    target = studio / "runtime/python.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2) + "\n")


def _load_release(source: Path) -> dict:
    metadata = source / "release.json"
    if not metadata.is_file():
        raise FileNotFoundError(f"release metadata missing: {metadata}")
    release = json.loads(metadata.read_text())
    required = ("version", "build", "repository", "feedURL", "publicKey", "automaticChecks")
    missing = [key for key in required if key not in release]
    if missing:
        raise ValueError(f"release metadata missing keys: {', '.join(missing)}")
    if release["repository"] != "oddmundleikvoll/sprintwake-studios":
        raise ValueError("release metadata points at an unexpected repository")
    return release


def _require_runtime_files(source: Path, sparkle: Path | None) -> None:
    required = (
        source / "runtime/build/libraven.dylib",
        source / "runtime/Raven/bindings/python/raven.py",
        source / "runtime/Raven/bindings/python/raven_xmlpy.py",
        source / "runtime/lib/libstoryboard_media.dylib",
        source / "runtime/lib/libstoryboard_update.dylib",
        source / "runtime/MoltenVK/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib",
    )
    missing = [str(path) for path in required if not path.is_file() and not path.is_symlink()]
    if missing:
        raise FileNotFoundError("native runtime files missing: " + ", ".join(missing))
    if sparkle is None or not (sparkle / "Sparkle").exists():
        raise FileNotFoundError("Sparkle.framework is required")


def _audit_macho(app: Path, forbidden: tuple[Path, ...], minimum_system: str = "14.0") -> None:
    """Reject copied binaries linked to developer machines or broken symlinks."""
    for path in app.rglob("*"):
        if path.is_symlink():
            if not path.resolve().exists() or not path.resolve().is_relative_to(app.resolve()):
                raise ValueError(f"dangling bundle symlink: {path}")
            continue
        if not path.is_file():
            continue
        kind = subprocess.run(["file", str(path)], capture_output=True, text=True, check=False).stdout
        if "Mach-O" not in kind:
            continue
        if "arm64" not in kind:
            raise ValueError(f"bundle Mach-O lacks arm64 slice: {path}")
        result = subprocess.run(["otool", "-arch", "arm64", "-L", str(path)], capture_output=True, text=True, check=False)
        if result.returncode:
            raise ValueError(f"cannot inspect Mach-O dependency list: {path}")
        load = subprocess.run(["otool", "-arch", "arm64", "-l", str(path)], capture_output=True, text=True, check=False)
        for line in load.stdout.splitlines():
            if line.strip().startswith("minos "):
                found = line.strip().split()[1]
                if tuple(map(int, found.split(".")[:2])) > tuple(map(int, minimum_system.split(".")[:2])):
                    raise ValueError(f"Mach-O minimum OS {found} exceeds bundle minimum {minimum_system}: {path}")
                break
        for dependency in result.stdout.splitlines():
            if "(compatibility version" not in dependency:
                continue
            name = dependency.strip().split(" (compatibility version", 1)[0]
            if any(str(root) in name for root in forbidden):
                raise ValueError(f"developer path in Mach-O dependency {path}: {name}")
            if name.startswith(("/usr/local/", "/opt/homebrew/", "/opt/local/")):
                raise ValueError(f"non-system absolute dependency {path}: {name}")


def _copy_licenses(source: Path, engine: Path, python_root: Path, studio: Path) -> None:
    destination = studio / "licenses"
    destination.mkdir(parents=True, exist_ok=True)
    candidates = {
        "raven-LICENSE": source / "runtime/Raven/LICENSE",
        "moltenvk-LICENSE": source / "runtime/MoltenVK/LICENSE",
        "sparkle-LICENSE": source / "runtime/Sparkle/LICENSE",
        "engine-LICENSE": engine / "LICENSE",
        "python-LICENSE": python_root / "LICENSE.txt",
    }
    for name, path in candidates.items():
        if path.is_file():
            _copy_file(path, destination / name)
    packages = python_root / "lib"
    for license_path in packages.glob("python3.*/site-packages/*dist-info/licenses/**/*",):
        if license_path.is_file():
            package = license_path.parents[1].name
            relative = license_path.relative_to(license_path.parents[2])
            _copy_file(license_path, destination / "python-" / package / relative)


def _find_ffmpeg(engine: Path, python_runtime: Path) -> Path:
    candidate = engine / "local/bin/ffmpeg"
    if candidate.is_file():
        return candidate.resolve()
    executable = python_runtime / "bin/python3.12"
    if not executable.exists():
        executable = python_runtime / "bin/python"
    result = subprocess.run([str(executable), "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"],
                            capture_output=True, text=True, check=True)
    candidate = Path(result.stdout.strip())
    if not candidate.is_file():
        raise FileNotFoundError("imageio-ffmpeg did not provide a binary")
    return candidate


def build_bundle(source: Path, output: Path, *, engine: Path | None = None,
                 python_runtime: Path | None = None, sparkle: Path | None = None,
                 feed_url: str = "", public_key: str = "", launcher: Path | None = None,
                 icon: Path | None = None) -> Path:
    destination = output.expanduser().absolute()
    if not destination.name.endswith(".app"):
        raise ValueError("bundle output must be an .app path")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to replace existing bundle output: {destination}")
    source = source.expanduser().resolve()
    engine = (engine or ((source / "engine") if (source / "engine").is_dir() else source.parent / "storyboard")).expanduser().resolve()
    if not engine.is_dir():
        raise FileNotFoundError(f"Story engine not found: {engine}")
    if python_runtime is None:
        python_runtime = Path(os.environ.get("RAVEN_BUNDLE_PYTHON", ""))
    if not python_runtime:
        raise ValueError("Pass --python-runtime (a standalone Python 3.12 runtime)")
    python_runtime = Path(python_runtime).expanduser().resolve()
    release = _load_release(source)
    _require_runtime_files(source, sparkle)

    staging_parent = destination.parent.resolve()
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".sprintwake-bundle-", dir=staging_parent))
    app = staging / APP_NAME
    contents = app / "Contents"
    studio = contents / "Resources" / "Studio"
    try:
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Resources").mkdir(parents=True)
        (contents / "Frameworks").mkdir(parents=True)

        # Python imports the native editor modules and the bundled server from
        # this stable directory. Tests, caches, models and user configs are not
        # copied into a release bundle.
        studio.mkdir(parents=True)
        for item in source.glob("*.py"):
            _copy_file(item, studio / item.name)
        for directory in ("assets", "contracts"):
            if (source / directory).is_dir():
                _copytree(source / directory, studio / directory)

        _copytree(engine / "server", studio / "engine/server")
        for name in ("start_fable.py", "stop_fable.py"):
            if (engine / "local" / name).is_file():
                _copy_file(engine / "local" / name, studio / "engine/local" / name)
        ffmpeg = _find_ffmpeg(engine, python_runtime)
        _copy_file(ffmpeg, studio / "engine/local/bin/ffmpeg")
        probe = next((candidate for candidate in (source / "runtime/ffprobe.py", engine / "tools/ffprobe.py", engine / "local/bin/ffprobe") if candidate.is_file()), None)
        if probe is None:
            raise FileNotFoundError("canonical ffprobe shim missing")
        target_script = studio / "engine/local/bin/ffprobe.py"
        _copy_file(probe, target_script)
        target_probe = studio / "engine/local/bin/ffprobe"
        target_probe.write_text("#!/bin/sh\nSCRIPT_DIR=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\nexec \"$SCRIPT_DIR/../../../../Python/bin/python3.12\" -I -B \"$SCRIPT_DIR/ffprobe.py\" \"$@\"\n")
        target_probe.chmod(0o755)
        provenance = subprocess.run([str(ffmpeg), "-version"], capture_output=True, text=True, check=False)
        digest = subprocess.run(["shasum", "-a", "256", str(ffmpeg)], capture_output=True, text=True, check=False)
        provenance_text = "FFmpeg binary provenance\n\n" + provenance.stdout + "\nSHA-256:\n" + digest.stdout
        (studio / "licenses").mkdir(parents=True, exist_ok=True)
        (studio / "licenses/ffmpeg-provenance.txt").write_text(provenance_text)
        for name in ("requirements-ci.txt", "server-config-sample.json"):
            if (engine / name).is_file():
                _copy_file(engine / name, studio / "engine" / name)

        for source_path, relative in (
            (source / "runtime/Raven/bindings/python/raven.py", "runtime/Raven/bindings/python/raven.py"),
            (source / "runtime/Raven/bindings/python/raven_xmlpy.py", "runtime/Raven/bindings/python/raven_xmlpy.py"),
            (source / "runtime/build/libraven.dylib", "runtime/build/libraven.dylib"),
            (source / "runtime/build/libraven.0.dylib", "runtime/build/libraven.0.dylib"),
            (source / "runtime/build/libraven.0.1.0.dylib", "runtime/build/libraven.0.1.0.dylib"),
            (source / "runtime/lib/libstoryboard_media.dylib", "runtime/lib/libstoryboard_media.dylib"),
            (source / "runtime/lib/libstoryboard_update.dylib", "runtime/lib/libstoryboard_update.dylib"),
            (source / "runtime/MoltenVK/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib", "runtime/MoltenVK/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib"),
        ):
            if source_path.is_dir():
                _copytree(source_path, studio / relative)
            elif source_path.is_file():
                _copy_file(source_path, studio / relative)

        python_root = contents / "Resources/Python"
        _copy_python(python_runtime, python_root)
        _write_python_manifest(studio, python_root)
        _copy_licenses(source, engine, python_root, studio)
        if sparkle:
            sparkle = Path(sparkle).expanduser().resolve()
            if sparkle.is_dir():
                _copytree(sparkle, contents / "Frameworks/Sparkle.framework")
            else:
                raise FileNotFoundError(sparkle)

        if launcher:
            _copy_file(Path(launcher), contents / "MacOS/Sprintwake Studios")
        if icon and Path(icon).is_file():
            _copy_file(Path(icon), contents / "Resources/AppIcon.icns")

        (contents / "Resources/release.json").write_text(json.dumps(release, indent=2) + "\n")
        _write_plist(contents / "Info.plist", release=release)
        if not (contents / "MacOS/Sprintwake Studios").is_file():
            raise FileNotFoundError("Native Sprintwake Studios launcher was not supplied")
        _audit_macho(app, (source.resolve(), python_runtime.resolve()), str(release.get("minimumSystemVersion", "14.0")))

        shutil.move(str(app), str(destination))
        return destination.resolve()
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--python-runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sparkle", type=Path)
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--icon", type=Path)
    parser.add_argument("--feed-url", default="")
    parser.add_argument("--public-key", default="")
    args = parser.parse_args(argv)
    print(build_bundle(args.source, args.output, engine=args.engine,
                       python_runtime=args.python_runtime, sparkle=args.sparkle,
                       feed_url=args.feed_url, public_key=args.public_key,
                       launcher=args.launcher, icon=args.icon))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

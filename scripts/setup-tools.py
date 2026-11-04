"""Materialize owned FFmpeg tools for source-tree development."""
from __future__ import annotations
import os, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "runtime/tools"
MARKER = TOOLS / ".raven-tools-v1"

def main() -> int:
    runtime = Path(os.environ.get("RAVEN_BUNDLE_PYTHON", ROOT / "runtime/bundle-venv"))
    python = runtime / "bin/python"
    if not python.is_file():
        raise SystemExit(f"Dedicated runtime missing: {python}; run setup-bundle-runtime.sh first")
    ffmpeg = Path(subprocess.check_output([str(python), "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"], text=True).strip())
    if not ffmpeg.is_file():
        raise SystemExit("imageio-ffmpeg did not provide FFmpeg")
    if TOOLS.exists() and not MARKER.exists() and any(TOOLS.iterdir()):
        raise SystemExit(f"Refusing to replace unmanaged tools directory: {TOOLS}")
    TOOLS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ffmpeg, TOOLS / "ffmpeg")
    probe = ROOT / "runtime/ffprobe.py"
    shutil.copy2(probe, TOOLS / "ffprobe.py")
    wrapper = TOOLS / "ffprobe"
    wrapper.write_text("#!/bin/sh\nSCRIPT_DIR=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\nexec \"$SCRIPT_DIR/../bundle-venv/bin/python\" -I -B \"$SCRIPT_DIR/ffprobe.py\" \"$@\"\n")
    wrapper.chmod(0o755); (TOOLS / "ffmpeg").chmod(0o755)
    MARKER.write_text("Raven tools v1\n")
    print(TOOLS)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

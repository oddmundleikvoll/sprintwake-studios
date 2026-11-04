#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f runtime/build/libraven.dylib ] || [ ! -f runtime/lib/libstoryboard_media.dylib ]; then
  printf 'Run scripts/setup.sh first.\n' >&2
  exit 1
fi
export RAVEN_LIBRARY="$PWD/runtime/build/libraven.dylib"
export PYTHONPATH="$PWD/runtime/Raven/bindings/python${PYTHONPATH:+:$PYTHONPATH}"
export DYLD_FALLBACK_LIBRARY_PATH="$PWD/runtime/MoltenVK/MoltenVK/dynamic/dylib/macOS"
export MVK_CONFIG_LOG_LEVEL=1
export PYTHONDONTWRITEBYTECODE=1
exec .venv/bin/python -B studio.py "$@"

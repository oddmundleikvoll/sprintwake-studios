#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export RAVEN_LIBRARY="$PWD/runtime/build/libraven.dylib"
export PYTHONPATH="$PWD/runtime/Raven/bindings/python${PYTHONPATH:+:$PYTHONPATH}"
export DYLD_FALLBACK_LIBRARY_PATH="$PWD/runtime/MoltenVK/MoltenVK/dynamic/dylib/macOS"
export MVK_CONFIG_LOG_LEVEL=1
export PYTHONDONTWRITEBYTECODE=1
exec .venv/bin/python -B scripts/capture-showcase.py "$@"

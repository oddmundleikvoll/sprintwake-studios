#!/bin/bash
set -euo pipefail
if [ "$#" != 2 ]; then
  printf 'Usage: %s /path/to/sprintwake-engine /path/to/runtime-venv\n' "$0"
  exit 2
fi
RAVEN_SETUP_ENGINE="$1"
RAVEN_SETUP_RUNTIME="$2"
RAVEN_SETUP_MARKER="$RAVEN_SETUP_RUNTIME/.raven-cpu-runtime-v1"
if [ ! -f "$RAVEN_SETUP_ENGINE/server/__main__.py" ] || [ ! -f "$RAVEN_SETUP_ENGINE/requirements-ci.txt" ]; then
  printf 'Select the matching Raven production-engine source.\n' >&2
  exit 1
fi
if [ -f "$RAVEN_SETUP_RUNTIME/pyvenv.cfg" ]; then
  if [ ! -f "$RAVEN_SETUP_MARKER" ]; then
    printf 'This runtime was not created by this helper. Choose a new dedicated directory; existing environments are preserved.\n' >&2
    exit 1
  fi
else
  if [ -e "$RAVEN_SETUP_RUNTIME" ] && [ -n "$(ls -A "$RAVEN_SETUP_RUNTIME")" ]; then
    printf 'Runtime destination is not an empty directory or a Python virtual environment.\n' >&2
    exit 1
  fi
  uv venv --python 3.12 "$RAVEN_SETUP_RUNTIME"
  printf 'Raven CPU runtime v1\n' > "$RAVEN_SETUP_MARKER"
fi
"$RAVEN_SETUP_RUNTIME/bin/python" -I -B -c 'import sys; assert sys.version_info[:2] == (3, 12), "Select a dedicated Python 3.12 runtime"'
uv pip sync --python "$RAVEN_SETUP_RUNTIME/bin/python" "$RAVEN_SETUP_ENGINE/requirements-ci.txt"
printf 'CPU runtime ready: %s/bin/python\nNo models were installed or started. Install FFmpeg separately and select paths in Raven.\n' "$RAVEN_SETUP_RUNTIME"

#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

ENGINE="${1:?engine source required}"
DESTINATION="${2:?destination venv required}"
MARKER="$DESTINATION/.raven-bundle-runtime-v1"
REQUIREMENTS="$PWD/runtime/requirements-bundle.in"
if [ ! -f "$ENGINE/server/__main__.py" ] || [ ! -f "$REQUIREMENTS" ]; then
  printf 'Select the matching Raven production-engine source.\n' >&2
  exit 1
fi
if [ -f "$DESTINATION/pyvenv.cfg" ]; then
  if [ ! -f "$MARKER" ]; then
    printf 'Existing runtime was not created by this helper; preserving it.\n' >&2
    exit 1
  fi
else
  if [ -e "$DESTINATION" ] && [ -n "$(ls -A "$DESTINATION" 2>/dev/null)" ]; then
    printf 'Runtime destination is not empty; preserving it.\n' >&2
    exit 1
  fi
  BASE_PYTHON="${RAVEN_BUNDLE_BASE_PYTHON:-$(uv python find 3.12)}"
  uv venv --python "$BASE_PYTHON" "$DESTINATION"
  printf 'Raven bundle runtime v1\n' > "$MARKER"
fi
"$DESTINATION/bin/python" -I -B -c 'import sys; assert sys.version_info[:2] == (3, 12)'
LOCK_FILE="$PWD/runtime/requirements-bundle.txt"
if [ "${RAVEN_REFRESH_BUNDLE_LOCK:-0}" = "1" ]; then
  INPUT_FILE="$(mktemp "${TMPDIR:-/tmp}/raven-bundle-requirements.XXXXXX")"
  trap 'rm -f "$INPUT_FILE"' EXIT
  cp "$REQUIREMENTS" "$INPUT_FILE"
  uv pip compile --python-version 3.12 --generate-hashes --no-header --output-file "$LOCK_FILE" "$INPUT_FILE"
fi
uv pip sync --python "$DESTINATION/bin/python" "$LOCK_FILE"
printf '%s\n' "$DESTINATION"

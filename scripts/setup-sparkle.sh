#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
SPARKLE_VERSION="$(.venv/bin/python -c 'import json; print(json.load(open("runtime/versions.json"))["sparkle"])')"
SPARKLE_HASH="$(.venv/bin/python -c 'import json; print(json.load(open("runtime/versions.json"))["sparkle_sha256"])')"
ARCHIVE="$PWD/runtime/downloads/Sparkle-$SPARKLE_VERSION.tar.xz"
mkdir -p runtime/downloads runtime/Sparkle
if [ ! -f "$ARCHIVE" ]; then
  curl -fL --retry 3 "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VERSION/Sparkle-$SPARKLE_VERSION.tar.xz" -o "$ARCHIVE"
fi
.venv/bin/python - "$ARCHIVE" "$SPARKLE_HASH" <<'PY'
from pathlib import Path
import hashlib,sys
if hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()!=sys.argv[2]:raise SystemExit('Sparkle archive checksum mismatch')
PY
tar -xf "$ARCHIVE" -C runtime/Sparkle

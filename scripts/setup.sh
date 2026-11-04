#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --frozen
mkdir -p runtime/downloads runtime/lib
ENGINE_ROOT="${RAVEN_BUNDLE_ENGINE:-$PWD/engine}"
scripts/setup-bundle-runtime.sh "$ENGINE_ROOT" "$PWD/runtime/bundle-venv"
uv run --frozen python -B scripts/setup-tools.py
RAVEN_REV=$( .venv/bin/python -c 'import json; print(json.load(open("runtime/versions.json"))["raven"])' )
if [ ! -d runtime/Raven/.git ]; then
  git clone https://github.com/oscar-soirey/Raven.git runtime/Raven
fi
if [ "$(git -C runtime/Raven rev-parse HEAD)" != "$RAVEN_REV" ]; then
  git -C runtime/Raven fetch origin "$RAVEN_REV"
  git -C runtime/Raven checkout --detach "$RAVEN_REV"
fi
for COCOA_PATCH in "$PWD/runtime/patches/cocoa-input.patch" "$PWD/runtime/patches/cocoa-minimize.patch"; do
  if git -C runtime/Raven apply --check "$COCOA_PATCH" 2>/dev/null; then
    git -C runtime/Raven apply "$COCOA_PATCH"
  elif ! git -C runtime/Raven apply --reverse --check "$COCOA_PATCH" 2>/dev/null; then
    printf 'The Raven Cocoa patch conflicts with local changes: %s\n' "$COCOA_PATCH" >&2
    exit 1
  fi
done
MOLTEN_VERSION=$( .venv/bin/python -c 'import json; print(json.load(open("runtime/versions.json"))["moltenvk"])' )
ARCHIVE="runtime/downloads/MoltenVK-macos-$MOLTEN_VERSION.tar"
if [ ! -f "$ARCHIVE" ]; then
  curl -L --fail --show-error "https://github.com/KhronosGroup/MoltenVK/releases/download/$MOLTEN_VERSION/MoltenVK-macos.tar" -o "$ARCHIVE"
fi
.venv/bin/python - "$ARCHIVE" <<'PY'
import hashlib, json, sys
from pathlib import Path
expected = json.loads(Path('runtime/versions.json').read_text())['moltenvk_sha256']
if hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest() != expected:
    raise SystemExit('MoltenVK checksum mismatch; remove the incomplete archive and retry.')
PY
tar -xf "$ARCHIVE" -C runtime
.venv/bin/cmake -S runtime/Raven -B runtime/build \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_DEPLOYMENT_TARGET=14.0 -DRAVEN_BUILD_SHARED=ON -DRAVEN_BUILD_TESTS=ON \
  -DRAVEN_BUILD_EXAMPLES=OFF -DRAVEN_BUILD_TOOLS=OFF -DRAVEN_BUILD_WEB=OFF \
  -DRAVEN_FETCH_VULKAN_HEADERS=OFF \
  -DRAVEN_VULKAN_INCLUDE_DIR="$PWD/runtime/MoltenVK/MoltenVK/include"
.venv/bin/cmake --build runtime/build --parallel 8
./scripts/setup-sparkle.sh
./scripts/build-native.sh
RAVEN_BUILD_ONLY=1 RAVEN_BUNDLE_PYTHON="$PWD/runtime/bundle-venv" RAVEN_PACKAGE_OUTPUT="${RAVEN_PACKAGE_OUTPUT:-$PWD/dist/Sprintwake Studios.app}" ./scripts/package-app.sh
printf 'Ready. Standalone app built without installation; verify it before installing.\n'

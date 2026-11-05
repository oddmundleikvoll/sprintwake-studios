#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

APP_NAME="Sprintwake Studios.app"
APP_ROOT="$HOME/Applications"
if [ -n "${RAVEN_BUNDLE_ENGINE:-}" ]; then
  ENGINE_ROOT="$RAVEN_BUNDLE_ENGINE"
elif [ -d "$PWD/engine" ]; then
  ENGINE_ROOT="$PWD/engine"
else
  ENGINE_ROOT="$PWD/engine"
fi
PYTHON_RUNTIME="${RAVEN_BUNDLE_PYTHON:-}"
FEED_URL="${RAVEN_SPARKLE_FEED_URL:-}"
PUBLIC_KEY="${RAVEN_SPARKLE_PUBLIC_KEY:-}"
SPARKLE_FRAMEWORK="${RAVEN_BUNDLE_SPARKLE:-$PWD/runtime/Sparkle/Sparkle.framework}"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sprintwake-package.XXXXXX")"
trap 'rm -rf "$BUILD_DIR"' EXIT

if [ -z "$PYTHON_RUNTIME" ]; then
  PYTHON_RUNTIME="$BUILD_DIR/bundle-venv"
  scripts/setup-bundle-runtime.sh "$ENGINE_ROOT" "$PYTHON_RUNTIME" >/dev/null
fi

LAUNCHER="$BUILD_DIR/Sprintwake Studios"
xcrun clang -fobjc-arc -O2 -Wall -Wextra -Werror -mmacosx-version-min=14.0 -framework Foundation native/launcher.m -o "$LAUNCHER"

ICON="$BUILD_DIR/AppIcon.icns"
ICONSET="$BUILD_DIR/AppIcon.iconset"
mkdir -p "$ICONSET"
xcrun clang -fobjc-arc -O2 -Wall -Wextra -Werror -mmacosx-version-min=14.0 -framework AppKit native/icon.m -o "$BUILD_DIR/draw-icon"
"$BUILD_DIR/draw-icon" "$ICONSET/icon_512x512@2x.png"
for SIZE in 16 32 128 256 512; do
  sips -z "$SIZE" "$SIZE" "$ICONSET/icon_512x512@2x.png" --out "$ICONSET/icon_${SIZE}x${SIZE}.png" >/dev/null
  if [ "$SIZE" != 512 ]; then
    DOUBLE=$((SIZE * 2))
    sips -z "$DOUBLE" "$DOUBLE" "$ICONSET/icon_512x512@2x.png" --out "$ICONSET/icon_${SIZE}x${SIZE}@2x.png" >/dev/null
  fi
done
iconutil -c icns "$ICONSET" -o "$ICON"

BUNDLE="$BUILD_DIR/$APP_NAME"
if [ -n "${RAVEN_PACKAGE_OUTPUT:-}" ]; then
  BUNDLE="$RAVEN_PACKAGE_OUTPUT"
fi
BUILD_ARGS=(
  --source "$PWD" --engine "$ENGINE_ROOT" --python-runtime "$PYTHON_RUNTIME"
  --output "$BUNDLE" --launcher "$LAUNCHER"
  --icon "$ICON"
  --feed-url "$FEED_URL" --public-key "$PUBLIC_KEY"
)
BUILD_ARGS+=(--sparkle "$SPARKLE_FRAMEWORK")
uv run --frozen python -B scripts/build_bundle.py "${BUILD_ARGS[@]}"

SIGN_IDENTITY="${RAVEN_CODESIGN_IDENTITY:--}"
sign_one() {
  if [ "$SIGN_IDENTITY" = "-" ]; then
    codesign --force --sign - "$1"
  else
    codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$1"
  fi
}
# Sign nested Mach-O payloads first. Sparkle's helper/XPC binaries otherwise
# make a superficially successful --deep sign fail after relocation.
while IFS= read -r -d '' MACHO; do
  if file "$MACHO" | grep -q 'Mach-O'; then
    sign_one "$MACHO"
  fi
done < <(find "$BUNDLE" -type f -print0 | sort -rz)
# Re-seal nested code containers after their executables. Sparkle ships XPC
# services and an updater app inside a versioned framework.
while IFS= read -r -d '' CODE_BUNDLE; do
  if [ "$CODE_BUNDLE" != "$BUNDLE" ]; then sign_one "$CODE_BUNDLE"; fi
done < <(find "$BUNDLE" -depth -type d \( -name '*.xpc' -o -name '*.app' -o -name '*.framework' \) -print0)
sign_one "$BUNDLE"
codesign --verify --deep --strict "$BUNDLE"

TARGET="$APP_ROOT/$APP_NAME"
if [ "${RAVEN_BUILD_ONLY:-0}" = "1" ]; then
  if [ -z "${RAVEN_PACKAGE_OUTPUT:-}" ]; then
    printf 'RAVEN_PACKAGE_OUTPUT is required with RAVEN_BUILD_ONLY=1\n' >&2
    exit 2
  fi
  printf 'Built (not installed) %s\n' "$BUNDLE"
  exit 0
fi
mkdir -p "$APP_ROOT"
uv run --frozen python -B scripts/install_app.py install "$APP_ROOT" "$BUNDLE" "$TARGET"
if [ -f scripts/migrate_local_settings.py ] && [ -x .venv/bin/python ]; then
  .venv/bin/python scripts/migrate_local_settings.py --legacy-engine "$PWD/../storyboard" --bundle "$TARGET"
fi
printf 'Installed %s\n' "$TARGET"

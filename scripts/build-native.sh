#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p runtime/lib
xcrun clang -dynamiclib -fobjc-arc -O2 -Wall -Wextra -Werror -mmacosx-version-min=14.0 \
  -framework Security -framework AppKit -framework AVFoundation -framework CoreVideo -framework CoreMedia \
  native/media.m -o runtime/lib/libstoryboard_media.dylib
xcrun clang -dynamiclib -fobjc-arc -O2 -Wall -Wextra -Werror -mmacosx-version-min=14.0 \
  -framework AppKit -F runtime/Sparkle -framework Sparkle \
  -Wl,-rpath,@loader_path/../../../../Frameworks \
  -Wl,-rpath,@loader_path/../Sparkle \
  native/updater.m -o runtime/lib/libstoryboard_update.dylib

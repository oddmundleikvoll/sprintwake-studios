#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --frozen python -B -m unittest discover -s tests -v
./scripts/build-native.sh
# Upstream's focused headless controls/layout/XML/thread tests; no models.
.venv/bin/ctest --test-dir runtime/build --output-on-failure \
  -R '^test_(layout|widgets|xml|threads|memory|tools|themes)$'
git diff --check
git diff --cached --check

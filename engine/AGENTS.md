# Engine contribution rules

The engine is part of the Sprintwake Studios monorepo and must remain usable
both from source and from `Contents/Resources/Studio/engine` in a relocated
app bundle. Do not encode developer home directories, personal model paths or
private credentials in engine code or defaults.

Keep CPU media dependencies pinned in the bundle lock. FFmpeg and ffprobe are
resolved through Raven's owned runtime/tools during development and through
bundle-relative paths in a packaged app. Models stay external to source,
bundles and CI.

Engine tests must not start ComfyUI, download models or mutate a user's
production workspace. Use temporary workspaces and explicit fixtures. Changes
affecting the native bridge, process lifetime, update gate or filesystem
contracts require a focused test and a source-plus-relocated-bundle check.

CPU checks must pass with RAVEN_MODEL_ROOT pointing at a nonexistent directory.
Image-generation fixtures must also provide model identity (qwen_model_signature),
so provenance/recipe checks never fall back to a developer's installed weights.

Numeric loopback HTTP binding must not call reverse DNS: hosted macOS resolvers
can block before the readiness endpoint begins serving. Keep standard server
naming for other bind addresses. Focused CPU check from this directory:

```sh
../runtime/bundle-venv/bin/python -B -m unittest tests.test_http_startup -v
```

The public native app and expert director guide use English by default, with the
user's requested project/dialogue language preserved. Human-readable contract
errors and the guide are canonical here; refresh the native contracts/ mirrors
with scripts/sync-contracts.py --write from the app root. Keep tool IDs, schemas,
frame rules and review/continuity requirements unchanged when translating prose.
Focused guide/anchor and native-facing error verification from this directory:

```sh
RAVEN_LTX_BRIDGE="$PWD/../qa/no-external-worker" ../runtime/bundle-venv/bin/python -B -m unittest tests.test_raven_contract tests.test_ltx_anchors tests.test_film_chat -v
```

This uses the explicit unavailable-external-worker branch; the pure contracts
remain required. Actual model/worker qualification is separate from UI localization.

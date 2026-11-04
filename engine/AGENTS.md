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

Numeric loopback HTTP binding must not call reverse DNS: hosted macOS resolvers
can block before the readiness endpoint begins serving. Keep standard server
naming for other bind addresses. Focused CPU check from this directory:

```sh
../runtime/bundle-venv/bin/python -B -m unittest tests.test_http_startup -v
```

# Contributing to Sprintwake Studios

The source targets Apple Silicon and macOS 14 or later. Install Apple's command
line tools and `uv`, then run from the repository root:

```sh
./scripts/setup.sh
./scripts/check.sh
./run.command --smoke-test
```

Setup uses the included `engine/`, the committed dependency locks and pinned
native dependencies. It builds an app under `dist/` without installing it.
For a subsequent setup build, choose a fresh `RAVEN_PACKAGE_OUTPUT`; existing
outputs are preserved. An external engine is optional and must be selected
explicitly with `RAVEN_BUNDLE_ENGINE=/absolute/path/to/engine`.

The full native smoke needs an unlocked graphical macOS session. It uses a
disposable production root and synthetic media without model inference. Do not
use real productions for tests. CI runs the source/native/CPU checks in
[.github/workflows/ci.yml](.github/workflows/ci.yml) without publishing artifacts.

For source-setup and publication-exclusion changes, the focused check is:

```sh
uv run --frozen python -B -m unittest tests.test_source_setup tests.test_bundle -v
```

Keep changes focused and describe the behavior, verification and limitations in
the pull request. Read [AGENTS.md](AGENTS.md) and the engine's instructions before
changing production contracts. Refresh matching pure contracts only through the
documented synchronization helper. Do not weaken production review, immutable
media, queue ownership or synchronous model-release barriers.

Keep personal connections, API keys, signing material, settings backups, model
weights, generated media, runtime downloads and QA captures out of commits.
Review both staged and unstaged changes before committing; `.gitignore` does not
remove files already tracked by Git. Credentials belong in the OS Keychain.

Preserve upstream copyrights and license notices. Contributions to Sprintwake
Studios use [CC BY-NC-SA 4.0](LICENSE), including the noncommercial and share-alike
conditions. The production engine and its copied contracts retain
[engine/LICENSE](engine/LICENSE). Separately licensed dependencies retain their
own terms; see [NOTICE.md](NOTICE.md) before proposing licensing changes.

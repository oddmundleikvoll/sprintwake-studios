# Sprintwake Studios distribution

Sprintwake Studios is distributed from one repository: the Raven desktop app,
the Storyboard engine, native runtime, packaging scripts and GitHub Releases all
live together. The repository is intended for Apple Silicon macOS 14 or newer.

The application bundle is self-contained. Models and large production media are
never committed or copied into the app. Projects, settings and Keychain items
remain in the user's existing Raven/Storyboard locations and use the legacy
domains for continuity.

## Local QA build

From the repository root:

```sh
uv sync --frozen
./scripts/setup.sh
# Subsequent builds reuse the isolated runtime:
uv run --frozen python -B scripts/setup-tools.py
RAVEN_BUILD_ONLY=1 \
RAVEN_PACKAGE_OUTPUT="$PWD/qa/fresh.noindex/Sprintwake Studios.app" \
RAVEN_BUNDLE_PYTHON="$PWD/runtime/bundle-venv" \
scripts/package-app.sh
```

The build refuses to replace an existing output bundle. Resolved output paths
must be below a `.noindex` directory, including when ancestors are symlinks.
It does not install an app or migrate settings. For normal standalone QA, use
the managed build/check path, which retires its app on success and failure:

```sh
RAVEN_BUNDLE_PYTHON="$PWD/runtime/bundle-venv" ./scripts/package-app.sh --verify
```

Reports and recovery receipts remain under `qa/`; executable test copies do not.
An existing explicit build can also be checked and consumed with:

```sh
.venv/bin/python -B scripts/check-bundle.py "$PWD/qa/fresh.noindex/Sprintwake Studios.app" --output "$PWD/qa/window-fresh.noindex"
```

## Updates and release policy

Sparkle checks the committed appcast/public key, but updates are applied only
after active productions quiesce: saves complete, the engine exits, and the
global reader/writer gate is clear. The update coordinator preserves projects,
model configuration and Keychain data.

The update signing account is the Keychain account
`sprintwake-studios-updates`; its public key is committed in `release.json`.
No Developer ID certificate is currently available, so local builds are
ad-hoc only. A public release additionally requires Developer ID signing,
notarization and a verified Sparkle appcast. The independently authored current source uses MIT; historical source retains its original terms. No downloadable app release or appcast has been published.

CI has read-only repository permission and never uploads artifacts or creates
releases. Signing/notarization jobs remain disabled until their secrets and
certificates are explicitly configured.

## Local installation and update verification

The normal installer command, after reviewing a fresh signed QA build:

```sh
.venv/bin/python -B scripts/install_app.py install "$HOME/Applications" "$PWD/qa/fresh.noindex/Sprintwake Studios.app" "$HOME/Applications/Sprintwake Studios.app"
.venv/bin/python -B scripts/migrate_local_settings.py --legacy-engine "$PWD/../storyboard" --bundle "$HOME/Applications/Sprintwake Studios.app"
```

Installation refuses a running old/new app, an active registered engine reader,
an invalid signature and an unknown destination. It verifies a copy staged on
the target volume before swapping, preserving source and previous app on failure.
Only after a verified replacement is committed does it retire the previous app,
known legacy launcher and input build. Each obsolete app is moved to Trash and
converted to a ZIP: file bytes, entry types, permissions, symlinks and an unchanged
source snapshot are checked before removing the executable directory. The ZIP
and recovery receipt are synced to disk first. Failure preserves the original
app directory. ZIPs cannot be rediscovered as app bundles by macOS.

Original and actual Trash URLs are unregistered. Recovery receipts live under
`~/Applications/qa/app-cleanup.noindex/`; each records `backup` and `restoreName`.
To recover, extract the ZIP and rename its root to the receipt's restore name.
If cleanup fails after commit, the error states that the new app is installed
and retains its handoff and recovery data.
The retired generic `Storyboard.app` is never touched. Local migration copies
missing private configuration, preserving explicit custom engines/runtimes and
all model/service selections. It never moves projects or weights.

The isolated Sparkle fixture prepares a signed loopback feed and synthetic
video production, exercises the real Sparkle UI/install/relaunch, compares retained
project/settings hashes, and separately checks rejection of a modified signed feed:

```sh
.venv/bin/python -B scripts/test-sparkle-update.py --bundle "$PWD/qa/fresh.noindex/Sprintwake Studios.app" --output "$PWD/qa/update-fresh.noindex"
```

The default fixture generates a disposable Ed25519 test key in a private temporary
directory, deletes it afterwards, and never reads the production private key.
Pass `--account sprintwake-studios-updates` only to test the real Keychain signing
account. A logged-in, unlocked graphical macOS session is required for the
standard Sparkle UI. Choose **Install Update**, then **Install and Relaunch**
when extraction is complete. The download indicator reopens a hidden update
window. The fixture then verifies production waiting, replacement and relaunch;
the modified-feed scenario is automatic. It never queries GitHub or starts a model.
Choose new output paths for each run. Disposable QA app IDs/data are separate
from the normal app. The fixture always retires its Installed/payload app copies
in `finally`, including failed or cancelled scenarios, and cleans registrations
for that run's unique QA identifier. Model inference is outside this distribution check.

Production release assets can be prepared locally, without uploading:

```sh
.venv/bin/python -B scripts/prepare-release.py --bundle "$HOME/Applications/Sprintwake Studios.app" --output "$PWD/dist/release-0.9.0"
```

Increase both version and integer build in `release.json` for a later release;
never change the bundle ID or update public key casually. Keep the private signing
key in Keychain and maintain a secure backup independently of the repository.
Developer ID builds use `RAVEN_CODESIGN_IDENTITY`; signing, notarization, stapling
and a fresh install/update test must precede public distribution. The application's
source uses MIT following replacement of the prior CC engine; dependencies
retain their own terms in NOTICE.md. Binary redistribution review remains a
separate release requirement.

## Data and process lifetime

- Projects/media: `~/Movies/Raven Studio` by default, or the user's chosen root.
- Connections: `~/Library/Application Support/Raven Studio/connections.json`.
- Engine settings: adjacent private `EngineSettings/`; update locks: `Updates/`.
- Credentials: existing endpoint-scoped `Raven Studio connections` Keychain service.
- App diagnostics: `~/Library/Logs/Sprintwake Studios/app.log`.

The app admits one native instance. All engines started from this bundled/source
version hold a lifetime reader. A persistent update marker blocks old-code starts
during Sparkle handoff, and only the matching updated build clears it. A separate
legacy standalone engine uses its own untouched source; replacing this app does
not replace that engine. No installer kills a foreign process. After an interrupted
installer, use the matching new full app build to complete the handoff; never
clear the pending marker just because a timeout elapsed.

The local qualification on 2026-11-04 passed a real signed replacement/relaunch,
production waiting, ten preserved QA files and signed-body tampering rejection.
The installed app also retained all eleven audited existing production/settings
files. Evidence is retained under ignored `qa/`; no public release was made.

Temporary app copies are retired with `scripts/cleanup-app.py`, which preserves
verified recovery ZIPs in Trash and receipts outside the app. Keep only
`~/Applications/Sprintwake Studios.app` discoverable; retain JSON/log reports
instead of executable QA bundles. `.noindex` staging also reduces indexing.
Unregistering an app folder in Trash alone is insufficient: macOS can rediscover
it hours later. Do not retain obsolete executable folders there.

App startup, backend reconnects and update handoff never enumerate Trash or
reconcile old app registrations. The exact matching new bundle/build completes
its lifetime handoff under the startup, instance and code-reader locks, without
filesystem maintenance. macOS denial of Trash access cannot block that handoff.
Transactional installation and disposable QA retain retirement of their own
explicitly known obsolete bundles, serialized by the cleanup lock. They do not
search Trash for other applications. Existing user media, models, settings,
Sprintwake Projects and vendor SDKs remain protected; Trash is never emptied
and the LaunchServices database is never reset. Installer errors remain visible
with recovery data retained.

# Sprintwake Studios production studio

Native macOS Raven/Python studio with its production engine in `engine/`.
The installed bundle is `~/Applications/Sprintwake Studios.app`, a self-contained
real directory. The bundle includes Python, native libraries, FFmpeg and Sparkle;
it must never point back into a developer checkout. Build staging is separate from
transactional installation. Existing build outputs are preserved. Only the prior
`Storyboard Raven.app` may be archived by the installer, after it has stopped.
Never restore the retired `Storyboard.app` wrapper.

Keep bundle ID `no.oddmund.storyboard.raven`, Raven Studio data/settings paths and
Keychain service stable. User media, weights, credentials and machine settings
must remain outside the bundle. `DISTRIBUTION.md` documents build/update commands.
The user authorized public source publication on 2026-11-04, then requested an independent replacement of the CC engine and chose MIT on 2026-11-05. The current engine/contracts are independently authored MIT code; historical versions retain their CC license.
Do not publish downloadable app releases without a separate instruction and the
release qualifications in DISTRIBUTION.md. No long production for implementation QA.

Read `PROJECT.md`, `README.md`, `CONTEXT.md`, `STATUS.md`, and `pyproject.toml` before changes.
`PROJECT.md` is the current goal/scope/release checklist. Earlier implementation
plans and older STATUS entries are history, not instructions to redo finished work.
The user authorized full production integration on 2026-10-26 and chose
`~/Movies/Raven Studio` for all new production data. This supersedes
the earlier prototype-only restriction. Keep the original Storyboard's existing
projects/configuration and oMLX app untouched. Use the included independent production
backend in a Raven-owned child process with external data-dir and desktop pipe
ownership. Keep canonical receipts/refs/shots; never bypass image-plan/review
gates or terminate foreign work. No long film generation for implementation QA.
Original imported files stay read-only; create independent Raven projects.

Use `uv sync --frozen`. Raven and MoltenVK are pinned by `runtime/versions.json`;
`scripts/setup.sh` builds their isolated runtime and the native AVFoundation bridge.
No top-level test runner or CI existed when this independent prototype was added.

Public-source preparation was requested on 2026-11-04. Keep local engine settings,
backups, locks, weights and runtime binaries out of Git; retain engine/local's
*-sample.json templates. LICENSE and engine/LICENSE cover independently authored code under MIT; NOTICE.md retains historical upstream credit and separate dependency/model terms. Never copy the retired CC implementation back into the current engine/contracts. Setup defaults to included engine/;
RAVEN_BUNDLE_ENGINE remains an explicit override. Focused verification:

```sh
uv run --frozen python -B -m unittest tests.test_source_setup tests.test_bundle -v
```

CPU-only import fixtures must explicitly disable model profiles and use private
empty language/speech/music settings with no inherited credentials. They must not
depend on optional service discovery or a developer's running model servers.

From this directory:

```sh
./scripts/setup.sh
./run.command
uv run --frozen python -B -m unittest discover -s tests -v
./scripts/check.sh
./run.command --smoke-test
```

`--smoke-test` opens the actual Raven/Vulkan window in a disposable production
root. It exercises project CRUD, authoring, uploads, references, comparison,
take history, preflight, native video/audio, concurrent saves, export and reopen.
It captures evidence and exits without starting model inference.
Native media code belongs in `native/media.m`; Raven owns the entire app UI.
Do not replace the app with a browser, webview, or screenshot facade.
Keep generated runtime, caches, local workspace and QA captures out of Git.

Shared images use library.py and Library/index.json. Imports are verified copies;
never delete source media that existing recipes may reference. The director writer
requires an explicit authoring action, snapshots each commit, runs bounded batches
and preserves revision guards. Never start image/video from manuscript authoring.

Director tools use the pure canonical catalogue in engine/server/raven_contract.py;
new capabilities require schema, expert-guide and executor mapping together.
Conversations live in System/Conversations; do not return to one file per project.
Reply/execution completions retain originating session identity. Model-provided
image IDs use shared:/project:/generated: prefixes, never arbitrary paths. Resolve
project IDs with the canonical pure anchor catalogue. Compare authoring digests,
use CAS on board writes and sync Script from the server-normalized board. A
persisted running execution is uncertain after restart and must not auto-replay.
A create_project → write_manuscript proposal is the sole combined create/author
exception. Bind its writer to the actual created board and rebase only the turn
with that creation receipt. Keep failed writer brief/count/checkpoint for explicit
same-project resume; never replay project creation to resume writing. Setup/shots
use stage-specific schemas and bounded validation feedback, with the full brief
and bible retained in every retry. Rebuild the smaller slot plan after truncation.
Director response diagnostics must survive session persistence and remain visible.
Default authoring batches are three shots; the continuous camera group cap is six.
Explicit resume_only is internal and must refuse changed/missing checkpoints before
model calls or writes. Keep pending-save reconciliation and done idempotence. Native
and core digests normalize only empty ravenNotes to None, preserving old journals.
The expert guide is adapted from the project film-regi/Comfy skills, not a promise
that the model has shell, web or arbitrary MCP access. Native smoke uses inert
model replies and real APIs; do not start media inference for routine UI QA.

Director editorial inspection (0.6.1): the pure shared projection in
engine/server/raven_editorial.py reads saved cut/coverage/structure/sound
without creating folders, reconciling new cards or writing journals/manuscripts.
Keep native v1 format parity covered by actual store roundtrips, reject unsafe
paths/malformed current/history state, and bound input/context size explicitly.
No media paths/history enter agent context. All-read batches may execute directly;
mixing inspection and mutation still requires separate proposals. Panels own edits.
Public setup/model-adapter work is recorded in OPEN_SOURCE_READINESS.md; do not
claim generic own-model support from the existing injectable backend parameters.

```sh
uv run --frozen python -B -m unittest tests.test_director_editorial tests.test_director_executor tests.test_director_workspace -v
```

Project deletion is explicit and recoverable: native UI confirms the selected
project and calls /api/raven/boards/<slug>/trash under the server admission lock.
Deleted productions live in Trash/<id>/Project plus entry.json. Restore must
preserve original slug/media refs and refuse collisions. Never permanently purge
user productions as UI QA. Exercise delete/restore only in the disposable smoke
workspace, including byte-identical files and retained media.

Room blocking belongs in Script/coverage.json through coverage.py, independently
of canonical production. Preserve the metre/axis/projection convention shared by
preview, warnings and descriptions. Native preview and layout PNG must use the
same z-buffer pixels; do not sort large room polygons by average depth. Commit
dragging once on mouse-up, including release outside the canvas. Coverage undo
never changes production shots. Append selected safe angles only through fresh
authoring-digest/timestamp CAS, with snapshots and server-normalized Script sync.
Each new angle starts a new directorScene; unchanged-angle retries are idempotent.
Never silently select an unusable framing candidate. A layout reference is simple
geometry, not generated media or an approval. Handoff must map proxy colours to
actual cast references and leave model generation to the image composer action.
Coverage I/O and project relocation exclude each other. Focused checks:

```sh
uv run --frozen python -B -m unittest discover -s tests -p 'test_coverage*.py' -v
```

Editorial decisions belong in Script/cut.json through cut.py, never in canonical
shot timing/order, outputs or production receipts. Preserve selected take bytes
as verified Input copies. Editing/undo must never call inference or the canonical
adopt endpoint. Keep picture/audio frame offsets aligned at 24 fps. Reconcile
native gestures only after mouse-up; callbacks fire while the C widget is still
using its items. One gesture is one undo step. Exported rough cuts may contain
labelled storyboard placeholders and must not count as production approval or
replace the canonical final movie. Scene widgets must release media references
before project trash/archive moves. Cut tests use synthetic local FFmpeg media,
and the disposable native smoke covers actual drag/trim/takes/reopen/export.

Narrative goals and assignments belong in Script/structure.json; sound cues and
bus state in Script/sound.json. Both use independent locked CAS journals with
50 undo/redo entries. Distinguish narrative scenes from directorScene camera
groups. Runtime goals never retime sources or launch inference. Apply narrative
order explicitly to CutStore, preserving all source/take/trim data. Keep a durable
known-shot register so reopen does not recreate deliberately removed assignments;
new cards must survive undo, and no-op opens must not increment revisions.

Sound uses absolute film frames at 24 fps; imported audio is immutable Input data.
One drag/trim is one commit after mouse-up. Separate Dialog/Music/Ambience/Effects
lanes can overlap and cross picture cuts. Existing video audio is a combined
source routed through Dialog, not automatically separated stems. Honor mute/solo,
source trims, gain and fades identically in preview/export. Source fade positions
must survive truncation at film end. Mix in bounded batches with float intermediate
audio; one final compensated limiter/PCM pass writes exactly cutFrames*2000 stereo
samples at 48 kHz. Validate source and cache bytes, paths and duration. Reuse
picture encoding for audio-only changes. Cache output is transient and must not
enter project catalogues/ZIPs; source audio and journals must. Do not claim live
mixing or quality qualification of a long film from synthetic signal tests.

Focused verification:

```sh
uv run --frozen python -B -m unittest tests.test_sequence tests.test_sound tests.test_sound_media tests.test_editorial_timing tests.test_editorial_export -v
```

Shared editing layout (0.7.0): film/scenes/structure/cut/sound share film.page,
with a compact ID-based outline, one central player, contextual right inspectors
and a bottom timeline. Keep the Copper palette and New York/SFNS families.
Native Border has one child; wrap multiple sections in a VBox. Preserve both
independent picture/sound timeline callback and mouse-up journal semantics.
Collapsed outline rows have no widget handles: selection must tolerate absence.
Read existing narrative groups on open; keep transient selection/collapse out of
project journals. Inline prepared-mix playback owns the existing single AVPlayer;
release ownership on edits, mode/project changes or modal review. Never create
production playback receipts for this editorial preview. Sound positions remain
absolute even when scrubbing beyond picture duration. Normal/compact geometry,
ID selection, collapse/transport, trims, audio and byte-preserving navigation are
covered by the full native smoke. Focused pure/layout boundary check:

```sh
uv run --frozen python -B -m unittest tests.test_layout tests.test_director_editorial -v
```

Start page (0.10.0): ordinary launch always shows the three image/clip/film
cards, including with a saved project-bound conversation. Restore that conversation
only through explicit director navigation. Keep Start separate from Prosjekter.
creation_panel.py owns the quick entrances and version-1 Script/creation.json room
hint; it never changes engine schemas. Clip jobs use the existing single-take API,
save first and retain external-workflow confirmation. Opening any room never runs
inference. Native smoke covers both sizes, actual card clicks, room creation,
one inert take request, persisted edits/reopen and original production bytes.
Studio opening must accept empty name/description; only explicit generation
requires a prompt. Exercise the actual dynamic open buttons with empty fields
as well as prefilled fields. Keep progress/errors beside those buttons.

```sh
uv run --frozen python -B -m unittest tests.test_creation tests.test_layout tests.test_workflow_panel -v
```


Model connections (0.8.0): `connections.py` owns version-1 installation/provider
settings under ~/Library/Application Support/Raven Studio, outside productions.
Defaults discover today's sibling engine/runtime/configured workspace and retain
Qwen/LTX/local language selections. Missing engine/runtime must open native setup.
`contracts/` bundles pure engine contracts; refresh only from matching independent source and
keep parity covered. Config saves apply on the next ordinary launch; never move
productions or restart active jobs implicitly. Credentials use Security.framework
Keychain entries bound to both profile and endpoint; no keys in JSON/recipes.
`connection_checks.py` does metadata GETs/runtime imports only, never inference.
Generic Comfy API profiles have explicit bindings and capabilities. Independent
workflow takes use the existing take history; whole-film preparation/resume stays
exclusive to qualified Qwen/LTX. Changing clip model/length/size is explicit at
confirmation; failed attempts retain prior take bytes and editorial selections.
`media_tools.py` resolves explicit RAVEN_FFMPEG/RAVEN_FFPROBE, selected engine, PATH
and common macOS locations. Native smoke sources are synthetic FFmpeg media.
Read MODEL_CONNECTIONS.md for portable setup and profile schema/limits.

```sh
./scripts/setup-engine.sh /path/to/sprintwake-engine /path/to/dedicated-runtime
uv run --frozen python -B scripts/sync-contracts.py
uv run --frozen python -B -m unittest tests.test_connections tests.test_connections_panel tests.test_workflow_panel -v
uv run --frozen python -B scripts/make-profile.py --help
```

setup-engine.sh installs only pinned CPU media dependencies into the explicitly
chosen Python 3.12 environment. Never run it against a user's inference runtime
for routine QA. No publishing, license choice or weight downloads are implied.

Explicit external video bindings carry modelProvider=comfy-workflow and the
profile digest in project defaults/per-shot overrides. Preserve them through Store
migration and authoring save/reopen; unmarked local migration stays unchanged.
A changed/mismatched workflow must fail before submission until explicitly chosen
again. Selected seed takes copy their verified recipe to the canonical clip;
failed adoption must not clear existing stills/media/recipe. CPU setup helper
syncs only dedicated environments bearing its own marker, never unrelated venvs.

## Standalone packaging and updates

```sh
.venv/bin/python -B scripts/sync-contracts.py
RAVEN_BUILD_ONLY=1 RAVEN_BUNDLE_PYTHON="$PWD/runtime/bundle-venv" RAVEN_PACKAGE_OUTPUT="$PWD/qa/fresh.noindex/Sprintwake Studios.app" ./scripts/package-app.sh
.venv/bin/python -B scripts/test-sparkle-update.py --bundle "$PWD/qa/fresh.noindex/Sprintwake Studios.app" --output "$PWD/qa/update-fresh.noindex"
```

Choose fresh output directories. Update QA uses disposable bundles and synthetic
HTTP/media fixtures, never user productions or models. Keep the shared lifetime
reader lock held throughout each app/engine process. Sparkle approval requires
all local work drained, authenticated backend quiescence, child exit, and the
exclusive lifetime lock. Preserve the handoff marker through parent exit until
the matching new build starts; never clear it on a timeout or PID guess. Keys stay
in Keychain. Developer ID and notarization are separate from EdDSA signing.

Manual installation uses that same lifetime gate and keeps the handoff marker
until matching new-app startup. Stage on the destination volume, verify both
signatures before swapping, and preserve source/previous app on copy or register
failure. Keep rollback coverage with an actually live reader process and injected
copy/registration failures. Bundled ffprobe must use `-I -B`; even a standalone
probe must preserve the sealed app and never write Python cache files inside it.

After QA, unregister and trash disposable app bundles; keep only the installed
`~/Applications/Sprintwake Studios.app` discoverable. Retain logs/JSON/screenshots,
not executable test copies. Put future bundle test runs inside `.noindex`
directories, and still unregister them after explicit GUI tests. Never remove
Sprintwake Projects or production/configuration/model folders during this cleanup.
Moving an app to Trash can register it again at its new URL. Capture
NSFileManager's resultingItemURL and unregister that actual Trash path after the
move; checking only the original path is insufficient. Final verification must
include main and QA bundle IDs throughout LaunchServices, including Trash paths,
and leave only the canonical app. Do not empty Trash or reset the whole database.
The user requested one local app on 2026-11-05: previous/QA app bundles are now
recoverable in Trash, rather than retained as live extensionless app archives.

## English public interface (0.10.2)

The native interface, menus, new-work defaults and default director language are
English. User/project text and dialogue can use any chosen language. Preserve legacy
stored names, status enums, identifiers, schema keys, folder paths and Keychain domains.
Modal titles also act as runtime routing sentinels: update both producers/consumers and
fixture expectations together. Canonical director/anchor copy belongs in engine/server;
refresh contracts/ through scripts/sync-contracts.py rather than editing its mirror.
The user explicitly requested the local update on 2026-11-05; the installed app
is now MIT 0.11.0/build 17. Its compatible private LTX/oMLX settings and independent
Comfy helper are installed, with private backups and the previous app in Trash. No public
Mac release or appcast is authorized by that local installation.

Reproducible README screenshots, with an unlocked graphical macOS session:

```sh
./scripts/capture-showcase.sh --output "$PWD/qa/showcase-fresh"
```

Use a fresh directory. This opens a disposable fictional project in the real Raven
window, uses existing public art and a clearly disclosed scripted director reply, and
starts no model inference. Inspect the captures before copying only the PNGs into
assets/screenshots/. Preserve user data and keep QA metadata/logs ignored.

## Independent engine (0.11)

`engine/server` is independently authored from native interfaces and production requirements. Do not restore the retired server, web client, Fable helpers or old copied director prose from history. Local video uses the external MIT ltx-2-mlx CLI with explicit installed model paths; Qwen uses native Comfy nodes and engine/comfy_nodes. Do not install that plugin, start inference servers, download weights or run model generation for ordinary implementation QA. Existing models, signed oMLX app, old Storyboard source/config/projects and installed Sprintwake app remain unchanged unless separately requested.

```sh
PATH="$PWD/runtime/tools:$PATH" runtime/bundle-venv/bin/python -B -m unittest discover -s engine/tests -v
.venv/bin/python -B scripts/sync-contracts.py
.venv/bin/python -B scripts/install-comfy-plugin.py /path/to/ComfyUI --dry-run
```

Pure contract mirrors include store/media dependencies; imports remain lazy and require no model packages. Provider journals and global GPU/language/process admission survive workspace changes. Pending Comfy cancellation requires an atomic never-started receipt; without that plugin capability, drain to terminal history rather than guessing from queue disappearance. App lifetime must retain the published Updates/code-reader.lock domain and accept matching legacy tokenless handoff markers only under exclusive startup/instance/reader ownership. No Mac Release/appcast publication is authorized by this source replacement.

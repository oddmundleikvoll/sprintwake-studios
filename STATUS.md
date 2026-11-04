# Current status — 2026-11-04

Project goal and current scope: [PROJECT.md](PROJECT.md).
Sprintwake Studios 0.10.1/build 15 is installed with the three-card Start page and
working optional-field image/clip entrances. Version 0.9.0's signed update/relaunch
and preservation qualification remains recorded below. The user authorized public
source publication under CC BY-NC-SA 4.0 on 2026-11-04. The source is public at
[oddmundleikvoll/sprintwake-studios](https://github.com/oddmundleikvoll/sprintwake-studios).
No downloadable Mac release or appcast is published; those additionally need
Developer ID/notarization and binary redistribution qualification.

The entries below are chronological implementation history. Older descriptions
of the installed app, source dependencies or missing features are superseded by
the current overview and later entries; they are not a remaining-work list.

# English public interface and source README — 2026-11-04

- Source version 0.10.2/build 16 uses English native navigation, forms, defaults,
  validation copy and File/Edit menus, with English bundle development locale.
  The director guide defaults to English and honors the user's chosen language.
  Existing project text, stored labels, IDs, profile/status enums and private
  data paths remain compatible. The installed 0.10.1/build 15 app is unchanged.
- The public description and README foreground long-form films, the built-in
  director and fully local workflows. Installation/build instructions follow
  the first screenshot. Local builds use included engine/runtime and require
  neither Developer ID nor notarization; model weights/services remain external.
- Three real native screenshots use a fictional project and already-public art.
  The director conversation is scripted and explicitly disclosed, with no model
  inference. scripts/capture-showcase.sh recreates the isolated capture session.
- Local verification passes 269 app tests, seven native CTest suites, pure
  contract parity, and 342 engine CPU tests with five expected optional external
  worker skips. The source window passes all 194 native checks with zero Raven
  errors. Additional tests cover English/legacy conversation auto-titles,
  save-before-generation ordering and valid director-guide JSON examples.
- Independent source/guide review is complete. Standalone relocated-bundle and
  hosted CI verification are in progress. No app binary, appcast or model weights
  are published; CC BY-NC-SA 4.0 and upstream notices remain unchanged.

# Public source foundation — 2026-11-04

- LICENSE preserves CC BY-NC-SA 4.0 and the original Storyboard attribution;
  NOTICE.md identifies upstream, modifications and dependency boundaries.
  CONTRIBUTING.md documents the source workflow and verification commands.
- Source setup now uses included engine/. A clean local Git clone without the
  developer's sibling installations fetched pinned runtime dependencies and
  built a standalone ad-hoc-signed app. It was not installed over the user's app.
- All 268 app tests ran without skips, all seven CTest suites passed and pure
  engine/contracts match. The exact CI CPU suite ran 342 tests with five expected
  skips for separately installed ComfyUI LTX worker/node source. Native clean-clone
  smoke passed 194 checks with zero Raven errors and no model inference.
- App integration/media tests use included engine/runtime/tools. External worker
  checks are optional and can use RAVEN_LTX_BRIDGE; pure anchor/prefix tests remain
  required. No film generation or new model qualification was performed.
- The initial publication tree contains 324 files (~4.93 MB). Staged-source review
  found no known credential-pattern matches, personal home paths in documentation,
  oversized files, ignored tracked paths or symlinks. Private service JSON, crash
  temporary files, backups, locks, keys and runtime artifacts stay excluded.
  Real project titles/prompts in qualification history were anonymized.
- Independent correctness/publication review found no remaining material issue.
  Local evidence is retained under ignored qa/public-source-60jarfch.noindex/.
  Hosted CI results are available in the repository's Actions tab; no signing
  credentials, model weights or app binaries are uploaded by CI.
- Source commit 5c6a88f was published to the public repository. The first hosted
  build passed, but its import fixture timed out. Provider isolation alone did
  not solve the hosted failure. A CPU-only diagnostic captured repeated child
  stacks inside socket.getfqdn → HTTPServer.server_bind for 35.6 seconds.
  Loopback binding now skips that unnecessary reverse-DNS lookup, retaining
  standard naming for other addresses. A real socket/readiness regression fails
  before the fix and passes with DNS deliberately unavailable. The import fixture
  also retains empty private providers/credentials/workspace and real persistence
  assertions. Startup limits remain unchanged. Temporary diagnostic code/workflow
  was removed; its evidence remains in ignored QA logs and GitHub run 41911115859.
- The DNS fix also passed all 194 native checks from a relocated, read-only app
  bundle. Its deep/strict code signature stayed valid and no model inference ran.
  Source CPU verification passed 342 tests with five external-worker skips;
  all 268 app tests and seven CTest suites passed without skips.
- Hosted run 41912248173 passed build, all app/native tests and contracts, then
  exposed one CPU fixture that stubbed Qwen images but still read real model-file
  metadata. It now uses the matching fake model identity and explicitly absent
  model root, preserving checkpoint/run reuse and byte/timestamp assertions.
  CPU CI explicitly selects an absent RAVEN_MODEL_ROOT to catch this dependency.
  The complete 342-test CPU command also passed locally with a fresh empty model
  root and absent external worker source (five expected skips, 87.3 seconds).
- Full hosted [GitHub CI](https://github.com/oddmundleikvoll/sprintwake-studios/actions/runs/41914411964)
  passed for source commit 663a2d7: clean Apple Silicon build, 268 app tests,
  seven CTest suites, pure contract parity and 342 CPU tests with five optional
  external-worker skips and no installed model root. Source publication is
  complete. A downloadable Mac release remains a separate qualified/signing task.

# Studio open-button repair — 0.10.1/build 15, 2026-11-04

- Reproduced the user's failure in the installed app: both studio open buttons
  required name and description, with the exception visible only in the footer.
  The earlier smoke filled both fields and bypassed the dynamic open buttons.
- Names/descriptions are now optional on opening. Empty work receives Nytt bilde
  or Nytt klipp; a supplied description can supply its title. Prompt validation
  remains at explicit generation. Progress/errors appear beside the open button;
  pending opens are disabled and a failed creation re-enables retry.
- All eight input combinations have regression coverage. Native smoke physically
  clicks both dynamic buttons with empty and prefilled forms. It waits for the
  fresh form after a deferred prior-project save; an initial smoke sequencing
  failure was diagnosed and corrected without changing user-data behavior.
- Verification: 265 unit tests, seven CTest suites, 194 source-window and 194
  packaged-window checks passed. Independent review is complete. The installed
  app was then tested through OS accessibility clicks in a separate disposable
  production/config root: both blank forms opened the real image/clip studios.
  No media inference was started. The normal user workspace was reopened at Start.
- All 11 existing production/settings fingerprints remain unchanged after install
  and ordinary launch; the deep/strict signature is valid. The disposable .app
  was unregistered and moved to Trash. Evidence: qa/open-buttons-source-20261104/,
  qa/open-buttons-bundle-20261104/, qa/open-buttons-manual-20261104/ and
  qa/open-buttons-install-verification.json. No source/release was published.

# Start, image and clip entrances — 0.10.0/build 14, 2026-11-04

- Ordinary launch stays at Start with three native, illustrated copper-palette
  cards: Generer et bilde, Lag et klipp and Filmstudio. A saved project-bound
  director conversation retains its draft and only opens through explicit
  navigation. Start and the full project list have distinct destinations.
- Image work has its own simple name/description entrance and existing image
  composer: references, four aspect ratios, seed, gallery/comparison and Output.
  It needs no manuscript or shot. Clip work creates exactly one canonical shot,
  exposes prompt/length/size, optional opening images, retained takes, playback,
  own-job stop and continuation into the existing film workspace.
- Script/creation.json is only a Raven room hint for explicit reopen; production
  board/recipe schemas, review gates and model orchestration remain unchanged.
  Clip generation saves first and requests one independent retained take. External
  workflows retain their explicit confirmation, prefilled with a supported size.
  Opening any room starts no inference.
- Verification: 264 Python unit tests; seven Raven CTest suites; 188 real-window
  source checks and 188 standalone-bundle checks, with no Raven errors. Coverage
  includes actual card mouse input, compact/normal geometry, real project APIs,
  prompt/timing persistence, room reopen, saved-conversation startup, an inert
  one-take request and byte-identical preservation of the original QA film.
  Independent review is complete; the supported-size form issue was fixed and
  rechecked. No new model inference or downloads were used for this UI work.
- Evidence: ignored qa/home-20261104/ and qa/home-bundle-20261104/, with existing
  production/settings fingerprints in qa/home-preservation-before.json. Build
  staging stays under .noindex; disposable app copies are unregistered/trashed
  after checks. No source or release has been published.
- The final installed 0.10.0/build 14 also passed all 188 window checks under
  qa/home-installed-20261104/. Ordinary startup was then inspected through native
  accessibility: Start is visible, no project is selected and the existing film
  appears in recent works. All 11 existing production/settings fingerprints are
  unchanged after install and ordinary launch. Deep/strict signature verification
  passed. Both task-created QA bundles were unregistered and moved to Trash;
  the installed app remains open at Start. See qa/home-install-verification.json.

# App copy cleanup — 2026-11-04

Twenty-four project QA bundles and three temporary read-only/relocated copies
were unregistered and moved to Trash at the user's request. The installed
`~/Applications/Sprintwake Studios.app` remains, with valid deep signature.
Sprintwake Projects, source, production/settings/model data and QA reports remain.
Future QA uses `.noindex` parent folders and must clean up executable copies.

# Sprintwake Studios — version 0.9.0 (build 13), 2026-11-04

- Installed and opened `~/Applications/Sprintwake Studios.app`. The app includes
  canonical `engine/`, isolated Python 3.12, CPU dependencies, FFmpeg/ffprobe,
  Raven/MoltenVK, AVFoundation media and Sparkle 2.10.1. It has no checkout runtime
  dependency. The installed app is the only retained discoverable bundle; disposable QA
  builds were moved to Trash after verification.
- Sprintwake Projects logo geometry is retained, using copper/gold over a dark
  plum/charcoal background. Existing Copper UI palette and typography remain.
- The same GitHub repository serves future source and Releases. No source, release
  assets or appcast have been uploaded. The repository remains private.
- Real local signed Sparkle update 0.9.0 → 0.9.1/build 14 passed: installation
  waited for active synthetic production, then replaced/relaunched the QA app and
  preserved all 10 project/settings fingerprints. Altering bytes inside the signed
  appcast body was rejected while production continued. QA used a disposable
  signing key; production private key remains in Keychain.
- 172 source window checks and 172 checks from a relocated, read-only standalone
  app passed without Raven errors or model inference. Direct embedded ffprobe
  preserves the code signature and creates no bytecode caches (`-I -B`).
- Final checks: 257 native Python tests, 332 engine CPU tests (two skipped), seven
  Raven CTest suites, native compilation, strict deep code signature and diff
  checks passed. Independent review covered updater, installer and migration.
- The installer joins the same global lifetime gate as Sparkle, refuses live
  engine readers, verifies source/stage signatures and stages on the target
  volume before swapping. Copy/registration failures retain the working app.
  Success retains the handoff marker until matching new app startup clears it.
- Installed startup confirmed bundled engine/Python and idle production state.
  All 11 existing production/configuration fingerprints remain identical; the
  existing project and recoverable Trash project remain intact. Six missing
  private engine settings files were migrated without changing their sources.
  Production root, workspace, local Qwen/LTX selections and Keychain identity are
  retained. The old known Raven app is archived under
  `~/Applications/qa/retired-apps.noindex/`.
- Public distribution still requires Developer ID/notarization and a source-license
  decision. The current local app is ad-hoc signed. No models were generated or
  downloaded, and no GitHub publication was performed. See DISTRIBUTION.md.

# Model connections — version 0.8.0 (build 12), 2026-11-04

- Native **Modeller** configures installation/production/workspace paths, language
  services and explicit Comfy API image/video profiles. Existing local Qwen/LTX
  selections, runtime and configured workspace remain discovered defaults.
  Settings are private and separate from productions; changes apply on ordinary
  restart. API keys live in endpoint-scoped Keychain entries and are excluded
  from profiles, project data, recipes and exports.
- Bundled pure contracts allow the native setup modal to open without an engine
  or Python runtime. A chosen companion engine and a dedicated Python 3.12 CPU
  environment can start without the original installation folders. Setup helper
  was exercised twice in a fresh disposable environment, with relocated source,
  preserving its existing files and starting no models. FFmpeg remains separate.
- Explicit schema-v1 workflows support independent image/video outputs and one
  separate video take from Regi. Frames/alignment/reference capabilities are
  checked; actual images/video are decoded before publication. Failed/cancelled
  attempts retain earlier media. Recipes preserve the selected API graph/profile.
  Uncertain own Comfy jobs persist outside production/workspace selection and
  block new language/media until passive client/prompt history recovery.
  Explicit model-provider/digest bindings survive save/reopen and enter render
  fingerprints; old local migration stays intact. Adopting a workflow take
  copies its verified recipe with the selected clip and preserves prior bytes.
- Automatic image-plan/camera-group production and continuation remain qualified
  only for local Qwen/LTX. External service GPU residency is provider-managed.
  Director live context includes active model capabilities and this distinction.
- Verification: 231 native Python tests, 326 companion CI Python tests, seven
  Raven CTest suites, native bridge build and 172 actual-window checks passed.
  Narrow connection checks also cover passive HTTP, standalone imports, a
  relocated engine, scoped credentials, real PNG/MP4 fixture adapters, failed
  take preservation, cancellation/recovery and publication rollback. The old
  H3 rewrite-text assertions now check current qualified LTX guidance.
  Separate native first-run check opened setup with missing runtime/engine and
  wrote no preferences. No inference or weight downloads were used for QA.
- At 0.8.0, the installed Storyboard Raven.app still depended on the source
  checkout/runtime. This historical setup was superseded by the self-contained
  Sprintwake Studios 0.9.0 installation. Current release work is in PROJECT.md.

# Status — 2026-11-04

Shared editing workspace — version 0.7.0:

- Scener, Struktur, Regi, Klipp and Lydspor share a compact left sequence/scene
  outline, central player, contextual right inspector and timeline below all three
  columns. The Copper palette file is byte-identical; New York/SFNS are retained.
  Sidebars use the same dark base colors with solid surfaces. Redundant global
  scene/cut tabs are consolidated under Arbeidsflate with contextual mode tabs.
- The outline groups actual shot IDs, shows video/still/missing states, preserves
  empty narrative scenes and supports transient collapse/expand. Existing narrative
  structure is read on project open. Selection remains correct across differing
  canonical/cut order and missing widgets in collapsed groups. Structure now edits
  only the selected group at the right instead of stacking full-page group cards.
- Sound has the existing locked picture plus four independent audio tracks below
  the player, with cue/mixer tabs at the right. The bottom area reserves space for
  every lane/handle. Prepared mixes play inline on the existing single AVPlayer;
  seeking/transport keep that source, and edits/mode changes release it. Sound
  scrubbing previews the picture while retaining absolute positions beyond film
  end. Inline editorial viewing creates no production review receipt; actual
  production review keeps its existing separate surface and approval gates.

Verification: 213 Python tests, native bridge build, seven CTest suites and diff
checks passed. The full native smoke passes 162 checks at 1440×920 and 1180×760,
including non-overlapping geometry, usable player/sidebars, correct inspectors,
selection through five modes, mixer clicks, collapse/transport, cross-cut audio
gestures, trims/undo, byte-identical navigation, inline AVFoundation media and
safe ownership release. An independent review found no outstanding material
issues. No inference or user-production edits; owned temporary backend exited.
Evidence: ignored qa/layout-*.log/png and production-smoke.json. Applications
bundle is updated to 0.7.0 (11); deep/strict signature verification passes.

Director editorial context and public-design review — version 0.6.1:

- The internal director now receives the actual saved cut, coverage, narrative
  structure and sound decisions. Four new validated inspection tools bring the
  catalogue to 41; panel navigation includes all five new work surfaces. Reading
  uses the same pure versioned projection in core/native, with bounded input and
  output, explicit absent/invalid/unsupported/truncated states, full current/history
  shape checks and project containment. Paths/undo contents never enter context.
- Read-only batches no longer sync manuscripts or initialize journals. Native
  receipts expose panel shortcuts. The expert guide explains 24 fps absolute sound
  placement, combined original audio, narrative versus camera grouping, native
  rough-cut versus canonical assembly and qualified local-model limitations.
  These tools inspect and advise; native panels still own editorial mutations.
- OPEN_SOURCE_READINESS.md records primary-source workflow/design references and
  a prioritized public setup/adapter plan. Current personal paths, sibling engine,
  runtime assumptions and lack of generic model adapters remain explicit. No repo
  was published and no public-install/own-model compatibility is claimed.

Verification: 209 Raven Python tests, 39 focused core regressions, native bridge
build, seven CTest suites and diff checks pass. The actual-window smoke passes
96 checks, including real four-journal agent reads, byte-identical canonical and
sidecar files, receipt UI and sound-panel navigation. Independent review found no
outstanding material issues after fixes. Inert model replies and existing/synthetic
QA media were used; no inference or user-production rewrite. The owned temporary
backend exited. Evidence: ignored qa/agent-flow-*.log, production-smoke.json and
studio-director-editorial.png. Applications bundle 0.6.1 (10) is updated and its
deep/strict signature verification passes. An already-running production backend
loads the new guide/contract on its next ordinary restart.

Narrative sequences and independent sound — version 0.6.0:

- Scener and Klipp expose native sequence/scene management with names, shot
  assignments, group order and runtime goals. Actual times follow included cut
  trims. Explicitly applying narrative order preserves every source/take/trim;
  goals never stretch sources or start generation. Metadata and 50 persistent
  undo/redo actions live in Script/structure.json. Newly arriving cards reconcile
  into history; deliberate unassignment and no-op reopen preserve user intent.
- Lydspor provides locked picture and four editable native lanes: Dialog,
  Musikk, Miljølyd and Effekter. Audio may overlap and span picture boundaries.
  Body/edge drags, exact timecode placement/source trims, splitting, duplication,
  gain, fades, clip mute and per-bus mute/solo use Script/sound.json with 50 undo
  steps. Imported media are verified immutable Input copies. Existing video sound
  is explicitly one combined source, routed through Dialog and optionally disabled.
- The same saved mix feeds actual AVFoundation WAV/film preview and rough-cut
  export. Mixing streams batches of 16 sources plus a float accumulator, with
  timestamp-based leading silence and one final compensated limiter/PCM pass.
  Output is exactly cutFrames*2000 stereo samples at 48 kHz. Original silent
  video contributes silence; missing audible imported media fail visibly.
  Cache identity/validation binds actual input/output bytes. Source fades retain
  their intended positions when the film ends early.
- Picture and audio caches are independent: changing only sound reuses encoded
  picture. Play in Klipp opens the prepared mixed preview once sound edits exist.
  Project ZIPs retain journals and Input audio but skip rebuildable caches.
  Relocation and sound I/O exclude each other, including direct native gestures.
- Native editorial reopen exposed an old default-field write: merely viewing an
  unreviewed shot no longer inserts ravenReviewed=false. Canonical production
  bytes stay unchanged during narrative/audio edits. Cover completion also avoids
  rebuilding moved project images while trash/archive work is pending.

Verification: all 204 Python tests, the native bridge build, seven Raven CTest
suites and diff checks pass. The full-window smoke passes 92 checks, including
native audio move/edge trim/Delete/undo, all four lanes, narrative goals/assignment
and order, actual sound/film playback, persistence and relocation. Measured tone
tests verify exact source/onset samples, overlaps, levels, mute/solo, fades,
trailing silence, batching equivalence and actual cross-cut exported audio. Export
checks prove source preservation, picture-cache reuse and portable journal/audio
retention. Independent review has no outstanding material findings after fixes.
Normal and compact native captures are under ignored qa/studio-structure.png and
qa/studio-sound*.png; logs are qa/sequence-sound-check.log and production-smoke.json.
No model inference or user-production rewrite was used for qualification. Full
creative long-film quality remains separate from these editing/synthetic tests.
The Applications bundle is updated to 0.6.0 (9), using this checkout/runtime.

Room blocking and coverage — version 0.5.0:

- Scener exposes a native metre-grid room map with draggable linked figures,
  cameras and camera targets, numerical placement/lens/height/gaze controls,
  per-location plans, configurable action axis/side and 30 persistent undo/redo
  steps. One drag is one saved action, including mouse release outside the map.
- Master/reverse/close-up proposals use the same geometry as the preview and
  direction text. Candidate search preserves the chosen axis half-plane and
  safe framing; cropped/unusable candidates are marked and deselected. Manual
  crossings and degenerate/crowded positions remain visible warnings and cannot
  be applied while selected.
- Selected angles append distinct storyboard camera-group drafts via fresh
  authoring-digest/timestamp CAS with snapshots. Stable IDs prevent retry
  duplication. Existing production media, timings and approvals are preserved.
- Optional image-composer handoff uploads a real deterministic geometry PNG,
  available cast images and explicit colour/identity mappings. Native preview
  uses the same z-buffer renderer; only drag resolution changes. This operation
  runs no inference. Existing image/video review gates remain unchanged.
- Script/coverage.json and its history survive reopen and ZIP export. File-lock
  revision checks and reciprocal project relocation guards preserve journal
  integrity.

Verification: 16 focused coverage tests include rotated/reversed axes, wall-edge
framing, actual backend CAS/apply/idempotence, PNG upload, source preservation,
malformed journals, symlink refusal and persistent history/export. Independent
review passed after fixing wall-adjacent master framing. `./scripts/check.sh`
passes all 174 Python tests, the native bridge build, seven Raven CTest suites
and both diff checks. `./run.command --smoke-test` passes 72 actual-window checks,
including native figure/camera/target dragging, checkbox selection, undo/reopen,
axis rejection, actual canonical append/idempotence, layout upload and reciprocal
relocation guards. No Raven errors or model inference. Normal and 1180×760
captures were inspected; evidence is under ignored qa/studio-coverage*.png and
qa/production-smoke.json. The installed Applications bundle is 0.5.0 (8), using
this checkout. Disposable backend processes close with their smoke workspaces.

Editorial workspace — version 0.4.0:

- Scener provides native camera-group contact sheets with location, planned
  timing and actual-video versus storyboard status. Cards open their direction.
- Klipp stores independent 24 fps take selection, source in/out, ripple order
  and reversible exclusion in Script/cut.json. Native body/edge dragging and
  exact frame/playhead controls use the same model. One gesture is one undo
  step; up to 50 undo/redo actions persist across reopen. Canonical manuscript,
  production timing/order, output refs and continuation recipes are untouched.
- Take choices preserve verified Input media/posters and read the actual seed
  history API without adopting a production take or starting inference. Native
  playback and scrubbing seek original picture/audio at the selected in point.
- Local rough-cut export follows take/order/trim and keeps labelled storyboard
  placeholders. NUT/PCM intermediates retain exact frame boundaries; final MP4
  and its cut recipe are new Output files, independent of production approvals.
- CAS/file-lock persistence rejects stale writes. New canonical cards reconcile
  without losing edit choices/history. Project relocation and cut I/O exclude
  each other in both directions. Old scene images are released on page/project
  exit. Production take cache timestamps resolve to the actual local file.

Verification: 158 Python tests (including 21 focused cut/media tests), seven
native CTest suites and 57 actual-window smoke checks pass. Synthetic media tests
verify exact exported frames, reordered colour frames and the trimmed audio;
native checks exercise real mouse trim/reorder, Delete/undo, alternate take
selection, source-time seek, placeholders/export, external card reconciliation,
reopen, relocation guards and byte-identical canonical production. Independent
review found no remaining material issues after fixes. No model inference ran.
Evidence is in ignored qa/production-smoke.json and studio-scenes/studio-cut
captures. The Applications bundle is updated to 0.4.0 (7), using this checkout.

Earlier production integration history follows.

Expanded the native prototype into a production studio connected to the existing
Storyboard core. Default production root: `~/Movies/Raven Studio`.
Original projects/configuration and all installed models remain in place.

Implemented: project create/open/import/archive, autosave and reopen, manuscript,
character/place/prop bible with references, direct Qwen image composer, reference
selection, history/assignment/publishing and side-by-side comparison; shot creation,
continuation, direction, timing, cast, dialogue, manual anchors, reorder/remove;
retained take preview/adoption; director conversation and revision-bound proposals;
production preflight, exact image-plan and camera-group gates, selected job stop
and resume, assembly, native final playback, media library and ZIP export.

The interface retains the editorial charcoal/paper/copper palette and adds
restrained glass, contextual next steps, empty states, focused sheets, a workflow
trail and searchable commands. Native AVFoundation provides actual decoded frames
and original audio; planned images remain visibly distinct from video.

Verification: 49 Python tests and 7/7 Raven CTest suites pass. The native
production smoke passes 16 checks with zero Raven/XML/theme errors. Manual
packaged-app checks confirm normal launch, single-line typing/⌘A replacement
and filtered ⌘K commands. The app is left on the real, empty project hub.

Verification evidence is generated under ignored `qa/`. Project checks, upstream
Raven headless suites and actual-window production smoke are the acceptance gates;
see README for exact commands. Import tests include a real temporary backend,
valid image upload, retained media and byte-identical source documents. Production
smoke operates entirely in a disposable root with existing media and no inference.

No fresh Qwen/LTX generation or long-film qualification was performed for this UI
expansion. The existing production engine and its model qualification are reused.
Comfy Desktop and local language services must be available when actually making
new media. Timeline dragging is not an editing interface; order and duration are
changed in shot Details. Advanced model jobs retain the original engine's limits.

The prior macOS fixes remain: tracked source patch for key eligibility and safe
FlagsChanged handling, actual keyboard/text input, native menus and clean teardown.
No runtime ISA mutation is used. The application bundle starts `studio.py`.


Consolidation and director repair: old Storyboard.app and bundle-specific
preferences/cache/WebKit removed after identity inventory. Engine sources,
configuration, projects/models and oMLX retained. Fifteen previous generated
images copied to shared Library with verified SHA-256 and source metadata;
second migration deduplicates all fifteen. Library opens without a project and
adopts references through canonical project upload.

The observed director failure was truncated JSON: a prose claim of 28 clips
with actions=[] and an empty real board. Core returns explicit proposalStatus
and discards that success claim. New direct authoring creates bible/setup first,
then saves <=6 shots per batch after explicit user action, with snapshots,
stale-edit checks, cancellation and matching-checkpoint resume. Saved manuscript
and cards are readable in-app; historical incomplete claims are labelled failed.
No handwritten replacement manuscript was inserted into the user's project.

Final repair verification: 79 Python tests and seven Raven CTest suites pass;
53 director-contract tests and 53 core save/film/image regressions pass with the
project-local ffmpeg/ffprobe on PATH. Native smoke passes 21 checks with real
project persistence and inert model replies. Manual packaged-app verification
shows all 15 shared images and opens the direct-authoring sheet prefilled with
the qualification brief. No new model inference was run; the user project
was not replaced with a hand-authored script.


Director workspace 0.3.0: 37 canonical tools now have real execution paths.
The adapted expert guide covers project setup, bounded manuscripts, bible and
character sheets, Qwen references, LTX scene/timing/continuity, dialogue/music,
retakes, exact review boundaries and delivery. New /api/raven/chat accepts a free
idea without a project and supplies authoritative project/library/image context.
The app starts in the director room, with project chooser, named/new/archived
conversations, independent drafts, late-reply routing and same-session project
creation. Legacy conversation files remain untouched and are copied forward;
unconfirmed legacy proposals and interrupted executions require fresh review.

Verification for 0.3: 107 Raven Python tests, seven native CTest suites, 73 focused
core director/save/film/image regressions, and 29 real-window checks pass. The
actual temporary backend test covers create, manuscript edits, shared/project/
generated refs, character image, published image and canonical anchor writes.
Native tests cover projectless chat -> real project creation, same conversation,
independent drafts, late replies and direct library-tool execution. Packaged app
was rebuilt and manually checked for startup, project selection and conversation
archive. No new Qwen/LTX/LLM inference was started for implementation QA; existing
production paths and their prior qualification remain the generation dependency.


macOS and project lifecycle repair — 2026-10-27, version 0.3.1:

- App inventory found one physical source bundle plus its Applications symlink.
  Packaging now installs one real ~/Applications/Storyboard Raven.app, with a
  StudioRoot resource pointing to the checkout/runtime. The old source bundle is
  unregistered and retained without an .app suffix in ignored qa/retired-apps.noindex.
  LaunchServices reports a single Raven registration at the Applications path.
- The custom-header Cocoa window omitted Miniaturizable. A separate idempotent
  patch adds it; the prior key eligibility/FlagsChanged patch stays intact.
  Native smoke injects actual yellow HeaderBar mouse events and verifies both
  minimization and restoration, not just a guessed flag or screenshot.
- Project cards, overview, settings and commands expose explicit deletion.
  Named confirmation moves the complete production into Raven's Trash, with a
  browsable Restore action. Core trash/restore routes hold the admission lock;
  create/import serialize with restore, and active work/collisions are rejected.
  Media and canonical references remain byte-identical on roundtrip. Conversations
  are retained and detached; outstanding local work blocks deletion. Committed
  moves remain reported as successful if a later local metadata refresh fails.

Verification: 121 Raven Python tests, 79 focused core regressions (including six
real HTTP trash/restore/concurrency tests), seven Raven CTest suites and 35 actual
native window checks pass. Installer migration/identity checks pass; packaged
Applications launch and discoverable deletion were checked manually. Tests use
only disposable productions and retained media. No model inference or user
project deletion was performed. The app remains open on Prosjekter.


Real director authoring repair — 2026-10-27, version 0.3.2:

- Strictly normalize known tool argument envelopes and give one correction using
  actual validation errors. Internal setup/shot stages narrow the tool catalogue.
  Invalid/model-service responses retain private diagnostic attempts and expose
  no executable actions. Diagnostic IDs/errors survive conversation persistence.
- One reviewed create_project → write_manuscript proposal creates the actual
  project, then writes against its fresh slug/digest. Partial results retain the
  saved project, counts and checkpoint. Actual creation receipts rebind only that
  executed turn; other stale proposals retain their guards.
- Native chat exposes explicit same-project manuscript resume, including after
  restart. Its internal resume_only path refuses changed/missing journals before
  inference or writes and preserves pending-save recovery/done idempotence.
- Writer requires acts/locations, full shot direction, legal frames and exact
  camera continuity. Corrections retain brief/bible and identify all invalid
  cards. Technical camera groups are distinct from narrative acts. Normal batches
  now contain three shots. A measured 306-second six-shot response exceeded the
  old bound, so authoring has a separate 600-second budget. Empty native note
  defaults normalize identically in core/client digests and no longer invalidate
  unchanged checkpoints.

Actual local-model qualification: Qwen3.8-27B-Uncensored-MLX received a qualification
brief through native chat, proposed and created a disposable qualification project,
and authored three acts, three locations, two characters and twelve storyboard
shots (~60.5 seconds). The first six and two later three-shot batches were saved
in the same project/conversation. Every creative shot field matches its actual
model tool response; Script/manus.md contains all twelve. Checkpoint is done 12/12.
No handwritten substitute or image/video inference was used. Private actual traces
and qualification hashes are recorded under System/director-traces and ignored
qa/director-real-qualification.json. After the user unlocked the Mac, the actual
chat showed the saved 12-shot/3-act receipt. Its Les manuset button opened all
three acts and twelve manuscript cards. Storyboard showed all twelve shots and
the 60.5-second timeline; selecting the last shot displayed its saved start,
action, end and camera direction. Manual native screenshot inspection passed.

Verification: 137 Raven Python tests, 96 focused core regressions, seven native
CTest suites, and 36 native-window smoke checks pass. Smoke now exercises one
reviewed chat proposal through actual project creation, bible/scenes, saved shots
and manuscript, followed by independent conversations and the existing production
flows. The full check script, native bridge build and diff checks pass. Disposable
smoke projects use inert model replies; this is distinct from the real model run.
Installed Applications bundle is 0.3.2 (6), pointing to the updated source/runtime.

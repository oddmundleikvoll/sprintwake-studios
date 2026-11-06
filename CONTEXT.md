# Purpose

Sprintwake Studios, the native production studio. The retired Storyboard.app wrapper was removed at
the user's request; the independently authored production engine is included in engine/. The user authorized expansion from
prototype on 2026-10-26 and selected ~/Movies/Raven Studio for new production data.
Start is the default launch destination, independent of the saved director
conversation. It offers image, independent clip and full film entrances. Film
progression is director conversation → project → brief → bible → images/shot plan
→ production review → delivery. Keep actual media distinct from planned stills.

Public source publication was authorized on 2026-11-04. On 2026-11-05 the user requested replacing the CC engine and selected MIT for independently authored code. Current engine/server and contracts use that replacement; old versions retain their CC terms in Git history. Native and bundled CPU tests use the included engine and synthetic media, without model startup or downloads. The independent engine preserves schema-v1 files, CAS, immutable media, production review and process/update ownership. Qwen uses native Comfy plus an independent memory barrier; LTX uses the external MIT CLI/local pack. Language/audio APIs and Comfy profiles may be local or cloud-hosted. Model/provider terms remain separate.

The user then explicitly requested the local app update. Installed Sprintwake is
now MIT **0.11.4/build 21**. Private
configuration backups precede compatible LTX CLI and direct oMLX connection entries;
the same language model and all production paths are retained. Credentials use the
existing endpoint-scoped Keychain service. The independent Comfy helper is installed,
but ComfyUI and inference were not started for installation QA. Existing production
files, weights, original Storyboard and the signed oMLX app/settings are preserved.

On 2026-11-06 the pending build-20 handoff prevented native startup because macOS
denied the app access to `~/.Trash`. The installed handoff callback was completed
successfully from the installation context under the existing lifetime locks;
no marker was manually deleted and no app code or privacy permissions changed.
The native Start window opens again. The user approved existing Keychain access,
and both the owned backend process and the native “Production core ready” state
are confirmed. The subsequent 0.11.4 update removes that maintenance callback
and global Trash reconciliation entirely; startup and handoff no longer depend
on Trash access. See STATUS.md for recovery and replacement evidence.

The user subsequently requested exactly one local Sprintwake Studios app.
Five obsolete archived bundles are recoverable in Trash; 49 main/QA app
registrations pointing into Trash were removed. Only the canonical installed
app remains registered. Cleanup must unregister the resulting Trash URL after
moving each app, since the move can create a new registration. Private backups,
projects/models and the separate Sprintwake Projects app are preserved.

LTX drain handles macOS zombie-only group `EPERM` using a bounded, validated
process-table snapshot. Permission errors alone never authorize another model;
live or uncertain membership keeps the global process journal and admission block.

# English interface

The 0.11 source uses English native chrome, menus, defaults and director guidance.
User text/dialogue and existing saved labels retain their language. Display copy must
not rename stored IDs, connection statuses, contracts or private paths. Public README
screenshots are real native captures from an isolated fictional project; the director
response is scripted and explicitly disclosed. Capture via scripts/capture-showcase.sh.

# Boundaries

- `engine/server/film_direction.py`: native Qwen/LTX prompt composition from the
  selected cast/location/props and planned stateBefore/stateAfter. Image reference
  roles follow the actual deduplicated pixel order. Video retains full camera-group
  identity with separate visible/offscreen roles, native dialogue delivery and separate-score intent. Its version enters
  the film source digest; recipes retain the resolved direction. Re-preparation
  archives prior production journals. `director_authoring.py` carries a bounded
  recent planned-state handoff and validates new manuscript continuity fields;
  `review_gate.py`/`panels.py` expose planned-versus-actual comparisons and optional
  human observations tied to clip hashes. `studio.py` exposes before/after state,
  speaker and language/delivery in the clip details panel. These 0.11.2 improvements
  are retained in 0.11.4/build 21 for the expressly requested local/GitHub update. Film-direction tests and model generation
  remain deferred; STATUS.md records installation separately from qualification.

- `creation_panel.py` / `creation_smoke.py`: native three-card start page and
  simple image/one-shot clip entrances. `Script/creation.json` (v1 image/clip)
  only selects the room on explicit project reopen; canonical board schemas
  remain unchanged. Image work needs no shots. Clip generation saves first,
  requests one retained take through the existing seed API, and uses the existing
  external-workflow confirmation when selected. Conversation restoration never
  overrides Start during ordinary bootstrap. No inference on room navigation.
  Name/description are optional when opening a studio; an empty work receives
  a default title. Description validation belongs to the explicit generate action.
  Creation progress/errors appear beside the open button, with retry after failure.

- `layout_model.py` / `layout_panel.py`: shared native editing shell, compact
  ID-based sequence/scene outline and contextual inspectors. film/scenes/cut/
  structure/sound alias the same Switcher child; non-editing pages remain separate.
  Persisted narrative data is read on open, never inferred from list order.
- `layout_playback.py`: inline prepared editorial mix on the existing single
  AVPlayer, with seek/transport and explicit ownership release. Actual production
  review remains modal; inline viewing never creates production review receipts.

- `director_editorial.py`: native receipt presentation/read-only policy;
  `director_contract.py` loads the pure, versioned core editorial projection.
  The director receives saved cut/coverage/structure/sound decisions with explicit
  absent/invalid/unsupported/truncated states, no media paths or undo contents.
  Inspection never initializes/reconciles journals or syncs manuscripts. Native
  panels remain the only writers of these four independent journals.

- `studio.py`: main-thread native UI state, scoped background completions,
  authoring saves, project lifecycle, director proposals and previews.
- `views.py` / `assets/studio.xml` / `assets/copper.xml`: real Raven widgets,
  restrained glass, charcoal/paper/copper surfaces, contextual next actions.
- `panels.py`: bible, image composer, library and production actions.
- `takes.py`: existing seed take history and adoption.
- `cut.py`: independent 24 fps cut journal, validation, CAS/atomic persistence,
  bounded persistent undo/redo, source trims, reordering and scene grouping.
- `cut_media.py`: measured and verified immutable Input take copies/posters,
  local FFmpeg rough-cut export with exact picture/audio offsets; no inference.
- `editing_panel.py`: scene inspector, cut inspector, one commit per
  timeline gesture and editorial take selection (never canonical adoption).
- `sequence.py` / `structure_panel.py`: persistent narrative groups/goals and
  native runtime comparison/assignment; explicit order projection into the cut.
- `sound.py` / `sound_media.py`: CAS sound journal, immutable audio imports,
  bounded streaming float mix, exact frame/sample alignment and verified WAV cache.
- `editorial_workspace.py` / `sound_panel.py` / `editorial_timing.py`: project-scoped
  document loading, four native sound lanes, source-preserving gestures and mixer.
- `coverage.py`: metre-based room/axis/camera geometry and projection, safe
  coverage proposals, append-only stable shot IDs and bounded CAS plan journal.
- `coverage_render.py` / `coverage_view.py`: deterministic z-buffer layout pixels
  shared by native camera preview and PNG reference; native floor-plan drawing.
- `coverage_panel.py` / `coverage_io.py`: native placement/aim drag and inspector,
  project-scoped journal, conditional storyboard writes and image-composer handoff.
- `backend.py`: owned child process and local JSON/upload/NDJSON transport.
  The existing Storyboard server owns production contracts and GPU orchestration.
- `workspace.py`: external folders, immutable Input/Output copies, manuscript,
  snapshots, portable exports and recoverable archive.
- `importing.py`: independent source import; rebind refs before PUT, never edit
  source media or transfer stale approval receipts.
- `production_model.py`: canonical board/view adaptation, whitelisted proposal
  edits and revision-bound film payloads.
- `review_gate.py`: actual image-plan/review shapes, media completeness and
  revision receipts. Every supplied still and requested video must resolve.
- `studio_state.py`: stable authoring digest and continuous-playback receipts.
- `app.py` / `project.py`: original native review model/player foundation.
- `media.py` / `native/media.m`: AVFoundation texture/audio and Mac menu bridge.

Models are never duplicated or started merely by opening Raven. Long generation
is not implementation QA. Preserve original Storyboard data/configuration, models
and oMLX. Canonical refs/shots/recipes stay under the external project directory;
engine scratch output retains its configured shared location. Never symlink
canonical production folders or bypass the existing approval/memory/queue gates.

Background requests capture project/shot identity. Ignore stale project replies;
never overwrite newer edits with a save or poll response. App close drains owned
work through the desktop pipe; do not terminate a foreign process or global queue.

Pinned Cocoa fixes construct a key/main-eligible NSWindow subclass at allocation
and avoid character queries on FlagsChanged. Do not change Objective-C ISA at
runtime. Accessibility text changes may omit upstream callbacks, so sync fields
before actions, saves and close. Raven remains the entire UI, with no webview.

Repair: library.py owns immutable content-addressed shared images; library_panel.py
provides browsing before project selection and canonical project adoption.
director_state.py distinguishes incomplete/pending/applied replies;
director_authoring.py is the explicit batch writer with snapshots/checkpoints;
director_panel.py exposes one-action native authoring and saved manuscript access.
The recovered partial chat prose is never treated as persisted project content.

Director workspace (0.3): conversations.py owns durable independent sessions in
System/Conversations, legacy copy migration, project rebinding and restart
reconciliation. director_workspace.py owns native chat/project selection and
origin-bound async replies. director_contract.py loads the core's pure schema and
anchor helpers without importing model runtimes. director_executor.py validates
fresh IDs/digests and maps tools to existing APIs, with snapshots and manuscript
sync. The core raven_chat.py supplies authoritative context and the Raven expert
guide, and only proposes actions. Media review cannot be approved by the model.

macOS/project lifecycle repair (0.3.1): one real application bundle under
~/Applications, with source/runtime located through Resources/StudioRoot.txt.
The source-folder bundle is retired outside .app indexing; no duplicate shortcut.
Cocoa borderless windows have Miniaturizable and retain the prior key/input fix.
project_removal.py owns native delete confirmation, trash list and restoration;
server/raven_projects.py owns the pure trash format and guarded API routes admit
filesystem moves under the production lock. Deleted media/recipes stay together.

Real director authoring repair (0.3.2): only the exact create_project →
write_manuscript pair may create and author in one reviewed proposal. Execute the
writer with the actual new slug/digest. Rebase that turn only on its real creation
receipt; leave other stale proposals unchanged. A failed writer retains brief,
count and checkpoint and exposes an explicit same-project resume action. Setup
requires acts/locations; shot validation checks full fields, frame rules and exact
camera continuity, with one contextual correction. Truncated large batches rebuild
the smaller prompt and slot plan. Core stages narrow the catalogue; private trace
IDs and validation errors travel through response, session and diagnostic UI.
Normal batches contain three shots; authoring calls allow 600 seconds separately
from ordinary chat. Explicit resume_only remains an internal UI/executor flag,
never a model tool field. It rejects changed/missing journals before inference or
writes, preserves pending-save recovery and treats a finished journal idempotently.
Both client/core digests normalize empty ravenNotes to missing, so UI defaults do
not invalidate real manuscript checkpoints; substantive note edits still do.

Editorial workspace (0.4): Scener is grouped by contiguous camera setup, not
narrative act. Klipp reuses the native viewer/timeline with an independent cloned
Project; sync_fields never writes that view to the canonical board. source_start
maps cut playback to the original video's in point. Only the picture track is
unlocked; source audio follows picture trims/order. Native change callbacks run
during dragging, so defer reconciliation until mouse-up and commit one whole
gesture. Cancelled gestures are no-ops. Clip removal excludes, retaining media.
All editorial writes go through Script/cut.json with a file lock/revision check;
undo/redo do not mutate or delete source media. New manuscript cards reconcile
into every history state and keep existing edit order. Existing selected media
never follows a later canonical take implicitly. Local take URLs may contain
cache timestamps; resolve their path while continuing to reject remote URLs.
Clear scene image widgets when leaving their page or releasing a project so a
recoverable trash move cannot leave lazy image loads pointing at moved files.
Rough-cut export uses NUT intermediates to preserve exact 1/24 timestamps and
uncompressed intermediate audio, with one final AAC encode. It is explicitly
separate from production approval/final publishing. No new director tools were
added; generation/approval contracts remain in the production engine.

Room blocking (0.5): Script/coverage.json stores location-keyed plans and 30
persistent undo/redo steps under a file lock/revision guard. The native Custom
canvas starts drag selection; the UI tick reads window mouse position/button so
release outside the canvas still commits one gesture. X=east, Y=north, Z=up;
camera right, projection, 180° warnings, screen descriptions and exported pixels
share the same geometry. The live preview uploads z-buffer pixels as a native
Raven texture (lower resolution while dragging), not depth-sorted polygons.
Suggestions try safe framing candidates and wide master lenses; unusable ones
remain visible but deselected. Applying selected warning-free cameras snapshots
the fresh board and uses core CAS to append new directorScene drafts, never
continuations or changes to old shots. Stable angle IDs make retries idempotent.
The optional layout PNG is an immutable Input/reference copy, not an AI image or
approved opening. The image composer receives explicit colour/identity mapping
and available bible images; generation requires its normal explicit action.
Coverage I/O excludes project relocation in both directions; callbacks are scoped
and project release destroys the preview texture. No new director tools added.

Narrative and sound workspace (0.6): structure.json stores sequences/scenes,
runtime targets and knownShotIds. Initialize seeds by contiguous location once;
new cut IDs reconcile into current and historical states, while intentionally
unassigned/deleted groups stay so. No-op opens preserve revision. Actual runtimes
sum included cut trims. Applying narrative order is an explicit CutStore action,
not a production/camera-group reorder or speed change. Structural undo is separate
from picture-order undo.

sound.json stores four fixed buses, absolute-film-time cues and explicit combined
take audio. Native Timeline uses its own semantic ID map and five lanes; picture
is locked, sound bodies/edges move/trim freely across cuts, and mouse-up commits
one complete gesture. A leading trim preserves source/time alignment and clamps
to actual available handles. Positions do not silently ripple with picture edits.
Import copies/validates audio and measures the retained bytes. Missing audible
sources fail, muted/out-of-film cues stay preserved. Original video without an
audio stream contributes silence; existing source sound follows Dialog bus state.

Mixing opens at most 16 new sources plus a streamed float accumulator per batch;
timestamp gaps become streamed silence through asetpts/aresample, not whole-film
delay buffers. Quantization and a latency-compensated limiter happen once at the
end. WAV samples equal cutFrames*2000. Cache identity includes actual media hashes
and mixer version, and validates output hash/format/sample count. Sources are
rechecked before publishing. Preview and rough-cut export share this mix. Picture
cache is independent of sound revisions; audio edits mux new sound with unchanged
cached picture. The UI clearly exposes prepared preview, not a real-time DSP engine.
Preview caches are transient; journals and source copies survive ZIP export.
Editorial I/O excludes project relocation in both directions. Generic preview seek
uses the saved cut duration. Native review defaults are not inserted into canonical
shots merely by viewing, preserving production bytes during editorial operations.


Model connections: connections.py/connection_checks.py and connections_panel.py
separate private machine paths/providers from project data; credentials.py uses
the native Security.framework bridge. contracts/ is the bundled pure companion
contract so first-run setup opens before an engine exists. backend.py selects the
configured engine/Python and external LLM settings; configured media profiles use
server/backends/configured.py + comfy_workflow_backend.py in the companion engine.
workflow_panel.py explicitly requests one independent seed take. No native LTX
film-continuation guarantee applies to an arbitrary workflow. See MODEL_CONNECTIONS.md.

# Distribution boundary

`engine/` is the canonical engine packaged with this app. The original sibling
installation is retained for compatibility. `scripts/build_bundle.py` creates a
relocatable app; `scripts/install_app.py` installs transactionally and preserves
unknown destinations. `installation.py` migrates private settings without moving
productions/models. `updates.py`, `update_panel.py` and `native/updater.m` bridge
Sparkle to authenticated engine quiescence. `engine/server/app_lifetime.py` is
synchronized into `contracts/` and owns process-lifetime update safety. A pending
marker blocks old startup during the installer handoff. No GitHub upload occurs
in build, signing, verification or installation scripts.

Automatic duplicate prevention: managed build paths resolve below `.noindex`.
`package-app.sh --verify` and Sparkle QA retire owned copies on every outcome;
committed installation consumes the input and retires old/legacy apps. Recovery
uses verified, synced ZIPs in Trash, with external receipts. Keeping executable
folders in Trash allows macOS to rediscover them despite unregistration. A shared
lock serializes retirement of explicitly selected obsolete bundles. Version
0.11.4 removes the global Trash scan and the native update-handoff maintenance
callback. App startup, updates and backend reconnects never enumerate Trash or
reconcile app registrations. Exact bundle/build validation and exclusive
startup/instance/code-reader ownership still protect marker consumption.
Transactional installation keeps its exact-bundle recovery and whole-Contents
active-process guards. Installer failures remain visible with recovery data.

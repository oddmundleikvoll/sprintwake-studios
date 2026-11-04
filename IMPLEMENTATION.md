# Raven Studio production expansion

> Historical production-expansion plan. Later releases added editing, model connections and standalone distribution; this is not the current backlog.
> Current goal, status and remaining work: [PROJECT.md](PROJECT.md), [STATUS.md](STATUS.md).

 User-approved production root: `~/Movies/Raven Studio`.
The original installation and saved projects remain in place.

## Acceptance flow

1. Project hub creates, opens, imports and archives real project documents.
2. Idea/manuscript, cast, places, props and selected references stay project-scoped.
3. Direct Qwen generation supports prompt, format, seed, references and durable
   history; results can become a scene opening or a character/reference image.
4. Shot authoring exposes start/action/end/camera, cast, timing, sound/dialogue,
   opening/closing and explicit anchors. Existing media and takes remain intact.
5. Director conversation streams real progress and presents reviewable proposals.
6. Film production preserves exact image-plan and camera-review gates, revisions,
   resumability, selected-job cancellation, serial model admission and cleanup.
7. Assembly/export and a real media library produce deliberate local deliveries.
8. Native app remains responsive during model/network work and owned cleanup.

## Implementation order

- `workspace.py`: external folders, safe imports, manuscripts, immutable outputs,
  portable export and recoverable archive; filesystem tests.
- `backend.py`: owned existing Storyboard backend with external data-dir, async UI
  calls, PID verification, NDJSON and graceful cleanup; inert transport tests.
- `production_model.py`: canonical board/view mapping, scoped proposals and exact
  production-gate payloads; model-free domain tests.
- `studio.py` / `assets/studio.xml`: a project hub and coherent guided workflow,
  contextual actions, composer, reference gallery, review surfaces and real jobs.
  Reuse the verified native Raven editor/player rather than browser embedding.
- Verification: focused tests, real no-inference backend lifecycle/CRUD/upload,
  native window/UI smoke and manual complete flow. No long production runs.

## Design

Retain charcoal, warm paper and copper. Add restrained glass, a persistent
workflow trail, spacious project cards, contextual next steps, focused creation
sheets, project-scoped drafts, progress feedback and searchable commands. Every
visible primary action must work. Empty states explain the next useful step.

Canonical backend directories remain unchanged because receipts, hashes,
resumability and media protection depend on them. Human-facing Script, Input
and Output folders are real; immutable deliveries are copied deliberately.
All are inside the external project root. Engine scratch data stays engine-owned.

## Repair and consolidation — 2026-10-26

- Remove only the retired macOS Storyboard wrapper and its bundle-specific
  preferences/cache/WebKit state. Preserve production engine dependencies,
  source projects, model weights and Raven. Inventory before deletion.
- Import previously generated images into a content-addressed, verified common
  Library with origin metadata. Originals remain for existing recipe references.
  Library browsing works without a project; adopting an image copies it into
  that project's canonical refs before use.
- Replace incomplete director success claims with explicit failure/proposal state.
  Limit shot proposals to small reviewable batches, support film-bible fields,
  show pending/applied/no-change state, and refresh manuscript/storyboard after
  applying. Do not infer applied state from prose.
- Add a real in-app manuscript view derived from the canonical board and one-action
  director authoring with bounded batches, snapshots and actual saved results.
  No image/video generation is part of this repair.
- Verify storage/idempotence/escaping, truncated response regression and actual
  native library/manuscript/apply flow before claiming done.

## Complete director workspace

Acceptance: start in a general director conversation, propose/create a real
project and retain the same conversation; select another project or start a
separate conversation without losing history/drafts; all existing production
capabilities have a validated, executable director tool. Real status/results
must replace inferred success. Media approvals remain actual human review.

- Canonical pure tool schemas and Raven expert guide in the production engine;
  `/api/raven/chat` accepts a nullable project and authoritative resource context.
- Session documents in System/Conversations, preserving legacy conversations.
- Pure executor maps every advertised tool to real core API/workspace operations,
  verifies resource IDs and revisions, and returns structured execution evidence.
- Native director workspace supplies project chooser, recent conversations,
  new conversation, durable composer, reviewable tool cards and output links.
- Verification includes catalog/executor coverage, no-project create/continue,
  independent sessions, stale proposals, gallery adoption/generation payloads,
  exact film revision/anchor operations, review gates, native app and shutdown.


## macOS installation, window controls and project removal

- Inventory physical bundles, shortcuts and registered app identities; install one
  real bundle in Applications and preserve the runtime reference explicitly.
- Reproduce yellow-button failure, add Cocoa miniaturization capability and test
  the actual HeaderBar button event path plus restore in the real native window.
- Expose project deletion on cards and in project settings/overview with a named
  confirmation. Retain a complete production in a browsable/recoverable trash.
- Serialize folder moves with backend admission; reject active work and restore
  collisions, keep media paths stable and preserve conversations.
- Verify actual temporary project delete/restore and unchanged files; do not
  delete any user production or generate new media for QA.


## Qualify real director authoring from chat

User authorization 2026-10-27 explicitly includes real local director inference
and saving the qualification project. Image/video generation remains out of scope.
Baseline live model returned a rejected tool call (flat-ID format validation),
and no project/manuscript. Preserve real validator traces and repair boundedly;
accept only the reviewed create_project -> write_manuscript text sequence.
Execute creation, then bind the writer to the actual new slug/digest. Writer
uses narrowed setup/shot schemas, described acts/locations and bounded corrections
with full brief; never hand-author replacement story content or weaken CAS/gates.
Verify original request via actual native chat/model, execute its proposal, then
inspect canonical project/manuscript, cast/locations, scene groups, full clip fields
and duration. Save real run trace IDs and inspect actual app Manus/Storyboard.
Keep simulated tests as regressions, clearly distinct from real-model qualification.


## Portable model connections (0.8.0)

The existing qualified local production flow remains default. connections.py
validates private installation/provider configuration; backend.py starts the
selected companion/runtime with a separate LLM configuration directory. The
configured backend composes explicit Comfy API profiles or disabled image/video
providers. workflow_panel.py confirms one independent take; full native film
resume requires Qwen/LTX. See MODEL_CONNECTIONS.md for setup, credential scoping,
profile contracts and recovery. Contracts are bundled/synchronized separately so
setup can open with no companion installed.

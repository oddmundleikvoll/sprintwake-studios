# Sprintwake Studios

**Project goal, completed work and remaining release steps:** [PROJECT.md](PROJECT.md).
**Current technical status and verification:** [STATUS.md](STATUS.md).

Native macOS film production studio, built with Raven, Vulkan/MoltenVK and
AVFoundation. The app includes its production engine, Python runtime, FFmpeg
and Sparkle updater. Models and model services are connected separately; no
weights download or inference starts when the app opens.

Licensed under [CC BY-NC-SA 4.0](LICENSE), including attribution, noncommercial
use and sharing adaptations under the same terms. The included Storyboard engine
and copied contracts retain their upstream notices. See [NOTICE.md](NOTICE.md)
for attribution and dependency provenance, and [CONTRIBUTING.md](CONTRIBUTING.md)
for development checks.

## Open or build

Open **`~/Applications/Sprintwake Studios.app`**. The app runs independently of
this checkout. Existing projects, private model settings and Keychain credentials
remain outside the bundle, using the established Raven Studio locations.

For development on Apple Silicon with macOS 14 or later, install Apple's command
line tools and `uv`, then run:

```sh
git clone https://github.com/oddmundleikvoll/sprintwake-studios.git
cd sprintwake-studios
./scripts/setup.sh
./run.command
```

Setup uses committed locks and pinned native dependencies. It builds a standalone
app under `dist/` without installing it. An existing build is preserved; choose a
fresh `RAVEN_PACKAGE_OUTPUT` when rebuilding. To install a reviewed build, use the
transactional installer described in [DISTRIBUTION.md](DISTRIBUTION.md).

The canonical production engine lives in `engine/` in this same repository.
The previous sibling `../storyboard` installation and its projects are retained.
Explicit external engine/runtime paths still work. The default local Qwen/LTX
connections are preserved during installation. New users select their own services
under **Modeller**. See [MODEL_CONNECTIONS.md](MODEL_CONNECTIONS.md) for supported
workflow capabilities and current limits.

Sparkle checks the same repository's GitHub Releases for signed updates. The
header shows **↓ Ny versjon** when one is available. Installation waits for
production, saves, media processing and owned engine processes to finish. The
source repository is [oddmundleikvoll/sprintwake-studios](https://github.com/oddmundleikvoll/sprintwake-studios).
No downloadable Mac release has been uploaded.
Build, signing, local update verification and release steps are documented in
[DISTRIBUTION.md](DISTRIBUTION.md).

## Choose what to create

The app always opens at **Start**, with three cards:

- **Generer et bilde:** open the image studio; name and description are optional
  at entry and can be filled in later.
  Choose up to ten references, four aspect ratios and an optional seed; compare
  results and save an image to Output. No manuscript or shot is required.
- **Lag et klipp:** open the clip studio, describe one independent shot, select its length and size,
  and explicitly request a new take. An optional opening image can be made in
  the image studio. Watch/select retained takes in Opptakshistorikk, stop your
  own job, or continue in Filmstudio. External workflows keep their existing
  model/length/size confirmation.
- **Filmstudio:** open your projects or begin with the director, then use the
  full manuscript, bible, production and editing workflow below.

Recent works appear below the cards. **Start** always returns to this chooser;
**Filmstudio / Prosjekter** lists all saved works. Image and clip works reopen
in their own studio. The previous conversation is retained and only opens when
you choose the director. Opening a room never starts model inference.

## Create a film

1. **Regissøren:** start with an idea in a free conversation and let the director
   propose a project. Apply its visible card to create the actual project and
   continue in the same conversation. Alternatively use **Prosjekter** to create a project with a name, idea and visual expression, or
   import an independent copy of an existing `storyboard.json` (⌘O).
2. **Manus:** describe the visible story, style and sound. A readable manuscript
   is saved automatically under `Script/manus.md`.
3. **Filmbibel:** add characters, places and props. Import reference images or
   generate a character sheet, then explicitly choose the image to use.
4. **Arbeidsflate → Regi:** add shots or continue a camera setup. Edit start/action/end,
   camera, location, visible cast, dialogue, native speech, seed and duration.
   Details also expose reorder, remove, manual anchors and advanced take tools.
5. **Bilder:** describe an image, choose up to ten actual references, format and
   optional seed. Generate Qwen images, compare results, assign an opening and
   publish selected media to Output. Generated history remains project-scoped.
6. **Produksjon:** prepare the image plan, inspect its actual images, then approve
   that exact revision. Play every requested shot with audio and inspect the
   boundary images before approving a camera-group review. Resume and stop target
   the selected project job. The production engine keeps its revision checks,
   GPU admission lock, memory barriers, immutable recipes and resumability.
7. Assemble the completed shots, play the final movie, or export a project ZIP.

Use **Lag manus og klipp** from Manus or Regissøren for direct authoring: enter
a brief and shot count, then **Lag og lagre manus**. The director sets up the bible
and normally writes three cards at a time. Each validated batch is snapshotted, saved
to the canonical project and reflected in Manus/Storyboard. Cancellation retains
committed work; matching unfinished drafts resume without duplicating cards.
This action writes text and shot cards; media generation remains a separate step.

The overview points to the next useful step. **Regissøren** streams progress and
presents proposals for review; edits are checked against the authoring revision.
The director has 41 validated tools for project/shot/bible authoring, images and
library adoption, take variants/refinement, dialogue/music, film preparation and
resume/stop, anchors, assembly and export. Each mutation or generation is shown
before execution; read-only status and library tools can run directly. Film
approvals lead to the normal production review surfaces. ⌘K searches projects
and commands. Changes autosave; closing waits for owned work to release resources.

The director also receives the saved cut, room blocking, narrative groups/runtime
goals and sound mix controls. Four read-only inspection tools refresh these facts
without changing journals or manuscripts. Editing remains in the native panels;
inspection never proves that video/audio has been reviewed. Missing, invalid,
unsupported and oversized editorial documents have explicit states.

Before publishing or connecting other models, see [model connections](MODEL_CONNECTIONS.md)
and [the public setup review](OPEN_SOURCE_READINESS.md). The qualified default
pipeline is local Qwen/LTX on macOS. External Comfy profiles are opt-in,
explicit API-export workflows with independent takes; Raven is not a general
arbitrary-workflow runner.
Personal shot review marks remain separate from production approvals.

Retained seed takes can be previewed and adopted from **Takes → Opptakshistorikk**.
**Arbeidsflate** shares one editing layout across **Scener**, **Struktur**,
**Regi**, **Klipp** and **Lydspor**. A compact sequence/scene outline stays on the
left, the native player in the middle, contextual details on the right and the
timeline below all three columns. Copper colors and New York/SFNS typography are
retained. The layout fits both the normal window and its 1180×760 minimum.

The outline shows actual video, storyboard stills and missing-image states, with
shot numbers and durations. Saved narrative groups appear when available; camera
setups provide the initial grouping. Collapse/expand groups without changing the
project. Selecting a group opens its details; selecting a shot retains its ID
across editing modes, including differing manuscript/cut orders.

**Struktur** edits the selected sequence or scene in the right inspector. Film
goals and explicit order application remain available there. **Lydspor** keeps the
locked picture and four audio lanes below the player; **Lydklipp / Mikser** switches
the right inspector. Prepared film/audio mixes play in the central viewer using
the same native player. Scrubbing the sound timeline also previews the picture;
audio positions beyond the picture end remain absolute and editable. Editing or
changing workspaces releases the preview. Production review retains its explicit
review surface and approval rules.

Open **Arbeidsflate → Romkart** to plan a room before generating media.
Choose a bible location, drag the coloured figures and cameras on the metre grid,
and drag the selected camera's target to aim freely. **Rom og akse…** sets room
dimensions, aspect ratio, the two axis figures and camera side. Select a figure or
camera and choose **Rediger plasseringen…** for precise coordinates, height,
character identity, gaze/target and focal length. Add/remove placements as needed.
The preview shows simple geometry with the actual camera projection.

**Foreslå kameravinkler** proposes a master, two reverse shots and two close-ups
for a two-character axis. Camera positions stay on the chosen side of the 180°
axis. The master keeps its subjects head-to-foot; warnings identify crossings,
crowded positions and cropped targets. Proposals without usable framing are
marked and deselected. Adjust the plan and choose which cameras to use.
**Angre / Gjør om** persists up to 30 steps; one drag is one step. Plans per
location and their history live independently in `Script/coverage.json` and are
included in project exports. Planning does not start any model.

**Legg valgte vinkler i storyboard** appends draft shots with linked bible cast
and location, lens/pose and screen-direction descriptions. Each angle starts a
new camera group. Applying the same unchanged angle again is idempotent; changed
blocking produces new drafts. Review action and dialogue before production.
Existing shots, takes and approvals are preserved. After applying an angle,
**Bruk utsnitt i Bilder** sends its actual geometry PNG and available character
references to the image composer, with explicit colour-to-character mappings.
Choose **Lag bilde** when ready. The geometry guides composition; final generated
images/video still require the existing production review.

**Klipp** is a separate, non-destructive edit of the film. Drag a clip to reorder
it, or drag either edge to trim; gaps close automatically and original sound
stays linked to picture. The inspector also accepts exact in/out frames at 24 fps
(out is exclusive), marks at the playhead, take selection, earlier/later and
reversible exclusion. **Angre ⌘Z / Gjør om** reverses whole editing actions;
native text fields keep their own text undo. Every edit is saved immediately,
including up to 50 undo/redo steps, and survives project/app reopen.

Editorial decisions live in `Script/cut.json`, independently of `storyboard.json`,
generation order and continuation receipts. Chosen videos and thumbnails are
verified Input copies, so rendering/adopting another production take does not
replace the cut's media. Selecting a different take resets that clip to its full
measured length; undo restores the earlier take and its trims. New manuscript
cards append without disturbing existing edit choices; removed manuscript cards
remain available in the cut. Existing empty cards stay storyboard placeholders
until **Bruk produksjonsopptaket** or **Velg take…** explicitly binds a video.

**Eksporter grovklipp** encodes the saved order, takes and trims with linked
48 kHz audio into `Output/Video/grovklipp-r….mp4`, with its cut recipe beside it.
Missing selected video blocks export. Unproduced cards remain labelled
**BILDEPLAN** placeholders. This editorial rough cut does not approve production
or change the canonical final movie. Project ZIP exports retain the cut journal,
history and pinned media. Storyboard's own timeline remains a review surface;
its Details edits still change the production plan.

## Sequences, scenes and runtime

Open **Scener → Sekvenser og spilletid** (also **Struktur** in Klipp). Create
named sequences and scenes, move scenes between sequences, and assign the saved
cut's cards to them. This narrative grouping is independent of production camera
setups. Initial scenes follow contiguous locations; newly arriving manuscript
cards enter **Ufordelt** without changing existing assignments.

Set a runtime goal for the film, sequence or scene with **Navn og tidsmål…**.
Times accept seconds, `mm:ss` or `hh:mm:ss`; `0` clears a goal. The view compares
goals with the actual cut duration after trims/exclusions and shows the difference.
Use **Rediger klippingen** to adjust the scene's picture. Goals never stretch
sources or start generation. Reorder sequences/scenes, then explicitly choose
**Bruk rekkefølgen i klippen…** to apply that order while preserving takes and
trims. Klipp's undo restores this picture-order action.

Structure edits have their own 50 persistent undo/redo steps in
`Script/structure.json`. Removing a scene/sequence only removes its grouping;
the picture cards remain available for assignment. Merely reopening a workspace
does not create an edit or invalidate another window's revision.

## Independent sound tracks

Open **Lydspor** from Scener, Klipp or the sequence workspace. The native timeline
shows locked picture plus **Dialog**, **Musikk**, **Miljølyd** and **Effekter**.
Choose a lane and **Importer lyd…**, or select existing project audio. Imports are
verified Input copies; a video with audio can also be used as a sound source.
Drag audio bodies to move them, drag edges to trim, or use the inspector for
exact film placement, source in/out, fades and gain in dB. Several cues may
overlap, and one cue can continue across any number of picture cuts. Duplicate,
split at the playhead and Delete are reversible. One native gesture is one undo.

The mixer has per-track gain, mute and solo. **Behold opptakslyden (samlet)**
includes the selected videos' existing combined soundtrack, following picture
trims and the Dialog bus. It does not separate a generated voice from ambience
already mixed into a video. Disable it when replacing that sound with separate
recordings. Audio cue positions use absolute film time; review their alignment
after reordering or trimming picture. Cues beyond the film end stay saved.

**Hør lydmiksen** plays the actual prepared WAV. **Se film med lydmiks** prepares
and plays picture with the same mix. After a sound journal exists, Play in Klipp
opens this mixed preview too. Picture is cached separately, so audio-only changes
require a new mix/mux rather than encoding all the pictures again. **Eksporter
grovklipp**, from either workspace, includes the saved sound mix and records both
cut and sound documents beside the movie. Production approvals/final movie remain
separate.

The mixer streams bounded batches of at most 16 sources plus an accumulator,
keeps floating-point headroom until the final limiter and writes 48 kHz stereo
PCM with 2,000 samples per 24 fps frame. Preview and export use the same source
trims, positions, levels and fades. No image/video/voice model starts here.
`Script/sound.json` keeps 50 persistent undo/redo actions. Project ZIP exports
include both editorial journals and original Input copies; rebuildable preview
caches are excluded.

## Delete or restore a project

Choose **Slett…** on its card in **Prosjekter**, or **Slett prosjekt…** in the
project overview/settings. The confirmation names the project; **Flytt til
papirkurv** moves its entire production out of the active project list. The server
holds the production admission lock and refuses deletion during active work.
**Papirkurv** on the project page lists deleted projects with **Gjenopprett**.
Restoration preserves the original project ID, references and media; a conflicting
existing ID must be renamed first. Conversations are retained as free conversations
and can be linked to the restored project using their project chooser. This flow
has no permanent-purge action. The yellow window button minimizes to the Dock.

## Director conversations

The app opens in **Regissøren** before a project is selected. **Ny samtale** starts
another conversation for the selected project; **Begynn med en fri idé** starts
without a project. The project chooser binds a conversation to an existing
project. Switch among saved conversations in the left rail, rename them or archive
them without deleting their files. Composer text and thinking level are saved per
conversation. Late replies remain attached to the originating conversation.

Ask for a new project and manuscript together to get **Opprett prosjekt og skriv
manus**. One reviewed proposal creates the project, binds this conversation, then
writes its bible, scenes and shot cards in bounded batches. The chat reports actual
saved counts. If writing stops, **Fortsett manusarbeidet** resumes the same brief
and checkpoint in that project, including after restarting the app.
Resume refuses a changed manuscript rather than starting another draft from an
old chat card. Ordinary empty note defaults do not count as manuscript edits.

The expert guide in `engine/server/raven_director_guide.md` adapts the
project's film-regi, character-sheet, clip-prompt and ComfyUI instructions to
Raven's actual tools. `/api/raven/chat` supplies the actual project bible, shots,
film state, prefixed image IDs and shared library, including without a project.
The UI executes validated proposals through `director_executor.py`; text claiming
success never counts as a saved result. Bounded manuscript writing uses this same
expert endpoint. Interrupted executions require fresh review after restart.
Invalid tool formats get one bounded correction with the actual validator errors.
Setup and shot writing use separate tool catalogues. Rejected replies expose
**Vis feildetaljer**; private raw replies and validation diagnostics live under
`System/director-traces/`. A diagnostic write failure is shown without discarding
an otherwise valid proposal.
Authoring has a separate ten-minute response budget for the local 27B model;
ordinary chat keeps its five-minute bound. Each call remains limited in output.

## Production files

Default production root: `~/Movies/Raven Studio` (changeable under Modeller).

```text
Raven Studio/
  Projects/<project>/
    storyboard.json            canonical production document
    Script/manus.md            readable manuscript and folder manifest
    Script/coverage.json       room plans and persistent undo/redo
    Script/structure.json      narrative sequences/scenes and runtime goals
    Script/sound.json          independent sound cues, mixer and undo/redo
    Input/                     immutable, digest-named source copies
    Output/Images/             deliberately published images
    Output/Video/              deliberately published movies
    Output/Audio/
    Output/Exports/
    System/                    authoring checkpoints and recoverable plan snapshots
    refs/, shots/, director/   engine media, recipes and production receipts
  Exports/                     project ZIP exports
  Archive/                     recoverable archived projects
  Trash/<id>/Project/           deleted production, retained for restoration
  Library/                     shared, indexed images with SHA-256 and origins
  Tools/
  System/Conversations/        durable named conversations, history and composer
  System/                      owned backend log, active conversation and UI drafts
```

Canonical engine paths stay intact because production receipts and resume logic
rely on them. Engine scratch runs still use the configured shared output outside
ComfyUI. Imports copy referenced media and retained shot files; they do not edit
source projects or carry old production approvals into a new revision. Archive
moves a complete project; no media is deleted. Existing project sources, production-engine configuration, models and the signed
oMLX application remain in place. The old Storyboard.app, its preferences, cache
and WebKit directory were removed; small preference/WebKit backups are under
System/migrations/retired-storyboard-state.

`./run.command --root /absolute/test/root` selects a separate production root.
`--project /absolute/project/storyboard.json` imports a source after startup, or
opens it directly when it is already in this Raven root.

## Shared image library

Bibliotek works before opening a project and combines shared images with the
current project's media. Search by original name or source. **Bruk som referanse**
copies a selected library asset into the current project's Input and canonical
refs before selecting it in the image composer. Fifteen previously generated
images were copied into the real Library and verified against their source hashes.
No source media was moved or deleted.

Explicit, repeatable migration (no inference):

```sh
uv run --frozen python -B scripts/migrate-images.py --root "$HOME/Movies/Raven Studio" --source "Generated=/absolute/output/folder"
```

## Verify

```sh
uv run --frozen python -B -m unittest discover -s tests -v
./scripts/check.sh
./run.command --smoke-test
```

`check.sh` runs project tests, builds the native bridge, runs seven focused Raven
CTest suites and checks the diff. The real-window smoke test uses a temporary
production root, actual backend APIs and retained media. It tests creation,
authoring, upload, preflight, reference selection, image comparison, take history,
video/audio playback, overlapping edits, export and reopen. Evidence is in ignored
`qa/`; no new inference or long film run is part of this test.

The director flow is tested with inert model replies, real project CRUD, actual
image uploads/adoption and canonical anchor writes. Native window checks include
no-project chat → create and write manuscript → same-session continuation,
independent composers and late replies. Separately, the actual local director
Qwen3.8-27B-Uncensored-MLX created a qualification project through native chat and
wrote three acts, three places, two characters and twelve planned shots on
2026-10-27. Canonical creative fields match the real model tool replies; the saved
manuscript/checkpoint confirms completion. This qualification generated text only.
Evidence is in ignored `qa/director-real-qualification.json`. After the Mac was
unlocked, manual native checks confirmed the chat's saved-result receipt, all three
acts and twelve manuscript cards, the twelve-shot storyboard/timeline and the last
shot's editable direction. The native window smoke passed using a disposable
project. The app connects to separately configured Qwen/LTX services. These
text-authoring checks do not qualify a new media/model installation. Comfy Desktop, installed
weights and the selected local language service must be available when generating.
Larger sizes and long films retain the existing engine's qualification limits.

## Runtime

Raven revision `abed7e6a695524d41ec73b5e29b46343d5293cb3` (Apache 2.0),
MoltenVK v1.4.2 with checked SHA-256, isolated Python 3.12.14.
`runtime/patches/cocoa-input.patch` fixes custom-header key eligibility and
FlagsChanged handling. Setup applies the patch idempotently. Source licenses stay
with the pinned runtime sources. The earlier model-free editor remains in
`app.py` as the native presentation/player foundation; `studio.py` is the app entry.

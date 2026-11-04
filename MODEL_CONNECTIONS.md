# Sprintwake Studios model connections

Sprintwake Studios separates connection, model profile and project data. Shipped defaults
remain the local setup: ComfyUI at `127.0.0.1:8188`, local Qwen for images,
local LTX-2.5 Q8 for video, and `~/Movies/Raven Studio` for production data.
Passive checks do not start models or download weights.

## Portable setup

### Installed app (0.9.0 and later)

The installed Sprintwake Studios app includes its production engine, Python CPU
runtime and FFmpeg/ffprobe. It does not require a developer checkout or personal
installation folders. Use **Models** to select production/workspace locations,
model services and explicit workflow profiles. Models and service installations
remain external; opening the app or testing a connection starts no inference.
See [PROJECT.md](PROJECT.md) for current scope and release status.

### Source development or an explicit external engine

The canonical engine source is `engine/` in this repository. A matching external
engine/runtime can still be selected deliberately under **Models**. The optional
`scripts/setup-engine.sh /path/to/engine /path/to/dedicated-venv` creates a Python
3.12 CPU environment from the engine's pinned `requirements-ci.txt`, without models.
The normal source setup provides owned media tools; if a custom setup has none,
provide FFmpeg on PATH or set `RAVEN_FFMPEG` and `RAVEN_FFPROBE`. FFmpeg has no path
field in the modal. These are development/custom-engine options, not additional
installation requirements for the packaged app.

Settings are private and atomic. API keys are bound to the profile and endpoint, so changing an address does not
reuse a key stored for another service. They come from the OS Keychain/credential
loader into the child environment and are never written to profiles, projects,
recipes or exports. OpenAI-compatible services and Ollama are supported; OpenAI
and optional Anthropic SDK profiles are available when their dependencies and
credentials are installed. URLs are HTTP(S) origins without credentials/query.

## Workflow profile v1

External Comfy workflows must be exported in API format and wrapped explicitly;
UI-format graphs are not accepted automatically:

```json
{
  "version": 1, "id": "my-video", "label": "My video workflow",
  "kind": "video",
  "workflow": {"10": {"class_type": "<class from API export>", "inputs": {"text": ""}}},
  "bindings": {"prompt": {"node": "10", "input": "text"}},
  "output": {"node": "<save node>", "key": "videos"},
  "capabilities": {"fps": 24, "frameRule": {"kind": "any", "step": 1,
  "offset": 0, "minimum": 1, "maximum": 121}, "maxFrames": 121,
  "sizeAlign": 64, "resolutions": ["768x512"]}
}
```

Placeholders must be replaced with the actual API-exported nodes and output;
this is not a runnable dummy workflow. Image profiles use `kind: image` and an
`images` output. Video explicitly declares 24 fps and its own limits. Optional
bindings include seed, width, height, frames, fps, start/end image and
`reference_1`–`reference_10`. Only declared bindings are accepted.

Raven runs independent takes only. It does not provide native continuation,
long-film resume, recorded speech, arbitrary style-reference translation or
implicit LTX frame rules for external workflows. Declared output is validated
and kept with an immutable profile/workflow digest recipe. Existing takes stay
untouched when a new take fails.

Preflight reads `/object_info` and `/queue` only. Pending jobs belong to their
own prompt ID; cancellation drains that job, while uncertain jobs block a new
run until recovery confirms terminal history. No global `/interrupt` or `/free`.


## Create and import a profile

Export the graph using ComfyUI's API export. Select real node/input IDs in that
file; no graph/model is guessed from a checkpoint name. For example, if text
is node 6 input `text` and SaveImage is node 9:

```sh
uv run --frozen python -B scripts/make-profile.py /path/to/workflow-api.json /path/to/my-image.json --id my-image --label "My image model" --kind image --bind prompt=6:text --output 9:images
```

For video use `--kind video --output NODE:videos --capabilities /path/to/caps.json`
and declare the actual graph's frame rule, 24 fps, maximum frames and alignment.
Bind `frames=NODE:INPUT`/`width=NODE:INPUT` etc. only where the graph accepts them.
The helper validates the profile, writes it privately, and refuses to replace an
existing file. Import it in **Models → Import Comfy profile**, select it for
images/video and save. Settings apply at the next ordinary restart. Current work
is not moved, and active connections remain in effect until then.

For external video, **Direction → Create workflow take** confirms and saves the desired
model, frame count and resolution on the clip card, then creates one separate
take. A failed attempt leaves those desired parameters available for retry; an
older take can show as stale but its media and independent Cut selection survive.
Choose a completed take explicitly in Take history and edit in Cut/Sound.

## Recovery and qualification

A lost response to `/prompt` keeps a private admission journal in the settings
folder's `Jobs/`. On a new attempt Raven reads queue/history to recover that own
client ID, then waits for its prompt's terminal history before admitting more
media or language work. No new prompt is submitted while unresolved. If the
provider removed history or the submission cannot be identified, leave the journal
in place until the provider's state has been checked; do not automatically retry
a potentially running job. A provider/server restart that discards history may
require explicit administrator cleanup of that endpoint's journal after confirming
no work remains. Changing the production/workspace folder does not bypass it.

Transport/contract qualification uses local fake HTTP services, real decoded
synthetic PNG/MP4 files, a relocated engine, and actual native controls. It does
not certify the visual quality or runtime of an untested third-party model. The
installed 0.9.0 app includes its engine and native/CPU runtime and was tested
after relocation in a read-only bundle. No weights are bundled. Public distribution
still requires the release steps recorded in [PROJECT.md](PROJECT.md).


External/unmanaged providers own their GPU residency and unloading. Raven does
not claim the local Qwen/LTX synchronous memory barrier for arbitrary services.
Profiles must not embed credentials for third-party custom nodes; the supported
credential path authenticates the configured service endpoint. The headless
backend_host.py accepts explicit RAVEN_LLM_API_KEY/RAVEN_COMFY_API_KEY environment
variables. The native UI instead uses only its endpoint-bound Keychain entries
and discards inherited provider-key variables before launching the child.


Projects retain an explicit `modelProvider: comfy-workflow` and profile digest
binding, including per-clip overrides. These survive the legacy local-project
migration and affect render fingerprints. A changed profile or mismatched active
model requires a fresh explicit confirmation; older media is not relabelled. New
projects inherit the active video profile. Adopting a take verifies and copies its
recipe alongside the selected clip. Unmarked legacy projects keep local LTX rules.

Only the exported graph and declared bindings drive external workflows. Separate
shot camera/action/dialogue/sound fields are not translated into model-specific
conditioning automatically: include the required content in the shot prompt or
workflow. Use native-compatible MP4 encoding (normally H.264/AAC). With own images
and local LTX, manual variants remain under Direction → Advanced takes; the automatic
image-plan/film flow requires local Qwen/LTX.

The setup helper marks the dedicated environments it creates. It refuses to sync
an unrelated existing environment, protecting inference installations and other
Python environments. Selecting an existing runtime in Raven only reads/uses it.

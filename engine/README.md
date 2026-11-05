# Sprintwake replacement engine

This directory contains the independently authored MIT-licensed production
engine used by Sprintwake Studios. It is a local-first service: projects and
media stay on disk, while language and audio providers may be local or hosted.

## Providers

- **Long films:** the external [dgrauet/ltx-2-mlx CLI](https://github.com/dgrauet/ltx-2-mlx) is invoked from a local
  model pack. The engine runs offline, keeps 25 carried frames between clips,
  validates decoded 24 fps / 48 kHz output, and never downloads weights.
- **Images:** Qwen Image 2.1 Turbo runs through a configured ComfyUI API
  workflow. Install the independent `SprintwakeReleaseMemory` node so the
  engine can synchronously release Comfy model state before LTX starts.
- **Language:** OpenAI-compatible, OpenAI API, Ollama, and Anthropic profiles
  are supported. Optional managed Ollama/oMLX profiles load one selected local
  model and require confirmed unload before media work continues.
- **Audio:** explicitly configured OpenAI-compatible or HTTP audio services
  can produce dialogue and soundtrack WAV files. The engine does not claim
  support for an unconfigured voice or music backend.

Cloud providers receive only the request fields documented by the active
operation. Credentials belong in the host application's Keychain or environment,
never in project JSON, recipes, or workflow graphs. Model and provider licenses
remain separate from this engine's MIT license.

## Private settings

Copy the sample files into the private EngineSettings directory outside Git.
Set `sprintwakeLtx.command` to the executable `ltx-2-mlx` and
`sprintwakeLtx.model` to an already installed local LTX-2.5 Q8 pack. The service
does not discover, download, or modify model files during startup.

`server-config.json` may also contain an explicit `languageLifecycle` map for
selected local `ollama` or `omlx` profiles. A 27B profile must allow at least
720 seconds for cold start and release. Unknown residency or failed unload is
fail-closed.

## Comfy node installation

Install the independent node into a dedicated ComfyUI `custom_nodes` directory:

```sh
python scripts/install-comfy-plugin.py /path/to/ComfyUI
```

The installer copies only `engine/comfy_nodes`, refuses symlinked
destinations or conflicting files, and never starts ComfyUI or submits a job.
Use `--dry-run` to inspect the exact destination first. Run the native film Comfy service with `--cache-classic` (or `--cache-none`); LRU and RAM-pressure caches are refused because they may retain CPU model weights across the barrier. Existing Comfy startup settings are not changed by the installer.

## Runtime

The qualified CPU runtime uses Python 3.12 with `av==18.1.0`, `Pillow==12.3.0`,
`numpy==2.5.3`, and `imageio-ffmpeg==0.8.1`. FFmpeg and a local LTX model pack
are external prerequisites; neither is bundled by this source directory.

## Verification

From the repository root:

```sh
PATH="$PWD/runtime/tools:$PATH" runtime/bundle-venv/bin/python -B -m unittest discover -s engine/tests -v
```

Tests use synthetic decoded media and fake loopback providers. They do not run
model inference or download weights.

The CLI interface was checked against installed MIT runtime revision `62b7651406f8ad4a0b7a3f36a93874a1015d59ca`. It needs distilled generation and native extension with repeatable image anchors. New-engine qualification uses synthetic media, not a new model-generated film. The separate installed app and model runtimes are preserved.

HTTP dialogue providers accept OpenAI-compatible speech requests at `/v1/audio/speech` and return 48 kHz stereo WAV. Optional `http-audio` soundtrack services use that same endpoint with `{model, prompt, response_format: "wav"}`; this is a specific adapter contract, not support for arbitrary vendor music APIs. A private service may name an `apiKeyEnv`; its value is read only at request time and never saved in media recipes. Native audio cloning and the retired direct MOSS/SA3 adapters are not included.

# Independent engine replacement — 0.11

Requested 4 November 2026; MIT selected by the user on 5 November.
The current `engine/server`, Comfy helper plugin, engine tests, copied pure
`contracts` and director prose are independently authored MIT code. The earlier
CC server, browser client, Fable helpers and upstream tests have been removed
from the current tree. Old commits and the separately preserved Storyboard
installation retain their original licenses; they were not relicensed.

## Authorship boundary

Implementation was authored from native call sites, schema-v1 data formats,
public provider/CLI APIs and production requirements. Implementation workers
were instructed not to read, copy or translate the retired engine/contracts.
Public tool names, routes, identifiers and file formats are interoperability
facts. The new repository, scheduler, journal and provider implementation use
a fresh architecture. Tests record engineering behavior, not a legal opinion.

The independent path also removes a mandatory external CC dependency: local
LTX is called through the external MIT ltx-2-mlx CLI, rather than the previous
Comfy LTX node that imported old Storyboard helpers. Qwen uses native Comfy
nodes and an independent synchronous memory-barrier/pending-cancellation plugin.
No installed Comfy files, models, signed oMLX app, projects or production settings
were changed during source replacement.

## Preserved behavior

- Schema-v1 projects, native authoring digest/CAS, snapshots and immutable media.
- Opaque director tools and bounded authoring/editorial context; no model work
  on startup, opening a panel or reading a project.
- Local/hosted language APIs and explicit remote/local Comfy workflow takes.
- Pixel-bound image approval, contiguous camera groups (at most six shots),
  24 fps / 48 kHz media, exact 25-frame/50,000-sample carry removal, durable
  CLI checkpoints, scoped cancellation and explicit final video/audio review.
- Retained takes (including read-only historical folder layout), recipe-verified
  selection, original bytes and recoverable trash/restore.
- Application lifetime/update protocol, including the published
  `Updates/code-reader.lock` domain and matching legacy handoff markers.

Optional direct MOSS/SA3 provider implementations are retired; configured speech
and soundtrack HTTP adapters are available. Models and providers keep their own
terms. No claim of feature-film visual quality or generation speed follows from
synthetic tests, and no new inference film was generated for this change.

## Verification

Run current source checks with:

```sh
./scripts/check.sh
PATH="$PWD/runtime/tools:$PATH" runtime/bundle-venv/bin/python -B -m unittest discover -s engine/tests -v
.venv/bin/python -B scripts/sync-contracts.py
./run.command --smoke-test
```

Qualification logs and screenshots are kept in ignored `qa/`; the final record
is summarized in `STATUS.md`. Public source publication is authorized. Publishing
Mac binaries, appcasts and notarized releases remains a separate user decision.

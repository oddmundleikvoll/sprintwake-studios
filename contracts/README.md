# Public connection contracts

The portable boundary is the versioned connection document and schema-v1
Comfy workflow profile described in [MODEL_CONNECTIONS.md](../MODEL_CONNECTIONS.md).
Credentials are never part of either.

The private connection document contains explicit root, engine, Python and
workspace paths, a Comfy origin, selected LLM/image/video IDs, LLM profiles and
workflow profiles. Raven validates it before starting the owned engine. Default
IDs (`local`, `qwen-local`, `ltx-local`) preserve the current local install.

Workflow profiles are explicit API-export graphs, not arbitrary UI graphs. They
require a prompt binding, declared output node/key, bounded identifiers and
typed capabilities. Video is fixed to Raven's 24 fps; frame rules, max frames,
size alignment and resolutions come from the profile. Unsupported inputs are
rejected rather than silently ignored.

Recipes contain profile, compiled workflow digest, model metadata and output
manifest, never API keys. A profile change creates a new take and never rewrites
old recipes or media.


The Python files and raven_director_guide.md here are verbatim copies of the
matching companion engine's pure modules. `__init__.py` is native packaging only.
Run `uv run --frozen python -B scripts/sync-contracts.py` to check parity, or add
`--engine /path/to/engine --write` for an intentional synchronized update. Tests
check imports from a standalone native checkout with no sibling engine.

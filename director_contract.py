"""Pure access to Raven's canonical director contract.

The authoritative contract lives with the Storyboard server.  Raven loads it
by file path so the desktop process does not need to import the server package
or any model/runtime dependencies.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def load_contract() -> ModuleType:
    path = Path(__file__).resolve().parent / "contracts/raven_contract.py"
    if not path.is_file():
        raise FileNotFoundError(f"The Raven contract is missing: {path}")
    spec = importlib.util.spec_from_file_location("raven_canonical_contract", path)
    if spec is None or spec.loader is None:
        raise ImportError("Could not load the Raven contract")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = load_contract()
TOOLS = CONTRACT.TOOLS
TOOL_SCHEMAS = CONTRACT.TOOL_SCHEMAS
catalogue = CONTRACT.catalogue
validate_actions = CONTRACT.validate_actions
PANEL_NAMES = CONTRACT.PANEL_NAMES
READ_ONLY_TOOLS = CONTRACT.READ_ONLY_TOOLS


def load_anchors():
    path = Path(__file__).resolve().parent / "contracts/ltx_anchors.py"
    spec = importlib.util.spec_from_file_location("raven_canonical_anchors", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ANCHORS = load_anchors()


def load_editorial():
    path = Path(__file__).resolve().parent / "contracts/raven_editorial.py"
    spec = importlib.util.spec_from_file_location("raven_canonical_editorial", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


editorial_context = load_editorial().editorial_context

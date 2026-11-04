"""Native-window smoke coverage for Raven's model connection modal.

The helper is intentionally opt-in from the existing smoke runner.  It swaps
only the in-memory ConnectionStore/credential provider for a temporary store,
then restores both objects before returning.
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import raven as r

from connections import ConnectionStore, defaults, credential_account
from connections_panel import ConnectionsMixin


class _FakeCredentials:
    def __init__(self):
        self.values = {}

    def get(self, account):
        return self.values.get(account, "")

    def set(self, account, value):
        self.values[account] = value

    def delete(self, account):
        self.values.pop(account, None)


def _profile():
    return {
        "version": 1, "id": "smoke-image", "label": "Smoke image", "kind": "image",
        "workflow": {"1": {"class_type": "SaveImage", "inputs": {"prompt": ""}}},
        "bindings": {"prompt": {"node": "1", "input": "prompt"}},
        "output": {"node": "1", "key": "images"},
        "capabilities": {"maxReferences": 0},
    }


def run(studio, pump, wait, check):
    """Exercise the actual native window without backend/model work."""
    old_store = getattr(studio, "connections", None)
    old_settings = getattr(studio, "connection_settings", None)
    old_credentials = getattr(studio, "credentials", None)
    ui_names=('connection_draft','connection_form','connection_status','connection_comfy_key_draft','connection_comfy_key_url','connection_modal_token')
    old_ui={name:copy.deepcopy(getattr(studio,name,None)) for name in ui_names}
    with tempfile.TemporaryDirectory(prefix="raven-connections-smoke-") as temp:
        path = Path(temp) / "connections.json"
        store = ConnectionStore(path)
        active = store.load()
        fake_credentials = _FakeCredentials()
        studio.connections = store
        studio.connection_settings = copy.deepcopy(active)
        studio.credentials = fake_credentials
        studio.initialize_connections()
        try:
            studio.on_action(None, "connections.show", None)
            pump(.2)
            check(studio.modal_kind == "Models and connections", "Native connection modal opens")
            check(studio.find("connections.root") != r.R_INVALID_ID and studio.find("connections.comfy.key") != r.R_INVALID_ID, "Connection paths and password field are present")
            from app import ROOT
            import os
            QA_ROOT=Path(os.environ.get("SPRINTWAKE_QA_DIR",str(ROOT/"qa")))
            studio.capture(QA_ROOT/"studio-model-connections.png")
            if hasattr(r, "Rtext_input_is_password"):
                check(bool(r.Rtext_input_is_password(studio.find("connections.comfy.key"))), "Comfy credential field is masked")

            studio.set_text("connections.root", str(Path(temp) / "production"))
            studio.on_action(None, "connections.llm.add", None)
            pump(.1)
            check(studio.modal_kind == "Add language model", "LLM add form opens from main modal")
            check(studio.connection_draft["paths"]["root"] == str(Path(temp) / "production"), "Main form draft survives opening LLM form")
            studio.on_action(None, "connections.llm.cancel", None)
            pump(.1)
            check(studio.modal_kind == "Models and connections" and studio.connection_draft["paths"]["root"] == str(Path(temp) / "production"), "Returning from LLM form preserves draft")

            profile_path = Path(temp) / "workflow.json"
            profile_path.write_text(json.dumps(_profile()), encoding="utf-8")
            original_choose = studio.player.choose_project
            studio.player.choose_project = lambda: str(profile_path).encode()
            try:
                studio.on_action(None, "connections.workflow.import", None)
                pump(.1)
            finally:
                studio.player.choose_project = original_choose
            check(any(p["id"] == "smoke-image" for p in studio.connection_draft["workflowProfiles"]), "Schema-v1 workflow imports and validates")

            studio.set_text("connections.comfy.key", "smoke-secret")
            studio.on_action(None, "connections.save", None)
            pump(.1)
            saved = path.read_text(encoding="utf-8")
            check("smoke-secret" not in saved, "Credentials never enter connections.json")
            check(fake_credentials.get(credential_account(studio.connection_draft,"comfy")) == "smoke-secret", "Explicit Comfy key save uses credential store")
            check(studio.connection_settings == active, "Saving next-launch settings leaves active settings untouched")
        finally:
            if getattr(studio, "modal_kind", ""):
                studio.modal_close()
            if old_store is not None:
                studio.connections = old_store
            else:
                studio.__dict__.pop("connections", None)
            if old_settings is not None:
                studio.connection_settings = old_settings
            else:
                studio.__dict__.pop("connection_settings", None)
            if old_credentials is not None:
                studio.credentials = old_credentials
            else:
                studio.__dict__.pop("credentials", None)
            for name,value in old_ui.items():setattr(studio,name,value)

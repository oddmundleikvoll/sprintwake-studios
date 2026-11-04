"""Native Raven model/installation connection settings.

This mixin deliberately owns presentation and draft state only.  Connection
documents contain no secrets; credentials are sent to the platform keychain
only when the user explicitly saves a non-empty key field.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import raven as r

import views as v
import connections


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in str(value))[:64]


def profile_summary(profile: dict) -> str:
    """Short, non-sensitive description suitable for the modal."""
    caps = profile.get("capabilities") or {}
    bits = [str(profile.get("kind", "workflow")), str(profile.get("label", profile.get("id", "")))]
    if caps.get("fps"):
        bits.append(f"{caps['fps']} fps")
    if caps.get("maxReferences") is not None:
        bits.append(f"{caps['maxReferences']} references")
    if caps.get("supportsStartAnchor"):
        bits.append("opening anchor")
    if caps.get("supportsEndAnchor"):
        bits.append("end anchor")
    return " · ".join(bits)


class ConnectionsMixin:
    """Actions and modal rendering for user-selectable Raven connections."""

    def initialize_connections(self):
        """Set up the UI-owned handles; callers may invoke before backend boot."""
        if not hasattr(self, "connections"):
            self.connections = connections.ConnectionStore()
        if not hasattr(self, "connection_settings"):
            self.connection_settings = self.connections.load()
        if not hasattr(self, "credentials"):
            from credentials import CredentialStore
            self.credentials = CredentialStore(self.player)
        self.connection_draft = copy.deepcopy(self.connection_settings)
        self.connection_form = None
        self.connection_status = getattr(self, "connection_status", "")
        self.connection_comfy_key_draft = ""
        self.connection_comfy_key_url = ""
        self.connection_modal_token = 0

    def _connection_doc(self):
        if not hasattr(self, "connection_draft"):
            self.initialize_connections()
        return self.connection_draft

    def _combo(self, name, values, selected=0):
        items = "|".join(v.esc(item.replace("|", " / ")) for item in values) or "None"
        return f'<ComboBox Name="{v.esc(name)}" Items="{items}" Selected="{max(0, selected)}"/>'

    def _main_connection_body(self):
        doc = self._connection_doc()
        paths = doc["paths"]
        body = v.label("The current local Qwen/LTX setup is used by default. You can point Raven to your own folders and services without moving folders.", 12, True)
        body += v.label("Changes are saved for the next launch. Tests are passive and do not start generation.", 12, True)
        if self.connection_status:body += v.label(self.connection_status,12,True,Name="connections.status")
        else:body += '<Label Name="connections.status" Text="" Class="muted" Wrap="true"/>'
        body += v.button("Test connections", "connections.test")
        body += '<Label Text="PRODUCTION AND ENGINE" Class="eyebrow"/>'
        for key, title in (("root", "Production folder"), ("engine", "Engine / storyboard"), ("python", "Python runtime"), ("workspace", "Workspace") ):
            body += v.field("connections." + key, title, paths[key], 36, multiline=False)
        body += '<Label Text="COMFYUI" Class="eyebrow"/>'
        body += v.field("connections.comfy", "ComfyUI address", doc["comfyUrl"], 36, "http://127.0.0.1:8188", False)
        body += v.field("connections.comfy.key", "ComfyUI key (optional, stored in Keychain)", "", 36, "Leave blank to keep the key for this address", False)
        body += '<Label Text="SELECTED MODELS" Class="eyebrow"/>'
        llms = ["local", "none"] + [p["id"] for p in doc["llmProfiles"]]
        llm_labels = ["Local Raven model", "No language model"] + [p["label"] for p in doc["llmProfiles"]]
        images = ["qwen-local", "none"] + [p["id"] for p in doc["workflowProfiles"] if p["kind"] == "image"]
        image_labels = ["Local Qwen", "No image generator"] + [profile_summary(p) for p in doc["workflowProfiles"] if p["kind"] == "image"]
        videos = ["ltx-local", "none"] + [p["id"] for p in doc["workflowProfiles"] if p["kind"] == "video"]
        video_labels = ["Local LTX", "No video generator"] + [profile_summary(p) for p in doc["workflowProfiles"] if p["kind"] == "video"]
        for key, title, ids, labels in (("llm", "Language model", llms, llm_labels), ("image", "Image", images, image_labels), ("video", "Video", videos, video_labels)):
            body += f'<Label Text="{v.esc(title.upper())}" Class="eyebrow"/>'
            selected = ids.index(doc["selected"][key]) if doc["selected"][key] in ids else 0
            body += self._combo("connections.selected." + key, labels, selected)
        body += '<HBox Spacing="8">' + v.button("+ Add language model", "connections.llm.add") + v.button("Import Comfy profile", "connections.workflow.import") + '</HBox>'
        for profile in doc["llmProfiles"]:
            sid = _safe_id(profile["id"])
            body += '<HBox Spacing="8">' + v.label(f"{profile['label']} · {profile['kind']} · {profile['model']}", 12, True, **{"Slot.Fill": "1"}) + v.button("Edit", "connections.llm.edit." + sid) + '</HBox>'
        for profile in doc["workflowProfiles"]:
            body += v.label("Imported: " + profile_summary(profile), 11, True)
        return body

    def show_connections(self):
        if not hasattr(self, "connection_draft"):
            self.initialize_connections()
        self.connection_form = None
        self.connection_modal_token += 1
        body = self._main_connection_body()
        self.show_modal("Models and connections", body, "connections.save", "Save for next launch", detail="Folders, services, and model profiles")
        key = self.find("connections.comfy.key")
        if key != r.R_INVALID_ID and hasattr(r, "Rtext_input_set_password"):
            r.Rtext_input_set_password(key, True)

    def _read_main(self):
        doc = self._connection_doc()
        for key in doc["paths"]:
            if self.find("connections." + key) == r.R_INVALID_ID:
                continue
            value = self.text("connections." + key).strip()
            if value:
                doc["paths"][key] = value
        if self.find("connections.comfy") != r.R_INVALID_ID:
            comfy = self.text("connections.comfy").strip()
            if comfy:
                doc["comfyUrl"] = comfy
        if self.find("connections.comfy.key") != r.R_INVALID_ID:
            typed_key = self.text("connections.comfy.key")
            if typed_key:
                self.connection_comfy_key_draft = typed_key
                self.connection_comfy_key_url = doc["comfyUrl"].rstrip("/")
            elif getattr(self,"connection_comfy_key_url","") != doc["comfyUrl"].rstrip("/"):
                self.connection_comfy_key_draft = ""
        ids = {}
        for key, ids_list in (("llm", ["local", "none"] + [p["id"] for p in doc["llmProfiles"]]), ("image", ["qwen-local", "none"] + [p["id"] for p in doc["workflowProfiles"] if p["kind"] == "image"]), ("video", ["ltx-local", "none"] + [p["id"] for p in doc["workflowProfiles"] if p["kind"] == "video"])):
            ids[key] = ids_list[max(0, min(self.selected("connections.selected." + key), len(ids_list) - 1))]
        doc["selected"].update(ids)
        return doc

    def _llm_form(self, profile=None):
        profile = profile or {"id": "", "label": "", "kind": connections.LLM_KINDS[0], "url": "", "model": "", "vision": False}
        body = v.label("API keys are never stored in JSON. Leave the field blank to keep an existing key.", 12, True)
        body += v.field("connections.llm.id", "Profile ID", profile["id"], 36, "my-model", False)
        if self.connection_llm_edit:
            body = body.replace('Name="connections.llm.id"', 'Name="connections.llm.id" Disabled="true"', 1)
        body += v.field("connections.llm.label", "Name", profile["label"], 36, "My language model", False)
        body += '<Label Text="TYPE" Class="eyebrow"/>' + self._combo("connections.llm.kind", list(connections.LLM_KINDS), list(connections.LLM_KINDS).index(profile["kind"]) if profile["kind"] in connections.LLM_KINDS else 0)
        body += v.field("connections.llm.url", "API address", profile["url"], 36, "https://…", False)
        body += v.field("connections.llm.model", "Model ID", profile["model"], 36, "model-name", False)
        body += '<Checkbox Name="connections.llm.vision" Text="Model supports images" Checked="' + str(bool(profile.get("vision"))).lower() + '"/>'
        body += v.field("connections.llm.key", "API key (optional)", "", 36, "Stored in Keychain", False)
        if getattr(self, "connection_form_error", ""):
            body += v.label(self.connection_form_error, 12, False, Color="#EFA1A1")
        body += '<HBox Spacing="8">' + v.button("Cancel", "connections.llm.cancel") + v.button("Save language model", "connections.llm.save", True) + '</HBox>'
        return body

    def _show_llm_form(self, profile=None):
        self.connection_form = "llm"
        self.connection_llm_edit = copy.deepcopy(profile) if profile else None
        self.show_modal("Add language model" if profile is None else "Edit language model", self._llm_form(profile), detail="OpenAI-compatible API, Ollama, or Anthropic")
        key = self.find("connections.llm.key")
        if key != r.R_INVALID_ID and hasattr(r, "Rtext_input_set_password"):
            r.Rtext_input_set_password(key, True)

    def _save_llm(self):
        doc = self._connection_doc()
        ident = self.text("connections.llm.id").strip()
        profile = {"id": ident, "label": self.text("connections.llm.label").strip(), "kind": connections.LLM_KINDS[max(0, min(self.selected("connections.llm.kind"), len(connections.LLM_KINDS)-1))], "url": self.text("connections.llm.url").strip(), "model": self.text("connections.llm.model").strip(), "vision": self.checked("connections.llm.vision")}
        old = self.connection_llm_edit or {}
        if old.get("id"):
            ident = old["id"]
            profile["id"] = ident
        self.connection_llm_draft = copy.deepcopy(profile)
        existing = [p for p in doc["llmProfiles"] if p["id"] != old.get("id")]
        existing.append(profile)
        candidate = copy.deepcopy(doc); candidate["llmProfiles"] = existing
        connections.validate(candidate)
        doc["llmProfiles"] = existing
        secret = self.text("connections.llm.key")
        if secret:
            self.credentials.set(connections.credential_account(doc,"llm:" + ident), secret)
        doc["selected"]["llm"] = ident
        self.connection_form_error = ""
        self.connection_status = "Language model added to the draft. Save the main panel for the next launch."
        self.show_connections()

    def _import_workflow(self):
        raw = self.player.choose_project()
        if not raw:
            return
        path = Path(raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)).expanduser()
        try:
            if path.stat().st_size > 4_000_000:
                raise ValueError("The profile is too large.")
            profile = json.loads(path.read_text(encoding="utf-8"))
            candidate = copy.deepcopy(self._connection_doc())
            candidate["workflowProfiles"] = [*candidate["workflowProfiles"], profile]
            connections.validate(candidate)
            self.connection_draft = candidate
            self.connection_status = "Imported and validated: " + profile_summary(profile)
        except (OSError, ValueError, json.JSONDecodeError):
            self.connection_status = "Could not import the profile. Choose a schema-v1 API profile with bindings and explicit capabilities."
        self.show_connections()

    def _connection_test_done(self, result):
        if (getattr(self, "connection_modal_token", 0) != getattr(self, "connection_test_token", -1)
                or getattr(self, "connection_form", None) is not None
                or getattr(self, "modal_kind", "") != "Models and connections"):
            return
        rows = result if isinstance(result, list) else []
        self._read_main()
        status_map = {"lokalt oppsett": "local setup", "mangler": "missing", "ikke valgt": "not selected", "ikke bekreftet": "not confirmed", "ukjent": "unknown", "klar": "ready", "utilgjengelig": "unavailable"}
        detail_map = {"Tjenesten svarte ikke med gyldige metadata.": "The service did not return valid metadata.", "Workflowens noder og modellvalg kunne ikke bekreftes.": "The workflow nodes and model selection could not be confirmed."}
        def display(row):
            status = status_map.get(str(row.get("status", "unknown")), str(row.get("status", "unknown")))
            detail = detail_map.get(str(row.get("detail", "")), str(row.get("detail", "")))
            return f"{row.get('name','Connection')}: {status} – {detail}"
        self.connection_status = "  ".join(display(row) for row in rows)[:1200] or "No test results."
        self.show_connections()

    def connections_action(self, action):
        if not action.startswith("connections."):
            return False
        try:
            if action == "connections.show":
                self.show_connections(); return True
            if action == "connections.llm.add":
                self._read_main()
                self._show_llm_form(); return True
            if action.startswith("connections.llm.edit."):
                self._read_main()
                sid = action.rsplit(".", 1)[-1]
                profile = next((p for p in self._connection_doc()["llmProfiles"] if _safe_id(p["id"]) == sid), None)
                if profile: self._show_llm_form(profile)
                return True
            if action == "connections.llm.cancel":
                self.show_connections(); return True
            if action == "connections.llm.save":
                self._save_llm(); return True
            if action == "connections.workflow.import":
                self._read_main()
                self._import_workflow(); return True
            if action == "connections.test":
                draft = connections.validate(copy.deepcopy(self._read_main()))
                self.connection_test_token = self.connection_modal_token
                from connection_checks import probe_connections
                comfy_key=self.connection_comfy_key_draft
                get_secret=self.credentials.get
                comfy_account=connections.credential_account(draft,"comfy")
                self.submit("connections.check", lambda: probe_connections(draft, lambda account:comfy_key if account==comfy_account and comfy_key else get_secret(account)), self._connection_test_done, scoped=False)
                return True
            if action == "connections.save":
                doc = connections.validate(self._read_main())
                secret = self.connection_comfy_key_draft
                if self.find("connections.comfy.key") != r.R_INVALID_ID:
                    secret = self.text("connections.comfy.key") or secret
                if secret: self.credentials.set(connections.credential_account(doc,"comfy"), secret)
                self.connections.save(doc)
                self.connection_comfy_key_draft = ""
                self.connection_comfy_key_url = ""
                self.connection_status = "Saved for the next launch. Active configuration changes after Raven restarts."
                self.show_connections(); return True
        except (ValueError, OSError, RuntimeError) as error:
            if self.connection_form == "llm":
                self.connection_form_error = "Could not save the language model. Check the fields and try again."
                self._show_llm_form(getattr(self, "connection_llm_draft", None) or getattr(self, "connection_llm_edit", None))
            else:
                self.connection_status = str(error)[:500]
                self.show_connections()
        return True

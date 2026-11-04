"""Stable, model-facing contract for Raven's expert director chat.

This module intentionally contains no Raven/UI imports and no side effects.  It
is the boundary between an LLM's prose and the reviewed executor.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

CATALOG_VERSION = "raven-director-2"
_THINKING = {"none", "low", "medium", "high", "xhigh"}
_ASPECTS = {"16:9", "9:16", "1:1", "4:3", "3:2", "2:3", "21:9"}

_TOOL_DEFINITIONS = (
    ("create_project", "Propose a new project from a name and brief.", "authoring", False),
    ("open_project", "Open an existing project by its known slug.", "navigation", False),
    ("rename_project", "Rename the current project.", "authoring", True),
    ("archive_project", "Archive the current project after explicit review.", "authoring", True),
    ("add_shot", "Add one authored shot to a project.", "authoring", True),
    ("edit_project", "Edit project metadata or scene information.", "authoring", True),
    ("add_character", "Add a character to the project bible.", "authoring", True),
    ("update_character", "Update a character in the project bible.", "authoring", True),
    ("edit_bible", "Edit the film bible while preserving continuity fields.", "authoring", True),
    ("edit_shot", "Edit one existing shot's authored fields.", "authoring", True),
    ("write_manuscript", "Draft a bounded manuscript and shot plan from a brief.", "authoring", True),
    ("list_library", "Inspect the shared library using stable asset IDs.", "inspection", False),
    ("adopt_library_image", "Propose adopting a shared library image by ID.", "resource", True),
    ("generate_image", "Propose one Qwen image generation using asset IDs.", "generation", True),
    ("render_seed_takes", "Render bounded seed alternatives for one shot.", "generation", True),
    ("stop_image", "Stop one known image job.", "generation", True),
    ("assign_image", "Assign a known image ID to a shot target.", "authoring", True),
    ("publish_image", "Publish a reviewed image take into the library.", "resource", True),
    ("list_seed_takes", "Inspect seed takes for a shot.", "inspection", True),
    ("adopt_seed_take", "Adopt one known seed take.", "authoring", True),
    ("refine_shot", "Propose a requirements-driven shot refinement.", "operation", True),
    ("prepare_dialogue", "Prepare dialogue for the project.", "operation", True),
    ("dub_dialogue", "Propose dialogue dubbing for a reviewed shot.", "operation", True),
    ("generate_soundtrack", "Propose soundtrack generation for a project.", "operation", True),
    ("prepare_film", "Prepare the film and create its Qwen image plan for review.", "operation", True),
    ("resume_film", "Resume a prepared film run after review.", "operation", True),
    ("stop_film", "Stop a known film job.", "operation", True),
    ("set_film_anchor", "Set one reviewed film continuation anchor.", "authoring", True),
    ("set_shot_anchors", "Set reviewed opening/closing anchors for a shot.", "authoring", True),
    ("assemble", "Assemble reviewed rendered material.", "operation", True),
    ("export_project", "Export a project after review.", "operation", True),
    ("review_clip", "Navigate to human review for a rendered clip.", "inspection", True),
    ("rewrite_text", "Propose a text rewrite without applying it.", "authoring", True),
    ("reveal", "Reveal a known project panel or artifact in the UI.", "inspection", False),
    ("open_panel", "Open a named Raven panel.", "inspection", False),
    ("status", "Inspect current production status.", "inspection", False),
    ("inspect_cut", "Inspect the non-destructive cut journal.", "inspection", True),
    ("inspect_coverage", "Inspect room blocking and camera coverage.", "inspection", True),
    ("inspect_structure", "Inspect narrative sequences and scenes.", "inspection", True),
    ("inspect_sound", "Inspect the independent sound lanes and mix state.", "inspection", True),
    ("review_production", "Navigate to human review; never approve it.", "inspection", True),
)
TOOLS = [{"name": name, "description": desc, "kind": kind,
          "requiresProject": req, "requiresApproval": kind not in {"inspection", "navigation"}}
         for name, desc, kind, req in _TOOL_DEFINITIONS]
_CATALOG = {name: {"name": name, "description": desc, "kind": kind,
                   "requiresProject": req, "requiresApproval": kind not in {"inspection", "navigation"}}
            for name, desc, kind, req in _TOOL_DEFINITIONS}
_FORBIDDEN = {"approve_image_plan", "approve_review", "accept_image_plan", "accept_review",
              "apply", "execute", "run_tool", "handoff"}

# Flat action schemas are deliberately explicit: the UI executor can render
# these forms without interpreting arbitrary model-generated argument bags.
_COMMON = {"slug": "string"}
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {}
def _schema(tool_name: str, required: tuple[str, ...] = (), **allowed: str) -> None:
    TOOL_SCHEMAS[tool_name] = {"required": list(required), "allowed": allowed}
_schema("create_project", ("name",), name="string", brief="string", style="string", newProject="bool")
_schema("open_project", ("slug",), slug="knownSlug")
_schema("rename_project", ("name",), name="string")
_schema("archive_project")
_schema("add_shot", ("title", "prompt"), title="string", prompt="string", start="string", action="string", end="string", camera="string", characterIds="idList", locationId="id", propIds="idList", frames="int", directorScene="int", dialogue="string", soundNote="string", dialogueSource="string", seed="seed", stateBefore="object", stateAfter="object")
_schema("edit_project", fields="object")
_schema("add_character", ("name",), name="string", description="string", imageId="imageId")
_schema("update_character", ("characterId",), characterId="characterId", name="string", description="string", imageId="imageId")
_schema("edit_bible", ("fields",), fields="object")
_schema("edit_shot", ("shotId",), shotId="shotId", title="string", prompt="string", start="string", action="string", end="string", camera="string", characterIds="idList", locationId="id", propIds="idList", frames="int", directorScene="int", dialogue="string", soundNote="string", dialogueSource="string", seed="seed", stateBefore="object", stateAfter="object")
_schema("write_manuscript", ("brief",), brief="string", count="int")
_schema("list_library", kind="string", query="string")
_schema("adopt_library_image", ("imageId",), imageId="sharedImageId")
_schema("generate_image", ("prompt",), prompt="string", referenceIds="imageIdList", aspect="aspect", seed="seed")
_schema("stop_image", ("jobId",), jobId="string")
_schema("render_seed_takes", ("shotId",), shotId="shotId", seeds="seedList", count="int")
_schema("assign_image", ("imageId", "target"), imageId="imageId", shotId="shotId", entityId="id", target="targetName")
_schema("publish_image", ("imageId",), imageId="imageId")
_schema("list_seed_takes", ("shotId",), shotId="shotId")
_schema("adopt_seed_take", ("shotId", "seed"), shotId="shotId", seed="seed")
_schema("refine_shot", ("shotId", "requirement"), shotId="shotId", requirement="string")
_schema("prepare_dialogue")
_schema("dub_dialogue", ("shotId",), shotId="shotId")
_schema("generate_soundtrack", prompt="string")
_schema("prepare_film", ("revision",), revision="string")
_schema("resume_film", ("revision",), revision="string")
_schema("stop_film", ("jobId",), jobId="string")
for _n in ("assemble", "export_project"):
    _schema(_n)
_schema("set_film_anchor", ("revision", "enabled"), revision="string", enabled="bool")
_schema("set_shot_anchors", ("shotId", "anchorRevision", "anchors"), shotId="shotId", anchorRevision="string", anchors="anchors")
_schema("reveal", target="string")
_schema("open_panel", ("panel",), panel="string")
_schema("status")
for _name in ("inspect_cut", "inspect_coverage", "inspect_structure", "inspect_sound"):
    _schema(_name)
_schema("review_production", shotId="shotId", scene="int")
_schema("review_clip", ("shotId",), shotId="shotId")
_schema("rewrite_text", ("scope", "text"), scope="string", text="string", shotId="shotId")

PANEL_NAMES = frozenset({"home", "overview", "brief", "bible", "images", "film", "production", "library", "director",
                         "scenes", "cut", "coverage", "structure", "sound"})
READ_ONLY_TOOLS = frozenset({"status", "list_library", "list_seed_takes", "inspect_cut", "inspect_coverage", "inspect_structure", "inspect_sound"})


_STAGE_TOOLS = {
    "setup": {"edit_project", "edit_bible", "add_character"},
    "shots": {"add_shot"},
    "combined": {"create_project", "write_manuscript"},
}


def catalogue(stage: str | None = None) -> list[dict[str, Any]]:
    """Return only the tools relevant to the current director stage.

    The full catalogue remains the default for compatibility, while stage
    prompts deliberately expose a much smaller attack/ambiguity surface.
    """
    if stage == "projectless":
        names = [name for name, spec in _CATALOG.items()
                 if not spec["requiresProject"] or name == "write_manuscript"]
    else:
        names = _STAGE_TOOLS.get(stage, _CATALOG.keys())
    return [{**dict(_CATALOG[name]), "schema": dict(TOOL_SCHEMAS.get(name, {"required": [], "allowed": {}}))}
            for name in names]


def validate_request(payload: Any, known_slugs: set[str] | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(payload, dict):
        return None, ["request must be an object"]
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return None, ["message is required"]
    slug = payload.get("slug")
    if slug is not None and (not isinstance(slug, str) or not slug.strip()):
        return None, ["slug must be null or a known project slug"]
    if known_slugs is not None and slug and slug not in known_slugs:
        return None, [f"unknown project slug: {slug}"]
    history = payload.get("history") or []
    if not isinstance(history, list) or any(not isinstance(x, dict) or x.get("role") not in {"user", "assistant"}
                                             or not isinstance(x.get("content", ""), str) for x in history[-40:]):
        return None, ["history must contain role/content messages"]
    thinking = payload.get("thinking")
    if thinking is not None and thinking not in _THINKING:
        return None, ["thinking must be one of none, low, medium, high, xhigh"]
    stage = payload.get("authoringStage", "general")
    if stage not in {"general", *(_STAGE_TOOLS.keys())}:
        return None, ["authoringStage must be general, setup, shots, or combined"]
    return {"slug": slug, "message": message.strip(), "history": history[-40:],
            "selectedShotId": payload.get("selectedShotId"), "thinking": thinking,
            "authoringStage": stage}, []


def _contains_path(value: Any) -> bool:
    if isinstance(value, dict):
        for k, v in value.items():
            if k.lower() in {"path", "url", "filepath", "file_path"}:
                return True
            if _contains_path(v): return True
    elif isinstance(value, list):
        return any(_contains_path(v) for v in value)
    elif isinstance(value, str):
        return value.startswith(("/", "file:", "http:", "https:"))
    return False


def _normalize_action(action: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Flatten only conventional, known-tool argument wrappers.

    The model may emit either Raven's flat form or a standard function-call
    envelope.  We never merge ambiguous top-level fields: a wrapper with any
    conflicting sibling data is rejected and then fully validated normally.
    """
    if not isinstance(action, dict):
        return None, "action must be an object"
    tool = action.get("tool")
    envelope = None
    if isinstance(tool, dict):
        envelope = tool
        tool = tool.get("name")
    elif tool is None and isinstance(action.get("name"), str) and ("args" in action or "arguments" in action):
        tool = action.get("name")
        envelope = action
    elif isinstance(action.get("function"), dict):
        envelope = action.get("function")
        tool = envelope.get("name")
    if envelope is None and isinstance(tool, str) and ("args" in action or "arguments" in action or "parameters" in action):
        envelope = action
    if envelope is None:
        return dict(action), None
    if not isinstance(tool, str) or tool not in _CATALOG:
        return None, "unknown wrapped tool"
    wrapper_keys = {"args", "arguments", "parameters"}
    present_wrappers = [k for k in wrapper_keys if k in envelope]
    if len(present_wrappers) != 1:
        return None, "wrapped arguments must use exactly one argument key"
    raw_args = envelope[present_wrappers[0]]
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except (TypeError, ValueError):
            return None, "wrapped arguments must be an object"
    if not isinstance(raw_args, dict):
        return None, "wrapped arguments must be an object"
    # The only accepted siblings are the wrapper's tool identity.  In
    # particular, do not silently preserve approval/path/unknown fields.
    if isinstance(action.get("tool"), dict):
        identity = {"tool"}
    elif isinstance(action.get("function"), dict):
        identity = {"function"}
    elif action.get("tool") is None:
        identity = {"name", *wrapper_keys}
    else:
        identity = {"tool", *wrapper_keys}
    envelope_keys = {"name", *wrapper_keys}
    if envelope is action:
        envelope_keys.add("tool")
    if any(k not in envelope_keys for k in envelope):
        return None, "wrapped arguments conflict with tool metadata"
    if any(k not in identity for k in action):
        return None, "wrapped arguments conflict with top-level fields"
    if "tool" in raw_args or any(k in raw_args for k in wrapper_keys):
        return None, "wrapped arguments contain reserved fields"
    return {"tool": tool, **raw_args}, None


def _ids(context: Any, key: str) -> set[str]:
    if not isinstance(context, dict): return set()
    values = context.get(key) or []
    if isinstance(values, dict): values = values.keys()
    return {str(x.get("id") or x.get("imageId") or x.get("slug")) for x in values if isinstance(x, dict) and (x.get("id") or x.get("imageId") or x.get("slug"))} | {str(x) for x in values if isinstance(x, str)}


def _type_ok(value: Any, typ: str, context: dict[str, Any]) -> bool:
    if typ == "string": return isinstance(value, str)
    if typ == "object": return isinstance(value, dict)
    if typ == "int": return type(value) is int
    if typ == "bool": return type(value) is bool
    if typ == "seed": return type(value) is int and 0 <= value <= 2**32 - 1
    if typ == "seedList": return isinstance(value, list) and 1 <= len(value) <= 24 and all(_type_ok(x, "seed", context) for x in value)
    if typ == "idList": return isinstance(value, list) and all(isinstance(x, str) for x in value)
    if typ == "imageIdList": return isinstance(value, list) and len(value) <= 10 and all(isinstance(x, str) and x in (_ids(context, "images") | _ids(context, "sharedLibrary")) for x in value)
    if typ == "aspect": return value in _ASPECTS
    if typ == "targetName": return value in {"opening", "closing", "character", "location", "prop"}
    if typ == "target":
        if not isinstance(value, dict) or value.get("kind") not in {"opening", "closing", "character", "location", "prop"}: return False
        entity = value.get("entityId")
        return isinstance(entity, str) or value.get("kind") in {"opening", "closing"}
    if typ == "anchors":
        if not isinstance(value, list) or len(value) > 32: return False
        return all(isinstance(a, dict) and isinstance(a.get("imageId"), str)
                   and (type(a.get("frame")) is int or a.get("frame") == "last")
                   and type(a.get("strength")) in (int, float) and 0 < a["strength"] <= 1
                   for a in value)
    if typ == "knownSlug": return value in _ids(context, "projects")
    if typ == "shotId": return value in _ids(context, "shots")
    if typ == "characterId": return value in _ids(context, "characters")
    if typ == "imageId": return value in (_ids(context, "images") | _ids(context, "sharedLibrary"))
    if typ == "sharedImageId": return value.startswith("shared:") and value in _ids(context, "sharedLibrary")
    if typ == "id": return isinstance(value, str)
    return False


def validate_actions(actions: Any, context: dict[str, Any] | None = None, *, slug: str | None = None,
                     known_slugs: set[str] | None = None, has_project: bool | None = None,
                     stage: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate normalized flat proposals; any error rejects the complete batch."""
    context = context or {}
    known_slugs = known_slugs or _ids(context, "projects")
    if known_slugs and not context.get("projects"):
        context["projects"] = list(known_slugs)
    if has_project is None: has_project = bool(context.get("project")) or bool(slug)
    if actions is None: return [], []
    if not isinstance(actions, list): return [], ["actions must be a list"]
    accepted, errors, kinds = [], [], []
    normalized: list[dict[str, Any] | None] = []
    for i, action in enumerate(actions):
        clean, error = _normalize_action(action) if isinstance(action, dict) else (None, "action must be an object")
        if error:
            errors.append(f"action {i}: {error}")
        normalized.append(clean)
    # This is the sole intentional multi-step authoring transaction.  It lets
    # a first-turn brief create a real project and immediately draft its
    # manuscript, while keeping every other create+mutation batch atomic and
    # rejected.  The executor still presents both cards for review.
    chain = [a.get("tool") for a in normalized if a]
    virtual_project = (chain == ["create_project", "write_manuscript"] and
                       not has_project and slug is None)
    bound_new_chain = (chain == ["create_project", "write_manuscript"] and
                       has_project and bool(slug) and normalized[0].get("newProject") is True)
    for i, action in enumerate(normalized):
        if not isinstance(action, dict): continue
        name = action.get("tool")
        if not isinstance(name, str) or name not in _CATALOG:
            errors.append(f"action {i}: unknown tool"); continue
        if name in _FORBIDDEN or any(str(k).lower().startswith(("accept", "approve")) for k in action):
            errors.append(f"action {i}: human approval cannot be automated"); continue
        if _contains_path(action): errors.append(f"action {i}: only flat ID-based fields are allowed"); continue
        spec = _CATALOG[name]; schema = TOOL_SCHEMAS[name]
        allowed = {"tool", *schema["allowed"]}
        unknown = set(action) - allowed
        if unknown: errors.append(f"action {i}: unknown fields {sorted(unknown)}"); continue
        effective_project = has_project or virtual_project or bound_new_chain
        if spec["requiresProject"] and not effective_project: errors.append(f"action {i}: project is required"); continue
        if stage in _STAGE_TOOLS and name not in _STAGE_TOOLS[stage]:
            errors.append(f"action {i}: tool is not allowed during {stage} stage"); continue
        if name == "create_project" and slug and not action.get("newProject"): errors.append(f"action {i}: create_project must be explicitly marked newProject when bound"); continue
        for field in schema["required"]:
            if field not in action: errors.append(f"action {i}: missing {field}")
        for field, typ in schema["allowed"].items():
            if field in action and not _type_ok(action[field], typ, context): errors.append(f"action {i}: invalid {field}")
        if name == "open_panel" and action.get("panel") not in PANEL_NAMES: errors.append(f"action {i}: unknown panel")
        if name == "open_panel" and not has_project and action.get("panel") not in {"home","library","director"}: errors.append(f"action {i}: project required for panel")
        if name == "reveal" and action.get("target","project") not in {"root","project","script","input","output","library"}: errors.append(f"action {i}: unknown folder")
        if name == "rewrite_text" and (action.get("scope") not in {"shot","project"} or (action.get("scope")=="shot" and action.get("shotId") not in _ids(context,"shots"))): errors.append(f"action {i}: invalid rewrite scope")
        if name in {"create_project","rename_project"} and not str(action.get("name","")).strip(): errors.append(f"action {i}: project name required")
        if name in {"add_shot","edit_shot"} and action.get("dialogueSource","native") not in {"native","recording"}: errors.append(f"action {i}: invalid dialogue source")
        if name == "render_seed_takes" and (not 1 <= action.get("count",3) <= 6 or len(action.get("seeds",[]))>6): errors.append(f"action {i}: choose 1..6 takes")
        if name == "assign_image":
            target = action.get("target")
            if target in {"opening", "closing"} and not isinstance(action.get("shotId"), str): errors.append(f"action {i}: shotId required for opening/closing")
            if target == "character" and action.get("entityId") not in _ids(context, "characters"): errors.append(f"action {i}: unknown character entityId")
            if target == "location" and action.get("entityId") not in _ids(context, "locations"): errors.append(f"action {i}: unknown location entityId")
            if target == "prop" and action.get("entityId") not in _ids(context, "props"): errors.append(f"action {i}: unknown prop entityId")
        if name == "set_shot_anchors":
            choices = {x for x in _ids(context, "images") if x.startswith("project:")}
            if any(a.get("imageId") not in choices for a in action.get("anchors", []) if isinstance(a, dict)): errors.append(f"action {i}: unknown anchor imageId")
        if action.get("slug") and name != "open_project" and action["slug"] != slug: errors.append(f"action {i}: slug does not match bound project")
        if name == "write_manuscript" and "count" in action and (type(action["count"]) is not int or not 1 <= action["count"] <= 48): errors.append(f"action {i}: count must be 1..48")
        accepted.append(action); kinds.append(spec["kind"])
    batch_tools = {"add_shot", "edit_project", "edit_bible", "add_character",
                   "update_character", "edit_shot", "assign_image", "rewrite_text"}
    if len(accepted) > 1 and not (virtual_project or bound_new_chain):
        # Existing authoring batches remain valid (for example a film-bible
        # setup or six-shot authoring batch).  Only a create+mutation batch is
        # special-cased above; other production/resource operations stay
        # single-operation and therefore cannot be smuggled into a batch.
        if not all(a["tool"] in READ_ONLY_TOOLS for a in accepted) and any(a["tool"] not in batch_tools for a in accepted):
            errors.append("project and production operations require separate responses")
    if len([a for a in accepted if a["tool"] == "add_shot"]) > 6: errors.append("at most 6 new shots may be proposed")
    if sum(k in {"resource", "generation", "operation"} for k in kinds) > 1: errors.append("one resource/generation/operation per response")
    if any(k in {"resource", "generation", "operation"} for k in kinds) and any(k == "authoring" for k in kinds): errors.append("cannot mix authoring edits with production operation")
    return ([], errors) if errors else (accepted, [])


def parse_model_reply(raw: Any) -> tuple[dict[str, Any], list[str]]:
    text = raw if isinstance(raw, str) else ""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try: obj = json.loads(cleaned)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match: return {"message": "I could not read a complete director proposal. Try again.", "actions": [], "proposalStatus": "incomplete"}, ["model did not return JSON"]
        try: obj = json.loads(match.group(0))
        except ValueError: return {"message": "Director proposal was incomplete and was not forwarded.", "actions": [], "proposalStatus": "incomplete"}, ["model returned malformed JSON"]
    if not isinstance(obj, dict): return {"message": "Director response had the wrong format. No changes were executed.", "actions": [], "proposalStatus": "incomplete"}, ["model reply must be an object"]
    return {"message": str(obj.get("message") or ""), "actions": obj.get("actions", [])}, []


def expert_system_prompt(stage: str | None = None) -> str:
    guide = (Path(__file__).with_name("raven_director_guide.md")).read_text(encoding="utf-8")
    # Compact JSON keeps the system prompt small enough that the model sees
    # the actual authoring brief and stage rules, not a wall of schema text.
    schema = json.dumps(catalogue(stage), ensure_ascii=False, separators=(",", ":"))
    stage_note = (f"Current authoring stage: {stage}. Use only the listed stage tools.\n"
                  if stage else "")
    return (f"{guide}\n\nCATALOG_VERSION: {CATALOG_VERSION}\n{stage_note}"
            f"AVAILABLE TOOLS (proposals only):\n{schema}\n"
            "Return JSON {message, actions}. Actions must be flat ID-based objects; "
            "never claim an action ran.")

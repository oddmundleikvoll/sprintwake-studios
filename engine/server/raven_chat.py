"""Raven director chat endpoint logic (proposal-only, no mutations)."""
from __future__ import annotations

import hashlib
import json
import copy
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from .raven_contract import (CATALOG_VERSION, catalogue, expert_system_prompt,
                             parse_model_reply, validate_actions, validate_request)
from .storyboard_chat import compact_board_context
from . import film_chat
from .ltx_anchors import image_catalog, public_catalog
from .llm import streaming
from .raven_editorial import editorial_context


def _write_trace(ctx, trace: dict[str, Any]) -> tuple[str, str | None]:
    """Persist private director diagnostics without breaking a valid reply."""
    trace_id = uuid.uuid4().hex
    data_dir = getattr(ctx, "data_dir", getattr(getattr(ctx, "store", None), "data_dir", Path.cwd()))
    root = Path(data_dir).parent / "System" / "director-traces"
    try:
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{trace_id}.json"
        fd, temporary = tempfile.mkstemp(prefix=f".{trace_id}-", suffix=".tmp", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(trace, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return trace_id, None
    except (OSError, TypeError, ValueError) as exc:
        return trace_id, str(exc)


def _library(ctx) -> list[dict[str, Any]]:
    data_dir = getattr(ctx, "data_dir", getattr(getattr(ctx, "store", None), "data_dir", Path.cwd()))
    root = Path(data_dir).parent / "Library"
    try:
        data = json.loads((root / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = []
    return [{"id": "shared:" + str(item["id"]), **{k: item[k] for k in ("kind", "name") if k in item}}
            for item in data if isinstance(item, dict) and item.get("id")]


def _generated_images(ctx, boards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose every durable generated-image record without leaking paths."""
    out = []
    for item in boards:
        slug = item.get("slug")
        if not slug:
            continue
        try:
            records = ctx.store.image_history(slug)
        except Exception:
            records = []
        for record in records:
            rid = record.get("id")
            if not rid:
                continue
            out.append({"imageId": "generated:" + str(rid), "projectSlug": slug,
                        "label": record.get("name") or record.get("label") or str(rid),
                        "prompt": record.get("prompt") or "", "status": record.get("status") or "unknown",
                        "recipe": {k: record[k] for k in ("seed", "aspect", "width", "height", "model") if k in record}})
    return out


def _without_media_paths(value):
    if isinstance(value, dict):
        return {k:_without_media_paths(v) for k,v in value.items() if k not in {"path","url","sourcePath","video","logUrl"}}
    if isinstance(value, list): return [_without_media_paths(v) for v in value]
    return value


def _board_context(ctx, slug: str, board: dict[str, Any]) -> dict[str, Any]:
    try:
        compact = compact_board_context(board, None, getattr(ctx.orch, "timings", None))
    except Exception:
        compact = {"name": board.get("name"), "shots": board.get("shots") or []}
    try:
        compact["filmStatus"] = film_chat.context(ctx.film, slug)
    except Exception:
        compact["filmStatus"] = {}
    try:
        compact["imageCatalog"] = [{**item, "imageId": "project:" + str(item["imageId"])}
                                    for item in public_catalog(image_catalog(board, ctx.store.data_dir))]
    except Exception:
        compact["imageCatalog"] = []
    # Preserve the full authored bible: the compact helper intentionally omits
    # some fields to save tokens, but the director needs them for continuity.
    compact["filmBible"] = board.get("filmBible") or board.get("bible") or {}
    compact["characters"] = board.get("characters") or []
    bible = compact["filmBible"] if isinstance(compact.get("filmBible"), dict) else {}
    compact["locations"] = board.get("locations") or bible.get("locations") or []
    compact["props"] = board.get("props") or bible.get("props") or []
    compact["shots"] = [{**shot, "id": shot.get("id")} for shot in (board.get("shots") or [])]
    try:
        compact["editorial"] = editorial_context(ctx.store.data_dir, slug, board)
    except Exception as exc:
        # A damaged optional journal must remain visible to the director as an
        # inspection problem; it must never make the canonical board unreadable.
        compact["editorial"] = {"projectSlug": slug, "editorialVersion": 1,
                                "editorialError": type(exc).__name__}
    return _without_media_paths(compact)


def chat(ctx, payload: dict[str, Any], on_event: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    boards = ctx.store.list_boards()
    known = {str(b.get("slug")) for b in boards if b.get("slug")}
    request, errors = validate_request(payload, known)
    if errors:
        return {"proposalStatus": "rejected", "errors": errors, "actions": [],
                "catalogVersion": CATALOG_VERSION}
    slug = request["slug"]
    board = None
    if slug:
        board = ctx.store.load(slug)
    library = _library(ctx)
    context = {"projects": [{k: b.get(k) for k in ("slug", "name", "shots", "updatedAt") if k in b} for b in boards],
               "sharedLibrary": library, "generatedImages": _generated_images(ctx, boards)}
    backend = getattr(ctx, "backend", None)
    context["modelConnections"] = {"nativeFilm": bool(getattr(backend, "native_film", True)),
                                   "models": [cap.to_json() for cap in backend.capabilities()] if backend and hasattr(backend,"capabilities") else []}
    if board is not None:
        context["project"] = _board_context(ctx, slug, board)
        context["shots"] = context["project"].get("shots") or []
        context["characters"] = context["project"].get("characters") or []
        context["locations"] = context["project"].get("locations") or []
        context["props"] = context["project"].get("props") or []
        context["images"] = (context["project"].get("imageCatalog") or []) + [
            item for item in context["generatedImages"] if item.get("projectSlug") == slug]
    stage = request.get("authoringStage") or "general"
    prompt_stage = "projectless" if stage == "general" and board is None else (None if stage == "general" else stage)
    system = expert_system_prompt(prompt_stage)
    user = ("RAVEN LIVE CONTEXT (IDs are authoritative; do not invent any):\n" +
            json.dumps(context, ensure_ascii=False) + "\n\n" +
            "USER MESSAGE:\n" + request["message"] + "\n" +
            "HISTORY:\n" + json.dumps(request["history"], ensure_ascii=False))
    if on_event:
        on_event({"type": "progress", "phase": "director", "text": "Regissøren vurderer brief og kontinuitet…"})
    service = ctx.llm(payload.get("service"))
    if request.get("thinking"):
        try:
            service = copy.copy(service)
            service.thinking = request["thinking"]
        except Exception:
            pass
    attempts: list[dict[str, Any]] = []
    raw = ""
    parsed: dict[str, Any] = {"message": "", "actions": []}
    parse_errors: list[str] = []
    action_errors: list[str] = []
    actions: list[dict[str, Any]] = []
    # Real 27B authoring reached 306s with a long existing shot context.
    # Keep its bounded text work separate from the ordinary chat budget.
    request_timeout = 600 if stage in {"setup", "shots"} else 300
    for attempt in range(2):
        reply_chunks: list[str] = []
        def emit_chunk(kind, text):
            if kind == "reply": reply_chunks.append(text)
            on_event({"type": "token", "kind": kind, "text": text})
        try:
            if on_event:
                with streaming(emit_chunk, max_seconds=request_timeout):
                    raw = service.complete(system, user, timeout=request_timeout, max_tokens=5000)
            else:
                raw = service.complete(system, user, timeout=request_timeout, max_tokens=5000)
        except Exception as exc:
            # A transport/model failure must retain earlier validation attempts
            # and any partial streamed reply, without exposing proposed actions.
            parse_errors = []
            action_errors = [f"Regissørmotoren feilet: {type(exc).__name__}: {exc}"]
            actions = []
            parsed = {"message": "", "actions": []}
            attempts.append({"attempt": attempt + 1, "errors": list(action_errors),
                             "proposedActions": [], "rawReply": "".join(reply_chunks)})
            break
        parsed, parse_errors = parse_model_reply(raw)
        actions, action_errors = validate_actions(
            parsed.get("actions"), context, slug=slug, known_slugs=known,
            has_project=board is not None, stage=stage if stage != "general" else None)
        errors_now = parse_errors + action_errors
        attempts.append({"attempt": attempt + 1, "errors": list(errors_now),
                         "proposedActions": copy.deepcopy(parsed.get("actions", [])),
                         "rawReply": raw if isinstance(raw, str) else str(raw)})
        if not errors_now:
            break
        if attempt == 0:
            # Give the second bounded attempt the actual validator feedback;
            # this repairs wrappers/shape mistakes without loosening policy.
            user = (user + "\n\nVALIDATION FEEDBACK FROM RAVEN (repair only; do not explain):\n" +
                    json.dumps(errors_now, ensure_ascii=False) +
                    "\nReturn one complete JSON object with flat actions. Preserve the user's intent.")
    all_errors = parse_errors + action_errors
    if parse_errors:
        status = "incomplete"
    elif action_errors and not actions:
        status = "rejected"
    elif actions:
        status = "pending"
    else:
        status = "no_changes"
    context_hash = hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    trace_id, trace_error = _write_trace(ctx, {"catalogVersion": CATALOG_VERSION,
        "slug": slug, "stage": stage, "attempts": attempts, "errors": all_errors,
        "finalActions": actions, "requestTimeout": request_timeout,
        "service": {"id": getattr(service, "id", None), "model": getattr(service, "model", None)}})
    diagnostics = {"traceId": trace_id, "attempts": len(attempts),
                   "repaired": len(attempts) > 1 and not all_errors}
    if trace_error:
        diagnostics["traceWriteError"] = trace_error
    return {"message": parsed.get("message", ""), "actions": actions,
            "proposalStatus": status, "errors": all_errors,
            "catalogVersion": CATALOG_VERSION, "contextVersion": context_hash,
            "project": {"slug": slug, "name": board.get("name")} if board else None,
            "catalog": catalogue(prompt_stage), **diagnostics}

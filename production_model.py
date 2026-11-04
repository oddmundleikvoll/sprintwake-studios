"""Pure production-model helpers used by Raven's native UI.

This module deliberately contains no HTTP, model, or process code.  It turns
the canonical Storyboard board into the older review model and constructs the
revision-bound film actions understood by Storyboard's server.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
import uuid

try:  # Raven is also run as loose modules by the native launcher.
    from .project import Project, Shot, Take
    from .workspace import Workspace, new_shot
except ImportError:  # pragma: no cover
    from project import Project, Shot, Take
    from workspace import Workspace, new_shot

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
IMAGE_ENGINE = "qwen-image-2.1-turbo"
_SHOT_EDITABLE = {
    "title", "prompt", "start", "action", "end", "camera", "locationId",
    "stateBefore", "stateAfter", "motionIntent", "propIds", "assetStates",
    "soundNote", "characterIds", "dialogue", "dialogueSource", "dialogueStyle",
    "dialogueVoice", "speakerId", "dubMode", "referenceImages", "imageAnchors",
    "resolution", "frames", "steps", "seed", "startRef", "endRef", "directorScene",
}
_CHAR_EDITABLE = {"name", "description", "image", "voice", "voiceText"}
_BOARD_EDITABLE = {"name", "title", "subtitle", "sceneDescription", "renderStyle", "soundscape", "soundtrackPrompt", "filmBible"}
_SERVER_FIELDS = {
    "outputs", "status", "reason", "progress", "runtimeSeconds", "validation", "logUrl",
    "renderedAs", "renderFingerprint", "locked", "filmAutomation", "imagePlan", "reviewPreview",
    "jobId", "busy", "phase", "completed", "total", "canResume", "canProduce", "revision",
    "createdAt", "updatedAt", "finalVideo", "productionError",
}


def _value(ref: Any) -> str:
    if isinstance(ref, dict):
        return str(ref.get("path") or ref.get("url") or ref.get("ref") or "")
    return str(ref or "")


def _media(ref: Any, workspace: Workspace) -> str:
    value = _value(ref)
    if not value:
        return ""
    path = workspace.local_media(value)
    return str(path) if path else ""


def _shot_media(slug: str, item: dict[str, Any], workspace: Workspace, index: int) -> tuple[str, str]:
    image = ""
    for ref in [item.get("startRef"), item.get("thumb"), item.get("startImage")]:
        candidate = _media(ref, workspace)
        if candidate and Path(candidate).suffix.lower() in IMAGE_EXTENSIONS:
            image = candidate
            break
    stills = item.get("stills") if isinstance(item.get("stills"), dict) else {}
    for ref in stills.values():
        candidate = _media(ref, workspace)
        if candidate and Path(candidate).suffix.lower() in IMAGE_EXTENSIONS:
            image = image or candidate
            break
    video = ""
    for ref in [item.get("video"), item.get("clip")]:
        candidate = _media(ref, workspace)
        if candidate and Path(candidate).suffix.lower() in VIDEO_EXTENSIONS:
            video = candidate
            break
    for ref in item.get("outputs") or []:
        candidate = _media(ref, workspace)
        if candidate and Path(candidate).suffix.lower() in VIDEO_EXTENSIONS:
            video = video or candidate
    # Never infer a take from a shot's ordinal folder.  Drafts can be
    # reordered or deleted, leaving an old clip at that path belonging to a
    # different shot.  Production receipts must be explicit in ``outputs``
    # (or in the named video/clip fields above).
    return image, video


def view_project(slug: str, board: dict[str, Any], workspace: Workspace) -> Project:
    """Build a review view without mutating the canonical board."""
    shots: list[Shot] = []
    for index, item in enumerate(board.get("shots") or []):
        if not isinstance(item, dict):
            continue
        frames = item.get("frames")
        duration = item.get("duration")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            duration = float(frames) / 24 if isinstance(frames, (int, float)) and not isinstance(frames, bool) else 5.0
        image, video = _shot_media(slug, item, workspace, index)
        takes = [Take("selected", "Produksjonsklipp", image=image, video=video,
                       provenance="Storyboard-produksjon")] if image or video else []
        shots.append(Shot(str(item.get("id") or f"s{index + 1}"), str(item.get("title") or f"Klipp {index + 1:02d}"),
                          float(duration), camera=str(item.get("camera") or ""),
                          start=str(item.get("start") or ""), action=str(item.get("action") or ""),
                          end=str(item.get("end") or ""), notes=str(item.get("ravenNotes", item.get("notes", "")) or ""),
                          reviewed=bool(item.get("ravenReviewed", item.get("reviewed", False))), takes=takes))
    character = next((c for c in (board.get("characters") or []) if isinstance(c, dict)), {})
    character_image = _media(character.get("image"), workspace)
    source = str(workspace.projects_dir / slug / "storyboard.json") if (workspace.projects_dir / slug / "storyboard.json").is_file() else ""
    return Project(slug, str(board.get("name") or board.get("title") or slug),
                   str(board.get("sceneDescription") or board.get("subtitle") or ""), shots,
                   source=source, character_image=character_image,
                   character_name=str(character.get("name") or ""), character_description=str(character.get("description") or ""))


def apply_view(board: dict[str, Any], view: Project) -> dict[str, Any]:
    result = copy.deepcopy(board)
    by_id = {str(s.get("id")): s for s in result.get("shots", []) if isinstance(s, dict)}
    original = {str(s.get("id")): s for s in board.get("shots", []) if isinstance(s, dict)}
    for rendered in view.shots:
        target = by_id.get(rendered.id)
        if target is None:
            continue
        for key in ("title", "start", "action", "end", "camera"):
            target[key] = getattr(rendered, key)
        target["ravenNotes"] = rendered.notes
        old = original.get(rendered.id, {})
        if "ravenReviewed" in old or bool(rendered.reviewed) != bool(old.get("reviewed",False)):
            target["ravenReviewed"] = bool(rendered.reviewed)
        old_duration = old.get("duration")
        old_duration = float(old_duration) if isinstance(old_duration, (int, float)) else (float(old.get("frames", 121)) / 24)
        if abs(float(rendered.duration) - old_duration) > 1e-9:
            target["frames"] = max(1, round(float(rendered.duration) * 24))
    return result


def _reject_server(fields: set[str]) -> None:
    bad = fields & _SERVER_FIELDS
    if bad:
        raise ValueError(f"Serverstyrte felter kan ikke foreslås: {', '.join(sorted(bad))}")


def _cast_ids(board: dict[str, Any]) -> set[str]:
    return {str(c.get("id")) for c in board.get("characters", []) if isinstance(c, dict) and c.get("id")}


def _validate_cast(fields: dict[str, Any], cast: set[str]) -> None:
    if "characterIds" in fields:
        ids = fields["characterIds"]
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in cast for i in ids):
            raise ValueError("Klippet refererer til en ukjent karakter")
    if "directorScene" in fields and (isinstance(fields["directorScene"], bool) or not isinstance(fields["directorScene"], int) or fields["directorScene"] < 1):
        raise ValueError("directorScene må være et positivt heltall")


_BIBLE_GROUPS = {"acts", "locations", "props"}
_BIBLE_ITEM_FIELDS = {
    "id", "name", "title", "description", "summary", "notes", "order",
    "role", "lighting", "season", "timeOfDay", "stateBefore", "stateAfter",
    "referenceImageId",
    "referenceImage", "referenceState",
}
_BIBLE_REFERENCE_FIELDS = {"kind", "path", "url", "label", "sha256", "width", "height"}


def _validate_bible_reference(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("filmBible-referansen må være et objekt")
    unknown = set(value) - _BIBLE_REFERENCE_FIELDS
    if unknown:
        raise ValueError("Ukjent felt i filmBible-referanse")
    clean = copy.deepcopy(value)
    path = clean.get("path")
    if path is not None:
        if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
            raise ValueError("filmBible-referansen kan bare bruke en trygg relativ sti")
    url = clean.get("url")
    if url is not None and (not isinstance(url, str) or not url.startswith("/media/")):
        raise ValueError("filmBible-url må være en lokal mediareferanse")
    return clean


def _validate_film_bible(value: Any, board: dict[str, Any]) -> dict[str, Any]:
    """Validate the editable, server-sanitized story bible shape.

    References are deliberately limited to catalog-style IDs or local media
    refs. Runtime receipts, paths outside the project, and server-owned state
    must never arrive through a director proposal.
    """
    if not isinstance(value, dict):
        raise ValueError("filmBible må være et objekt")
    unknown_groups = set(value) - _BIBLE_GROUPS - {"storyArc", "theme"}
    if unknown_groups:
        raise ValueError("Ukjent filmBible-seksjon")
    existing = board.get("filmBible") if isinstance(board.get("filmBible"), dict) else {}
    result: dict[str, Any] = copy.deepcopy(existing)
    for field in ("storyArc", "theme"):
        if field in value:
            if not isinstance(value[field], str):
                raise ValueError(f"filmBible.{field} må være tekst")
            result[field] = value[field][:8000]
    for group in _BIBLE_GROUPS:
        if group not in value:
            continue
        entries = value[group]
        if not isinstance(entries, list):
            raise ValueError(f"filmBible.{group} må være en liste")
        old = {str(item.get("id")): item for item in (existing.get(group) or [])
               if isinstance(item, dict) and item.get("id")}
        clean: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in entries:
            if not isinstance(raw, dict):
                raise ValueError(f"filmBible.{group} inneholder et ugyldig element")
            if set(raw) - _BIBLE_ITEM_FIELDS:
                raise ValueError(f"Ukjent felt i filmBible.{group}")
            item = copy.deepcopy(raw)
            ident = item.get("id") or f"{group[:-1]}-{uuid.uuid4().hex[:8]}"
            if not isinstance(ident, str) or not ident or ident in seen:
                raise ValueError(f"filmBible.{group} må ha unike ID-er")
            item["id"] = ident
            if "referenceImage" in item:
                item["referenceImage"] = _validate_bible_reference(item["referenceImage"])
            if "referenceImageId" in item and item["referenceImageId"] is not None \
                    and (not isinstance(item["referenceImageId"], str) or not item["referenceImageId"]):
                raise ValueError("referenceImageId må være en tekst eller null")
            # A partial edit must not erase a known, server-sanitized ref.
            previous = old.get(ident)
            if previous:
                for key in ("referenceImage", "referenceState", "referenceImageId"):
                    if key not in item and key in previous:
                        item[key] = copy.deepcopy(previous[key])
            seen.add(ident)
            clean.append(item)
        result[group] = clean
    return result


def apply_proposals(board: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
    result = copy.deepcopy(board)
    if not isinstance(actions, list):
        raise ValueError("Forslag må være en liste")
    cast = _cast_ids(result)
    for action in actions:
        if not isinstance(action, dict) or action.get("reviewed") is not True:
            raise ValueError("Bare gjennomgåtte redigeringsforslag kan brukes")
        op = action.get("operation") or action.get("tool") or action.get("op")
        payload = action.get("fields") if isinstance(action.get("fields"), dict) else action.get("shot") if isinstance(action.get("shot"), dict) else action.get("character") if isinstance(action.get("character"), dict) else {}
        if op == "set_board_fields":
            _reject_server(set(payload))
            unknown = set(payload) - _BOARD_EDITABLE
            if unknown: raise ValueError(f"Ukjente prosjektfelter: {', '.join(sorted(unknown))}")
            if "filmBible" in payload:
                payload = {**payload, "filmBible": _validate_film_bible(payload["filmBible"], result)}
            result.update(copy.deepcopy(payload))
        elif op == "add_character":
            _reject_server(set(payload)); unknown = set(payload) - _CHAR_EDITABLE - {"id"}
            if unknown: raise ValueError("Ukjent karakterfelt")
            ident = payload.get("id") or f"c{uuid.uuid4().hex[:8]}"
            if not isinstance(ident, str) or not ident or ident in cast: raise ValueError("Ny karakter må ha en unik ID")
            payload = {**payload, "id": ident}
            result.setdefault("characters", []).append(copy.deepcopy(payload)); cast.add(ident)
        elif op == "update_character":
            ident = action.get("id") or action.get("characterId") or payload.get("id")
            target = next((c for c in result.get("characters", []) if isinstance(c, dict) and c.get("id") == ident), None)
            if target is None: raise ValueError("Ukjent karakter")
            fields = {k: v for k, v in payload.items() if k != "id"}; _reject_server(set(fields))
            if set(fields) - _CHAR_EDITABLE: raise ValueError("Ukjent karakterfelt")
            target.update(copy.deepcopy(fields))
        elif op == "add_shot":
            _reject_server(set(payload)); unknown = set(payload) - _SHOT_EDITABLE - {"id", "directorScene"}
            if unknown: raise ValueError("Ukjent klippfelt")
            if cast and "characterIds" not in payload: raise ValueError("Ny scene må ha eksplisitte karakterkoblinger")
            _validate_cast(payload, cast)
            shot = new_shot(str(payload.get("title") or "Nytt klipp"), len(result.get("shots", [])) + 1)
            shot["dialogueSource"] = "native"
            ident = payload.get("id")
            if ident is not None:
                if not isinstance(ident, str) or any(s.get("id") == ident for s in result.get("shots", [])): raise ValueError("Ugyldig eller brukt klipp-ID")
                shot["id"] = ident
            shot.update(copy.deepcopy({k: v for k, v in payload.items() if k != "id"})); result.setdefault("shots", []).append(shot)
        elif op == "update_shot":
            ident = action.get("id") or action.get("shotId") or payload.get("id")
            target = next((s for s in result.get("shots", []) if isinstance(s, dict) and s.get("id") == ident), None)
            if target is None: raise ValueError("Ukjent klipp")
            fields = {k: v for k, v in payload.items() if k != "id"}; _reject_server(set(fields))
            if set(fields) - _SHOT_EDITABLE: raise ValueError("Ukjent klippfelt")
            _validate_cast(fields, cast); target.update(copy.deepcopy(fields))
        else:
            raise ValueError("Ukjent forslagshandling")
    return result


def proposal_title(action: dict[str, Any]) -> str:
    return {"set_board_fields": "Oppdater prosjekt", "add_shot": "Legg til klipp", "update_shot": "Oppdater klipp", "add_character": "Legg til karakter", "update_character": "Oppdater karakter"}.get(action.get("operation") or action.get("tool") or action.get("op"), "Rediger forslag")


def _project_context(context: dict[str, Any]) -> tuple[dict[str, Any], str]:
    project = context.get("project") if isinstance(context.get("project"), dict) else context
    if not isinstance(project, dict): raise ValueError("Mangler filmstatus")
    slug = project.get("slug")
    if not isinstance(slug, str) or not slug: raise ValueError("Mangler film-ID")
    return project, slug


def film_action(context: dict[str, Any], kind: str, accepted: bool = False, note: str = "") -> dict[str, Any]:
    project, slug = _project_context(context)
    is_film = bool(project.get("isFilm"))
    anchor = context.get("anchorRevision") or project.get("anchorRevision") or project.get("revision")
    revision = project.get("revision") if is_film else anchor
    if not isinstance(revision, str) or not revision:
        raise ValueError("Mangler gjeldende filmrevisjon")
    continuation = project.get("continuationAnchor", False)
    if not isinstance(continuation, bool):
        raise ValueError("Ugyldig videreføringsanker")
    if kind == "prepare":
        tool = "resume_film" if is_film else "render_storyboard"
        action = {"tool": tool, "slug": slug, "revision": revision, "imageEngine": IMAGE_ENGINE, "continuationAnchor": continuation, "prepareOnly": True}
        if tool == "render_storyboard": action.update(copy.deepcopy(project.get("plannedFilm") or {}))
        return action
    if kind == "produce":
        tool = "resume_film" if is_film else "render_storyboard"
        action = {"tool": tool, "slug": slug, "revision": revision, "imageEngine": IMAGE_ENGINE, "continuationAnchor": continuation}
        if tool == "render_storyboard": action.update(copy.deepcopy(project.get("plannedFilm") or {}))
        return action
    if not is_film: raise ValueError("Dette prosjektet er ikke en filmjobb")
    if kind in {"approve_images", "approve_review"} and not accepted: raise ValueError("Godkjenning krever eksplisitt bekreftelse")
    action = {"tool": "resume_film", "slug": slug, "revision": revision, "continuationAnchor": continuation}
    if kind == "approve_images":
        plan = project.get("imagePlan") or {}; digest = plan.get("digest")
        if project.get("imagePlanStale") or not project.get("imageApprovalRequired", True) or not isinstance(digest, str) or not digest: raise ValueError("Bildeplanen er tom, ikke ventende eller foreldet")
        action["acceptImagePlan"] = digest
    elif kind == "approve_review":
        if not project.get("reviewRequired") or not project.get("reviewPreview"):
            raise ValueError("Kvalitetsgjennomgangen mangler eller er ikke ventende")
        action["acceptReview"] = True; action["reviewNote"] = str(note or "")
    elif kind != "resume": raise ValueError("Ukjent filmhandling")
    return action


def next_step(board: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, str]:
    shots = [s for s in board.get("shots") or [] if isinstance(s, dict)]
    if not board.get("sceneDescription"): return {"label": "Skriv brief", "page": "brief", "action": "create_brief", "detail": "Start med én setning om hva filmen skal føles som."}
    if not board.get("characters") and not shots: return {"label": "Bygg filmbibel", "page": "bible", "action": "add_character", "detail": "Legg til karakterer hvis historien trenger dem, eller gå rett til et scenery-klipp."}
    if not shots: return {"label": "Lag første klipp", "page": "storyboard", "action": "add_shot", "detail": "Beskriv åpningen, handlingen og kameraet."}
    status = (context.get("project") if isinstance(context, dict) and isinstance(context.get("project"), dict) else context) if context else {}
    status = status if isinstance(status, dict) else {}
    if status.get("imageApprovalRequired") and status.get("imagePlan"):
        return {"label": "Godkjenn bildene", "page": "production", "action": "approve_images", "detail": "Se gjennom bildeplanen før video kan produseres."}
    if status.get("reviewRequired"):
        return {"label": "Gå gjennom filmen", "page": "production", "action": "approve_review", "detail": "Se gjennom kvalitetsrapporten før filmen fortsetter."}
    if status and status.get("canProduce") is False:
        return {"label": "Vent på produksjonen", "page": "production", "action": "film_status", "detail": "Produksjonen arbeider fortsatt eller trenger en oppdatert plan."}
    if any(not all(s.get(k) for k in ("start", "action", "end", "camera")) for s in shots): return {"label": "Form scenene", "page": "storyboard", "action": "edit_shot", "detail": "Gi hvert klipp en tydelig start, handling, slutt og kameraføring."}
    if not any(s.get("startRef") or s.get("thumb") or s.get("stills") for s in shots): return {"label": "Lag åpningsbilder", "page": "images", "action": "prepare_images", "detail": "Forhåndsvis og godkjenn bildene før video."}
    return {"label": "Produser filmen", "page": "production", "action": "produce", "detail": "Alt er klart for å starte produksjonen."}

"""Explicit, resumable manuscript authoring through the Raven director.

This module is deliberately limited to canonical board authoring.  It never
calls a media, render, image, audio, or film-operation endpoint.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from production_model import apply_proposals
from studio_state import authoring_digest


MAX_SHOTS = 48
BATCH_SIZE = 3
AUTHORING_TOOLS = {"set_board_fields", "add_character"}
SHOT_TOOLS = {"add_shot"}
FORBIDDEN_TOOLS = {
    "start_render", "render_storyboard", "resume_film", "start_film", "stop_film",
    "dub_shot", "assemble", "stills", "create_board", "delete_board",
}


def _cancelled(cancel: Any) -> bool:
    if cancel is None:
        return False
    return bool(cancel.is_set() if hasattr(cancel, "is_set") else cancel())


def _emit(callback: Callable[[dict[str, Any]], None] | None, phase: str,
          completed: int, total: int, detail: str = "") -> None:
    if callback:
        callback({"phase": phase, "completed": completed, "total": total, "detail": detail})


def _board(response: Any) -> dict[str, Any]:
    value = response.get("board") if isinstance(response, dict) and isinstance(response.get("board"), dict) else response
    if not isinstance(value, dict):
        raise ValueError("Backend returnerte ikke et storyboard")
    return copy.deepcopy(value)


def _actions(result: Any, allowed: set[str]) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        raise ValueError("Regissøren returnerte ikke et forslag")
    status = result.get("proposalStatus")
    if status in {"incomplete", "rejected"}:
        diagnostics = result.get("errors") or result.get("error") or status
        raise ValueError(f"Regissørforslaget var ufullstendig eller ble avvist: {diagnostics}")
    actions = result.get("actions")
    if not isinstance(actions, list):
        raise ValueError("Regissøren returnerte ingen gyldige manusendringer (actions mangler)")
    clean = []
    for action in actions:
        if not isinstance(action, dict) or action.get("tool") not in allowed:
            tool = action.get("tool") if isinstance(action, dict) else type(action).__name__
            raise ValueError(f"Manusforfatteren foreslo en ikke-tillatt operasjon: {tool}")
        clean.append(copy.deepcopy(action))
    if not clean:
        raise ValueError("Regissøren returnerte ingen manusendringer")
    return clean


def _authoring_chat(backend: Any, payload: dict[str, Any], stage: str) -> Any:
    """Call the bounded authoring endpoint, retaining the test/backend fallback."""
    request = dict(payload)
    request["authoringStage"] = stage
    method = getattr(backend, "authoring_chat", None)
    if method is None:
        method = backend.chat
    return method(request)


def _retry_feedback(brief: str, board: dict[str, Any], error: Exception) -> str:
    """Give the model the complete source context, never a locally invented fix."""
    bible = board.get("filmBible") if isinstance(board.get("filmBible"), dict) else {}
    return (
        "\n\nKORRIGERINGSRUNDE (maksimalt én): Forrige forslag ble avvist av valideringen. "
        f"Den faktiske valideringsfeilen var: {error}. Rett bare denne feilen og returner et "
        "komplett nytt forslag i samme format; ikke forklar og ikke finn på innhold utenfor "
        "briefen eller global kontekst.\n"
        "ORIGINAL BRIEF (må bevares uendret):\n" + brief +
        "\nGLOBAL KONTEXT SOM ALLTID GJELDER:\n" +
        json.dumps({"filmBible": bible, "name": board.get("name"),
                    "existingShots": len(board.get("shots", []))},
                   ensure_ascii=False, sort_keys=True)
    )


def _shot_prompt(board: dict[str, Any], brief: str, count: int, completed: int, batch: int) -> str:
    first_group = max((s.get("directorScene", 0) for s in board.get("shots", [])
                       if type(s.get("directorScene")) is int), default=0) + 1
    slots = [{"position": completed + i + 1, "directorScene": first_group + i,
              "frames": 121} for i in range(batch)]
    return (
        f"Fortsett manusplanen mot det globale målet på {count} nye klipp. Det er allerede "
        f"lagret {completed}; foreslå posisjonene {completed + 1}–{completed + batch} og "
        f"stopp etter batchen. Dette er den siste batchen hvis den avslutter på {count}. "
        f"Foreslå nøyaktig {batch} nye add_shot-kort med eksisterende characterIds og "
        "directorScene, som del av én sammenhengende, ordnet global plot outline på tvers "
        "av alle batcher. Hvert kort må ha kjent locationId, English prose prompt, tydelig "
        "start/action/end/camera, 4–6 sekunder, og samme kamera innen en kontinuerlig "
        "kameragruppe (maks seks; bruk eksakt samme camera-tekst innen gruppen). Foreslå bare add_shot; "
        "ikke skriv at kortene er lagret, og bruk ingen media- eller produksjonsoperasjoner. Briefen var:\n" + brief +
        "\nTEKNISK KLIPPLAN FOR DETTE UTKASTET: Bruk et eget kameraoppsett per nytt klipp. "
        "directorScene er kameraoppsett, IKKE aktnummer. Bruk følgende eksakte numre/frames i rekkefølge, "
        "og varier utsnitt/kamera etter scenens behov. Skriv den kreative handlingen selv fra brief og bibel.\n" +
        json.dumps(slots, ensure_ascii=False)
    )


def _validated_shot_candidate(board: dict[str, Any], result: Any, batch: int) -> tuple[list[dict[str, Any]], dict[str, Any], set[str]]:
    actions = _actions(result, SHOT_TOOLS)
    if len(actions) != batch:
        raise ValueError(f"Regissøren returnerte {len(actions)} klipp; forventet {batch}")
    locations = {(item.get("id") if isinstance(item, dict) else None)
                 for item in (board.get("filmBible") or {}).get("locations", [])}
    groups: dict[Any, dict[str, Any]] = {}
    for existing in board.get("shots", []):
        if isinstance(existing, dict) and existing.get("directorScene") is not None:
            scene_key = existing.get("directorScene")
            group = groups.setdefault(scene_key, {"camera": existing.get("camera"), "count": 0})
            if group["camera"] == existing.get("camera"):
                group["count"] += 1
    problems=[]
    for offset,action in enumerate(actions,1):
        shot=action.get("shot")
        label=f"Klipp {len(board.get('shots',[]))+offset}"
        if not isinstance(shot,dict) or any(not isinstance(shot.get(key),str) or not shot.get(key).strip() for key in ("prompt","start","action","end","camera")):
            problems.append(label+": mangler full start/action/end/camera og prompt")
            continue
        if shot.get("locationId") not in locations: problems.append(label+": mangler en kjent locationId")
        frames=shot.get("frames",121)
        scene=shot.get("directorScene")
        if type(scene) is not int or scene<1:
            problems.append(label+": directorScene må være et positivt heltall")
            continue
        group=groups.get(scene)
        allowed={96,120,144} if group else {97,121,145}
        if type(frames) is not int or frames not in allowed:
            problems.append(f"{label}: ugyldig åpnings-/fortsettelsesramme {frames} i kameragruppe {scene}; bruk {sorted(allowed)}")
        if group and group["camera"]!=shot.get("camera"):
            problems.append(f"{label}: En kontinuerlig kameragruppe kan ikke bytte kamera. directorScene={scene} har camera={group['camera']!r}, men dette klippet har {shot['camera']!r}. Et nytt utsnitt/sted trenger NY directorScene og frames=121; en videreføring trenger nøyaktig den eksisterende camera-teksten og frames=120.")
        if group and group["count"]>=6: problems.append(label+": En kameragruppe kan ha maksimalt seks klipp")
        groups[scene]={"camera":group["camera"] if group else shot["camera"],"count":(group["count"] if group else 0)+1}
    if problems: raise ValueError("\n".join(problems))
    before_ids = {str(s.get("id")) for s in board.get("shots", []) if isinstance(s, dict) and s.get("id")}
    candidate = apply_proposals(board, [dict(a, reviewed=True) for a in actions])
    return actions, candidate, before_ids


def _checkpoint(workspace: Any, slug: str, payload: dict[str, Any]) -> None:
    root = workspace.ensure_project(slug)
    path = root / "System" / "director-authoring.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".director-authoring.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _checkpoint_path(workspace: Any, slug: str) -> Path:
    return workspace.ensure_project(slug) / "System" / "director-authoring.json"


def _candidate_matches(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    """Match the proposed authored values while tolerating server defaults."""
    for key in ("name", "sceneDescription", "renderStyle", "soundscape", "soundtrackPrompt",
                "defaults", "filmBible", "styleRefs", "ravenSettings"):
        if candidate.get(key) != current.get(key):
            return False
    for collection, fields in (("characters", ("id", "name", "description", "image", "voice", "voiceText")),
                               ("shots", ("id", "title", "prompt", "start", "action", "end", "camera",
                                           "locationId", "stateBefore", "stateAfter", "motionIntent", "propIds",
                                           "assetStates", "soundNote", "characterIds", "dialogue", "dialogueSource",
                                           "dialogueStyle", "dialogueVoice", "speakerId", "dubMode", "referenceImages",
                                           "imageAnchors", "resolution", "frames", "steps", "seed", "startRef",
                                           "endRef", "directorScene", "ravenNotes"))):
        actual = {str(item.get("id")): item for item in current.get(collection, []) if isinstance(item, dict) and item.get("id")}
        for item in candidate.get(collection, []):
            if not isinstance(item, dict) or str(item.get("id")) not in actual:
                return False
            target = actual[str(item["id"])]
            if any(key in item and item.get(key) != target.get(key) for key in fields):
                return False
    return True


def _checkpoint_payload(session: str, slug: str, brief: str, total: int,
                        initial_ids: set[str], added_ids: set[str], setup_done: bool,
                        digest: str, status: str, error: str = "",
                        pending: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"session": session, "slug": slug, "brief": brief, "total": total,
            "initialIds": sorted(initial_ids), "addedIds": sorted(added_ids),
            "completed": len(added_ids), "setupDone": setup_done, "digest": digest,
            "status": status, "error": error, "pending": pending, "updatedAt": time.time()}


def author_project(
    backend: Any, workspace: Any, slug: str, brief: str, count: int,
    *, cancel: Any = None, on_progress: Callable[[dict[str, Any]], None] | None = None,
    expected_digest: str | None = None, resume_only: bool = False,
) -> dict[str, Any]:
    """Author exactly ``count`` new shots after an explicit user action.

    Each staged response is validated locally, persisted with a revision/digest
    check, and reloaded before the next model call.  A failed later batch never
    rolls back an earlier committed batch.
    """
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError("Manuskriptbriefen kan ikke være tom")
    if type(count) is not int or not 1 <= count <= MAX_SHOTS:
        raise ValueError(f"Antall klipp må være mellom 1 og {MAX_SHOTS}")
    session = uuid.uuid4().hex
    board = _board(backend.get(f"/api/boards/{slug}"))
    initial_ids = {str(s.get("id")) for s in board.get("shots", []) if isinstance(s, dict) and s.get("id")}
    initial_digest = authoring_digest(board)
    token = expected_digest or initial_digest
    if token != initial_digest:
        raise ValueError("Prosjektet er endret siden manusforfatteren ble åpnet")
    completed = 0
    added_ids: set[str] = set()
    setup_done = False
    # An explicit resume must not create a project/System directory while
    # probing for its journal.  Ordinary authoring retains the existing helper
    # semantics and creates the project structure as needed.
    checkpoint = (_checkpoint_path(workspace, slug) if not resume_only else
                  Path(workspace.projects_dir) / slug / "System" / "director-authoring.json")
    saved = None
    if resume_only:
        try:
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise ValueError("Kan ikke gjenoppta: manusjournalen mangler eller er ugyldig") from error
        if not isinstance(saved, dict):
            raise ValueError("Kan ikke gjenoppta: manusjournalen er ugyldig")
        saved_ids = {str(value) for value in saved.get("addedIds", [])}
        saved_initial_ids = {str(value) for value in saved.get("initialIds", [])}
        current_ids = {str(s.get("id")) for s in board.get("shots", []) if isinstance(s, dict) and s.get("id")}
        valid_checkpoint = (saved.get("slug") == slug and saved.get("brief") == brief
                            and saved.get("total") == count and saved_ids <= current_ids
                            and saved_initial_ids <= current_ids
                            and saved_ids.isdisjoint(saved_initial_ids))
        pending = saved.get("pending")
        pending_applied = (valid_checkpoint and isinstance(pending, dict)
                           and isinstance(pending.get("candidate"), dict)
                           and _candidate_matches(pending["candidate"], board))
        if not valid_checkpoint:
            raise ValueError("Kan ikke gjenoppta: manusjournalen gjelder ikke dette prosjektet/briefen")
        if saved.get("digest") != initial_digest and not pending_applied:
            raise ValueError("Kan ikke gjenoppta: prosjektet er endret siden siste manusjournal")
        if saved.get("status") == "done" and saved.get("digest") == initial_digest:
            return {"board": board, "completed": len(saved_ids), "total": count,
                    "status": "done", "setupDone": bool(saved.get("setupDone"))}
    try:
        if saved is None:
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        saved_ids = {str(value) for value in saved.get("addedIds", [])}
        saved_initial_ids = {str(value) for value in saved.get("initialIds", [])}
        current_ids = {str(s.get("id")) for s in board.get("shots", []) if isinstance(s, dict) and s.get("id")}
        valid_checkpoint = (saved.get("slug") == slug and saved.get("brief") == brief
                             and saved.get("total") == count and saved_ids <= current_ids
                             and saved_initial_ids <= current_ids
                             and saved_ids.isdisjoint(saved_initial_ids))
        pending = saved.get("pending")
        pending_applied = (valid_checkpoint and isinstance(pending, dict)
                           and isinstance(pending.get("candidate"), dict)
                           and _candidate_matches(pending["candidate"], board))
        if pending_applied or (valid_checkpoint and saved.get("digest") == initial_digest):
            session = str(saved.get("session") or session)
            initial_ids = saved_initial_ids
            added_ids = saved_ids
            completed = len(added_ids)
            setup_done = bool(saved.get("setupDone"))
            if pending_applied:
                pending_ids = {str(value) for value in pending.get("addedIds", [])}
                # A process can die after PUT and before the normal completed
                # checkpoint.  Reconcile the canonical save by digest; the
                # digest excludes server-owned runtime receipts.
                added_ids.update(pending_ids)
                completed = len(added_ids)
                setup_done = bool(pending.get("setupDone", setup_done))
                token = authoring_digest(board)
                _checkpoint(workspace, slug, _checkpoint_payload(
                    session, slug, brief, count, initial_ids, added_ids,
                    setup_done, token, "running"))
    except (OSError, ValueError, TypeError):
        pass

    def save(candidate: dict[str, Any], detail: str, *, pending_added: set[str] | None = None,
             pending_setup: bool = False) -> dict[str, Any]:
        nonlocal token
        pending_added = pending_added or set()
        current = _board(backend.get(f"/api/boards/{slug}"))
        if authoring_digest(current) != token:
            raise ValueError("Prosjektet er endret av brukeren; manusforfatteren stoppet uten å overskrive endringen")
        workspace.snapshots(slug, current)
        _checkpoint(workspace, slug, _checkpoint_payload(
            session, slug, brief, count, initial_ids, added_ids, setup_done,
            authoring_digest(current), "pending", pending={
                "candidate": candidate, "addedIds": sorted(pending_added),
                "setupDone": pending_setup, "digestBefore": authoring_digest(current),
            }))
        put_kwargs = {"expected_digest": authoring_digest(current)}
        if current.get("updatedAt") is not None:
            put_kwargs["expected_updated_at"] = str(current["updatedAt"])
        backend.put(f"/api/boards/{slug}", candidate, **put_kwargs)
        saved = _board(backend.get(f"/api/boards/{slug}"))
        token = authoring_digest(saved)
        workspace.sync_script(slug, saved)
        return saved

    try:
        _emit(on_progress, "setup", completed, count, "Regissøren setter opp idé, bibel og karakterer")
        if _cancelled(cancel):
            raise InterruptedError("Manusforfatteren ble avbrutt")
        if not setup_done:
            setup_prompt = (
                "Skriv oppsettet for dette filmmanuset ut fra briefen nedenfor. "
                "Foreslå bare set_board_fields med sceneDescription, renderStyle, "
                "soundscape og filmBible (acts, locations, props), samt add_character. "
                "Film-bibelen må ha minst én beskrevet act og minst én navngitt location med "
                "unik, stabil ID. Nye karakterer får navn og beskrivelse; Raven tildeler deres ID-er. "
                "Gjenbruk eksisterende karakterer når de finnes. Ikke foreslå klipp ennå og "
                "ikke foreslå noen produksjonsoperasjon. Dette er et forslag som skal lagres "
                "av appen.\n\nORIGINAL BRIEF:\n" + brief
            )
            setup_actions = None
            for attempt in range(2):
                setup = _authoring_chat(backend, {"slug": slug, "message": setup_prompt,
                                                   "history": []}, "setup")
                if _cancelled(cancel):
                    raise InterruptedError("Manusforfatteren ble avbrutt")
                try:
                    setup_actions = _actions(setup, AUTHORING_TOOLS)
                    candidate = apply_proposals(board, [dict(a, reviewed=True) for a in setup_actions])
                    bible = candidate.get("filmBible") if isinstance(candidate.get("filmBible"), dict) else {}
                    if not isinstance(bible.get("acts"), list) or not bible["acts"]:
                        raise ValueError("Oppsettet mangler minst én filmBible-act")
                    if not isinstance(bible.get("locations"), list) or not bible["locations"]:
                        raise ValueError("Oppsettet mangler minst én filmBible-location")
                    break
                except (TypeError, ValueError) as error:
                    if attempt:
                        raise
                    setup_prompt += _retry_feedback(brief, board, error)
                    _emit(on_progress, "setup", completed, count,
                          f"Oppsettet ble avvist ({error}); ber regissøren korrigere")
            if setup_actions is None:
                raise ValueError("Regissøren returnerte ikke et gyldig oppsett")
            board = save(candidate, "Oppsett lagret", pending_setup=True)
            setup_done = True
            _checkpoint(workspace, slug, _checkpoint_payload(session, slug, brief, count,
                         initial_ids, added_ids, setup_done, token, "running"))
            if _cancelled(cancel):
                raise InterruptedError("Manusforfatteren ble avbrutt")

        while completed < count:
            remaining = count - completed
            batch = min(BATCH_SIZE, remaining)
            _emit(on_progress, "shots", completed, count, f"Foreslår batch på {batch} klipp")
            shot_prompt = _shot_prompt(board, brief, count, completed, batch)
            result = _authoring_chat(backend, {"slug": slug, "message": shot_prompt,
                                                "history": []}, "shots")
            if _cancelled(cancel):
                raise InterruptedError("Manusforfatteren ble avbrutt")
            if isinstance(result, dict) and result.get("proposalStatus") == "incomplete" and batch > 3:
                # A truncated large response gets one deliberately smaller
                # retry.  The next loop will request the remaining cards.
                batch = min(3, batch)
                _emit(on_progress, "shots", completed, count, "Svar ble avbrutt; prøver en mindre batch")
                shot_prompt = "Forrige svar ble avbrutt; denne mindre batchen erstatter det.\n" + _shot_prompt(board, brief, count, completed, batch)
                result = _authoring_chat(backend, {"slug": slug, "message": shot_prompt,
                                                  "history": []}, "shots")
                if _cancelled(cancel):
                    raise InterruptedError("Manusforfatteren ble avbrutt")
            for correction_attempt in range(2):
                try:
                    actions, candidate, before_ids = _validated_shot_candidate(board, result, batch)
                    break
                except (TypeError, ValueError) as error:
                    if correction_attempt:
                        raise
                    shot_prompt += _retry_feedback(brief, board, error)
                    _emit(on_progress, "shots", completed, count,
                          f"Batchen ble avvist ({error}); ber regissøren korrigere")
                    result = _authoring_chat(backend, {"slug": slug, "message": shot_prompt,
                                                        "history": []}, "shots")
                    if _cancelled(cancel):
                        raise InterruptedError("Manusforfatteren ble avbrutt")
            candidate_ids = {str(s.get("id")) for s in candidate.get("shots", []) if isinstance(s, dict) and s.get("id")}
            board = save(candidate, f"Lagret batch på {batch} klipp",
                         pending_added=candidate_ids - before_ids, pending_setup=setup_done)
            after_ids = {str(s.get("id")) for s in board.get("shots", []) if isinstance(s, dict) and s.get("id")}
            added = after_ids - before_ids
            if len(added) != batch or added & initial_ids:
                raise ValueError("Kunne ikke bekrefte riktig antall nye klipp")
            added_ids.update(added)
            completed += batch
            _checkpoint(workspace, slug, _checkpoint_payload(session, slug, brief, count,
                         initial_ids, added_ids, setup_done, token, "running"))
            _emit(on_progress, "shots", completed, count, f"{batch} klipp lagret")
        _checkpoint(workspace, slug, _checkpoint_payload(session, slug, brief, count,
                     initial_ids, added_ids, setup_done, token, "done"))
        return {"board": board, "completed": completed, "total": count, "status": "done", "setupDone": setup_done}
    except InterruptedError as error:
        try:
            board = _board(backend.get(f"/api/boards/{slug}"))
            token = authoring_digest(board)
        except Exception:
            pass
        _checkpoint(workspace, slug, _checkpoint_payload(session, slug, brief, count,
                     initial_ids, added_ids, setup_done, token, "cancelled", str(error)))
        return {"board": board, "completed": completed, "total": count, "status": "cancelled", "setupDone": setup_done, "error": str(error)}
    except Exception as error:  # keep committed batches and expose actual board
        try:
            board = _board(backend.get(f"/api/boards/{slug}"))
            token = authoring_digest(board)
        except Exception:
            pass
        _checkpoint(workspace, slug, _checkpoint_payload(session, slug, brief, count,
                     initial_ids, added_ids, setup_done, token, "failed", str(error)))
        return {"board": board, "completed": completed, "total": count, "status": "failed", "setupDone": setup_done, "error": str(error)}

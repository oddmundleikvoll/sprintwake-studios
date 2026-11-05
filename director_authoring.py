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
        raise ValueError("The backend did not return a storyboard")
    return copy.deepcopy(value)


def _actions(result: Any, allowed: set[str]) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        raise ValueError("Director returned no proposal")
    status = result.get("proposalStatus")
    if status in {"incomplete", "rejected"}:
        diagnostics = result.get("errors") or result.get("error") or status
        raise ValueError(f"Director proposal was incomplete or rejected: {diagnostics}")
    actions = result.get("actions")
    if not isinstance(actions, list):
        raise ValueError("Director returned no valid script changes (actions missing)")
    clean = []
    for action in actions:
        if not isinstance(action, dict) or action.get("tool") not in allowed:
            tool = action.get("tool") if isinstance(action, dict) else type(action).__name__
            raise ValueError(f"The writer proposed an unsupported operation: {tool}")
        clean.append(copy.deepcopy(action))
    if not clean:
        raise ValueError("Director returned no script changes")
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
        "\n\nCORRECTION ROUND (at most one): Validation rejected the previous proposal. "
        f"The actual validation error was: {error}. Correct only this error and return a "
        "complete new proposal in the same format; do not explain or invent content outside "
        "the brief or global context.\n"
        "ORIGINAL BRIEF (must be preserved unchanged):\n" + brief +
        "\nGLOBAL CONTEXT THAT ALWAYS APPLIES:\n" +
        json.dumps({"filmBible": bible, "name": board.get("name"),
                    "existingShots": len(board.get("shots", []))},
                   ensure_ascii=False, sort_keys=True)
    )


def _planned_handoff(board: dict[str, Any]) -> list[dict[str, Any]]:
    """Bound recent authored state explicitly; never send outputs, recipes or paths."""
    result=[]
    for shot in board.get("shots", [])[-6:]:
        item={key:shot[key][:600] for key in ("title","start","action","end","camera","locationId",
              "stateBefore","stateAfter","soundNote") if isinstance(shot.get(key),str)}
        for key in ("characterIds","propIds"):
            item[key]=[value[:128] for value in (shot.get(key) or [])[:16] if isinstance(value,str)]
        states=shot.get("assetStates")
        if isinstance(states,dict):
            item["assetStates"]={key[:128]:value[:300] for key,value in list(states.items())[:16]
                                 if isinstance(key,str) and isinstance(value,str)}
        result.append(item)
    return result


def _shot_prompt(board: dict[str, Any], brief: str, count: int, completed: int, batch: int) -> str:
    first_group = max((s.get("directorScene", 0) for s in board.get("shots", [])
                       if type(s.get("directorScene")) is int), default=0) + 1
    slots = [{"position": completed + i + 1, "directorScene": first_group + i,
              "frames": 121} for i in range(batch)]
    return (
        f"Continue the script plan toward the global goal of {count} new shots. Already "
        f"saved {completed}; propose positions {completed + 1}–{completed + batch} and "
        f"stop after the batch. This is the final batch if it ends at {count}. "
        f"Propose exactly {batch} new add_shot cards with existing characterIds and "
        "directorScene, as part of one continuous, ordered global plot outline across "
        "all batches. Each card must have a known locationId, English prose prompt, clear "
        "start/action/end/camera, 4–6 seconds, and the same camera within a continuous "
        "camera group (maximum six; use exactly the same camera text within the group). "
        "Include non-empty stateBefore/stateAfter describing positions, eyelines, clothing "
        "and prop ownership before and after the action. Use propIds for relevant registered "
        "props and assetStates for their concrete physical details, keyed by known cast/prop "
        "IDs with text values. These are planned states, not observations of generated video. "
        "Propose only add_shot; "
        "do not claim the cards are saved, and do not use media or production operations. The brief was:\n" + brief +
        "\nTECHNICAL SHOT PLAN FOR THIS DRAFT: Use a separate camera setup for each new shot. "
        "directorScene is a camera setup, NOT an act number. Use the following exact numbers/frames in order, "
        "and vary framing/camera to suit the scene. Write the creative action from the brief and bible.\n" +
        json.dumps(slots, ensure_ascii=False) +
        "\nCINEMATIC COVERAGE: Give each angle a story purpose: establish geography, reveal "
        "information, show a reaction, follow an action or show its consequence. Select only "
        "the coverage this scene needs; avoid repeating the same action to fill the count. "
        "Keep screen direction, eyelines, light and prop ownership coherent across cuts. "
        "One card contains one achievable action and one camera setup, with brief dialogue "
        "in its chosen language. Put language/delivery in dialogueStyle and select speakerId "
        "for native speech. Plan scene ambience in soundNote; soundtrackPrompt is a separate "
        "film score, not a new musical opening in every clip.\n"
        "RECENT PLANNED HANDOFF (not evidence of rendered action):\n" +
        json.dumps(_planned_handoff(board), ensure_ascii=False)
    )


def _validated_shot_candidate(board: dict[str, Any], result: Any, batch: int) -> tuple[list[dict[str, Any]], dict[str, Any], set[str]]:
    actions = _actions(result, SHOT_TOOLS)
    if len(actions) != batch:
        raise ValueError(f"Director returned {len(actions)} shots; expected {batch}")
    locations = {(item.get("id") if isinstance(item, dict) else None)
                 for item in (board.get("filmBible") or {}).get("locations", [])}
    cast = {item.get("id") for item in board.get("characters", []) if isinstance(item, dict)}
    props = {item.get("id") for item in (board.get("filmBible") or {}).get("props", []) if isinstance(item, dict)}
    groups: dict[Any, dict[str, Any]] = {}
    for existing in board.get("shots", []):
        if isinstance(existing, dict) and existing.get("directorScene") is not None:
            scene_key = existing.get("directorScene")
            group = groups.setdefault(scene_key, {"camera": existing.get("camera"),
                "locationId": existing.get("locationId"), "resolution": existing.get("resolution", "384x256"), "count": 0})
            if group["camera"] == existing.get("camera"):
                group["count"] += 1
    problems=[]
    last_scene = board.get("shots", [{}])[-1].get("directorScene") if board.get("shots") else None
    for offset,action in enumerate(actions,1):
        shot=action.get("shot")
        label=f"Shot {len(board.get('shots',[]))+offset}"
        if not isinstance(shot,dict) or any(not isinstance(shot.get(key),str) or not shot.get(key).strip() for key in ("prompt","start","action","end","camera")):
            problems.append(label+": missing complete start/action/end/camera and prompt")
            continue
        if shot.get("locationId") not in locations: problems.append(label+": missing a known locationId")
        if any(not isinstance(shot.get(key), str) or not shot[key].strip() for key in ("stateBefore", "stateAfter")):
            problems.append(label+": stateBefore and stateAfter must describe concrete planned continuity")
        for key,known in (("characterIds",cast),("propIds",props)):
            values=shot.get(key,[])
            if not isinstance(values,list) or any(not isinstance(ident,str) or ident not in known for ident in values):
                problems.append(label+": "+key+" must use known IDs")
        states=shot.get("assetStates",{})
        if not isinstance(states,dict) or any(key not in cast|props or not isinstance(value,str) or not value.strip() for key,value in states.items()):
            problems.append(label+": assetStates must map known character/prop IDs to non-empty physical descriptions")
        if shot.get("speakerId") and shot["speakerId"] not in cast:
            problems.append(label+": speakerId must use a known character ID")
        speech_source=shot.get("dialogueSource", "native")
        if speech_source not in {"native","recording"}:
            problems.append(label+": dialogueSource must be native or recording")
        if shot.get("dialogue") and speech_source=="native" and not shot.get("speakerId") and len(shot.get("characterIds") or [])!=1:
            problems.append(label+": native dialogue needs a known speakerId or one visible character")
        frames=shot.get("frames",121)
        scene=shot.get("directorScene")
        if type(scene) is not int or scene<1:
            problems.append(label+": directorScene must be a positive integer")
            continue
        group=groups.get(scene)
        if group and last_scene!=scene: problems.append(label+": a camera group must be contiguous; use a new directorScene for a returning angle")
        allowed={96,120,144} if group else {97,121,145}
        if type(frames) is not int or frames not in allowed:
            problems.append(f"{label}: invalid opening/continuation frame {frames} in camera group {scene}; use {sorted(allowed)}")
        if group and group["camera"]!=shot.get("camera"):
            problems.append(f"{label}: A continuous camera group cannot change camera. directorScene={scene} has camera={group['camera']!r}, but this shot has {shot['camera']!r}. A new angle/location needs a NEW directorScene and frames=121; a continuation needs the exact existing camera text and frames=120.")
        if group and group["count"]>=6: problems.append(label+": a camera group can contain at most six shots")
        if group and (group["locationId"]!=shot.get("locationId") or group["resolution"]!=shot.get("resolution", "384x256")):
            problems.append(label+": a continuous camera group cannot change location or resolution")
        groups[scene]={"camera":group["camera"] if group else shot["camera"],
            "locationId":shot.get("locationId"),"resolution":shot.get("resolution", "384x256"),
            "count":(group["count"] if group else 0)+1}
        last_scene=scene
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
        raise ValueError("Script brief cannot be empty")
    if type(count) is not int or not 1 <= count <= MAX_SHOTS:
        raise ValueError(f"Shot count must be between 1 and {MAX_SHOTS}")
    session = uuid.uuid4().hex
    board = _board(backend.get(f"/api/boards/{slug}"))
    initial_ids = {str(s.get("id")) for s in board.get("shots", []) if isinstance(s, dict) and s.get("id")}
    initial_digest = authoring_digest(board)
    token = expected_digest or initial_digest
    if token != initial_digest:
        raise ValueError("Project changed after the script author was opened")
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
            raise ValueError("Cannot resume: the script journal is missing or invalid") from error
        if not isinstance(saved, dict):
            raise ValueError("Cannot resume: the script journal is invalid")
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
            raise ValueError("Cannot resume: the script journal belongs to a different project or brief")
        if saved.get("digest") != initial_digest and not pending_applied:
            raise ValueError("Cannot resume: the project has changed since the last script journal")
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
            raise ValueError("Project was changed by the user; authoring stopped without overwriting the change")
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
        _emit(on_progress, "setup", completed, count, "Director is setting up the idea, bible, and characters")
        if _cancelled(cancel):
            raise InterruptedError("Script authoring was cancelled")
        if not setup_done:
            setup_prompt = (
                "Write the setup for this film script from the brief below. "
                "Propose only set_board_fields with sceneDescription, renderStyle, "
                "soundscape and filmBible (acts, locations, props), plus add_character. "
                "Film bible must have at least one described act and at least one named location with "
                "a unique, stable ID. New characters get names and descriptions; Raven assigns their IDs. "
                "Define each character's stable face, silhouette and costume; describe recurring "
                "locations with geography, time and lighting, and recurring props with appearance "
                "and initial ownership. Keep changing poses and emotions in individual shot cards. "
                "Plan the story beats and scene transitions across the complete requested shot count "
                "before writing the first batch. Separate ongoing ambience from the musical score. "
                "Reuse existing characters when available. Do not propose shots yet and "
                "do not propose any production operation. This is a proposal to be saved "
                "by the app.\n\nORIGINAL BRIEF:\n" + brief
            )
            setup_actions = None
            for attempt in range(2):
                setup = _authoring_chat(backend, {"slug": slug, "message": setup_prompt,
                                                   "history": []}, "setup")
                if _cancelled(cancel):
                    raise InterruptedError("Script authoring was cancelled")
                try:
                    setup_actions = _actions(setup, AUTHORING_TOOLS)
                    candidate = apply_proposals(board, [dict(a, reviewed=True) for a in setup_actions])
                    bible = candidate.get("filmBible") if isinstance(candidate.get("filmBible"), dict) else {}
                    if not isinstance(bible.get("acts"), list) or not bible["acts"]:
                        raise ValueError("Setup requires at least one filmBible act")
                    if not isinstance(bible.get("locations"), list) or not bible["locations"]:
                        raise ValueError("Setup requires at least one filmBible location")
                    break
                except (TypeError, ValueError) as error:
                    if attempt:
                        raise
                    setup_prompt += _retry_feedback(brief, board, error)
                    _emit(on_progress, "setup", completed, count,
                          f"Setup was rejected ({error}); asking the director to correct it")
            if setup_actions is None:
                raise ValueError("Director returned no valid setup")
            board = save(candidate, "Setup saved", pending_setup=True)
            setup_done = True
            _checkpoint(workspace, slug, _checkpoint_payload(session, slug, brief, count,
                         initial_ids, added_ids, setup_done, token, "running"))
            if _cancelled(cancel):
                raise InterruptedError("Script authoring was cancelled")

        while completed < count:
            remaining = count - completed
            batch = min(BATCH_SIZE, remaining)
            _emit(on_progress, "shots", completed, count, f"Proposing a batch of {batch} shots")
            shot_prompt = _shot_prompt(board, brief, count, completed, batch)
            result = _authoring_chat(backend, {"slug": slug, "message": shot_prompt,
                                                "history": []}, "shots")
            if _cancelled(cancel):
                raise InterruptedError("Script authoring was cancelled")
            if isinstance(result, dict) and result.get("proposalStatus") == "incomplete" and batch > 3:
                # A truncated large response gets one deliberately smaller
                # retry.  The next loop will request the remaining cards.
                batch = min(3, batch)
                _emit(on_progress, "shots", completed, count, "Response was interrupted; trying a smaller batch")
                shot_prompt = "The previous response was interrupted; this smaller batch replaces it.\n" + _shot_prompt(board, brief, count, completed, batch)
                result = _authoring_chat(backend, {"slug": slug, "message": shot_prompt,
                                                  "history": []}, "shots")
                if _cancelled(cancel):
                    raise InterruptedError("Script authoring was cancelled")
            for correction_attempt in range(2):
                try:
                    actions, candidate, before_ids = _validated_shot_candidate(board, result, batch)
                    break
                except (TypeError, ValueError) as error:
                    if correction_attempt:
                        raise
                    shot_prompt += _retry_feedback(brief, board, error)
                    _emit(on_progress, "shots", completed, count,
                          f"Batch was rejected ({error}); asking the director to correct it")
                    result = _authoring_chat(backend, {"slug": slug, "message": shot_prompt,
                                                        "history": []}, "shots")
                    if _cancelled(cancel):
                        raise InterruptedError("Script authoring was cancelled")
            candidate_ids = {str(s.get("id")) for s in candidate.get("shots", []) if isinstance(s, dict) and s.get("id")}
            board = save(candidate, f"Saved batch of {batch} shots",
                         pending_added=candidate_ids - before_ids, pending_setup=setup_done)
            after_ids = {str(s.get("id")) for s in board.get("shots", []) if isinstance(s, dict) and s.get("id")}
            added = after_ids - before_ids
            if len(added) != batch or added & initial_ids:
                raise ValueError("Could not verify the expected number of new shots")
            added_ids.update(added)
            completed += batch
            _checkpoint(workspace, slug, _checkpoint_payload(session, slug, brief, count,
                         initial_ids, added_ids, setup_done, token, "running"))
            _emit(on_progress, "shots", completed, count, f"{batch} shots saved")
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

"""Honest proposal state, including historical truncated director replies."""
from __future__ import annotations

import copy

INCOMPLETE_MESSAGE = "Regissørsvaret ble avbrutt. Ingen manusendringer eller klipp ble opprettet fra dette svaret. Be om en mindre del, med opptil seks klipp om gangen."
REJECTED_MESSAGE = "Forslaget kunne ikke brukes. Ingen endringer er lagt i prosjektet."


def normalize_turn(turn):
    result = copy.deepcopy(turn)
    if result.get("role") != "assistant":
        return result
    if result.get("authoringStatus"):
        return result
    actions = result.get("actions")
    actions = actions if isinstance(actions, list) else []
    content = str(result.get("content") or "")
    status = result.get("proposalStatus")
    if "The proposal was incomplete" in content or "director reply was incomplete" in content.lower():
        status = "incomplete"
    if status == "incomplete":
        result.setdefault("originalContent", content)
        result.update(content=INCOMPLETE_MESSAGE, actions=[], proposalStatus="incomplete", applied=False)
    elif status == "rejected":
        result.setdefault("originalContent", content)
        result.update(content=REJECTED_MESSAGE + (" " + content if content and content != REJECTED_MESSAGE and not result.get("originalContent") else ""), actions=[], proposalStatus="rejected", applied=False)
    else:
        result["actions"] = actions
        result["proposalStatus"] = "applied" if result.get("applied") else "ready" if actions else "no_changes"
    return result


def response_turn(response, source):
    return normalize_turn({"role": "assistant", "content": response.get("message") or response.get("reply") or "Regissøren svarte uten tekst.", "actions": response.get("actions", []), "proposalStatus": response.get("proposalStatus"), "source": source,"errors":response.get("errors",[]),"traceId":response.get("traceId"),"attempts":response.get("attempts"),"repaired":response.get("repaired",False),"traceWriteError":response.get("traceWriteError")})


def proposal_summary(actions):
    count = sum(a.get("tool") == "add_shot" for a in actions if isinstance(a, dict))
    return f"Forslag · {count} nye klipp · ikke lagret ennå" if count else f"Forslag · {len(actions)} endringer · ikke lagret ennå"


def authoring_turn(result):
    completed, total = result.get("completed", 0), result.get("total", 0)
    saved = bool(completed or result.get("setupDone"))
    if result.get("status") == "done":
        content = f"Manuset er opprettet og lagret i prosjektet: {completed} nye klipp med filmidé og filmbibel. Du kan lese og redigere dem i Manus og Storyboard."
    else:
        content = f"Manusarbeidet stoppet. {completed} av {total} nye klipp er lagret. "
        if result.get("setupDone"):
            content += "Filmideen og filmbibelen er lagret. "
        content += result.get("error", "Du kan fortsette senere.")
    return {"role":"assistant", "content":content, "actions":[], "applied":saved,
            "proposalStatus":"applied" if saved else "rejected", "authoringStatus":result.get("status")}


def rebase_created_execution(session, index, result):
    """An actual create receipt binds this executed turn to its new project."""
    target=result.get("slug")
    messages=session.get("messages",[])
    if not isinstance(target,str) or not 0 <= index < len(messages): return False
    if not any(item.get("tool")=="create_project" and item.get("slug")==target for item in result.get("results",[]) if isinstance(item,dict)):
        return False
    turn=messages[index]
    if turn.get("sourceSlug")==target and not turn.get("stale"): return False
    from studio_state import authoring_digest
    turn["sourceSlug"]=target
    if isinstance(result.get("board"),dict): turn["source"]=authoring_digest(result["board"])
    turn.pop("stale",None); turn.pop("staleReason",None)
    if turn.get("proposalStatus")=="stale": turn["proposalStatus"]="applied" if turn.get("applied") else "ready"
    return True

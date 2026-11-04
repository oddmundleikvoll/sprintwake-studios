"""Honest proposal state, including historical truncated director replies."""
from __future__ import annotations

import copy

INCOMPLETE_MESSAGE = "The director response was interrupted. No script changes or shots were created from this response. Request a smaller batch, with up to six shots at a time."
REJECTED_MESSAGE = "The proposal could not be used. No changes were applied to the project."


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
    return normalize_turn({"role": "assistant", "content": response.get("message") or response.get("reply") or "The director returned no text.", "actions": response.get("actions", []), "proposalStatus": response.get("proposalStatus"), "source": source,"errors":response.get("errors",[]),"traceId":response.get("traceId"),"attempts":response.get("attempts"),"repaired":response.get("repaired",False),"traceWriteError":response.get("traceWriteError")})


def proposal_summary(actions):
    count = sum(a.get("tool") == "add_shot" for a in actions if isinstance(a, dict))
    return f"Proposal · {count} new shots · not saved yet" if count else f"Proposal · {len(actions)} changes · not saved yet"


def authoring_turn(result):
    completed, total = result.get("completed", 0), result.get("total", 0)
    saved = bool(completed or result.get("setupDone"))
    if result.get("status") == "done":
        content = f"Script created and saved in the project: {completed} new shots with film idea and film bible. You can read and edit them in Script and Storyboard."
    else:
        content = f"Script work stopped. {completed} of {total} new shots are saved. "
        if result.get("setupDone"):
            content += "Film idea and film bible are saved. "
        content += result.get("error", "You can continue later.")
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

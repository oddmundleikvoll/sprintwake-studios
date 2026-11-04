"""Reviewed, revision-bound execution of Raven director actions.

This module is intentionally boring: the director proposes flat actions, this
class re-reads the canonical board and catalogues, validates them again, then
performs only the corresponding owned API operation.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import quote

from director_contract import validate_actions, ANCHORS, editorial_context
from director_editorial import EDITORIAL_TOOLS, READ_ONLY_TOOLS, inspection_result
from production_model import apply_proposals, film_action
from studio_state import authoring_digest


_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_BOARD_MUTATING = {"add_shot", "edit_project", "add_character", "update_character",
                   "edit_bible", "edit_shot", "assign_image",
                   "rewrite_text"}
_DIGEST_MUTATING = _BOARD_MUTATING | {"write_manuscript", "rename_project", "archive_project", "adopt_library_image", "generate_image", "render_seed_takes",
                                     "assign_image", "publish_image", "adopt_seed_take", "refine_shot",
                                     "prepare_dialogue", "dub_dialogue", "generate_soundtrack", "assemble"}


class DirectorExecutor:
    def __init__(self, backend: Any, workspace: Any, library: Any):
        self.backend, self.workspace, self.library = backend, workspace, library

    def _board(self, slug: str) -> dict[str, Any]:
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
            raise ValueError("Invalid project ID")
        board = self.backend.get(f"/api/boards/{quote(slug, safe='-_.~')}")
        if isinstance(board, dict) and isinstance(board.get("board"), dict):
            board = board["board"]
        if not isinstance(board, dict):
            raise ValueError("The backend did not return a storyboard")
        return copy.deepcopy(board)

    def _projects(self) -> list[dict[str, Any]]:
        result = self.backend.get("/api/boards")
        values = result.get("boards", []) if isinstance(result, dict) else result
        return values if isinstance(values, list) else []

    def _context(self, slug: str | None) -> dict[str, Any]:
        projects = self._projects()
        board = self._board(slug) if slug else None
        film_bible = (board or {}).get("filmBible") or {}
        images = []
        if slug:
            images = [{"id":"project:"+key, **value} for key,value in self._project_images(slug,board).items()]
            history = self.backend.get(f"/api/images?slug={quote(slug, safe='-_.~')}")
            images += [{**item,"id":"generated:"+str(item["id"])} for item in history.get("images",[]) if isinstance(item,dict) and item.get("id")]
        shared = [dict(item, id="shared:" + str(item.get("id"))) for item in (self.library.catalogue() if hasattr(self.library, "catalogue") else []) if item.get("id")]
        return {"project": board, "projects": projects, "shots": (board or {}).get("shots", []),
                "characters": (board or {}).get("characters", []), "images": images,
                "sharedLibrary": shared, "locations": (board or {}).get("locations", film_bible.get("locations", [])),
                "props": (board or {}).get("props", film_bible.get("props", []))}

    def _project_images(self, slug, board=None):
        return ANCHORS.image_catalog(board if board is not None else self._board(slug), self.workspace.projects_dir)

    def _film_state(self, slug: str) -> dict[str, Any]:
        state = self.backend.get(f"/api/film/status?slug={quote(slug, safe='-_.~')}")
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _result(message: str, *, results=None, **extra) -> dict[str, Any]:
        return {"message": message, "results": results or [], "operationStatus": "done", **extra}

    def _fresh_validate(self, actions: list[dict[str, Any]], slug: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        context = self._context(slug)
        clean, errors = validate_actions(actions, context, slug=slug,
                                         known_slugs={str(x.get("slug")) for x in context["projects"] if isinstance(x, dict) and x.get("slug")},
                                         has_project=bool(slug))
        if errors:
            raise ValueError("; ".join(errors))
        # A second read is deliberate: IDs and board revision must be fresh at execution time.
        return clean, self._context(slug)

    def _check_digest(self, board: dict[str, Any], expected: str | None, actions: list[dict[str, Any]]) -> None:
        if not any(a.get("tool") in _DIGEST_MUTATING for a in actions):
            return
        actual = authoring_digest(board)
        if not expected:
            raise ValueError("This script change requires expected_digest")
        if expected != actual:
            raise ValueError("Project changed after the director proposed this action")

    def _save_board(self, slug: str, old: dict[str, Any], candidate: dict[str, Any]) -> Any:
        self.workspace.snapshots(slug, old)
        result=self.backend.put(f"/api/boards/{quote(slug, safe='-_.~')}", candidate,
                                expected_digest=authoring_digest(old),
                                expected_updated_at=old.get("updatedAt"))
        self.workspace.sync_script(slug,self._board(slug))
        return result

    def _apply_board_actions(self, slug: str, board: dict[str, Any], actions: list[dict[str, Any]], expected: str | None) -> dict[str, Any]:
        original = copy.deepcopy(board)
        self._check_digest(original, expected, actions)
        transformed = []
        direct_changed = False
        for action in actions:
            tool = action["tool"]
            if tool == "add_shot":
                fields = {k: v for k, v in action.items() if k != "tool"}
                transformed.append({"tool": "add_shot", "reviewed": True, "fields": fields})
            elif tool == "edit_shot":
                shot = next((s for s in board.get("shots", []) if s.get("id") == action["shotId"]), None)
                if not shot: raise ValueError("Unknown shot")
                if shot.get("locked"): raise ValueError("Shot is locked")
                transformed.append({"tool": "update_shot", "reviewed": True, "shotId": action["shotId"], "fields": {k: v for k, v in action.items() if k not in {"tool", "shotId"}}})
            elif tool == "assign_image":
                image_ref = self._materialize_ref(slug, action["imageId"])
                target = action["target"]
                if target in {"opening", "closing"}:
                    shot = next((s for s in board.get("shots", []) if s.get("id") == action.get("shotId")), None)
                    if not shot: raise ValueError("Unknown shot")
                    if shot.get("locked"): raise ValueError("Shot is locked")
                    field = {"opening": "startRef", "closing": "endRef"}[target]
                    transformed.append({"tool": "update_shot", "reviewed": True, "shotId": action["shotId"], "fields": {field: image_ref}})
                else:
                    entity = action.get("entityId")
                    collection = {"character": "characters", "location": "locations", "prop": "props"}[target]
                    bible = board.get("filmBible") if isinstance(board.get("filmBible"), dict) else {}
                    items = board.get(collection, []) or bible.get(collection, [])
                    item = next((x for x in items if x.get("id") == entity), None)
                    if not item: raise ValueError("Unknown image entity")
                    item["image" if target == "character" else "referenceImage"] = image_ref
                    direct_changed = True
            elif tool == "edit_project":
                transformed.append({"tool": "set_board_fields", "reviewed": True, "fields": action.get("fields", {})})
            elif tool == "edit_bible":
                transformed.append({"tool": "set_board_fields", "reviewed": True, "fields": {"filmBible": action["fields"]}})
            elif tool in {"add_character", "update_character"}:
                fields={k:v for k,v in action.items() if k not in {"tool","characterId","imageId","voiceId"}}
                if action.get("imageId"):
                    fields["image"]=self._materialize_ref(slug,action["imageId"])
                if action.get("voiceId"):
                    raise ValueError("Select an actual voice recording in the media library before dubbing.")
                transformed.append({"tool":tool,"reviewed":True,
                                    **({"character":fields} if tool == "add_character" else {"characterId":action["characterId"],"fields":fields})})
            elif tool == "rewrite_text":
                if action.get("scope") == "shot":
                    transformed.append({"tool": "update_shot", "reviewed": True, "shotId": action.get("shotId"), "fields": {"prompt": action["text"]}})
                else:
                    transformed.append({"tool": "set_board_fields", "reviewed": True, "fields": {"sceneDescription": action["text"]}})
            else:
                continue
        if not transformed:
            candidate = copy.deepcopy(board)
            if direct_changed:
                self._save_board(slug, original, candidate)
                return self._board(slug)
            return candidate
        candidate = apply_proposals(board, transformed)
        self._save_board(slug, original, candidate)
        return self._board(slug)

    def _image_id(self, image_id: str) -> tuple[str, Path]:
        record = self._image_record(image_id)
        record_id = str(record.get("id") or image_id)
        try:
            path = self.library.resolve(record_id) if hasattr(self.library, "resolve") else None
        except (ValueError, OSError):
            path = None
        if path is None:
            ref = record.get("ref") or record.get("path") or record.get("image") or record.get("url")
            if isinstance(ref, dict):
                ref = ref.get("path") or ref.get("url") or ref.get("ref")
            path = self.workspace.local_media(ref) if hasattr(self.workspace, "local_media") else None
        if path is None or not Path(path).is_file(): raise ValueError("Image ID does not point to an actual image")
        return record_id, Path(path)

    def _image_record(self, image_id: str) -> dict[str, Any]:
        if image_id.startswith("shared:"):
            record=next((x for x in self.library.catalogue() if x["id"] == image_id[7:]),None)
        elif image_id.startswith("project:"):
            record=self._project_images(self._active_slug).get(image_id[8:])
            if record: record={"id":image_id[8:],**record}
        elif image_id.startswith("generated:"):
            values=self.backend.get(f"/api/images?slug={quote(self._active_slug)}").get("images",[])
            record=next((x for x in values if str(x.get("id")) == image_id[10:] and x.get("status") in {"done","complete","completed"}),None)
        else:
            record=None
        if not record: raise ValueError("Unknown or unfinished image ID")
        return record

    def _image_ref(self, image_id: str) -> Any:
        record = self._image_record(image_id)
        ref=record.get("ref") or {"path":record.get("path"),"url":record.get("url")}
        if not isinstance(ref,dict) or not self.workspace.local_media(ref.get("path") or ref.get("url")):
            raise ValueError("The image reference is missing from the project")
        return copy.deepcopy(ref)

    def _materialize_ref(self, slug: str, image_id: str) -> Any:
        """Return the server's canonical ref object, adopting shared media first."""
        record = self._image_record(image_id)
        if image_id.startswith("shared:"):
            bare = str(record.get("id") or image_id.removeprefix("shared:"))
            adopted = self.library.adopt(slug, bare, self.backend)
            if isinstance(adopted, dict):
                return copy.deepcopy(adopted.get("ref") or adopted.get("reference") or adopted)
        return self._image_ref(image_id)

    def execute(self, actions: list[dict[str, Any]], slug: str | None = None, expected_digest: str | None = None,
                on_progress: Callable[[dict[str, Any]], None] | None = None, cancel: Any = None,
                *, resume_only: bool = False) -> dict[str, Any]:
        if not isinstance(actions, list) or not actions: raise ValueError("actions must be a non-empty list")
        if slug is not None and not _SLUG.fullmatch(slug): raise ValueError("Invalid project ID")
        if any(a.get("tool") == "create_project" for a in actions) and slug and not any(a.get("newProject") for a in actions):
            raise ValueError("create_project must explicitly be newProject")
        create_and_write=[a.get("tool") for a in actions]==["create_project","write_manuscript"]
        if len(actions)>1 and not create_and_write and not all(a.get("tool") in _BOARD_MUTATING for a in actions) and not all(a.get("tool") in READ_ONLY_TOOLS for a in actions):
            raise ValueError("Production and project actions must be executed separately")
        clean, context = self._fresh_validate(actions, slug)
        self._active_slug = slug
        if cancel is not None and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()):
            return self._result("Cancelled", operationStatus="cancelled")
        board = context.get("project")
        self._check_digest(board, expected_digest, clean) if board else None
        if board and any(item.get("tool") in _DIGEST_MUTATING for item in clean) and not expected_digest:
            raise ValueError("This production change requires expected_digest")
        expected_token = expected_digest
        results: list[dict[str, Any]] = []
        editorial_state = None
        current_slug = slug
        if current_slug and board is not None and clean and all(item.get("tool") in _BOARD_MUTATING for item in clean):
            board = self._apply_board_actions(current_slug, board, clean, expected_token)
            return self._result("Script changes saved.", results=[{"tool": item["tool"], "slug": current_slug} for item in clean], slug=current_slug, board=board)
        for index, action in enumerate(clean, 1):
            if on_progress: on_progress({"completed": index - 1, "total": len(clean), "tool": action["tool"]})
            tool = action["tool"]
            if tool == "create_project":
                response = self.backend.post("/api/boards", {"name": action["name"]})
                current_slug = response.get("slug") if isinstance(response, dict) else None
                if not current_slug: raise ValueError("The backend did not create the project")
                board = response.get("board", {}) if isinstance(response, dict) else {}
                original=copy.deepcopy(board)
                if action.get("brief"):
                    board["sceneDescription"] = action["brief"]
                if action.get("style"):
                    board["renderStyle"] = action["style"]
                self.workspace.ensure_project(current_slug)
                self._save_board(current_slug,original,board)
                board=self._board(current_slug)
                expected_token=authoring_digest(board)
                if hasattr(self.workspace, "sync_script"):
                    self.workspace.sync_script(current_slug, board)
                results.append({"tool": tool, "slug": current_slug, "board": board})
            elif tool == "rename_project":
                if not current_slug: raise ValueError("A project is required")
                response = self.backend.post(f"/api/boards/{quote(current_slug, safe='-_.~')}/rename", {"name": action["name"]})
                current_slug = response.get("slug", current_slug) if isinstance(response, dict) else current_slug
                board = response.get("board", board) if isinstance(response, dict) else board
                results.append({"tool": tool, **(response if isinstance(response, dict) else {})})
            elif tool in {"set_film_anchor", "set_shot_anchors"}:
                if not current_slug: raise ValueError("A project is required")
                state = self._film_state(current_slug)
                key="anchorRevision" if tool == "set_shot_anchors" else "revision"
                revision=action[key]
                actual=state.get("anchorRevision") if tool == "set_shot_anchors" else (state.get("project") or {}).get("revision")
                if revision != actual: raise ValueError("Film revision is stale")
                inner={k:v for k,v in action.items() if k != "anchorRevision"}
                inner.update(slug=current_slug,revision=revision)
                if tool == "set_shot_anchors":
                    inner["anchors"]=[{**item,"imageId":item["imageId"].removeprefix("project:")} for item in action["anchors"]]
                response = self.backend.post("/api/film/action", {"slug": current_slug, "action": inner})
                results.append({"tool": tool, "response": response, "operationStatus": "done"})
            elif tool == "write_manuscript":
                if not current_slug or board is None: raise ValueError("A project is required for script writing")
                self._check_digest(board, expected_token, [action])
                from director_authoring import author_project
                # resume_only is an internal UI intent, deliberately not read
                # from the model-provided action payload.
                authored = author_project(self.backend, self.workspace, current_slug, action["brief"], action.get("count", 1), expected_digest=expected_token, cancel=cancel, on_progress=on_progress, resume_only=resume_only)
                board = authored.get("board") or self._board(current_slug)
                expected_token = authoring_digest(board)
                results.append({"tool": tool, "board": board, "authoring": authored,"brief":action["brief"],"count":action.get("count",1)})
                if authored.get("status") not in {None, "done"}:
                    return self._result(f"Project saved. {authored.get('completed',0)} of {authored.get('total',0)} new shots saved. Script writing stopped: {authored.get('error','cancelled')}", results=results, slug=current_slug, board=board, operationStatus=authored.get("status"))
            elif tool in _BOARD_MUTATING:
                if not current_slug or board is None: raise ValueError("A project is required")
                board = self._apply_board_actions(current_slug, board, [action], expected_token)
                expected_token = authoring_digest(board)
                results.append({"tool": tool, "slug": current_slug, "board": board})
            elif tool == "open_project":
                current_slug = action["slug"]; board = self._board(current_slug); results.append({"tool": tool, "slug": current_slug, "board": board})
            elif tool == "archive_project":
                state = self._film_state(current_slug) if current_slug else {}
                image_state = self.backend.get(f"/api/images?slug={quote(current_slug, safe='-_.~')}") if current_slug else {}
                if state.get("active") or (state.get("project") or {}).get("busy") or ((image_state.get("job") or {}).get("busy") if isinstance(image_state, dict) else False):
                    raise ValueError("The project has an active job")
                archived = self.workspace.archive(current_slug)
                current_slug, board = None, None
                results.append({"tool": tool, "artifact": str(archived), "slug": None})
            elif tool == "list_library":
                values = self.library.catalogue(); query = action.get("query", ""); results.append({"tool": tool, "items": [x for x in values if not query or query.lower() in json.dumps(x, ensure_ascii=False).lower()]})
            elif tool in EDITORIAL_TOOLS:
                if editorial_state is None:
                    editorial_state = editorial_context(self.workspace.projects_dir, current_slug, board)
                results.append(inspection_result(tool, editorial_state))
            elif tool == "adopt_library_image":
                image_id, path = self._image_id(action["imageId"]); results.append({"tool": tool, "imageId": image_id, "response": self.library.adopt(current_slug, image_id, self.backend)})
            elif tool == "publish_image":
                image_id, path = self._image_id(action["imageId"])
                artifact = self.workspace.publish_image(current_slug, path)
                library_result = self.library.import_images({"published": artifact}) if hasattr(self.library, "import_images") else None
                results.append({"tool": tool, "imageId": image_id, "artifact": str(artifact), "library": library_result})
            elif tool == "generate_image":
                refs = [self._materialize_ref(current_slug,x) for x in action.get("referenceIds", [])]
                dimensions = {"16:9": (1024, 576), "9:16": (576, 1024), "1:1": (768, 768), "4:3": (1024, 768), "3:2": (768, 512), "2:3": (512, 768), "21:9": (1344, 576)}
                width, height = dimensions.get(action.get("aspect"), (1024, 576))
                response = self.backend.post("/api/images", {"slug": current_slug, "prompt": action["prompt"], "references": refs, "aspect": action.get("aspect") or "16:9", "width": width, "height": height, "seed": action.get("seed")})
                job = (response or {}).get("job") if isinstance(response, dict) else None
                results.append({"tool": tool, "jobId": (job or {}).get("jobId") if isinstance(job, dict) else None, "response": response, "operationStatus": "queued" if isinstance(response, dict) and response.get("accepted") else "done"})
            elif tool == "list_seed_takes":
                results.append({"tool": tool, "takes": self.backend.get(f"/api/boards/{current_slug}/shots/{action['shotId']}/seed-takes")})
            elif tool == "adopt_seed_take":
                response = self.backend.post(f"/api/boards/{current_slug}/shots/{action['shotId']}/seed-takes/{action['seed']}/adopt", {}); board = response.get("board", board) if isinstance(response, dict) else board; results.append({"tool": tool, "response": response, "board": board})
            elif tool == "export_project":
                artifact = self.workspace.export(current_slug); results.append({"tool": tool, "artifact": str(artifact)})
            elif tool in {"review_production", "review_clip"}:
                results.append({"tool": tool, "navigate": "production-review", "needsReview": True, "operationStatus": "review_required"})
            elif tool == "reveal":
                target=action.get("target","project")
                roots={"root":self.workspace.root,"project":self.workspace.projects_dir/current_slug if current_slug else self.workspace.root,
                       "library":self.workspace.root/"Library"}
                if current_slug:
                    roots.update({key:self.workspace.projects_dir/current_slug/value for key,value in {"script":"Script","input":"Input","output":"Output"}.items()})
                if target not in roots: raise ValueError("Choose a known production folder")
                results.append({"tool":tool,"artifact":str(roots[target])})
            elif tool == "status":
                state=self._film_state(current_slug) if current_slug else {}
                project=state.get("project") or {}
                images=self.backend.get("/api/images?slug="+current_slug) if current_slug else {}
                job=images.get("job") or {}
                text="No project selected. The library is available." if not current_slug else f"Film: {project.get('phase') or project.get('status') or 'ready'}. Image job: {job.get('phase') or ('working' if job.get('busy') else 'no active job')}."
                results.append({"tool":tool,"status":state,"images":images,"statusText":text})
            elif tool == "open_panel":
                results.append({"tool":tool,"navigate":action["panel"]})
            else:
                endpoint = {"prepare_dialogue": "/api/prepare-dialogue", "dub_dialogue": f"/api/boards/{current_slug}/shots/{action.get('shotId')}/dub", "generate_soundtrack": f"/api/boards/{current_slug}/soundtrack", "prepare_film": "/api/film", "resume_film": "/api/film", "stop_film": "/api/film/stop", "stop_image": "/api/images/stop", "render_seed_takes": "/api/render-seeds", "refine_shot": "/api/refine", "assemble": "/api/assemble"}.get(tool)
                if not endpoint: raise ValueError("Tool is missing a production connection")
                payload = {**action, "slug": current_slug}
                if tool == "assemble":
                    project=self._film_state(current_slug).get("project") or {}
                    if project.get("busy") or project.get("imageApprovalRequired") or project.get("reviewRequired"):
                        raise ValueError("Complete the review in Production before assembling the film")
                if tool in {"prepare_film","resume_film"}:
                    state=self._film_state(current_slug)
                    project=state.get("project") or {}
                    inner=film_action(state,"prepare" if tool == "prepare_film" else "produce")
                    if action["revision"] != inner["revision"]: raise ValueError("Film revision is stale")
                    if tool == "resume_film" and (project.get("imageApprovalRequired") or project.get("reviewRequired")):
                        results.append({"tool":tool,"navigate":"production","needsReview":True,"operationStatus":"review_required"})
                        continue
                    response=self.backend.post("/api/film/action",{"slug":current_slug,"action":inner},timeout=1200)
                elif tool == "stop_film":
                    active=self._film_state(current_slug).get("active") or {}
                    if active.get("jobId") != action["jobId"] or active.get("slug") != current_slug: raise ValueError("The film job is no longer active")
                    response=self.backend.post("/api/film/stop",{"slug":current_slug,"jobId":action["jobId"]})
                else:
                    payload.pop("tool",None)
                    if tool == "refine_shot": payload.update(draft=True,maxAttempts=2,passPercent=90)
                    if tool == "generate_soundtrack" and action.get("prompt"):
                        candidate=copy.deepcopy(board); candidate["soundtrackPrompt"]=action["prompt"]
                        self._save_board(current_slug,board,candidate); board=self._board(current_slug)
                    response=self.backend.post(endpoint,payload,timeout=1200)
                item={"tool":tool,"response":response,"jobId":response.get("jobId") if isinstance(response,dict) else None}
                if tool in {"prepare_film","resume_film","refine_shot","render_seed_takes","prepare_dialogue"}:
                    item["operationStatus"]="queued"
                if tool == "assemble":
                    source=self.workspace.projects_dir/current_slug/"final.mp4"
                    if not source.is_file(): raise ValueError("Assembly did not confirm an actual movie")
                    item["artifact"]=str(self.workspace.publish_video(current_slug,source))
                results.append(item)
        if on_progress: on_progress({"completed": len(clean), "total": len(clean), "detail": "ferdig"})
        status="review_required" if any(x.get("needsReview") for x in results) else "queued" if any(x.get("operationStatus")=="queued" or (x.get("response") or {}).get("accepted") for x in results) else "done"
        message="Review production before the next step." if status=="review_required" else "Job started. Follow progress in the production overview." if status=="queued" else "Actions completed."
        written=next((x for x in results if x.get("tool")=="write_manuscript"),None)
        if written:
            authored=written["authoring"]
            scenes=len((board.get("filmBible") or {}).get("acts",[]))
            message=f"Script saved: {authored.get('completed',0)} new shots and {scenes} scenes/acts in «{board.get('name',current_slug)}»."
        if any(x.get("tool") in {"stop_image","stop_film"} for x in results):
            message="Stop requested. Follow the status until the job releases its resources."
        read_only = all(item["tool"] in READ_ONLY_TOOLS for item in clean)
        if read_only:
            message = "\n".join(item["statusText"] for item in results if item.get("statusText")) or "Information retrieved."
        if current_slug:
            board=self._board(current_slug)
            if not read_only:
                self.workspace.sync_script(current_slug,board)
        return self._result(message, results=results, slug=current_slug, board=board,operationStatus=status)

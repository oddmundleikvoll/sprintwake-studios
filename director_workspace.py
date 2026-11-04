"""Project-aware native director conversations and reviewed tool execution."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import threading
import time

import raven as r

from conversations import ConversationStore
from director_executor import DirectorExecutor
from director_contract import PANEL_NAMES
from director_editorial import EDITORIAL_TOOLS, READ_ONLY_TOOLS
from director_state import normalize_turn, rebase_created_execution
from project import Project
from studio_state import authoring_digest
from workspace import _atomic_text
import views as v


LABELS = {
    "create_project":"Create project", "open_project":"Open project", "rename_project":"Rename project",
    "archive_project":"Archive project", "edit_project":"Update film idea and settings", "edit_bible":"Update film bible",
    "add_character":"Add character", "update_character":"Update character", "add_shot":"Add shot",
    "edit_shot":"Update shot", "write_manuscript":"Create and save script", "list_library":"Browse library",
    "adopt_library_image":"Copy image into project", "generate_image":"Create image", "stop_image":"Stop image job",
    "assign_image":"Use image", "publish_image":"Publish image", "list_seed_takes":"View take history",
    "adopt_seed_take":"Choose take", "render_seed_takes":"Create take variants", "refine_shot":"Refine take",
    "prepare_dialogue":"Prepare dialogue", "dub_dialogue":"Create dialogue take", "generate_soundtrack":"Create music",
    "prepare_film":"Prepare storyboard", "resume_film":"Resume production", "stop_film":"Stop film job",
    "set_film_anchor":"Change continuation anchor", "set_shot_anchors":"Update image anchors", "assemble":"Assemble film",
    "export_project":"Export project", "review_clip":"Review take", "rewrite_text":"Update text",
    "reveal":"Show in Finder", "open_panel":"Open workspace", "status":"Read production status",
    "review_production":"Review production",
    "inspect_cut":"Inspect cut", "inspect_coverage":"Inspect room plan",
    "inspect_structure":"Inspect structure and runtime", "inspect_sound":"Inspect sound tracks",
}


class DirectorWorkspaceMixin:
    def initialize_conversations(self):
        self.conversations = ConversationStore(self.workspace)
        self.conversations.recover_interrupted()
        self.director_executor = DirectorExecutor(self.backend,self.workspace,self.library)
        self.conversation_updating = False
        self.pending_conversation_id = None
        self.project_destination = "overview"
        self.director_starting = False
        self.chat_starting = False
        self.chat_origin = None
        self.director_cancel = None
        self.composer_checkpoint = None
        self.composer_deadline = 0
        self.director_ui_path = self.workspace.system_dir / "director-ui.json"
        for item in self.summaries:
            try:
                migrated=self.conversations.migrate_legacy(item["slug"])
                if migrated and not migrated.get("legacyComposerImported"):
                    draft=self.drafts.get(item["slug"],{}).get("message")
                    if draft and not (migrated.get("composer") or {}).get("text"):
                        migrated["composer"]={"text":draft,"thinking":0}
                    migrated["legacyComposerImported"]=True
                    self.conversations.save(migrated)
            except (OSError, ValueError):
                pass
        for summary in self.conversations.list(include_archived=True):
            session=self.conversations.load(summary["id"])
            changed=False
            for i,message in enumerate(session.get("messages",[])):
                if isinstance(message,dict):
                    result=(message.get("execution") or {}).get("result") or {}
                    changed=rebase_created_execution(session,i,result) or changed
            if changed: self.conversations.save(session)
        chosen = None
        try:
            saved = json.loads(self.director_ui_path.read_text())
            chosen = self.conversations.load(saved["activeConversationId"])
            if chosen.get("archived"): chosen = None
        except (OSError,ValueError,KeyError):
            pass
        if not chosen:
            general = self.conversations.list(None)
            chosen = self.conversations.load(general[0]["id"]) if general else self.conversations.create()
        self.current_conversation = chosen
        self.history = [normalize_turn(x) for x in chosen["messages"] if isinstance(x,dict)]
        self.restore_composer()

    def _remember_conversation(self):
        _atomic_text(self.director_ui_path,json.dumps({"activeConversationId":self.current_conversation["id"]})+"\n")

    def restore_composer(self):
        composer = self.current_conversation.get("composer") or {}
        self.set_text("director.message",composer.get("text", ""))
        r.Rcombo_box_set_selected(self.find("director.thinking"),max(0,min(2,int(composer.get("thinking",0)))))
        self.composer_checkpoint = (self.text("director.message"),self.selected("director.thinking"))

    def capture_session_draft(self):
        if not getattr(self,"live",False) or not getattr(self,"current_conversation",None): return
        composer = (self.text("director.message"),self.selected("director.thinking"))
        if composer == self.composer_checkpoint: return
        self.current_conversation["composer"] = {"text":composer[0],"thinking":composer[1]}
        self.persist_conversation()
        self.composer_checkpoint = composer

    def persist_conversation(self):
        if not getattr(self,"current_conversation",None): return
        self.current_conversation["messages"] = self.history
        self.conversations.save(self.current_conversation)
        self._remember_conversation()
        if self.mode == "director": self.render_conversation_sidebar()

    def load_conversation(self):
        target = self.pending_conversation_id
        self.pending_conversation_id = None
        if target:
            session = self.conversations.load(target)
        else:
            previous = self.conversations.list(self.slug)
            session = self.conversations.load(previous[0]["id"]) if previous else self.conversations.create(self.slug)
        self.current_conversation = session
        self.history = [normalize_turn(x) for x in session["messages"] if isinstance(x,dict)]
        self.restore_composer()
        self._remember_conversation()
        self.render_conversation()
        self.set_label("director.status","Ready when you are.")

    def _free_project(self):
        self.epoch += 1
        self.player.close(); self.active_video=""; self.playing=False
        self.slug=""; self.board=None; self.images=[]; self.image_refs=[]; self.catalogue=[]
        self.selected_image=None; self.film_context={}; self.image_state={}; self.gate_key=None
        self.project=Project("empty","Raven Studio","Begin with the director",[])
        self.projects=[self.project]; self.index=0; self.dirty=False
        self.populate_project()

    def activate_conversation(self, identifier):
        if self.director_starting or "director.execute" in self.pending or "director.author" in self.pending:
            self.toast("Let the active action finish before switching conversations."); return
        if self.dirty:
            self.save(after=lambda:self.activate_conversation(identifier)); return
        self.capture_session_draft()
        session=self.conversations.load(identifier)
        if session.get("archived"): raise ValueError("Conversation is archived.")
        if session.get("unread"):
            session["unread"]=False
            self.conversations.save(session)
        self.current_conversation=session
        self.history=[normalize_turn(x) for x in session["messages"] if isinstance(x,dict)]
        self.restore_composer(); self._remember_conversation()
        target=session.get("projectSlug")
        if target and target != self.slug:
            if not self.ready:
                self.render_conversation(); return
            self.pending_conversation_id=identifier
            self.project_destination="director"
            self.open_project(target)
        else:
            if not target and self.slug: self._free_project()
            self.mode_set("director")
        self.set_label("director.status","The director is working in another conversation." if "chat" in self.pending and self.chat_origin != identifier else "Ready when you are.")

    def new_conversation(self, general=False):
        self.capture_session_draft()
        session=self.conversations.create(None if general else self.slug or None)
        self.activate_conversation(session["id"])
        r.Rwidget_focus(self.find("director.message"))

    def select_conversation_project(self):
        if self.conversation_updating: return
        index=self.selected("conversation.project")
        target=self.summaries[index-1]["slug"] if 0 < index <= len(self.summaries) else None
        self.capture_session_draft()
        identifier=self.current_conversation["id"]
        self.conversations.bind(identifier,target)
        self.activate_conversation(identifier)

    def rename_conversation(self, apply=False):
        if apply:
            title=self.text("conversation.title").strip()
            if not title: raise ValueError("Conversation needs a name.")
            self.current_conversation["title"]=title[:80]
            self.persist_conversation(); self.modal_close(); self.render_conversation()
        else:
            self.show_modal("Name conversation",v.field("conversation.title","Name",self.current_conversation.get("title",""),36,multiline=False),"conversation.rename.save","Save name")

    def archive_conversation(self):
        identifier=self.current_conversation["id"]
        if self.chat_origin==identifier and ("chat" in self.pending or self.chat_starting):
            raise ValueError("Wait for the director to respond before archiving the conversation.")
        self.capture_session_draft()
        self.conversations.archive(identifier)
        remaining=self.conversations.list()
        self.activate_conversation(remaining[0]["id"] if remaining else self.conversations.create()["id"])

    def render_conversation_sidebar(self):
        if not getattr(self,"current_conversation",None): return
        self.conversation_updating=True
        box=self.find("conversation.project")
        r.Rcombo_box_clear_items(box)
        r.Rcombo_box_add_item(box,"Free idea / new project")
        target=self.current_conversation.get("projectSlug")
        selected=0
        for i,item in enumerate(self.summaries,1):
            r.Rcombo_box_add_item(box,item.get("name",item["slug"]))
            if item["slug"] == target: selected=i
        r.Rcombo_box_set_selected(box,selected)
        self.conversation_updating=False
        names={item["slug"]:item.get("name",item["slug"]) for item in self.summaries}
        cards=[]
        for item in self.conversations.list()[:80]:
            active=item["id"] == self.current_conversation["id"]
            title=item["title"]
            if item.get("unread") or (self.chat_origin == item["id"] and "chat" in self.pending): title="• "+title
            detail=names.get(item.get("projectSlug"),"Free idea")
            cards.append(f'<Button Class="nav{" nav-active" if active else ""}" OnClicked="conversation.open.{item["id"]}" Metric.PaddingX="10" Metric.PaddingY="12"><VBox HitTestable="false" Spacing="7">{v.label(title,13)}{v.label(detail,10,True)}</VBox></Button>')
        self.replace_host("conversation.list",''.join(cards))
        self.set_label("director.title",self.current_conversation.get("title","New conversation"))
        self.set_label("director.context",names.get(target,"An idea is enough. The director can create the project."))
        self.set_attr("director.author.button","Enabled",bool(self.board))
        self.set_attr("director.read.button","Enabled",bool(self.board))
        self.set_attr("director.author.button","Visibility","Visible" if self.board else "Collapsed")
        self.set_attr("director.read.button","Visibility","Visible" if self.board else "Collapsed")
        self.set_attr("director.send","Enabled",self.ready and not self.chat_starting and "chat" not in self.pending and "director.execute" not in self.pending)

    def _action_description(self,action):
        parts=[]
        for key in ("name","brief","prompt","requirement","text"):
            if isinstance(action.get(key),str): parts.append(action[key])
        fields=action.get("fields") or {}
        for key,value in fields.items():
            if isinstance(value,str): parts.append(f"{key}: {value}")
        if action.get("count"): parts.append(f"{action['count']} shots / variants")
        if action.get("target"): parts.append("Use as: "+str(action["target"]))
        return "\n".join(parts)[:1400]

    def render_conversation(self):
        if not getattr(self,"current_conversation",None): return
        self.render_conversation_sidebar()
        parts=[]
        for i,turn in enumerate(self.history):
            if not isinstance(turn,dict): continue
            role="YOU" if turn.get("role")=="user" else "DIRECTOR"
            content=turn.get("content", "")
            detail=""
            execution=turn.get("execution") or {}
            actions=turn.get("actions") or []
            if actions:
                for action in actions:
                    heading=LABELS.get(action.get("tool"),action.get("tool","Proposal"))
                    detail += '<Separator/>'+v.label(heading,16)+v.label(self._action_description(action),12,True)
                status=execution.get("status")
                if turn.get("applied") or status in {"done","queued","review_required"}:
                    detail += v.label(execution.get("message") or "Saved in project",12,True)
                    if execution.get("result"):
                        detail += v.button("View result",f"director.result.{i}")
                        if any(x.get("tool")=="write_manuscript" for x in execution["result"].get("results",[])):
                            detail += v.button("Read script","director.open_script",True)
                elif turn.get("stale") or turn.get("sourceSlug") != (self.slug or None):
                    detail += v.label(execution.get("message") or "Project context changed. Request an updated proposal.",12,True)+v.button("Request updated proposal",f"director.retry.{i}")
                elif status in {"failed","interrupted","cancelled"}:
                    detail += v.label(execution.get("message","The action could not be verified."),12,True)+v.button("Request updated proposal",f"director.retry.{i}")
                    if any(x.get("tool")=="write_manuscript" and x.get("brief") for x in (execution.get("result") or {}).get("results",[])):
                        detail += v.button("Resume script authoring",f"director.resume.{i}",True)
                elif status == "running":
                    detail += v.label("Executing action…",12,True)
                else:
                    label="Create project and write script" if [a.get("tool") for a in actions]==["create_project","write_manuscript"] else "Execute action" if len(actions)==1 else "Apply changes"
                    detail += v.button(label,f"director.apply.{i}",True)
            elif turn.get("applied"):
                detail += v.label("Saved in project",12,True)+v.button("Read script","director.open_script")
            elif turn.get("proposalStatus") in {"incomplete","rejected"}:
                detail += v.label("No action was executed.",12,True)+v.button("Try again",f"director.retry.{i}")
                if turn.get("errors") or turn.get("traceId"):
                    detail += v.button("Show error details",f"director.diagnostics.{i}")
            if turn.get("traceWriteError") and turn.get("proposalStatus") not in {"rejected","incomplete"}:
                detail += v.button("Review diagnostic log",f"director.diagnostics.{i}")
            color="#2B2D28" if role=="YOU" else "#1B2529"
            parts.append(f'<Border Background="{color}" CornerRadius="11" Padding="20"><VBox Spacing="12"><Label Text="{role}" Class="eyebrow"/>{v.label(content,14)}{detail}</VBox></Border>')
        if not parts:
            parts=[v.title("Your director","What do you want to create?","Describe the film, subject, or style. I will help from idea to finished production.")+'<WrapBox Spacing="10">'+v.button("Start with a film idea","director.starter.idea")+v.button("Find images in the library","director.starter.library")+v.button("Plan a scene","director.starter.scene")+'</WrapBox>']
        self.replace_host("director.history",''.join(parts))

    def starter_message(self,kind):
        messages={"idea":"I want to make a film about ","library":"Show me the images in the library.","scene":"Help me shape a scene with clear action and a strong visual style. "}
        self.set_text("director.message",messages.get(kind,""))
        r.Rwidget_focus(self.find("director.message"))

    def _scroll_director(self):
        widget=self.find("director.messages")
        if widget != r.R_INVALID_ID: r.Rscroll_box_scroll_to_end(widget)

    def send_chat(self):
        if not self.ready: raise ValueError("Director engine is still starting.")
        if self.chat_starting or "chat" in self.pending: raise ValueError("The director is still working on the previous message.")
        message=self.text("director.message").strip()
        if not message: raise ValueError("Write a message first.")
        session_id=self.current_conversation["id"]
        slug=self.current_conversation.get("projectSlug")
        if slug != (self.slug or None): raise ValueError("Wait for the project to open.")
        history=[]
        for row in self.history[-40:]:
            if row.get("role") not in {"user","assistant"}: continue
            content=row.get("content","")
            execution=row.get("execution") or {}
            if execution:
                content += "\n[Raven bekrefter: "+str(execution.get("status"))+" · "+str(execution.get("message",""))+"]"
            history.append({"role":row["role"],"content":content})
        thinking=["none","low","high"][max(0,min(2,self.selected("director.thinking")))]
        self.chat_starting=True
        self.history.append({"role":"user","content":message})
        self.set_text("director.message","")
        self.current_conversation["composer"]={"text":"","thinking":self.selected("director.thinking")}
        self.composer_checkpoint=("",self.selected("director.thinking"))
        self.persist_conversation(); self.render_conversation(); self._scroll_director()
        self.chat_origin=session_id
        self.set_label("director.status","The director is reading the project…" if slug else "The director is shaping the idea with you…")
        payload={"slug":slug,"message":message,"history":history,"thinking":thinking,"conversationId":session_id}
        if self.raw_shot: payload["selectedShotId"]=self.raw_shot["id"]
        def progress(event):
            def show(data):
                if self.current_conversation["id"] != session_id: return
                label=data.get("text") if data.get("type") == "progress" else "The director is writing…"
                self.set_label("director.status",label or "The director is working…")
            self.completions.put(("chat.progress",None,show,event,None))
        def operation():
            try:
                digest=None
                if slug:
                    value=self.backend.get('/api/boards/'+slug)
                    digest=authoring_digest(value.get("board",value))
                response=self.backend.raven_chat(payload,progress)
                return {"response":response,"digest":digest}
            except Exception as error:
                return {"error":str(error)}
        def done(result):
            session=self.conversations.load(session_id)
            if result.get("error"):
                turn={"role":"assistant","content":"The director could not complete the response: "+result["error"],"actions":[],"proposalStatus":"error"}
            else:
                response=result["response"]
                turn=normalize_turn({"role":"assistant","content":response.get("message") or "No new changes proposed.","actions":response.get("actions",[]),"proposalStatus":response.get("proposalStatus"),"sourceSlug":slug,"source":result.get("digest"),"catalogVersion":response.get("catalogVersion"),"contextVersion":response.get("contextVersion"),"errors":response.get("errors",[]),"traceId":response.get("traceId"),"attempts":response.get("attempts"),"repaired":response.get("repaired",False),"traceWriteError":response.get("traceWriteError")})
            session["messages"].append(turn)
            session["unread"]=self.current_conversation["id"] != session_id
            self.conversations.save(session)
            self.chat_origin=None
            if self.closing: return
            if self.current_conversation["id"] == session_id:
                self.current_conversation=session; self.history=session["messages"]
                self.render_conversation(); self._scroll_director()
                self.set_label("director.status","Response ready. Actions are shown before execution.")
                if turn.get("actions") and all(a.get("tool") in READ_ONLY_TOOLS for a in turn["actions"]):
                    self.execute_turn(len(self.history)-1)
            else: self.render_conversation_sidebar()
        def launch():
            self.submit("chat",operation,done,scoped=False)
            self.chat_starting=False
            self.render_conversation_sidebar()
        if self.dirty: self.save(after=launch)
        else: launch()

    def apply_chat(self,index):
        self.execute_turn(index)

    def execute_turn(self,index, *, resume_only=False):
        if self.director_starting or "director.execute" in self.pending: return
        if not 0 <= index < len(self.history): return
        turn=self.history[index]
        if turn.get("applied") or (turn.get("execution") or {}).get("status"): return
        if turn.get("stale") or turn.get("sourceSlug") != (self.slug or None):
            raise ValueError("Proposal belongs to another project context. Request a new proposal.")
        actions=copy.deepcopy(turn.get("actions") or [])
        if not actions: return
        session_id=self.current_conversation["id"]
        slug=self.slug or None
        digest=turn.get("source")
        self.director_starting=True
        self.director_cancel=threading.Event()
        cancel=self.director_cancel
        def launch():
            if cancel.is_set():
                self.director_starting=False
                return
            turn["execution"]={"status":"running","at":time.time()}
            self.persist_conversation(); self.render_conversation()
            self.show_modal("Director is executing the action",'<Label Name="director.execution.progress" Text="Checking project and resources…" Wrap="true"/>',"director.cancel","Stop after the current step",allow_close=False)
            def progress(data):
                def show(event): self.set_label("director.execution.progress",event.get("detail") or f"{event.get('completed',0)} / {event.get('total',1)} · working")
                self.completions.put(("director.execution.progress",None,show,data,None))
            def operation():
                try:
                    response=self.director_executor.execute(actions,slug=slug,expected_digest=digest,on_progress=progress,cancel=cancel,resume_only=resume_only)
                    response["projects"]=self.backend.get('/api/boards').get('boards',[])
                    return response
                except Exception as error: return {"error":str(error)}
            def done(result):
                session=self.conversations.load(session_id)
                record=session["messages"][index]
                if result.get("error"):
                    record["execution"]={"status":"failed","message":result["error"]}
                    self.conversations.save(session)
                    if self.closing: return
                    self.current_conversation=session; self.history=session["messages"]
                    self.modal_close(); self.render_conversation()
                    self.toast(result["error"],True); return
                status=result.get("operationStatus","done")
                record["execution"]={"status":status,"message":result.get("message","Action confirmed."),"result":result}
                record["applied"]=status not in {"failed","not_started","cancelled"}
                self.conversations.save(session)
                if self.closing:
                    target=result.get("slug",slug)
                    if target != slug:
                        if slug: self.conversations.rebind_project(slug,target)
                        bound=self.conversations.bind(session_id,target)
                        if rebase_created_execution(bound,index,result): self.conversations.save(bound)
                    return
                self.current_conversation=session; self.history=session["messages"]
                self.summaries=result.get("projects",self.summaries)
                target=result.get("slug",slug)
                self.modal_close()
                if target != slug:
                    if slug:
                        self.conversations.rebind_project(slug,target)
                    session=self.conversations.bind(session_id,target)
                    if rebase_created_execution(session,index,result): self.conversations.save(session)
                    self.current_conversation=session; self.history=session["messages"]
                    self.activate_conversation(session_id)
                elif result.get("board") and target:
                    self.board=result["board"]; self.dirty=False; self.refresh_view(); self.fill_brief()
                self.render_conversation()
                self.poll_deadline=0
                self.toast(result.get("message","Action confirmed."))
                if not self.opening:
                    self.route_director_result(result)
            self.submit("director.execute",operation,done,scoped=False)
            self.director_starting=False
        if self.dirty: self.save(after=launch)
        else: launch()

    def cancel_director(self):
        if self.director_cancel:
            self.director_cancel.set()
            self.set_label("director.execution.progress","Stopping after the current step. Saved results are preserved.")

    def route_director_result(self,result):
        result_shown = False
        for item in result.get("results",[]):
            if item.get("tool")=="list_library":
                self.mode_set("library")
                continue
            if not result_shown and item.get("tool") in READ_ONLY_TOOLS - {"list_library"}:
                index=next((i for i,t in enumerate(self.history) if (t.get("execution") or {}).get("result")==result),None)
                if index is not None:
                    self.show_director_result(index)
                    result_shown = True
            if item.get("tool")=="reveal" and item.get("artifact"):
                self.player.reveal(str(item["artifact"]).encode())
            if item.get("needsReview"):
                self.mode_set("production"); self.poll_deadline=0
            navigate=item.get("navigate")
            if navigate in PANEL_NAMES: self.mode_set(navigate)
            if item.get("tool") == "generate_image":
                self.mode_set("images"); self.poll_deadline=0

    def show_director_result(self,index):
        if not 0 <= index < len(self.history): return
        result=(self.history[index].get("execution") or {}).get("result") or {}
        self.director_result=result
        body=v.label(result.get("message","Result"),15)
        for i,item in enumerate(result.get("results",[])):
            body += v.label(LABELS.get(item.get("tool"),"Result"),18)
            values=item.get("items") or item.get("takes") or []
            if isinstance(values,dict): values=values.get("takes",[])
            for value in values[:25]:
                if isinstance(value,dict): body += v.label(value.get("name") or value.get("label") or ("Seed "+str(value["seed"]) if "seed" in value else str(value.get("id",""))),12,True)
            if item.get("artifact"): body += v.button("Open result",f"director.artifact.{i}")
            if item.get("statusText"): body += v.label(item["statusText"],13,True)
            if item.get("tool") in EDITORIAL_TOOLS:
                panel, title = EDITORIAL_TOOLS[item["tool"]]
                body += v.button("Open " + title, "page." + panel)
            if item.get("jobId"): body += v.label("Job registered. Follow progress in the production overview.",12,True)
        self.show_modal("Director result",body)

    def open_director_artifact(self,index):
        result=getattr(self,"director_result",{})
        items=result.get("results",[])
        if not 0 <= index < len(items): return
        path=Path(items[index].get("artifact","")).resolve()
        if not path.exists() or not path.is_relative_to(self.workspace.root.resolve()):
            raise ValueError("The result is missing from the production folder.")
        if path.suffix.lower() in {".png",".jpg",".jpeg",".webp",".mp4",".mov",".m4v",".wav",".mp3"}: self.preview(path,path.name)
        else: self.player.reveal(str(path).encode())

    def show_director_diagnostics(self,index):
        if not 0 <= index < len(self.history): return
        turn=self.history[index]
        body=v.label("The proposal was checked before any action could be executed.",14)
        for error in turn.get("errors",[]): body+=v.label(str(error),12,True)
        if turn.get("traceWriteError"): body+=v.label("The diagnostic log could not be saved: "+str(turn["traceWriteError"]),12,True)
        if turn.get("traceId"): body+=v.label("Trace ID: "+turn["traceId"],11,True)
        self.show_modal("Director error details",body)

    def resume_director_manuscript(self,index):
        if not 0 <= index < len(self.history) or not self.board: return
        result=(self.history[index].get("execution") or {}).get("result") or {}
        if result.get("slug")!=self.slug: raise ValueError("Open the original project before continuing.")
        item=next((x for x in result.get("results",[]) if x.get("tool")=="write_manuscript" and x.get("brief")),None)
        if not item: return
        self.sync_fields()
        turn={"role":"assistant","content":"Resuming script authoring from the last saved batch.","sourceSlug":self.slug,"source":authoring_digest(self.board),"actions":[{"tool":"write_manuscript","brief":item["brief"],"count":item["count"]}],"internalIntent":"resume_manuscript"}
        self.history.append(normalize_turn(turn)); self.persist_conversation()
        self.execute_turn(len(self.history)-1, resume_only=True)

    def retry_director(self,index):
        message=next((turn.get("content","") for turn in reversed(self.history[:index]) if turn.get("role")=="user"),"")
        self.set_text("director.message",message+"\nReply with a bounded, valid proposal. No change was executed from the previous response.")
        self.mode_set("director"); r.Rwidget_focus(self.find("director.message"))

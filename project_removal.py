"""Discoverable project deletion with a recoverable Raven trash."""
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import views as v


class ProjectRemovalMixin:
    def show_delete_project(self, slug=None):
        slug=slug or self.slug
        if not slug: raise ValueError("Choose the project to delete.")
        if not self.ready: raise ValueError("Wait until the production core is ready.")
        if self.dirty:
            self.save(after=lambda:self.show_delete_project(slug)); return
        if any(key not in {"poll","covers","library.catalogue","take.history"} for key in self.pending):
            raise ValueError("Wait for active work in the app to finish before deleting the project.")
        item=next((x for x in self.summaries if x["slug"]==slug),None)
        if not item: raise ValueError("The project no longer exists.")
        self.delete_target=slug
        self.delete_name=item.get("name",slug)
        name=item.get("name",slug)
        body=v.label(f'«{name}» will be removed from the project overview.',16)
        body+=v.label("Script, images, video, and production history move together to Raven's Trash. You can restore the project from Trash on the project page. Your conversations are preserved.",13,True)
        self.show_modal("Delete project?",body,"project.delete.confirm","Move to Trash")

    def delete_project(self):
        if "project.delete" in self.pending: return
        slug=getattr(self,"delete_target",None)
        if not slug: raise ValueError("Choose the project to delete.")
        self.capture_drafts()
        self.epoch+=1  # Replies polled before deletion cannot restore the old view.
        self.show_modal("Moving project to Trash",v.label("Checking that production is idle…",13,True),allow_close=False)
        def operation():
            try:
                result=self.backend.post("/api/raven/boards/"+quote(slug,safe="")+"/trash",{"confirmName":self.delete_name})
            except Exception as error:
                return {"error":str(error)}
            # The server has committed the move. Local UI metadata cannot undo it.
            warnings=[]
            try: self.conversations.rebind_project(slug,None)
            except Exception as error: warnings.append("conversation binding: "+str(error))
            try: boards=self.workspace.project_summaries()
            except Exception as error:
                boards=[x for x in self.summaries if x["slug"]!=slug]
                warnings.append("project list: "+str(error))
            return {"entry":result["entry"],"boards":boards,"warnings":warnings}
        def done(result):
            if self.closing: return
            if result.get("error"):
                self.show_modal("Could not delete project",v.label(result["error"],13,True)); return
            self.summaries=result["boards"]
            if self.slug==slug: self._free_project()
            try: self.current_conversation=self.conversations.load(self.current_conversation["id"])
            except Exception as error: result["warnings"].append("conversation: "+str(error))
            if self.current_conversation.get("projectSlug")==slug:
                self.current_conversation["projectSlug"]=None
                for message in self.current_conversation["messages"]:
                    if isinstance(message,dict) and message.get("actions") and not message.get("applied"): message["stale"]=True
            self.history=self.current_conversation["messages"]
            self.modal_close(); self.mode_set("home")
            if result["warnings"]:
                self.show_modal("Project moved to Trash",v.label("Production is preserved. Local views need another refresh: "+"; ".join(result["warnings"]),13,True))
            self.toast("Project moved to Trash. You can restore it from there.")
        self.submit("project.delete",operation,done,scoped=False)

    def show_project_trash(self):
        items=self.workspace.trashed_projects()
        body=v.label("Deleted projects",22)
        body+=v.label("The full production is preserved. Restoring puts the project back with the same images, takes, and file references.",13,True)
        for item in items:
            when=datetime.fromtimestamp(item["deletedAt"],ZoneInfo("Europe/Oslo")).strftime("%d.%m.%Y kl. %H:%M")
            body+=f'<Border Padding="16" Background="#1C2528" CornerRadius="9"><HBox Spacing="18"><VBox Slot.Fill="1" Spacing="6">{v.label(item["name"],18)}{v.label("Deleted "+when,12,True)}</VBox>{v.button("Restore", "project.restore."+item["id"])}</HBox></Border>'
        if not items: body+=v.label("Trash is empty.",14,True)
        self.show_modal("Trash",body)

    def restore_deleted_project(self, identifier):
        if "project.restore" in self.pending: return
        if not self.ready: raise ValueError("Wait until the production core is ready.")
        if self.dirty:
            self.save(after=lambda:self.restore_deleted_project(identifier)); return
        self.show_modal("Restoring project",v.label("Putting production back in place…",13,True),allow_close=False)
        def operation():
            try:
                result=self.backend.post("/api/raven/trash/"+quote(identifier,safe="")+"/restore",{})
            except FileExistsError:
                return {"error":"A project with the same ID already exists. Rename the existing project before restoring this one."}
            except Exception as error:
                return {"error":str(error)}
            slug=result["slug"]
            try: boards=self.workspace.project_summaries()
            except Exception as error:
                return {"slug":slug,"boards":[*self.summaries,{"slug":slug,"name":slug}],"warning":str(error)}
            return {"slug":slug,"boards":boards}
        def done(result):
            if self.closing: return
            if result.get("error"):
                self.show_modal("Could not restore project",v.label(result["error"],13,True)); return
            self.summaries=result["boards"]
            self.modal_close()
            if result.get("warning"):
                self.show_modal("Project restored",v.label("Production is back in place. The overview could not refresh: "+result["warning"],13,True),"project.open."+result["slug"],"Open project")
            else: self.open_project(result["slug"])
        self.submit("project.restore",operation,done,scoped=False)

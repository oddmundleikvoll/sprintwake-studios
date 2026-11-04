"""Explicit, one-action access to the director's canonical manuscript writer."""
import json
import threading

from director_authoring import author_project
from studio_state import authoring_digest
from director_state import authoring_turn
import views as v


class DirectorAuthoringMixin:
    def show_authoring(self):
        if not self.board:
            self.show_new_project(); return
        if "chat" in self.pending or "director.author" in self.pending:
            raise ValueError("The director is already working. Wait for the response to finish.")
        brief = self.board.get("sceneDescription") or next((turn.get("content", "") for turn in reversed(self.history) if turn.get("role") == "user"), "")
        count = 12
        checkpoint = self.workspace.ensure_project(self.slug) / "System" / "director-authoring.json"
        try:
            previous = json.loads(checkpoint.read_text())
            if previous.get("status") in {"failed", "cancelled", "running"} and previous.get("brief") and previous.get("digest") == authoring_digest(self.board):
                brief, count = previous["brief"], previous["total"]
        except (OSError, ValueError, KeyError):
            pass
        body = v.field("author.brief", "What should the film be about?", brief, height=170)
        body += v.field("author.count", "New shots in draft · 1 to 48", str(count), height=36, multiline=False)
        body += v.label("The director creates the film idea, scenes, film bible, and shots directly in this project. Each part is saved as it is created and can be edited afterward. Images are next.",13,True)
        self.show_modal("Create script and shots", body, "director.author.start", "Create and save script")

    def start_authoring(self):
        if getattr(self, "author_starting", False) or "director.author" in self.pending:
            return
        brief = self.text("author.brief").strip()
        count = int(self.text("author.count"))
        if not brief or not 1 <= count <= 48:
            raise ValueError("Write a film idea and choose between 1 and 48 shots.")
        if self._busy() or "chat" in self.pending:
            raise ValueError("Wait for the current work to finish before the director writes the script.")
        self.author_starting = True
        self.author_cancel = threading.Event()
        def launch():
            if "director.author" in self.pending or self.author_cancel.is_set():
                self.author_starting = False
                return
            slug, epoch = self.slug, self.epoch
            digest = authoring_digest(self.board)
            body = v.label("The director is writing and saving the project.",20)
            body += '<Label Name="author.progress" Text="Preparing script…" Wrap="true"/>'
            body += v.label(f"Target: {count} shots. Stopping preserves everything already saved.",13,True)
            self.show_modal("The director is writing the script",body,"director.author.cancel","Stop script work",allow_close=False)
            def progress(event):
                def show(data):
                    text = f"{data.get('completed',0)} of {data.get('total',count)} shots saved · {data.get('detail','Working…')}"
                    self.set_label("author.progress",text)
                    self.toast(text)
                self.completions.put(("director.author.progress",epoch,show,event,None))
            def done(result):
                self.board = result["board"]
                self.dirty = False
                self.refresh_view()
                status = result.get("status")
                turn = authoring_turn(result)
                content = turn["content"]
                self.history.append(turn)
                self.persist_conversation()
                if self.closing: return
                self.render_conversation()
                self.modal_close()
                self.brief_view = "script" if self.board.get("shots") else "idea"
                self.mode_set("brief" if self.board.get("shots") else "director")
                self.poll_deadline = 0
                self.toast(content, status == "failed")
            self.submit("director.author",lambda:author_project(self.backend,self.workspace,slug,brief,count,cancel=self.author_cancel,on_progress=progress,expected_digest=digest),done)
            self.author_starting = False
        self.save(after=launch)

    def cancel_authoring(self):
        event = getattr(self,"author_cancel",None)
        if event:
            event.set()
            self.set_label("author.progress","Stopping after the current director response. Already saved parts are preserved.")

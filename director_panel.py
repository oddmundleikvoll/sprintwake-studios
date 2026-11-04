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
            raise ValueError("Regissøren arbeider allerede. Vent til svaret er klart.")
        brief = self.board.get("sceneDescription") or next((turn.get("content", "") for turn in reversed(self.history) if turn.get("role") == "user"), "")
        count = 12
        checkpoint = self.workspace.ensure_project(self.slug) / "System" / "director-authoring.json"
        try:
            previous = json.loads(checkpoint.read_text())
            if previous.get("status") in {"failed", "cancelled", "running"} and previous.get("brief") and previous.get("digest") == authoring_digest(self.board):
                brief, count = previous["brief"], previous["total"]
        except (OSError, ValueError, KeyError):
            pass
        body = v.field("author.brief", "Hva skal filmen handle om?", brief, height=170)
        body += v.field("author.count", "Antall nye klipp i utkastet · 1 til 48", str(count), height=36, multiline=False)
        body += v.label("Regissøren oppretter filmidé, scener, filmbibel og klipp direkte i dette prosjektet. Hver del lagres fortløpende og kan redigeres etterpå. Bilder er neste steg.",13,True)
        self.show_modal("Lag manus og klipp", body, "director.author.start", "Lag og lagre manus")

    def start_authoring(self):
        if getattr(self, "author_starting", False) or "director.author" in self.pending:
            return
        brief = self.text("author.brief").strip()
        count = int(self.text("author.count"))
        if not brief or not 1 <= count <= 48:
            raise ValueError("Skriv en filmidé og velg mellom 1 og 48 klipp.")
        if self._busy() or "chat" in self.pending:
            raise ValueError("Vent til pågående arbeid er ferdig før regissøren skriver manuset.")
        self.author_starting = True
        self.author_cancel = threading.Event()
        def launch():
            if "director.author" in self.pending or self.author_cancel.is_set():
                self.author_starting = False
                return
            slug, epoch = self.slug, self.epoch
            digest = authoring_digest(self.board)
            body = v.label("Regissøren skriver og lagrer prosjektet.",20)
            body += '<Label Name="author.progress" Text="Forbereder manus…" Wrap="true"/>'
            body += v.label(f"Mål: {count} klipp. Et avbrudd beholder det som allerede er lagret.",13,True)
            self.show_modal("Regissøren lager manus",body,"director.author.cancel","Stopp manusarbeidet",allow_close=False)
            def progress(event):
                def show(data):
                    text = f"{data.get('completed',0)} av {data.get('total',count)} klipp lagret · {data.get('detail','Arbeider…')}"
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
            self.set_label("author.progress","Stopper etter gjeldende regissørsvar. Allerede lagrede deler beholdes.")

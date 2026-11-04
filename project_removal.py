"""Discoverable project deletion with a recoverable Raven trash."""
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import views as v


class ProjectRemovalMixin:
    def show_delete_project(self, slug=None):
        slug=slug or self.slug
        if not slug: raise ValueError("Velg prosjektet som skal slettes.")
        if not self.ready: raise ValueError("Vent til produksjonskjernen er klar.")
        if self.dirty:
            self.save(after=lambda:self.show_delete_project(slug)); return
        if any(key not in {"poll","covers","library.catalogue","take.history"} for key in self.pending):
            raise ValueError("Vent til pågående arbeid i appen er ferdig før du sletter prosjektet.")
        item=next((x for x in self.summaries if x["slug"]==slug),None)
        if not item: raise ValueError("Prosjektet finnes ikke lenger.")
        self.delete_target=slug
        self.delete_name=item.get("name",slug)
        name=item.get("name",slug)
        body=v.label(f'«{name}» fjernes fra prosjektoversikten.',16)
        body+=v.label("Manus, bilder, video og produksjonshistorikk flyttes samlet til Ravens papirkurv. Du kan gjenopprette prosjektet fra Papirkurv på prosjektsiden. Samtalene dine bevares.",13,True)
        self.show_modal("Slett prosjekt?",body,"project.delete.confirm","Flytt til papirkurv")

    def delete_project(self):
        if "project.delete" in self.pending: return
        slug=getattr(self,"delete_target",None)
        if not slug: raise ValueError("Velg prosjektet som skal slettes.")
        self.capture_drafts()
        self.epoch+=1  # Replies polled before deletion cannot restore the old view.
        self.show_modal("Flytter prosjektet til papirkurven",v.label("Kontrollerer at produksjonen er ledig…",13,True),allow_close=False)
        def operation():
            try:
                result=self.backend.post("/api/raven/boards/"+quote(slug,safe="")+"/trash",{"confirmName":self.delete_name})
            except Exception as error:
                return {"error":str(error)}
            # The server has committed the move. Local UI metadata cannot undo it.
            warnings=[]
            try: self.conversations.rebind_project(slug,None)
            except Exception as error: warnings.append("samtaletilknytningen: "+str(error))
            try: boards=self.workspace.project_summaries()
            except Exception as error:
                boards=[x for x in self.summaries if x["slug"]!=slug]
                warnings.append("prosjektoversikten: "+str(error))
            return {"entry":result["entry"],"boards":boards,"warnings":warnings}
        def done(result):
            if self.closing: return
            if result.get("error"):
                self.show_modal("Prosjektet kunne ikke slettes",v.label(result["error"],13,True)); return
            self.summaries=result["boards"]
            if self.slug==slug: self._free_project()
            try: self.current_conversation=self.conversations.load(self.current_conversation["id"])
            except Exception as error: result["warnings"].append("samtalen: "+str(error))
            if self.current_conversation.get("projectSlug")==slug:
                self.current_conversation["projectSlug"]=None
                for message in self.current_conversation["messages"]:
                    if isinstance(message,dict) and message.get("actions") and not message.get("applied"): message["stale"]=True
            self.history=self.current_conversation["messages"]
            self.modal_close(); self.mode_set("home")
            if result["warnings"]:
                self.show_modal("Prosjektet er flyttet til papirkurven",v.label("Produksjonen er bevart. Oppdateringen av lokale visninger trenger et nytt forsøk: "+"; ".join(result["warnings"]),13,True))
            self.toast("Prosjektet er flyttet til papirkurven. Du kan gjenopprette det derfra.")
        self.submit("project.delete",operation,done,scoped=False)

    def show_project_trash(self):
        items=self.workspace.trashed_projects()
        body=v.label("Slettede prosjekter",22)
        body+=v.label("Hele produksjonen er bevart. Gjenoppretting legger prosjektet tilbake med de samme bildene, opptakene og filreferansene.",13,True)
        for item in items:
            when=datetime.fromtimestamp(item["deletedAt"],ZoneInfo("Europe/Oslo")).strftime("%d.%m.%Y kl. %H:%M")
            body+=f'<Border Padding="16" Background="#1C2528" CornerRadius="9"><HBox Spacing="18"><VBox Slot.Fill="1" Spacing="6">{v.label(item["name"],18)}{v.label("Slettet "+when,12,True)}</VBox>{v.button("Gjenopprett", "project.restore."+item["id"])}</HBox></Border>'
        if not items: body+=v.label("Papirkurven er tom.",14,True)
        self.show_modal("Papirkurv",body)

    def restore_deleted_project(self, identifier):
        if "project.restore" in self.pending: return
        if not self.ready: raise ValueError("Vent til produksjonskjernen er klar.")
        if self.dirty:
            self.save(after=lambda:self.restore_deleted_project(identifier)); return
        self.show_modal("Gjenoppretter prosjektet",v.label("Legger produksjonen tilbake på plass…",13,True),allow_close=False)
        def operation():
            try:
                result=self.backend.post("/api/raven/trash/"+quote(identifier,safe="")+"/restore",{})
            except FileExistsError:
                return {"error":"Et prosjekt med samme ID finnes allerede. Endre navn på det eksisterende prosjektet før du gjenoppretter dette."}
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
                self.show_modal("Prosjektet kunne ikke gjenopprettes",v.label(result["error"],13,True)); return
            self.summaries=result["boards"]
            self.modal_close()
            if result.get("warning"):
                self.show_modal("Prosjektet er gjenopprettet",v.label("Produksjonen er tilbake på plass. Oversikten kunne ikke oppdateres: "+result["warning"],13,True),"project.open."+result["slug"],"Åpne prosjektet")
            else: self.open_project(result["slug"])
        self.submit("project.restore",operation,done,scoped=False)

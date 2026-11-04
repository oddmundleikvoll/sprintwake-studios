"""Narrative scene/sequence authoring, independent of production camera groups."""
import copy

import raven as r

from sequence import add_sequence, add_scene, assign_shots, edit_group, move_group, order_cut, remove_group, timing
import views as v
from editorial_timing import time_frames, time_text


class StructurePanelMixin:
    def structure_commit(self, state, label):
        self.sequence_doc = self.sequence_store.commit(self.sequence_doc, state, label)
        self.render_structure()
        self.toast(label + " · structure saved")

    def render_structure(self):
        if not self.sequence_doc or not self.cut_doc: return
        report = timing(self.sequence_doc["state"], self.cut_doc["clips"])
        def duration(item):
            actual, goal = item["frames"], item["targetFrames"]
            return time_text(actual) + " actual" + (f" · target {time_text(goal)} · {('+' if actual>=goal else '−')}{time_text(abs(actual-goal))}" if goal else " · target not set")
        body = v.label("SEQUENCES AND RUNTIME",10,True)
        body += v.label("The film",22,**{"Font":"/System/Library/Fonts/NewYork.ttf"}) + v.label(duration(report),12,True)
        body += v.button("Film target…","structure.edit.film.film") + v.button("+ Sequence","structure.add.sequence",True)
        body += '<HBox Spacing="7">' + v.button("Undo","structure.undo",Enabled=bool(self.sequence_doc["undo"])) + v.button("Redo","structure.redo",Enabled=bool(self.sequence_doc["redo"])) + '</HBox>'
        kind, ident = self.layout_group
        item = None
        for seq in report["sequences"]:
            if kind=="sequence" and seq["id"]==ident: item=seq
            for scene in seq["scenes"]:
                if kind=="scene" and scene["id"]==ident: item=scene
        if item:
            body += '<Separator/>'+v.label("SELECTED " + ("SEQUENCE" if kind=="sequence" else "SCENE"),10,True)
            body += v.label(item["title"],20)+v.label(duration(item),12,True)
            body += v.button("Name and time target…",f"structure.edit.{kind}.{ident}")
            if kind=="sequence": body += v.button("+ Scene","structure.add.scene."+ident)
            else:
                titles={s["id"]:s.get("title") or "Shot" for s in self.board.get("shots",[])}
                body += v.label(" · ".join(titles.get(sid,"Preserved shot") for sid in item["shotIds"]) or "No shots assigned yet.",12,True)
                body += v.button("Choose shots…","structure.assign."+ident)+v.button("To another sequence…","structure.reparent."+ident)
                body += v.button("Edit cut","structure.cut."+ident)
            body += '<HBox Spacing="7">'+v.button("↑ Tidligere",f"structure.up.{kind}.{ident}")+v.button("↓ Senere",f"structure.down.{kind}.{ident}")+'</HBox>'
            body += v.button("Remove " + ("sequence" if kind=="sequence" else "scene"),f"structure.remove.{kind}.{ident}")
        else:
            self.layout_group=("film","film")
            body += v.label("Choose a sequence or scene in the list on the left to edit its name, runtime, and shot assignment.",13,True)
        body += '<Separator/>'+v.button("Apply order to cut…","structure.order")
        if report["unassigned"]: body += v.label(f"{len(report['unassigned'])} shots have no scene. Assign them with Choose shots.",12,True)
        if report["missing"]: body += v.label("Some scene references are not in the cut; assignments are preserved.",12,True)
        body += v.label("Time targets do not change takes. Audio placement follows absolute film time; review it after recutting.",11,True)
        self.replace_host("structure.content",body)
        self.render_outline()

    def structure_group(self, kind, ident):
        state=self.sequence_doc["state"]
        if kind=="film": return {"title":self.board.get("name","The film"),"targetFrames":state["targetFrames"]}
        for seq in state["sequences"]:
            if kind=="sequence" and seq["id"]==ident: return seq
            if kind=="scene":
                for scene in seq["scenes"]:
                    if scene["id"]==ident: return scene
        raise ValueError("The scene or sequence no longer exists.")

    def structure_action(self, action):
        if not action.startswith("structure."): return False
        if not self.sequence_doc: return True
        state=self.sequence_doc["state"]
        if action in {"structure.undo","structure.redo"}:
            self.sequence_doc=self.sequence_store.history(self.sequence_doc,action.endswith("redo"));self.render_structure()
        elif action=="structure.add.sequence":
            self.show_modal("New sequence",v.field("structure.name","Sequence name","New sequence",34,multiline=False),"structure.create.sequence")
        elif action.startswith("structure.add.scene."):
            self.structure_parent=action.removeprefix("structure.add.scene.")
            self.show_modal("New scene",v.field("structure.name","Scene name","New scene",34,multiline=False),"structure.create.scene")
        elif action.startswith("structure.create."):
            name=self.text("structure.name").strip()
            result=add_sequence(state,name) if action.endswith("sequence") else add_scene(state,self.structure_parent,name)
            self.structure_commit(result,"Add sequence" if action.endswith("sequence") else "Add scene");self.modal_close()
        elif action.startswith("structure.edit."):
            _,_,kind,ident=action.split(".",3)
            item=self.structure_group(kind,ident);self.structure_edit=(kind,ident)
            body=(v.field("structure.name","Name",item["title"],34,multiline=False) if kind!="film" else "")
            body+=v.field("structure.goal","Target · mm:ss (0 = not set)",time_text(item["targetFrames"]),34,multiline=False)
            self.show_modal("Name and runtime",body,"structure.save")
        elif action=="structure.save":
            kind,ident=self.structure_edit
            self.structure_commit(edit_group(state,kind,ident,title=self.text("structure.name").strip() if kind!="film" else None,targetFrames=time_frames(self.text("structure.goal"))),"Change name or time target");self.modal_close()
        elif action=="structure.assign.save":
            result=copy.deepcopy(state)
            for seq in result["sequences"]:
                for scene in seq["scenes"]:
                    if scene["id"]==self.structure_assign: scene["shotIds"]=[]
            chosen=[c["shotId"] for i,c in enumerate(self.structure_choices) if self.checked("structure.shot."+str(i))]
            self.structure_commit(assign_shots(result,self.structure_assign,chosen),"Assign shots to scene");self.modal_close()
        elif action.startswith("structure.assign."):
            ident=action.removeprefix("structure.assign.");scene=self.structure_group("scene",ident)
            self.structure_assign=ident
            self.structure_choices=list(self.cut_doc["clips"])
            titles={s["id"]:s.get("title") or "Shot" for s in self.board.get("shots",[])}
            body=v.label("Selected shots move to this scene. Other shots keep their assignments.",12,True)
            for i,c in enumerate(self.structure_choices):
                name=titles.get(c["shotId"],"Preserved shot")+(" · excluded" if not c["included"] else "")
                body+=f'<Checkbox Name="structure.shot.{i}" Text="{v.esc(name)}" Checked="{str(c["shotId"] in scene["shotIds"]).lower()}"/>'
            self.show_modal("Shots in "+scene["title"],body,"structure.assign.save")
        elif action.startswith("structure.reparent."):
            self.structure_reparent=action.removeprefix("structure.reparent.")
            body='<ComboBox Name="structure.parent" Items="'+v.esc("|".join(s["title"].replace("|"," ") for s in state["sequences"]))+'"/>'
            self.show_modal("Move scene to sequence",body,"structure.parent.save")
            current=next(i for i,s in enumerate(state["sequences"]) if any(c["id"]==self.structure_reparent for c in s["scenes"]))
            r.Rcombo_box_set_selected(self.find("structure.parent"),current)
        elif action=="structure.parent.save":
            result=copy.deepcopy(state)
            index=self.selected("structure.parent")
            if not 0<=index<len(result["sequences"]): raise ValueError("Choose a sequence first.")
            scene=next(c for s in result["sequences"] for c in s["scenes"] if c["id"]==self.structure_reparent)
            for s in result["sequences"]: s["scenes"]=[c for c in s["scenes"] if c["id"]!=scene["id"]]
            result["sequences"][index]["scenes"].append(scene)
            self.structure_commit(result,"Move scene to sequence");self.modal_close()
        elif action.startswith(("structure.up.","structure.down.","structure.remove.")):
            _,verb,kind,ident=action.split(".",3)
            result=remove_group(state,kind,ident) if verb=="remove" else move_group(state,kind,ident,-1 if verb=="up" else 1)
            self.structure_commit(result,"Remove assignment" if verb=="remove" else "Move scene or sequence")
        elif action=="structure.order":
            self.show_modal("Apply scene order to cut?",v.label("Shots will be ordered by the sequences and scenes above. Takes and in/out points are preserved. Audio clips stay at absolute film time and must be reviewed afterward.",13),"structure.order.apply","Apply order")
        elif action=="structure.order.apply":
            self.cut_doc=self.cut_store.commit(self.cut_doc,order_cut(state,self.cut_doc["clips"]),"Apply sequence and scene order")
            self.render_structure();self.modal_close()
            self.toast("Scene order applied. Cut → Undo restores the previous shot order.")
        elif action.startswith("structure.cut."):
            scene=self.structure_group("scene",action.removeprefix("structure.cut."));ids=set(scene["shotIds"])
            self.mode_set("cut")
            self.story_cut_selection=ids
        return True

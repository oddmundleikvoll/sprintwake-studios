"""Native, persistent room blocking and camera coverage workspace."""
from __future__ import annotations

import copy
import math
import uuid

import raven as r

from coverage import CoverageStore, actors_by_id, camera_key, digest, new_plan, propose, target, validate, warnings
from coverage_io import apply_coverage, layout_reference
from coverage_render import render_pixels
from coverage_view import draw_plan, draw_preview, from_pixel, to_pixel
from studio_state import authoring_digest
import views as v


def coverage_page():
    return '''<VBox Name="page.coverage" Visibility="Collapsed" Spacing="14" Slot.Padding="26,20">
      <HBox Spacing="12"><VBox Slot.Fill="1" Spacing="5"><Label Text="Romkart og coverage" TextSize="27" Font="/System/Library/Fonts/NewYork.ttf"/><Label Text="Dra figurer, kameraer og kamerasiktet. Rutenettet er én meter." Class="muted"/></VBox><Button Text="Tilbake til Scener" OnClicked="page.scenes"/></HBox>
      <HBox Spacing="10"><ComboBox Name="coverage.room" Width="230" OnChanged="coverage.room"/><Button Text="Rom og akse…" OnClicked="coverage.room.edit"/><Button Text="+ Figur" OnClicked="coverage.actor.add"/><Button Text="+ Kamera" OnClicked="coverage.camera.add"/><Spacer Slot.Fill="1"/><Button Name="coverage.undo" Text="Angre" OnClicked="coverage.undo"/><Button Name="coverage.redo" Text="Gjør om" OnClicked="coverage.redo"/></HBox>
      <HBox Spacing="18" Slot.Fill="1"><VBox Slot.Fill="1" Spacing="12"><Custom Name="coverage.plan" Slot.Fill="1" MinHeight="220" Focusable="true"/><Custom Name="coverage.preview" Height="230"/><Label Name="coverage.status" Text="" Class="muted" Wrap="true"/></VBox>
      <Border Width="310" Background="#1C2427" CornerRadius="11" Padding="17"><ScrollBox><VBox Name="coverage.inspector" Spacing="14"/></ScrollBox></Border></HBox>
    </VBox>'''


class CoverageMixin:
    def initialize_coverage(self):
        texture = getattr(self,"coverage_texture",r.R_INVALID_ID)
        if texture != r.R_INVALID_ID:
            r.Rtexture_destroy(texture)
        self.coverage_texture = r.R_INVALID_ID
        self.coverage_preview_key = None
        self.coverage_compact = None
        self.coverage_store = None
        self.coverage_doc = None
        self.coverage_key = ""
        self.coverage_plan = None
        self.coverage_selected = None
        self.coverage_drag = None
        self.coverage_loading = False

    def _free_project(self):
        self.initialize_coverage()
        self.replace_host("coverage.inspector", "")
        return super()._free_project()

    def bind_coverage(self):
        def paint_plan(widget, painter, width, height, _):
            if self.coverage_plan:
                draw_plan(painter, width, height, self.coverage_plan, self.coverage_selected)
        def paint_preview(widget, painter, width, height, _):
            if self.coverage_plan:
                draw_preview(painter, width, height, self.coverage_plan, self.coverage_camera, self.coverage_texture)
        self.coverage_callbacks = (paint_plan, paint_preview, self.coverage_event)
        r.Rcustom_set_paint_callback(self.find("coverage.plan"), paint_plan)
        r.Rcustom_set_paint_callback(self.find("coverage.preview"), paint_preview)
        r.Rwidget_set_event_callback(self.find("coverage.plan"), self.coverage_event)

    @property
    def coverage_camera(self):
        if not self.coverage_plan:
            return None
        cameras = self.coverage_plan["cameras"]
        if self.coverage_selected and self.coverage_selected[0] in ("camera", "target"):
            return next((c for c in cameras if c["id"] == self.coverage_selected[1]), None)
        return cameras[0] if cameras else None

    def open_coverage(self):
        if not self.board or any(k in self.pending for k in ("rename", "archive")):
            return
        self.coverage_store = CoverageStore(self.workspace, self.slug)
        self.coverage_doc = self.coverage_store.load()
        locations = (self.board.get("filmBible") or {}).get("locations", [])
        self.coverage_rooms = [(x["id"], x.get("title") or x.get("name") or x["id"]) for x in locations]
        self.coverage_rooms += [(key, plan["title"] + " · sted mangler") for key, plan in self.coverage_doc["plans"].items()
                                if key and key not in {x[0] for x in self.coverage_rooms} and key != "draft"]
        self.coverage_rooms.append(("draft", "Fritt rom · velg sted før storyboard"))
        if self.coverage_key not in {x[0] for x in self.coverage_rooms}:
            self.coverage_key = self.coverage_rooms[0][0]
        self.coverage_loading = True
        combo = self.find("coverage.room")
        r.Rcombo_box_clear_items(combo)
        for _, name in self.coverage_rooms:
            r.Rcombo_box_add_item(combo, name)
        r.Rcombo_box_set_selected(combo, next(i for i, x in enumerate(self.coverage_rooms) if x[0] == self.coverage_key))
        self.coverage_loading = False
        self.select_coverage_room(self.coverage_key)

    def select_coverage_room(self, key):
        self.finish_coverage_drag()
        self.coverage_key = key
        self.coverage_plan = copy.deepcopy(self.coverage_doc["plans"].get(key) or new_plan(self.board, "" if key == "draft" else key))
        self.coverage_selected = ("actor", self.coverage_plan["actors"][0]["id"]) if self.coverage_plan["actors"] else None
        if key not in self.coverage_doc["plans"]:
            self.commit_coverage(self.coverage_plan)
        self.render_coverage()

    def commit_coverage(self, plan):
        validate(plan)
        plans = copy.deepcopy(self.coverage_doc["plans"])
        plans[self.coverage_key] = plan
        self.coverage_doc = self.coverage_store.commit(self.coverage_doc, plans)
        self.coverage_plan = copy.deepcopy(self.coverage_doc["plans"][self.coverage_key])
        self.render_coverage()

    def coverage_undo(self, redo=False):
        self.coverage_doc = self.coverage_store.commit(self.coverage_doc, self.coverage_doc["plans"], history="redo" if redo else "undo")
        # Undo may remove the very first room creation; do not create another history entry.
        self.coverage_plan = copy.deepcopy(self.coverage_doc["plans"].get(self.coverage_key) or new_plan(self.board, "" if self.coverage_key == "draft" else self.coverage_key))
        self.coverage_selected = None
        self.render_coverage()

    def invalidate_coverage(self):
        camera=self.coverage_camera
        key=(digest(self.coverage_plan),camera["id"],bool(self.coverage_drag)) if camera else None
        if key != self.coverage_preview_key:
            self.coverage_preview_key=key
            if camera:
                try:
                    pixels,width,height=render_pixels(self.coverage_plan,camera,192 if self.coverage_drag else 480)
                except ValueError:
                    pixels,width,height=bytes((25,32,35,255)),1,1
                if self.coverage_texture==r.R_INVALID_ID:
                    self.coverage_texture=r.Rtexture_create_from_pixels(pixels,width,height)
                else:
                    r.Rtexture_update(self.coverage_texture,pixels,width,height)
        for name in ("coverage.plan", "coverage.preview"):
            r.Rwidget_invalidate(self.find(name))

    def layout_coverage(self):
        if self.mode!="coverage": return
        _,height=r.Rwindow_get_size(self.window)
        compact=height<850
        if compact!=self.coverage_compact:
            self.coverage_compact=compact
            self.set_attr("coverage.preview","Height",140 if compact else 230)

    def render_coverage(self):
        plan = self.coverage_plan
        if not plan:
            return
        selected = self.coverage_selected
        body = v.button("Foreslå kameravinkler", "coverage.propose", True)
        body += v.label("Totalbilde, motskudd og nærbilder på samme side av handlingsaksen.", 12, True)
        body += v.label("FIGURER", 10, True)
        for a in plan["actors"]:
            mark = "● " if selected == ("actor", a["id"]) else ""
            body += v.button(mark + a["name"], "coverage.select.actor." + a["id"])
        body += '<Separator/>' + v.label("KAMERAER", 10, True)
        for i, c in enumerate(plan["cameras"]):
            body += f'<HBox Spacing="6"><Checkbox Name="coverage.use.{i}" Checked="{str(c["selected"]).lower()}" OnChanged="coverage.use.{i}"/>'
            prefix="⚠ " if warnings(plan,c) else ""
            body += v.button(f"{prefix}{i+1} · {c['name']}", "coverage.select.camera." + c["id"], **{"Slot.Fill": "1"}) + '</HBox>'
        if any(warnings(plan,c) for c in plan["cameras"]):
            body += v.label("Vinkler med varsel må justeres før de brukes. Forslag uten trygt utsnitt er fravalgt.",12,True)
        if selected:
            group = plan["actors"] if selected[0] == "actor" else plan["cameras"]
            item = next((x for x in group if x["id"] == selected[1]), None)
            if item:
                body += '<Separator/>' + v.label(item["name"], 18)
                body += v.label(f"X {item['x']:.2f} m · Y {item['y']:.2f} m", 12, True)
                body += v.button("Rediger plasseringen…", "coverage.item.edit")
                body += v.button("Fjern fra romkartet", "coverage.item.remove")
        camera = self.coverage_camera
        problems = warnings(plan, camera) if camera else []
        if camera:
            body += '<Separator/>' + v.label(f"{camera['lens']:g} mm · høyde {camera['z']:.2f} m", 13)
            body += v.label(" ".join(problems) if problems else "Valgt kamera holder handlingsaksen og målet i bildet.", 12, Color="#EFA1A1" if problems else "#B7C6AA")
            body += v.button("Bruk utsnitt i Bilder", "coverage.image")
        body += '<Separator/>' + v.button("Legg valgte vinkler i storyboard", "coverage.apply", True)
        body += v.label("Nye kameraoppsett legges til som utkast. Gjennomgå handling, dialog og scenebilder før produksjon.", 12, True)
        self.replace_host("coverage.inspector", body)
        self.set_attr("coverage.undo", "Enabled", bool(self.coverage_doc["undo"]))
        self.set_attr("coverage.redo", "Enabled", bool(self.coverage_doc["redo"]))
        self.set_label("coverage.status", f"{plan['title']} · lagret revisjon {self.coverage_doc['revision']} · plassering og utsnitt, ikke ferdig scenografi")
        self.toast("Romkartet er lagret.")
        self.invalidate_coverage()

    def coverage_event(self, widget, event, _):
        if event.type != r.R_EVENT_MOUSE_DOWN or event.button != r.R_MOUSE_LEFT or self.mode != "coverage" or self.modal_kind or self.closing or any(k.startswith("coverage.") for k in self.pending):
            return 0
        plan = self.coverage_plan
        if not plan:
            return 0
        _, _, width, height = r.Rwidget_get_rect(widget)
        x, y = event.local_x, event.local_y
        hits = []
        for kind, items in (("actor", plan["actors"]), ("camera", plan["cameras"])):
            for item in items:
                px, py = to_pixel(plan, item["x"], item["y"], width, height)
                hits.append((math.hypot(x-px,y-py), kind, item["id"]))
        camera = self.coverage_camera
        if camera and self.coverage_selected and self.coverage_selected[0] in ("camera", "target"):
            px, py = to_pixel(plan, *target(plan,camera)[:2], width, height)
            distance = math.hypot(x-px,y-py)
            if distance < 9:
                hits = [(-1, "target", camera["id"])]
        if not hits or min(hits)[0] > 20:
            return 0
        _, kind, ident = min(hits)
        self.coverage_selected = (kind, ident)
        self.coverage_drag = copy.deepcopy(plan)
        self.render_coverage()
        return 1

    def tick_coverage_drag(self):
        if self.coverage_drag is None:
            return
        widget = self.find("coverage.plan")
        left, top, width, height = r.Rwidget_get_rect(widget)
        mouse = r.Rwindow_get_mouse_position(self.window)
        x,y = from_pixel(self.coverage_plan, mouse[0]-left, mouse[1]-top, width, height)
        kind, ident = self.coverage_selected
        items = self.coverage_plan["actors"] if kind == "actor" else self.coverage_plan["cameras"]
        item = next(a for a in items if a["id"] == ident)
        if kind == "target":
            aim = target(self.coverage_plan,item)
            item.update(aimIds=[], aim=[x,y,aim[2]])
        else:
            item.update(x=x,y=y)
        self.invalidate_coverage()
        if not r.Rwindow_is_mouse_button_down(self.window,r.R_MOUSE_LEFT):
            self.finish_coverage_drag()

    def finish_coverage_drag(self, cancel=False):
        original, self.coverage_drag = self.coverage_drag, None
        if original is None:
            return
        if cancel:
            self.coverage_plan = original
        elif original != self.coverage_plan:
            try:
                self.commit_coverage(self.coverage_plan)
            except (ValueError,OSError) as error:
                self.coverage_plan = original
                self.toast(error,True)
        self.render_coverage()

    def edit_coverage_room(self):
        plan = self.coverage_plan
        body = v.field("room.title","Romnavn",plan["title"],34,multiline=False)
        for key, heading in (("width","Bredde · m"),("depth","Dybde · m"),("height","Takhøyde · m")):
            body += v.field("room."+key,heading,plan[key],34,multiline=False)
        names = v.esc("|".join(a["name"].replace("|"," ") for a in plan["actors"]) or "Ingen figurer")
        body += f'<Label Text="HANDLINGSAKSE · FRA / TIL" Class="eyebrow"/><ComboBox Name="room.axis.a" Items="{names}"/><ComboBox Name="room.axis.b" Items="{names}"/>'
        body += '<Label Text="KAMERASIDE OG FORMAT" Class="eyebrow"/><ComboBox Name="room.side" Items="Venstre side av A → B|Høyre side av A → B"/><ComboBox Name="room.aspect" Items="Film · 16:9|Landskap · 3:2|Kvadrat · 1:1|Portrett · 2:3"/>'
        self.show_modal("Rom og handlingsakse",body,"coverage.room.save")
        for i,key in enumerate(("a","b")):
            r.Rcombo_box_set_selected(self.find("room.axis."+key),next((j for j,a in enumerate(plan["actors"]) if len(plan["axis"])>i and a["id"]==plan["axis"][i]),0))
        r.Rcombo_box_set_selected(self.find("room.side"),0 if plan["side"]==1 else 1)
        aspects=[16/9,3/2,1,2/3]
        r.Rcombo_box_set_selected(self.find("room.aspect"),min(range(4),key=lambda i:abs(aspects[i]-plan["aspect"])))

    def save_coverage_room(self):
        plan = copy.deepcopy(self.coverage_plan)
        plan["title"] = self.text("room.title").strip()
        # Reject dimensions that would clip placed objects, preserving their coordinates.
        for key in ("width","depth","height"):
            plan[key] = float(self.text("room."+key))
        plan["axis"] = [plan["actors"][self.selected("room.axis."+k)]["id"] for k in ("a","b")] if len(plan["actors"])>=2 else []
        plan["side"] = 1 if self.selected("room.side")==0 else -1
        plan["aspect"] = [16/9,3/2,1,2/3][self.selected("room.aspect")]
        self.commit_coverage(plan)
        self.modal_close()

    def edit_coverage_item(self):
        kind, ident = self.coverage_selected
        plan = self.coverage_plan
        item = next(a for a in plan["actors"] if a["id"]==ident) if kind=="actor" else next(c for c in plan["cameras"] if c["id"]==ident)
        self.coverage_edit_target = (kind,ident)
        body = v.field("placement.name","Navn",item["name"],34,multiline=False)
        fields = (("x","X · øst"),("y","Y · nord"),("height","Figurhøyde · m")) if kind=="actor" else (("x","X · øst"),("y","Y · nord"),("z","Kamerahøyde · m"),("lens","Brennvidde · mm"))
        for key, label in fields:
            body += v.field("placement."+key,label,item[key],34,multiline=False)
        if kind=="actor":
            cast = self.board.get("characters",[])
            body += '<Label Text="KARAKTER I FILMBIBELEN" Class="eyebrow"/><ComboBox Name="placement.character" Items="' + v.esc("|".join(["Plassholder"]+[c.get("name") or c["id"] for c in cast])) + '"/>'
            choices=[a for a in plan["actors"] if a["id"]!=ident]
            chosen=[item["lookAt"]] if item["lookAt"] else []
        else:
            choices=plan["actors"]
            chosen=item["aimIds"]
        self.coverage_aim_choices = choices
        body += '<Label Text="BLIKK / KAMERASIKTE" Class="eyebrow"/>'
        for i,a in enumerate(choices):
            body += f'<Checkbox Name="placement.aim.{i}" Text="{v.esc(a["name"])}" Checked="{str(a["id"] in chosen).lower()}"/>'
        self.show_modal("Figurplassering" if kind=="actor" else "Kameraplassering",body,"coverage.item.save")
        if kind=="actor":
            r.Rcombo_box_set_selected(self.find("placement.character"),next((i+1 for i,c in enumerate(cast) if c["id"]==item["characterId"]),0))

    def save_coverage_item(self):
        kind, ident = self.coverage_edit_target
        plan = copy.deepcopy(self.coverage_plan)
        item = next(a for a in plan["actors"] if a["id"]==ident) if kind=="actor" else next(c for c in plan["cameras"] if c["id"]==ident)
        item["name"] = self.text("placement.name").strip()
        for key in (("x","y","height") if kind=="actor" else ("x","y","z","lens")):
            item[key] = float(self.text("placement."+key))
        chosen = [a["id"] for i,a in enumerate(self.coverage_aim_choices) if self.checked("placement.aim."+str(i))]
        if kind=="actor":
            if len(chosen)>1: raise ValueError("Velg høyst én figur å se mot.")
            index=self.selected("placement.character")-1
            item["characterId"] = self.board["characters"][index]["id"] if index>=0 else ""
            if item["characterId"] and any(a["id"]!=ident and a["characterId"]==item["characterId"] for a in plan["actors"]):
                raise ValueError("Karakteren står allerede i romkartet.")
            item["lookAt"] = chosen[0] if chosen else ""
        else:
            item["aim"] = list(target(plan,item))
            item["aimIds"] = chosen
        self.commit_coverage(plan)
        self.modal_close()

    def remove_coverage_item(self):
        kind, ident = self.coverage_selected
        plan = copy.deepcopy(self.coverage_plan)
        if kind=="actor":
            plan["actors"] = [a for a in plan["actors"] if a["id"]!=ident]
            for a in plan["actors"]:
                if a["lookAt"]==ident: a["lookAt"]=""
            for c in plan["cameras"]:
                c["aim"] = list(target(self.coverage_plan,c))
                c["aimIds"] = [i for i in c["aimIds"] if i!=ident]
            if ident in plan["axis"]:
                plan["axis"] = [a["id"] for a in plan["actors"][:2]] if len(plan["actors"])>=2 else []
        else:
            plan["cameras"] = [c for c in plan["cameras"] if c["id"]!=ident]
        self.coverage_selected = None
        self.commit_coverage(plan)

    def add_coverage_item(self, kind):
        plan = copy.deepcopy(self.coverage_plan)
        ident = uuid.uuid4().hex[:12]
        if kind=="actor":
            used={a["characterId"] for a in plan["actors"]}
            c=next((c for c in self.board.get("characters",[]) if c["id"] not in used),{})
            plan["actors"].append({"id":ident,"characterId":c.get("id",""),"name":c.get("name") or "Ny figur","x":plan["width"]*.4,"y":plan["depth"]*.4,"height":1.7,"lookAt":""})
            if len(plan["actors"])==2: plan["axis"]=[a["id"] for a in plan["actors"]]
        else:
            c=copy.deepcopy(self.coverage_camera) if self.coverage_camera else propose(plan)[0]
            c.update(id=ident,name="Eget kamera")
            plan["cameras"].append(c)
        self.coverage_selected=(kind,ident)
        self.commit_coverage(plan)

    def apply_coverage_shots(self):
        if self._busy(): raise ValueError("Vent til produksjonsjobben er ferdig før du legger til klipp.")
        plan,slug=copy.deepcopy(self.coverage_plan),self.slug
        expected=authoring_digest(self.board)
        def done(result):
            self.board,count=result
            self.edit_version+=1
            self.set_dirty(False)
            self.refresh_view()
            self.fill_brief()
            self.toast(f"{count} nye kameraoppsett lagret i storyboardet." if count else "Disse kameravinklene finnes allerede i storyboardet.")
        self.submit("coverage.apply",lambda:apply_coverage(self.workspace,self.backend,slug,expected,plan),done)

    def coverage_to_image(self):
        camera=self.coverage_camera
        if not camera: raise ValueError("Velg et kamera først.")
        sid="scov"+camera_key(self.coverage_plan,camera)[:16]
        index=next((i for i,s in enumerate(self.board.get("shots",[])) if s["id"]==sid),None)
        if index is None: raise ValueError("Legg vinkelen i storyboardet først. Da knyttes scenebildet til riktig klipp.")
        slug,plan,camera=self.slug,copy.deepcopy(self.coverage_plan),copy.deepcopy(camera)
        cast={c["id"]:c for c in self.board.get("characters",[])}
        refs=[]
        mapping=[]
        colors=["orange","blue","green","purple","yellow","turquoise","lavender","red"]
        for i,a in enumerate(plan["actors"]):
            if a["characterId"] not in cast:
                raise ValueError("Knytt figurene til eksisterende karakterer i filmbibelen først.")
            c=cast[a["characterId"]]
            if c.get("image"):
                refs.append(copy.deepcopy(c["image"]))
            identity=f" (appearance reference {len(refs)+1})" if c.get("image") else ""
            mapping.append(f"The {colors[i]} proxy is {a['name']}{identity}: {c.get('description','')}.")
        def done(result):
            ref,prompt=result
            prompt+=" "+" ".join(mapping)
            self.index=index
            self.refresh_view()
            self.mode_set("images")
            self.image_refs=[ref]+refs[:9]
            self.image_target={"kind":"shot","shot":sid}
            self.set_text("image.prompt",prompt)
            aspect=min(range(4),key=lambda i:abs([3/2,16/9,2/3,1][i]-plan["aspect"]))
            r.Rcombo_box_set_selected(self.find("image.aspect"),aspect)
            self.render_images()
            self.capture_drafts()
            self.toast("Utsnittet og karakterreferansene er klare i Bilder. Velg Lag bilde når beskrivelsen er klar.")
        self.submit("coverage.image",lambda:layout_reference(self.workspace,self.backend,slug,plan,camera),done)

    def coverage_action(self, action):
        if action in {"edit.undo","edit.redo"} and self.mode=="coverage":
            focus=r.Rwindow_get_focused_widget(self.window)
            if focus==r.R_INVALID_ID or r.Rwidget_get_type(focus) not in {r.R_WIDGET_TEXT_INPUT,r.R_WIDGET_TEXT_EDITOR}:
                self.coverage_undo(action=="edit.redo")
                return True
        if not action.startswith("coverage."):
            return False
        if self.coverage_loading:
            return True
        if action=="coverage.room":
            index=self.selected("coverage.room")
            if 0<=index<len(self.coverage_rooms): self.select_coverage_room(self.coverage_rooms[index][0])
        elif action.startswith("coverage.select."):
            _,_,kind,ident=action.split(".",3)
            self.coverage_selected=(kind,ident);self.render_coverage()
        elif action=="coverage.propose":
            plan=copy.deepcopy(self.coverage_plan);plan["cameras"]=propose(plan)
            self.coverage_selected=("camera",plan["cameras"][0]["id"]);self.commit_coverage(plan)
        elif action in {"coverage.undo","coverage.redo"}: self.coverage_undo(action.endswith("redo"))
        elif action=="coverage.room.edit": self.edit_coverage_room()
        elif action=="coverage.room.save": self.save_coverage_room()
        elif action=="coverage.item.edit": self.edit_coverage_item()
        elif action=="coverage.item.save": self.save_coverage_item()
        elif action=="coverage.item.remove": self.remove_coverage_item()
        elif action=="coverage.actor.add": self.add_coverage_item("actor")
        elif action=="coverage.camera.add": self.add_coverage_item("camera")
        elif action.startswith("coverage.use."):
            i=int(action.rsplit(".",1)[1]);plan=copy.deepcopy(self.coverage_plan)
            plan["cameras"][i]["selected"]=self.checked("coverage.use."+str(i));self.commit_coverage(plan)
        elif action=="coverage.apply": self.save(after=self.apply_coverage_shots)
        elif action=="coverage.image": self.save(after=self.coverage_to_image)
        return True

"""Native independent sound lanes and their exact-frame editing controls."""
import copy
from pathlib import Path
import uuid

import raven as r

from cut import frames, visible_clips
from cut_media import export_cut
from production_model import view_project
from sound import SoundStore, TRACKS, remove_clip, update_clip
from sound_media import preserve_audio, render_mix
from editorial_timing import sound_gesture, time_frames, time_text
import views as v

COLORS = [(0.50,.70,.83),(.74,.58,.82),(.40,.69,.56),(.85,.64,.39)]
SOUND_HEADER = 152



class SoundPanelMixin:
    def bind_sound(self):
        timeline=self.find("sound.timeline")
        r.Rtimeline_set_selection_callback(timeline,self.on_sound_selection)
        r.Rtimeline_set_changed_callback(timeline,self.on_sound_change)
        r.Rtimeline_set_removed_callback(timeline,self.on_sound_removed)
        r.Rtimeline_set_time_callback(timeline,self.on_sound_scrub)

    def sound_commit(self,state,label):
        self.stop_workspace_preview(restore=True)
        self.sound_doc=self.sound_store.commit(self.sound_doc,state,label)
        self.render_sound()
        self.toast(label+" · sound layer saved")

    def render_sound(self):
        if not self.sound_doc or not self.cut_doc: return
        self.sound_loading=True
        state=self.sound_doc["state"]
        body=v.label("MIXER · LEVEL IN dB",11,True)
        for i,t in enumerate(state["tracks"]):
            body+='<VBox Spacing="6"><HBox Spacing="8">'+v.label(t["name"],14,**{"Slot.Fill":"1"})
            body+=f'<TextInput Name="sound.bus.{i}.gain" Text="{t["gainDb"]:g}" Width="64"/><Label Text="dB" Class="muted"/></HBox>'
            body+=f'<HBox Spacing="10"><Checkbox Name="sound.bus.{i}.mute" Text="Av" Checked="{str(t["muted"]).lower()}"/><Checkbox Name="sound.bus.{i}.solo" Text="Solo" Checked="{str(t["solo"]).lower()}"/></HBox></VBox><Separator/>'
        body+= '<Checkbox Name="sound.original" Text="Keep take audio" Checked="'+str(state["original"]["enabled"]).lower()+'"/>'
        body+=v.field("sound.original.gain","Take audio · dB",state["original"]["gainDb"],32,multiline=False)
        body+=v.label("Take audio is one combined source and follows the picture trim and Dialogue track level. 0 dB preserves the level; negative values attenuate.",11,True)
        self.replace_host("sound.mixer",body)
        self.render_sound_inspector()
        timeline=self.find("sound.timeline")
        old_view=r.Rtimeline_get_view(timeline) if self.sound_items else None
        r.Rtimeline_clear(timeline)
        picture=r.Rtimeline_add_track(timeline,"PICTURE")
        r.Rtimeline_set_track_locked(timeline,picture,True)
        r.Rtimeline_set_track_height(timeline,picture,28)
        names={s["id"]:s.get("title") or "Shot" for s in self.board.get("shots",[])}
        cursor=0
        for clip in visible_clips(self.cut_doc["clips"]):
            count=clip["outFrame"]-clip["inFrame"]
            item=r.Rtimeline_add_clip(timeline,picture,cursor/24,count/24,names.get(clip["shotId"],"Preserved shot"))
            r.Rtimeline_set_item_color(timeline,item,.45,.34,.25)
            cursor+=count
        self.sound_items={};self.sound_tracks={}
        for i,t in enumerate(state["tracks"]):
            track=r.Rtimeline_add_track(timeline,t["name"].upper())
            self.sound_tracks[track]=t["id"]
            r.Rtimeline_set_track_height(timeline,track,34)
            r.Rtimeline_set_track_color(timeline,track,*COLORS[i])
            r.Rtimeline_set_track_muted(timeline,track,t["muted"])
            for c in state["clips"]:
                if c["track"]!=t["id"]: continue
                item=r.Rtimeline_add_clip(timeline,track,c["atFrame"]/24,(c["outFrame"]-c["inFrame"])/24,c["label"]+(" · av" if c["muted"] else ""))
                self.sound_items[item]=c["id"]
                r.Rtimeline_set_item_color(timeline,item,*COLORS[i])
                if c["id"]==self.sound_selection: r.Rtimeline_select(timeline,item,False)
        end=max([cursor]+[c["atFrame"]+c["outFrame"]-c["inFrame"] for c in state["clips"]])
        r.Rtimeline_set_duration(timeline,max(10,end/24+10))
        r.Rtimeline_set_frame_rate(timeline,24)
        if old_view: r.Rtimeline_set_view(timeline,*old_view)
        else: r.Rtimeline_set_view(timeline,0,max(10,min(180,end/24+2)))
        r.Rtimeline_set_time(timeline,self.sound_cursor/24)
        self.set_label("sound.status",f"Film {time_text(cursor)} · cursor {time_text(self.sound_cursor)} · {len(state['clips'])} audio clips · drag edges to trim · Delete removes reversibly")
        self.set_attr("sound.undo","Enabled",bool(self.sound_doc["undo"]))
        self.set_attr("sound.redo","Enabled",bool(self.sound_doc["redo"]))
        self.sound_loading=False

    def render_sound_inspector(self):
        state=self.sound_doc["state"]
        clip=next((c for c in state["clips"] if c["id"]==self.sound_selection),None)
        body=v.label("VALGT SOUNDKLIPP",11,True)
        if clip:
            body+=v.field("sound.label","Name",clip["label"],32,multiline=False)
            body+='<ComboBox Name="sound.track" Items="Dialogue|Music|Ambience|Effects"/>'
            for key,title in (("atFrame","Position in film"),("inFrame","Source in"),("outFrame","Source out"),("fadeIn","Fade in"),("fadeOut","Fade out")):
                body+=v.field("sound."+key,title+" · mm:ss",time_text(clip[key]),32,multiline=False)
            body+=v.field("sound.gain","Clip level · dB",clip["gainDb"],32,multiline=False)
            body+=f'<Checkbox Name="sound.clip.mute" Text="Mute this clip" Checked="{str(clip["muted"]).lower()}"/>'
            body+='<HBox Spacing="7">'+v.button("Duplicate","sound.duplicate")+v.button("Split at cursor","sound.split")+'</HBox>'
            body+=v.button("Remove audio clip","sound.remove")
            body+=v.label(f"Available source: {time_text(clip['sourceFrames'])}. Out is the first unused frame.",11,True)
        else:
            body+=v.label("Choose an audio clip on the timeline, or import an audio file to the selected track.",13,True)
            body+=v.label("Dialogue, music, ambience, and effects are independent of picture cut boundaries. Multiple audio clips may overlap.",12,True)
        self.replace_host("sound.inspector",body)
        self.set_attr("sound.apply","Enabled",bool(clip))
        if clip: r.Rcombo_box_set_selected(self.find("sound.track"),next(i for i,t in enumerate(TRACKS) if t[0]==clip["track"]))

    def on_sound_selection(self,_sender,item,_user):
        if self.mode!="sound" or self.sound_loading or self.editorial_io_busy() or self.modal_kind: return
        if item in self.sound_items:
            self.sound_selection=self.sound_items[item]
            self.render_sound_inspector()

    def on_sound_change(self,_sender,_item,_user):
        if self.mode=="sound" and not self.sound_loading and not self.editorial_io_busy() and not self.modal_kind:
            self.sound_gesture_pending=True

    def on_sound_removed(self,_sender,item,_user):
        if self.mode=="sound" and not self.modal_kind and not self.sound_loading and not self.editorial_io_busy() and item in self.sound_items:
            self.sound_removed.add(self.sound_items[item]);self.sound_gesture_pending=True

    def on_sound_scrub(self,_sender,seconds,_user):
        if not self.sound_loading:
            self.sound_cursor=max(0,frames(seconds))
            self.seek(seconds)
            self.set_label("sound.status",f"Cursor {time_text(self.sound_cursor)} · position measured from the start of the film")

    def flush_sound_gesture(self):
        if self.mode!="sound" or not self.sound_doc or self.sound_loading or self.editorial_io_busy() or r.Rwindow_is_mouse_button_down(self.window,r.R_MOUSE_LEFT): return
        timeline=self.find("sound.timeline")
        if self.sound_gesture_pending:
            self.sound_gesture_pending=False
            changes={ident:(frames(r.Rtimeline_get_item_start(timeline,item)),frames(r.Rtimeline_get_item_length(timeline,item)),self.sound_tracks.get(r.Rtimeline_get_item_track(timeline,item),next(c["track"] for c in self.sound_doc["state"]["clips"] if c["id"]==ident))) for item,ident in self.sound_items.items() if ident not in self.sound_removed}
            try:
                self.sound_commit(sound_gesture(self.sound_doc["state"],changes,self.sound_removed),"Move or trim audio")
            except (ValueError,OSError) as error:
                self.render_sound();self.toast(error,True)
            self.sound_removed.clear()
        else:
            state=copy.deepcopy(self.sound_doc["state"])
            muted={ident:bool(r.Rtimeline_is_track_muted(timeline,track)) for track,ident in self.sound_tracks.items()}
            for t in state["tracks"]: t["muted"]=muted.get(t["id"],t["muted"])
            if state!=self.sound_doc["state"]:
                try: self.sound_commit(state,"Toggle sound track")
                except (ValueError,OSError) as error: self.render_sound();self.toast(error,True)

    def import_sound(self, source=None):
        if any(k in self.pending for k in ("rename","archive")) or self.editorial_io_busy(): return
        if source is None:
            raw=self.player.choose_media()
            if not raw: return
            source=Path(raw.decode())
        store=self.sound_store
        track=TRACKS[max(0,min(3,self.selected("sound.import.track")))][0]
        at=self.sound_cursor
        def done(clip):
            clip.update(track=track,atFrame=at)
            state=copy.deepcopy(self.sound_doc["state"]);state["clips"].append(clip)
            self.sound_selection=clip["id"]
            self.sound_commit(state,"Import audio")
            self.modal_close()
        self.submit("sound.import",lambda:preserve_audio(store,source),done)

    def show_project_sound(self):
        records=[x for x in self.workspace.catalogue(self.slug) if x.get("kind")=="audio"]
        self.sound_library=records
        body="".join(v.button(Path(x["path"]).name,"sound.library."+str(i)) for i,x in enumerate(records)) or v.label("No audio files in the project yet. Import an audio file or a take.",13,True)
        self.show_modal("Choose project audio",body)

    def sound_preview(self, kind):
        store,doc,cut= self.sound_store,copy.deepcopy(self.sound_doc),copy.deepcopy(self.cut_doc)
        production=view_project(self.slug,self.board,self.workspace)
        self.toast("Preparing the sound mix…" if kind=="listen" else "Preparing film with sound mix…")
        def operation():
            if kind=="listen": return render_mix(store,doc,cut)
            return export_cut(self.cut_store,cut,production,sound_store=store,sound_doc=doc,preview=kind=="film")
        def done(path):
            self.preview(path,"Sound mix" if kind=="listen" else "Film with sound mix",inline=kind in {"listen","film"})
            duration=sum(c["outFrame"]-c["inFrame"] for c in cut["clips"] if c["included"])/24
            self.player.seek(min(self.sound_cursor/24,max(0,duration-1/24)))
            self.on_action(None,"preview.play",None)
        self.submit("sound."+kind,operation,done)

    def preview_cut_mix(self):
        self.sound_store=SoundStore(self.workspace,self.slug)
        self.sound_doc=self.sound_store.load()
        self.sound_cursor=max(0,frames(self.clock))
        self.sound_preview("film")

    def sound_action(self, action):
        if not action.startswith("sound."): return False
        if not self.sound_doc: return True
        state=self.sound_doc["state"]
        if action=="sound.import": self.import_sound()
        elif action=="sound.library": self.show_project_sound()
        elif action.startswith("sound.library."):
            self.import_sound(Path(self.sound_library[int(action.rsplit(".",1)[1])]["path"]))
        elif action in {"sound.undo","sound.redo"}:
            self.stop_workspace_preview(restore=True)
            self.sound_doc=self.sound_store.history(self.sound_doc,action.endswith("redo"));self.render_sound()
        elif action=="sound.mixer.apply":
            updated=copy.deepcopy(state)
            for i,t in enumerate(updated["tracks"]):
                t.update(gainDb=float(self.text(f"sound.bus.{i}.gain")),muted=self.checked(f"sound.bus.{i}.mute"),solo=self.checked(f"sound.bus.{i}.solo"))
            updated["original"].update(enabled=self.checked("sound.original"),gainDb=float(self.text("sound.original.gain")))
            self.sound_commit(updated,"Change sound mixer")
        elif action=="sound.clip.apply" and self.sound_selection:
            values={key:time_frames(self.text("sound."+key)) for key in ("atFrame","inFrame","outFrame","fadeIn","fadeOut")}
            values.update(label=self.text("sound.label").strip(),track=TRACKS[self.selected("sound.track")][0],gainDb=float(self.text("sound.gain")),muted=self.checked("sound.clip.mute"))
            self.sound_commit(update_clip(state,self.sound_selection,**values),"Edit audio clip")
        elif action=="sound.remove" and self.sound_selection:
            self.sound_commit(remove_clip(state,self.sound_selection),"Remove audio clip")
        elif action in {"sound.duplicate","sound.split"} and self.sound_selection:
            updated=copy.deepcopy(state)
            clip=next(c for c in updated["clips"] if c["id"]==self.sound_selection)
            new=copy.deepcopy(clip);new["id"]=uuid.uuid4().hex
            length=clip["outFrame"]-clip["inFrame"]
            if action=="sound.split":
                offset=self.sound_cursor-clip["atFrame"]
                if not 0<offset<length: raise ValueError("Place the cursor inside the audio clip first.")
                clip["outFrame"]=clip["inFrame"]+offset;clip["fadeOut"]=0;clip["fadeIn"]=min(clip["fadeIn"],offset)
                new.update(atFrame=self.sound_cursor,inFrame=clip["outFrame"],fadeIn=0,fadeOut=min(new["fadeOut"],length-offset))
            else: new["atFrame"]+=length
            updated["clips"].append(new);self.sound_selection=new["id"]
            self.sound_commit(updated,"Split audio clip" if action=="sound.split" else "Duplicate audio clip")
        elif action in {"sound.listen","sound.film","sound.export"}: self.sound_preview(action.split(".")[1])
        return True

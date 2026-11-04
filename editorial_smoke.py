"""Real native structure/sound gestures in the disposable production workspace."""
import array
import copy
import math
import json
from pathlib import Path
import wave

import raven as r

from app import ROOT
import os
QA_ROOT=Path(os.environ.get("SPRINTWAKE_QA_DIR",str(ROOT/"qa")))
from sequence import timing
from sound_panel import SOUND_HEADER


def run(studio,pump,wait,check):
    canonical=studio.workspace.projects_dir/studio.slug/"storyboard.json"
    before=canonical.read_bytes()
    studio.on_action(None,"page.structure",None)
    wait(lambda:studio.mode=="structure" and "editorial.load" not in studio.pending,"Native structure loaded")
    original_cut=copy.deepcopy(studio.cut_doc["clips"])
    report=timing(studio.sequence_doc["state"],studio.cut_doc["clips"])
    check(report["frames"]==sum(c["outFrame"]-c["inFrame"] for c in original_cut if c["included"]),"Narrative runtime uses actual cut trims and exclusions")
    studio.on_action(None,"structure.edit.film.film",None)
    studio.set_text("structure.goal","02:00")
    studio.on_action(None,"structure.save",None)
    check(studio.sequence_doc["state"]["targetFrames"]==120*24 and studio.cut_doc["clips"]==original_cut,"Film runtime goal persists without stretching or rewriting picture")
    studio.on_action(None,"structure.add.sequence",None)
    studio.set_text("structure.name","Etter møtet")
    studio.on_action(None,"structure.create.sequence",None)
    seq=studio.sequence_doc["state"]["sequences"][-1]
    studio.on_action(None,"structure.add.scene."+seq["id"],None)
    studio.set_text("structure.name","En ny beslutning")
    studio.on_action(None,"structure.create.scene",None)
    scene=studio.sequence_doc["state"]["sequences"][-1]["scenes"][-1]
    studio.on_action(None,"structure.assign."+scene["id"],None)
    r.Rcheckbox_set_checked(studio.find("structure.shot.0"),True)
    studio.on_action(None,"structure.assign.save",None)
    assigned=studio.sequence_doc["state"]["sequences"][-1]["scenes"][-1]["shotIds"]
    check(assigned==[original_cut[0]["shotId"]],"Native scene assignment moves actual cut IDs into narrative scene")
    studio.on_action(None,"structure.order",None)
    studio.on_action(None,"structure.order.apply",None)
    check(studio.cut_doc["clips"]!=original_cut and {c["shotId"]:c for c in studio.cut_doc["clips"]}=={c["shotId"]:c for c in original_cut},"Applying narrative order preserves each take and source trim")
    studio.cut_doc=studio.cut_store.history(studio.cut_doc)
    pump(.2)
    studio.capture(QA_ROOT/"studio-structure.png")
    structure=copy.deepcopy(studio.sequence_doc)
    studio.on_action(None,"page.sound",None)
    wait(lambda:studio.mode=="sound" and "editorial.load" not in studio.pending,"Native sound workspace loaded")
    check(r.Rtimeline_get_track_count(studio.find("sound.timeline"))==5,"Native sound timeline has locked picture and four independent audio lanes")
    tone=studio.workspace.ensure_project(studio.slug)/"System/fixture-dialog.wav"
    values=array.array("h",(round(math.sin(2*math.pi*440*i/48000)*2000) for i in range(6*48000)))
    stereo=array.array("h",(sample for x in values for sample in (x,x)))
    with wave.open(str(tone),"wb") as out:
        out.setparams((2,2,48000,0,"NONE","not compressed"));out.writeframes(stereo.tobytes())
    studio.sound_cursor=24
    studio.import_sound(tone)
    wait(lambda:"sound.import" not in studio.pending,"Dialog audio imported")
    clip=studio.sound_doc["state"]["clips"][0]
    check(clip["atFrame"]==24 and clip["outFrame"]==144 and (studio.sound_store.project/clip["path"]).is_file(),"Imported dialogue has measured duration and preserved Input bytes")
    pump(.2)
    timeline=studio.find("sound.timeline")
    r.Rtimeline_set_view(timeline,0,15);r.Rtimeline_set_snapping(timeline,False)
    pump(.1)
    left,top,width,height=r.Rwidget_get_rect(timeline)
    lane=width-SOUND_HEADER
    x=left+SOUND_HEADER+3/15*lane
    y=top+30+28+17
    undo=len(studio.sound_doc["undo"])
    r.Rwindow_inject_mouse_move(studio.window,x,y)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,True);pump(.05)
    r.Rwindow_inject_mouse_move(studio.window,x+lane/15,y);pump(.1)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,False);pump(.2)
    moved=studio.sound_doc["state"]["clips"][0]
    check(abs(moved["atFrame"]-48)<=1 and moved["inFrame"]==0 and len(studio.sound_doc["undo"])==undo+1,"Native audio body drag moves across picture cuts as one undo action")
    studio.on_action(None,"sound.undo",None)
    check(studio.sound_doc["state"]["clips"][0]["atFrame"]==24,"Sound undo restores absolute film placement")
    pump(.2)
    left,top,width,height=r.Rwidget_get_rect(timeline)
    lane=width-SOUND_HEADER
    y=top+30+28+17
    tail=left+SOUND_HEADER+7/15*lane-2
    r.Rwindow_inject_mouse_move(studio.window,tail,y)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,True);pump(.05)
    r.Rwindow_inject_mouse_move(studio.window,tail-lane/15,y);pump(.1)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,False);pump(.2)
    if abs(studio.sound_doc["state"]["clips"][0]["outFrame"]-120)>2:
        studio.capture(QA_ROOT/"layout-sound-trim-failure.png")
        print("Sound trim diagnostic",studio.sound_doc["state"]["clips"][0],"rect",r.Rwidget_get_rect(timeline),"view",r.Rtimeline_get_view(timeline),flush=True)
    check(abs(studio.sound_doc["state"]["clips"][0]["outFrame"]-120)<=2,"Native audio edge drag trims the source without moving its film start")
    studio.on_action(None,"sound.undo",None)
    r.Rwidget_focus(timeline)
    r.Rwindow_inject_key(studio.window,r.R_KEY_DELETE,True,0)
    r.Rwindow_inject_key(studio.window,r.R_KEY_DELETE,False,0);pump(.2)
    check(not studio.sound_doc["state"]["clips"] and (studio.sound_store.project/clip["path"]).is_file(),"Native audio Delete preserves source bytes and can be undone")
    studio.on_action(None,"sound.undo",None)
    # Exact controls edit source trim, film placement, fades and gain.
    studio.sound_selection=clip["id"];studio.render_sound()
    studio.set_text("sound.atFrame","00:01");studio.set_text("sound.inFrame","00:01");studio.set_text("sound.outFrame","00:05")
    studio.set_text("sound.fadeIn","00:00.50");studio.set_text("sound.fadeOut","00:00.50");studio.set_text("sound.gain","-6")
    studio.on_action(None,"sound.clip.apply",None)
    edited=studio.sound_doc["state"]["clips"][0]
    check((edited["inFrame"],edited["outFrame"],edited["fadeIn"],edited["gainDb"])==(24,120,12,-6),"Native audio inspector saves source trim, fades and decibel gain")
    for track in (1,2,3):
        r.Rcombo_box_set_selected(studio.find("sound.import.track"),track)
        studio.sound_cursor=0;studio.import_sound(tone)
        wait(lambda:"sound.import" not in studio.pending,"Audio lane imported")
    check({c["track"] for c in studio.sound_doc["state"]["clips"]}=={"dialogue","music","ambience","effects"},"Independent dialogue, music, ambience and effects persist together")
    studio.set_text("sound.bus.1.gain","-12")
    r.Rcheckbox_set_checked(studio.find("sound.bus.2.mute"),True)
    r.Rcheckbox_set_checked(studio.find("sound.original"),False)
    studio.on_action(None,"sound.mixer.apply",None)
    check(studio.sound_doc["state"]["tracks"][2]["muted"] and studio.sound_doc["state"]["tracks"][1]["gainDb"]==-12,"Track mixer persists mute and group levels")
    sound=copy.deepcopy(studio.sound_doc)
    pump(.3);studio.capture(QA_ROOT/"studio-sound.png")
    r.Rwindow_set_size(studio.window,1180,760);pump(.3);studio.capture(QA_ROOT/"studio-sound-compact.png")
    r.Rwindow_set_size(studio.window,1440,920);pump(.2)
    studio.on_action(None,"sound.listen",None)
    wait(lambda:"sound.listen" not in studio.pending and studio.preview_media is not None,"Sound mix preview built",timeout=60)
    wait(lambda:studio.player.has_audio() and studio.player.time()>.1,"Actual native sound preview playing")
    check(studio.preview_media["playing"] and studio.player.has_audio(),"Native AVFoundation plays the actual saved four-track mix")
    studio.modal_close()
    studio.on_action(None,"sound.film",None)
    wait(lambda:"sound.film" not in studio.pending and studio.preview_media is not None,"Picture with mix preview built",timeout=90)
    wait(lambda:studio.player.has_audio() and studio.frame_count>0,"Picture and mixed audio preview playing")
    check(studio.preview_media["path"].endswith(".mp4") and "/System/editorial-preview/" in studio.preview_media["path"],"Film preview uses a retained mix without publishing production")
    studio.modal_close()
    studio.mode_set("scenes");studio.mode_set("structure")
    wait(lambda:studio.mode=="structure" and not studio.editorial_io_busy(),"Structure reopened")
    check(studio.sequence_doc==structure,"Narrative groups and runtime goals survive reopen")
    studio.mode_set("sound")
    wait(lambda:studio.mode=="sound" and not studio.editorial_io_busy(),"Sound reopened")
    if studio.sound_doc!=sound or canonical.read_bytes()!=before:
        old,new=json.loads(before),json.loads(canonical.read_bytes())
        print("Editorial reopen diagnostic",{"sound_changed":studio.sound_doc!=sound,"sound_before":sound,"sound_after":studio.sound_doc,"board_changed":{k:[old.get(k),new.get(k)] for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}},flush=True)
    check(studio.sound_doc==sound and canonical.read_bytes()==before,"Sound lanes and history survive reopen without canonical production changes")
    from concurrent.futures import Future
    for relocation in ("rename","archive"):
        studio.pending[relocation]=Future()
        try:
            before_sound=studio.sound_store.path.read_bytes()
            studio.on_action(None,"sound.remove",None);studio.open_story_workspace("structure")
            check(studio.sound_store.path.read_bytes()==before_sound and "editorial.load" not in studio.pending,f"Pending {relocation} blocks narrative and sound writes")
        finally: studio.pending.pop(relocation)
    studio.pending["sound.import"]=Future()
    try:
        for relocate in (lambda:studio.rename_project("Moved"),studio.archive_project):
            try: relocate()
            except ValueError: pass
            else: raise AssertionError("Relocation must wait for sound I/O")
        check("rename" not in studio.pending and "archive" not in studio.pending,"Sound I/O blocks project relocation in the opposite direction")
    finally: studio.pending.pop("sound.import")
    studio.mode_set("brief")

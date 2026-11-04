"""Native full-workflow test with real storage/API and no model inference."""
import copy
import json
from pathlib import Path
import shutil
import time

import raven as r

from app import ROOT
import os
QA_ROOT=Path(os.environ.get("SPRINTWAKE_QA_DIR",str(ROOT/"qa")))
from production_model import film_action


def run(studio):
    passed = []
    def pump(seconds=.1):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if not r.Rwait_frame(1/60): raise AssertionError("Studio-vinduet ble lukket")
            studio.tick()
    def wait(predicate, message, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not predicate(): pump(.05)
        assert predicate(), message
    def check(value, message):
        assert value, message
        passed.append(message)
    wait(lambda: studio.ready, "Backend ready")
    pump(.3)
    from connections_smoke import run as run_connections_smoke
    run_connections_smoke(studio,pump,wait,check)
    r.Rwindow_inject_mouse_move(studio.window,41.5,32)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,True)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,False)
    wait(lambda:r.Rwindow_is_minimized(studio.window),"Native minimize animation completed",timeout=3)
    check(r.Rwindow_is_minimized(studio.window), "Yellow custom-header button actually minimizes on macOS")
    r.Rwindow_restore(studio.window)
    pump(.4)
    check(not r.Rwindow_is_minimized(studio.window), "Minimized window restores normally")
    studio.capture(QA_ROOT / "studio-home.png")
    check(studio.mode == "home" and studio.board is None, "Start opens before project selection")
    source_image, probe_fixture = portable_fixtures(studio.workspace.root / "Tools/qa-media")
    studio.library.import_images({"Retained QA media": source_image})
    studio.mode_set("library")
    wait(lambda: bool(studio.library_items), "Shared library loaded without a project")
    check(studio.board is None and len(studio.library_items) == 1, "Shared library available before opening a project")
    studio.open_library(0); pump(.2)
    check(studio.preview_media is not None, "Shared library preview resolves real image")
    studio.modal_close(); studio.mode_set("home")
    # A proposal-only chat reply creates a REAL project only after native apply.
    actual_raven_chat=studio.backend.raven_chat
    session_id=studio.current_conversation["id"]
    studio.mode_set("director")
    studio.backend.raven_chat=lambda payload,progress=None:{"message":"Jeg foreslår prosjektet Samtaletest med manus.","proposalStatus":"pending","catalogVersion":"raven-director-1","actions":[{"tool":"create_project","name":"Samtaletest","brief":"En fugl finner hjem."},{"tool":"write_manuscript","brief":"En fugl finner hjem.","count":2}]}
    studio.set_text("director.message","Lag et prosjekt og skriv manus og klipp for filmen min.")
    studio.send_chat()
    wait(lambda:"chat" not in studio.pending and len(studio.history)==2,"Director proposal arrived")
    check(studio.board is None and not studio.summaries,"Director proposal does not claim or create unsaved projects")
    actual_authoring_chat=studio.backend.authoring_chat
    chat_authoring=[
        {"actions":[{"tool":"set_board_fields","fields":{"filmBible":{"acts":[{"id":"act-home","title":"Hjem"}],"locations":[{"id":"loc-home","title":"Hagen"}],"props":[]}}}]},
        {"actions":[{"tool":"add_shot","shot":{"title":f"Samtaleklipp {i}","prompt":"A small bird lands beside a quiet nest in warm daylight.","start":"A nest rests in a tree.","action":"A bird lands beside the nest.","end":"The bird rests by the nest.","camera":"Static wide shot","locationId":"loc-home","characterIds":[],"directorScene":i,"frames":121}} for i in range(1,3)]},
    ]
    studio.backend.authoring_chat=lambda payload:chat_authoring.pop(0)
    try:
        studio.apply_chat(1)
        wait(lambda:studio.board is not None and not studio.opening and "director.execute" not in studio.pending,"Director created actual project and manuscript")
    finally:
        studio.backend.authoring_chat=actual_authoring_chat
    check(studio.current_conversation["id"]==session_id and studio.current_conversation["projectSlug"]==studio.slug,"Director creates project and binds original conversation")
    check(studio.mode=="director" and (studio.workspace.projects_dir/studio.slug/"Script/manus.md").is_file(),"Created project remains in native chat with real manuscript")
    check(len(studio.board["shots"])==2 and studio.history[1]["execution"]["status"]=="done" and "Samtaleklipp 2" in (studio.workspace.projects_dir/studio.slug/"Script/manus.md").read_text(),"One reviewed chat proposal creates project, bible, scenes and persisted shot manuscript")
    studio.set_text("director.message","Fortsett med samme uttrykk.")
    studio.new_conversation()
    wait(lambda:studio.current_conversation["id"]!=session_id,"New conversation opens after pending authoring save")
    second_id=studio.current_conversation["id"]
    check(second_id!=session_id and not studio.history and not studio.text("director.message"),"New conversation has separate history and composer")
    studio.activate_conversation(session_id)
    check(studio.text("director.message")=="Fortsett med samme uttrykk.","Conversation switch restores its draft")
    # A late answer must land in its originating session, even after switching.
    import threading
    release=threading.Event()
    def late_reply(payload,progress=None):
        release.wait(5)
        return {"message":"Svar til den opprinnelige samtalen.","actions":[]}
    studio.backend.raven_chat=late_reply
    studio.set_text("director.message","Vurder kontinuiteten.")
    studio.send_chat(); studio.activate_conversation(second_id); release.set()
    wait(lambda:"chat" not in studio.pending,"Late reply saved")
    check(not studio.history and studio.conversations.load(session_id)["messages"][-1]["content"]=="Svar til den opprinnelige samtalen.","Late replies stay with their originating conversation")
    studio.backend.raven_chat=actual_raven_chat
    studio.new_conversation(True)
    check(studio.board is None and studio.mode=="director","Free idea starts without selected project")
    studio.backend.raven_chat=lambda payload,progress=None:{"message":"Her er biblioteket.","actions":[{"tool":"list_library"}]}
    studio.set_text("director.message","Vis biblioteket.")
    studio.send_chat()
    wait(lambda:studio.mode=="library" and "director.execute" not in studio.pending,"Director library tool completed")
    check(studio.board is None and studio.history[-1]["execution"]["status"]=="done","Read-only director tools run directly and persist actual results")
    studio.backend.raven_chat=actual_raven_chat
    studio.mode_set("director")
    studio.capture(QA_ROOT/"studio-director.png")
    studio.mode_set("home")
    studio.show_new_project()
    studio.set_text("new.title", "Replace this title")
    r.Rwidget_focus(studio.find("new.title"))
    studio.on_action(None, "edit.all", None)
    pump(.05)
    r.Rwindow_inject_text(studio.window, "En hilsen i hagen")
    pump(.05)
    check(studio.text("new.title") == "En hilsen i hagen", "Native edit menu selects and replaces single-line input")
    studio.set_text("new.brief", "En liten rød trefigur oppdager en solsikke i en varm hage.")
    r.Rcombo_box_set_selected(studio.find("new.style"), 1)
    studio.create_project()
    wait(lambda: studio.board is not None and "open" not in studio.pending, "Project created")
    check((studio.workspace.projects_dir / studio.slug / "Script/manus.md").is_file(), "New project and production folders outside engine")
    studio.mode_set("brief")
    studio.set_text("brief.description", "En rød trefigur står ved en solsikke i en varm hage.")
    studio.set_text("brief.style", "Warm handcrafted animation, red wooden character, soft daylight.")
    studio.sync_fields(); studio.save()
    wait(lambda: not studio.dirty and "save" not in studio.pending, "Brief saved")
    studio.show_entity("character")
    studio.set_text("entity.name", "Rødbert")
    studio.set_text("entity.description", "A small red wooden character with round eyes and a sunflower painted on the chest.")
    studio.save_entity()
    wait(lambda: not studio.dirty and "save" not in studio.pending, "Character saved")
    check(studio.board["characters"][0]["name"] == "Rødbert", "Film bible character persists")
    studio.add_shot()
    studio.set_text("clip.title", "Et lite hei")
    studio.set_text("clip.camera", "Static medium-wide shot at eye level")
    studio.set_text("clip.prompt", "A small red wooden character stands beside a sunflower, raises one hand and gives a gentle wave. Soft sunlight. Quiet garden ambience.")
    studio.set_text("clip.dialogue", "")
    r.Rcheckbox_set_checked(studio.find("clip.cast.0"), True)
    studio.apply_shot_details()
    studio.set_text("start", "The red wooden character stands beside the potted sunflower.")
    studio.set_text("action", "The character raises one hand and gives a gentle wave.")
    studio.set_text("end", "The raised hand remains visible beside the sunflower.")
    studio.sync_fields(); studio.save()
    wait(lambda: not studio.dirty and "save" not in studio.pending, "Shot saved")
    check(studio.board["shots"][0]["directorScene"] == 1 and studio.board["shots"][0]["characterIds"], "Shot authoring and cast links")
    studio.mode_set("library")
    wait(lambda: "library.catalogue" not in studio.pending, "Project library loaded")
    studio.adopt_library(0)
    wait(lambda: studio.mode == "images" and bool(studio.image_refs), "Shared image adopted into active project")
    check(studio.image_refs[0]["path"].startswith(studio.slug + "/"), "Shared image adoption uses canonical project refs")
    source = source_image
    slug = studio.slug
    def operation():
        original = studio.workspace.import_input(slug, source)
        return studio.backend.upload(slug, original)
    result = []
    studio.submit("smoke.upload", operation, result.append)
    wait(lambda: bool(result), "Reference uploaded")
    studio.board["shots"][0]["startRef"] = result[0]
    studio.refresh_view(); studio.set_dirty(True); studio.save()
    wait(lambda: not studio.dirty and "save" not in studio.pending, "Reference saved")
    studio.poll_deadline = 0
    wait(lambda: bool(studio.film_context), "Film status read")
    studio.poll_deadline = 0
    pump(2.4)
    check(studio.film_context["project"].get("canProduce"), "Core strict production preflight accepts reviewed authoring")
    action = film_action(studio.film_context, "prepare")
    check(action["prepareOnly"] is True and action["revision"] == studio.film_context["anchorRevision"], "Image preparation uses exact server revision")
    studio.mode_set("film"); pump(.4)
    studio.capture(QA_ROOT / "studio-production-editor.png")
    studio.mode_set("bible"); pump(.2)
    studio.capture(QA_ROOT / "studio-production-bible.png")
    studio.mode_set("images"); studio.render_images(); pump(.2)
    studio.capture(QA_ROOT / "studio-images.png")
    # Actual reference chooser must handle both absolute catalogue paths and
    # canonical server references without losing check state.
    studio.image_refs = [result[0]]
    studio.show_references(); pump(.1)
    studio.save_references()
    check(bool(studio.image_refs) and studio.image_refs[0]["path"].startswith(slug + "/"), "Reference chooser preserves canonical image selection")
    # Exercise compare and direct assignment with real retained pixels; this
    # fixture is explicitly retained media, not a claim of new generation.
    studio.images = [{"id":"retained-1", "status":"done", "ref":result[0], "prompt":"Retained reference", "seed":1},
                     {"id":"retained-2", "status":"done", "ref":result[0], "prompt":"Comparison fixture", "seed":2}]
    studio.selected_image = studio.images[0]
    studio.render_images(); studio.compare_images(); pump(.2)
    studio.capture(QA_ROOT / "studio-compare.png")
    check(studio.find("compare.a.image") != r.R_INVALID_ID, "Native image comparison uses actual retained files")
    studio.modal_close()
    studio.on_action(None, "image.assign", None)
    wait(lambda: not studio.dirty and "save" not in studio.pending, "Image assigned")
    studio.show_take_history(); wait(lambda: "take.history" not in studio.pending, "Take history loaded")
    check(studio.modal_kind == "Opptakshistorikk", "Actual take endpoint and empty history")
    studio.modal_close()
    studio.mode_set("production"); studio.render_production(); pump(.2)
    studio.capture(QA_ROOT / "studio-production.png")
    # Video/audio decode and full playback happen through the production modal.
    probe = probe_fixture
    video = studio.workspace.import_input(slug, probe)
    studio.preview(video, "Bevart avspillingstest · original lyd")
    wait(lambda: studio.player.duration() > 0, "Native movie ready")
    frames = studio.frame_count
    studio.on_action(None, "preview.play", None)
    try:
        wait(lambda: str(video.resolve()) in getattr(studio, "review_played", set()), "Full video/audio playback credited", timeout=20)
    except AssertionError:
        print("Playback diagnostic", {"path":str(video), "credited":list(getattr(studio,"review_played",set())), "clock":studio.player.time(), "duration":studio.player.duration(), "state":studio.preview_media, "frames":studio.frame_count}, flush=True)
        raise
    check(studio.frame_count > frames and studio.player.has_audio(), "Native AVFoundation frames and original audio through production preview")
    studio.capture(QA_ROOT / "studio-review.png")
    studio.modal_close()
    # Edits made while the previous save is in flight must survive its response.
    studio.mode_set("brief")
    studio.set_text("brief.sound", "First save")
    studio.sync_fields(); studio.save()
    studio.set_text("brief.sound", "Second edit survives the in-flight save")
    studio.sync_fields()
    wait(lambda: not studio.dirty and "save" not in studio.pending, "Concurrent edits saved")
    check(studio.board["soundscape"] == "Second edit survives the in-flight save", "In-flight save preserves newer authoring")
    studio.command_palette(); studio.set_text("command.query", "bilde"); studio.update_commands(); pump(.1)
    check(studio.find("command.results") != r.R_INVALID_ID, "Searchable native command palette")
    studio.modal_close()
    # Canonical authoring through the real native wizard and real CRUD API.
    # The model alone is inert; all persistence, schema mapping and UI are real.
    actual_chat = studio.backend.authoring_chat
    cast_id = studio.board["characters"][0]["id"]
    locations = studio.board["filmBible"]["locations"]
    location_id = locations[0]["id"]
    fixture = [
        {"proposalStatus":"ready", "actions":[{"tool":"set_board_fields", "fields":{"sceneDescription":"En liten figur vinker til en solsikke.","filmBible":{"acts":[{"id":"act1","title":"Hilsenen","description":"En liten figur hilser til en solsikke."}],"locations":locations}}}]},
        {"proposalStatus":"ready", "actions":[{"tool":"add_shot", "shot":{"title":f"Regissørklipp {i}","prompt":"A red wooden character stands beside a sunflower and slowly lowers one hand. Static medium shot in soft daylight, quiet garden ambience, no speech.","start":"The character holds one hand raised.","action":"The character lowers the hand.","end":"The hand rests at the side.","camera":"Static medium shot at eye level","locationId":location_id,"characterIds":[cast_id],"directorScene":i+1,"frames":121}} for i in range(1,3)]}
    ]
    studio.backend.authoring_chat = lambda payload: fixture.pop(0)
    try:
        studio.show_authoring()
        studio.set_text("author.brief","En liten figur vinker til en solsikke.")
        studio.set_text("author.count","2")
        studio.start_authoring()
        wait(lambda: "director.author" not in studio.pending and len(studio.board["shots"]) == 3, "Director writer saved actual shots")
        check(studio.mode == "brief" and studio.brief_view == "script", "Director authoring opens the saved manuscript in app")
        check(studio.board["shots"][-1]["title"] == "Regissørklipp 2" and "Regissørklipp 2" in (studio.workspace.projects_dir / studio.slug / "Script/manus.md").read_text(), "Director changes persist to canonical board and manuscript")
        studio.capture(QA_ROOT / "studio-manuscript.png")
    finally:
        studio.backend.authoring_chat = actual_chat
    # Editorial workflow uses real retained media and real seed-history API.
    # Creating fixture take receipts is not rendering or canonical adoption.
    sid = studio.board["shots"][0]["id"]
    for seed in (7, 8):
        take_dir = studio.workspace.projects_dir / slug / "seed-takes" / sid / str(seed)
        take_dir.mkdir(parents=True)
        shutil.copy2(probe, take_dir / "clip.mp4")
        (take_dir / "take.json").write_text(json.dumps({"seed":seed,"status":"done","outputs":["clip.mp4"]}))
    canonical_path = studio.workspace.projects_dir / slug / "storyboard.json"
    studio.save()
    wait(lambda:not studio.dirty and "save" not in studio.pending,"Authoring saved before editorial isolation check")
    canonical_before = canonical_path.read_bytes()
    studio.mode_set("scenes"); pump(.2)
    check(studio.find("shot.0") != r.R_INVALID_ID and studio.find("shot.2") != r.R_INVALID_ID and studio.find("scene.details") != r.R_INVALID_ID, "Compact native scene outline groups actual manuscript cards beside the player")
    from layout_smoke import geometry as layout_geometry
    layout_geometry(studio,check)
    studio.capture(QA_ROOT / "studio-scenes.png")
    studio.mode_set("cut")
    wait(lambda: studio.mode == "cut" and "cut.load" not in studio.pending, "Editorial cut opened")
    studio.select_shot(0)
    studio.on_action(None,"cut.takes",None)
    wait(lambda: "cut.takes" not in studio.pending, "Editorial take choices loaded")
    check(len(studio.cut_history_takes) == 2, "Editorial take picker reads real retained take endpoint")
    studio.on_action(None,"cut.take.0",None)
    wait(lambda:"cut.take" not in studio.pending,"Editorial take copied and selected")
    if not studio.cut_clip["source"]:
        print("Editorial take diagnostic", {"status":r.Rlabel_get_text(studio.w("status")), "clip":studio.cut_clip,"takes":studio.cut_history_takes},flush=True)
    check(studio.cut_clip["source"]["id"] == "7" and Path(studio.active_video).is_file(), "Take choice binds preserved video without production adoption")
    studio.set_text("cut.in","24"); studio.set_text("cut.out","96")
    studio.on_action(None,"cut.trim",None); pump(.3)
    check(abs(studio.shot.duration-3) < .001 and abs(studio.player.time()-1) < .1, "Editorial trim seeks original video and linked audio to its in point")
    studio.seek(.5); pump(.2)
    check(abs(studio.player.time()-1.5) < .1, "Editorial scrubbing maps timeline time to source time")
    # Real native mouse gesture: one tail drag is one persisted undo step.
    timeline=studio.w("timeline")
    r.Rtimeline_set_snapping(timeline,False)
    pump(.2)
    x,y,width,height=r.Rwidget_get_rect(timeline)
    start,end=r.Rtimeline_get_view(timeline)
    lane_width=width-112
    tail_x=x+112+(studio.shot.duration-start)/(end-start)*lane_width-2
    drag_y=y+30+21
    history_before=len(studio.cut_doc["undo"])
    r.Rwindow_inject_mouse_move(studio.window,tail_x,drag_y)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,True)
    pump(.05)
    r.Rwindow_inject_mouse_move(studio.window,tail_x-lane_width/(end-start)*.5,drag_y)
    pump(.1)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,False)
    pump(.2)
    check(studio.shot.duration < 2.8 and len(studio.cut_doc["undo"])==history_before+1, "Native timeline edge drag trims once with one undo step")
    studio.on_action(None,"cut.undo",None)
    check(abs(studio.shot.duration-3)<.001,"Undo restores the whole native trim gesture")
    pump(.2)
    timeline=studio.w("timeline")
    x,y,width,height=r.Rwidget_get_rect(timeline)
    start,end=r.Rtimeline_get_view(timeline)
    lane_width=width-112
    body_x=x+112+(1.5-start)/(end-start)*lane_width
    r.Rwindow_inject_mouse_move(studio.window,body_x,y+51)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,True)
    pump(.05)
    r.Rwindow_inject_mouse_move(studio.window,body_x+lane_width/(end-start)*6,y+51)
    pump(.1)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,False)
    pump(.2)
    check(studio.project.shots[1].id==sid,"Native timeline body drag reorders clips and closes gaps")
    studio.on_action(None,"cut.undo",None)
    r.Rwidget_focus(studio.w("timeline"))
    r.Rwindow_inject_key(studio.window,r.R_KEY_DELETE,True,0)
    r.Rwindow_inject_key(studio.window,r.R_KEY_DELETE,False,0)
    pump(.2)
    check(len(studio.project.shots)==2,"Native timeline delete excludes reversibly without deleting media")
    studio.on_action(None,"cut.undo",None)
    studio.select_shot(next(i for i,s in enumerate(studio.project.shots) if s.id==sid))
    studio.on_action(None,"cut.down",None)
    check(studio.project.shots[1].id == sid,"Editorial reorder changes the cut without reordering the manuscript")
    studio.on_action(None,"cut.undo",None); studio.on_action(None,"cut.redo",None)
    check(studio.project.shots[1].id == sid,"Editorial redo restores clip order")
    studio.on_action(None,"cut.exclude",None)
    check(len(studio.project.shots)==2,"Editorial exclusion retains the source and removes only the cut segment")
    studio.on_action(None,"cut.undo",None)
    studio.select_shot(1)
    studio.on_action(None,"cut.takes",None)
    wait(lambda:"cut.takes" not in studio.pending,"Take history reloaded")
    studio.on_action(None,"cut.take.1",None)
    wait(lambda:"cut.take" not in studio.pending,"Alternative take selected")
    check(studio.cut_clip["source"]["id"]=="8","Alternative take selection changes only the editorial source")
    studio.on_action(None,"cut.undo",None)
    check(studio.cut_clip["source"]["id"]=="7" and studio.cut_clip["inFrame"]==24,"Undo take choice restores its earlier trim too")
    pump(.5)
    studio.capture(QA_ROOT / "studio-cut.png")
    r.Rwindow_set_size(studio.window,1180,760); pump(.3)
    studio.capture(QA_ROOT / "studio-cut-compact.png")
    r.Rwindow_set_size(studio.window,1440,920); pump(.2)
    # Placeholder export is explicitly a rough cut, separate from approvals.
    studio.on_action(None,"cut.export",None)
    wait(lambda:"cut.export" not in studio.pending and studio.preview_media is not None,"Editorial rough cut exported",timeout=60)
    check(Path(studio.preview_media["path"]).is_file(),"Editorial export contains selected takes, trims and storyboard placeholders")
    studio.modal_close()
    saved_cut=copy.deepcopy(studio.cut_doc)
    studio.mode_set("film")
    if canonical_path.read_bytes()!=canonical_before:
        before_doc=json.loads(canonical_before)
        after_doc=json.loads(canonical_path.read_bytes())
        print("Editorial isolation diagnostic",{k:[before_doc.get(k),after_doc.get(k)] for k in before_doc.keys()|after_doc.keys() if before_doc.get(k)!=after_doc.get(k)},flush=True)
    check(studio.project.shots[0].id==sid and canonical_path.read_bytes()==canonical_before,"Editorial operations leave the canonical manuscript and production bytes unchanged")
    studio.mode_set("cut")
    wait(lambda:studio.mode=="cut" and "cut.load" not in studio.pending,"Cut reopened")
    check(studio.cut_doc==saved_cut,"Cut source choices, order, trim and undo history survive reopen")
    # A new canonical card arriving from another client is appended without
    # disturbing the current edit or its undo states.
    from workspace import new_shot
    external=copy.deepcopy(studio.board)
    new_card=new_shot("Tilkommet manusklipp",10)
    new_card["locationId"]=external["filmBible"]["locations"][0]["id"]
    external["shots"].append(new_card)
    studio.backend.put(f"/api/boards/{slug}",external)
    studio.poll_deadline=0
    wait(lambda:any(c["shotId"]==new_card["id"] for c in studio.cut_doc["clips"]),"External manuscript card reconciled into open cut")
    check(studio.cut_doc["clips"][:-1]==saved_cut["clips"],"New manuscript cards preserve existing editorial choices and order")
    from concurrent.futures import Future
    cut_before=studio.cut_store.path.read_bytes()
    for relocation in ("rename","archive"):
        studio.pending[relocation]=Future()
        try:
            studio.mode_set("cut")
            studio.open_cut()
            studio.on_action(None,"cut.down",None)
            check("cut.load" not in studio.pending and studio.cut_store.path.read_bytes()==cut_before,f"Pending {relocation} blocks cut I/O and editorial mutation")
        finally:
            studio.pending.pop(relocation)
    studio.pending["cut.export"]=Future()
    try:
        for action in (lambda:studio.rename_project("Moved"),studio.archive_project):
            try: action()
            except ValueError: pass
            else: raise AssertionError("Relocation must wait for cut I/O")
        check("rename" not in studio.pending and "archive" not in studio.pending,"Cut I/O blocks rename and archive in the opposite direction too")
    finally:
        studio.pending.pop("cut.export")
    # Coverage uses its own journal and real native custom-widget drag gestures.
    studio.mode_set("film")
    studio.show_entity("character")
    studio.set_text("entity.name","Blåbert")
    studio.set_text("entity.description","A small blue wooden character.")
    studio.save_entity()
    wait(lambda:not studio.dirty and "save" not in studio.pending,"Second coverage character saved")
    studio.mode_set("coverage");pump(.3)
    canonical_coverage_before=canonical_path.read_bytes()
    check(studio.find("coverage.plan")!=r.R_INVALID_ID and len(studio.coverage_plan["actors"])==2,"Native room map links the actual project cast")
    def drag_room(start,end):
        from coverage_view import to_pixel
        left,top,width,height=r.Rwidget_get_rect(studio.find("coverage.plan"))
        sx,sy=to_pixel(studio.coverage_plan,*start,width,height)
        ex,ey=to_pixel(studio.coverage_plan,*end,width,height)
        r.Rwindow_inject_mouse_move(studio.window,left+sx,top+sy)
        r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,True);pump(.05)
        r.Rwindow_inject_mouse_move(studio.window,left+ex,top+ey);pump(.1)
        r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,False);pump(.2)
    a=studio.coverage_plan["actors"][0]
    undo_before=len(studio.coverage_doc["undo"])
    drag_room((a["x"],a["y"]),(a["x"]-.5,a["y"]))
    check(abs(studio.coverage_plan["actors"][0]["x"]-2.5)<.05 and len(studio.coverage_doc["undo"])==undo_before+1,"Native actor drag persists as one undo step")
    studio.on_action(None,"coverage.undo",None)
    check(abs(studio.coverage_plan["actors"][0]["x"]-3)<.001,"Room map undo restores figure placement")
    studio.on_action(None,"coverage.propose",None);pump(.2)
    check(len(studio.coverage_plan["cameras"])==5,"Coverage proposes master, reverse shots and close-ups")
    cx,cy,cw,ch=r.Rwidget_get_rect(studio.find("coverage.use.0"))
    r.Rwindow_inject_mouse_move(studio.window,cx+cw/2,cy+ch/2)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,True);pump(.05)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,False);pump(.1)
    check(not studio.coverage_plan["cameras"][0]["selected"],"Native coverage checkbox persists camera selection")
    studio.on_action(None,"coverage.undo",None)
    studio.on_action(None,"coverage.select.camera.master",None)
    c=studio.coverage_plan["cameras"][0]
    drag_room((c["x"],c["y"]),(c["x"],1))
    from coverage import warnings,target
    check(bool(warnings(studio.coverage_plan,studio.coverage_camera)),"Native camera drag across axis exposes continuity warning")
    before_count=len(studio.board["shots"])
    studio.on_action(None,"coverage.apply",None)
    wait(lambda:"coverage.apply" not in studio.pending,"Unsafe coverage rejected")
    check(len(studio.board["shots"])==before_count,"Unsafe camera proposal cannot write storyboard shots")
    studio.on_action(None,"coverage.undo",None)
    studio.on_action(None,"coverage.select.camera.master",None)
    c=studio.coverage_camera
    aim=target(studio.coverage_plan,c)
    drag_room(aim[:2],(aim[0]+.3,aim[1]))
    check(not studio.coverage_camera["aimIds"],"Native camera target drag changes the actual aim")
    studio.on_action(None,"coverage.undo",None)
    studio.on_action(None,"coverage.select.camera.master",None)
    studio.capture(QA_ROOT/"studio-coverage.png")
    r.Rwindow_set_size(studio.window,1180,760);pump(.3)
    studio.capture(QA_ROOT/"studio-coverage-compact.png")
    r.Rwindow_set_size(studio.window,1440,920);pump(.2)
    saved_coverage=copy.deepcopy(studio.coverage_doc)
    studio.mode_set("scenes");studio.mode_set("coverage")
    check(studio.coverage_doc==saved_coverage and canonical_path.read_bytes()==canonical_coverage_before,"Room placements and undo history reopen without changing production")
    studio.on_action(None,"coverage.apply",None)
    wait(lambda:"coverage.apply" not in studio.pending,"Coverage applied to actual backend")
    check(len(studio.board["shots"])==before_count+5 and len({s["directorScene"] for s in studio.board["shots"][-5:]})==5,"Selected camera angles create five separate canonical camera groups")
    studio.on_action(None,"coverage.apply",None)
    wait(lambda:"coverage.apply" not in studio.pending,"Repeated coverage applied")
    check(len(studio.board["shots"])==before_count+5,"Repeated identical coverage application does not duplicate shots")
    studio.on_action(None,"coverage.image",None)
    wait(lambda:"coverage.image" not in studio.pending,"Layout reference prepared")
    check(studio.mode=="images" and studio.workspace.local_media(studio.image_refs[0]["path"]).read_bytes().startswith(b"\x89PNG"),"Camera handoff supplies a real layout image without inference")
    studio.mode_set("coverage")
    coverage_bytes=studio.coverage_store.path.read_bytes()
    for relocation in ("rename","archive"):
        studio.pending[relocation]=Future()
        try:
            studio.on_action(None,"coverage.propose",None)
            check(studio.coverage_store.path.read_bytes()==coverage_bytes,f"Pending {relocation} blocks room-map writes")
        finally: studio.pending.pop(relocation)
    studio.pending["coverage.image"]=Future()
    try:
        for relocation in (lambda:studio.rename_project("Moved"),studio.archive_project):
            try: relocation()
            except ValueError: pass
            else: raise AssertionError("Relocation must wait for coverage I/O")
        check("rename" not in studio.pending and "archive" not in studio.pending,"Coverage I/O blocks project relocation")
    finally: studio.pending.pop("coverage.image")
    from editorial_smoke import run as run_editorial
    run_editorial(studio,pump,wait,check)
    from layout_smoke import run as run_layout
    run_layout(studio,pump,wait,check)
    # Inert model proposal, real native auto-inspection and persisted receipts.
    from director_contract import CONTRACT
    from director_editorial import EDITORIAL_TOOLS
    studio.mode_set("director")
    if studio.dirty: studio.save()
    wait(lambda:not studio.dirty and "save" not in studio.pending,"Editorial context saved before inspection")
    files = [studio.workspace.projects_dir / slug / "Script" / (name + ".json") for name in ("cut","coverage","structure","sound")]
    files.append(studio.workspace.projects_dir / slug / "storyboard.json")
    before_inspection = {path: path.read_bytes() for path in files}
    actual_chat = studio.backend.raven_chat
    studio.backend.raven_chat = lambda payload,progress=None: {"message":"Jeg leser de lagrede redigeringsvalgene.","proposalStatus":"pending","catalogVersion":CONTRACT.CATALOG_VERSION,"actions":[{"tool":name} for name in EDITORIAL_TOOLS]}
    try:
        studio.set_text("director.message","Se på klippen, romkartet, sekvensene og lydsporene.")
        studio.send_chat()
        wait(lambda:"chat" not in studio.pending and "director.execute" not in studio.pending and bool((studio.history[-1].get("execution") or {}).get("result")),"Director inspected editorial journals automatically")
    finally: studio.backend.raven_chat = actual_chat
    inspected = studio.history[-1]["execution"]["result"]
    check({row["tool"] for row in inspected["results"]}==set(EDITORIAL_TOOLS) and all(row["editorial"]["status"]=="ok" for row in inspected["results"]),"Director reads all four real editorial journals through validated tools")
    check(all(path.read_bytes()==data for path,data in before_inspection.items()),"Agent inspection preserves canonical production and every journal byte")
    check(studio.modal_kind=="Regissørens resultat","Read-only director receipt opens in the actual native window")
    studio.capture(QA_ROOT/"studio-director-editorial.png")
    studio.on_action(None,"page.sound",None)
    wait(lambda:studio.mode=="sound" and "editorial.load" not in studio.pending,"Director receipt opens native sound panel")
    check(studio.mode=="sound","Editorial receipt navigates to the correct native work surface")
    studio.mode_set("brief")
    output = []
    studio.submit("smoke.export", lambda: studio.workspace.export(slug), output.append)
    wait(lambda: bool(output), "Export complete")
    check(output[0].is_file(), "Portable export contains real project")
    original_slug = studio.slug
    studio.board = None
    studio.dirty = False
    studio.open_project(original_slug)
    wait(lambda: studio.board is not None and "open" not in studio.pending, "Project reopened")
    check(studio.board["shots"][0]["title"] == "Et lite hei" and bool(studio.board["shots"][0]["startRef"]), "Reopen preserves script, cast and references")
    # Delete and restore only this disposable test production through native UI actions.
    project_path=studio.workspace.projects_dir/original_slug
    preserved_ref=studio.board["shots"][0]["startRef"]["path"]
    studio.mode_set("home")
    studio.on_action(None,"project.delete."+original_slug,None)
    wait(lambda:studio.modal_kind=="Slett prosjekt?", "Delete confirmation opens after pending edits save")
    check(studio.modal_kind=="Slett prosjekt?", "Delete is discoverable on project cards with explicit confirmation")
    before=(project_path/"storyboard.json").read_bytes()
    studio.on_action(None,"project.delete.confirm",None)
    wait(lambda:"project.delete" not in studio.pending,"Project deletion completed")
    check(not project_path.exists() and original_slug not in {x["slug"] for x in studio.summaries} and studio.board is None,"Delete removes project from active library and clears selected state")
    entries=studio.workspace.trashed_projects()
    check(len(entries)==1 and (studio.workspace.root/"Trash"/entries[0]["id"]/"Project/storyboard.json").read_bytes()==before,"Deleted production is byte-identical in recoverable trash")
    studio.show_project_trash(); pump(.2)
    studio.capture(QA_ROOT/"studio-project-trash.png")
    studio.on_action(None,"project.restore."+entries[0]["id"],None)
    wait(lambda:studio.slug==original_slug and studio.board is not None and not studio.opening,"Project restored")
    check((project_path/"storyboard.json").read_bytes()==before and studio.workspace.local_media(preserved_ref).is_file(),"Restore keeps manuscript, cast, actual media and canonical references")
    from creation_smoke import run as run_creation
    run_creation(studio,pump,wait,check)
    check(not studio.errors, "No Raven XML/theme/runtime errors")
    report = {"passed": passed, "raven_errors": studio.errors, "backend_pid": studio.backend.process.pid,
              "root": str(studio.workspace.root), "models_started": False}
    (QA_ROOT / "production-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


def portable_fixtures(directory):
    """Synthetic audio/video sources; a clean checkout needs no private media."""
    import subprocess
    from app import write_png
    from media_tools import executable
    directory.mkdir(parents=True,exist_ok=True)
    image=directory/"reference.png"
    pixels=bytes(channel for y in range(256) for x in range(384) for channel in (x%256,y,((x//24+y//24)%2)*180+40,255))
    write_png(image,pixels,384,256)
    video=directory/"source.mp4"
    subprocess.run([executable('ffmpeg'),'-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=384x256:rate=24','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','7.291666667','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-ac','2','-threads','2',str(video)],check=True,capture_output=True,timeout=60)
    return image,video

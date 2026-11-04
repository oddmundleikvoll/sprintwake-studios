import copy
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from coverage import CoverageStore, camera_basis, camera_key, new_plan, project_point, propose, side_distance, storyboard_shots, target, validate, warnings
from coverage_io import apply_coverage, layout_reference
from coverage_render import render_pixels, write_layout
from studio_state import authoring_digest
from workspace import Workspace, new_shot


def board_fixture():
    return {"name":"Dialog", "characters":[{"id":"ada","name":"Ada","description":"Red coat"},{"id":"bo","name":"Bo","description":"Blue coat"}],
            "filmBible":{"locations":[{"id":"room","title":"Stuen"}]},"shots":[new_shot("Eksisterende",7)]}


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.board=board_fixture()
        self.plan=new_plan(self.board,"room")
        self.plan["cameras"]=propose(self.plan)
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace=Workspace(Path(self.temporary.name))
        self.store=CoverageStore(self.workspace,"film")

    def test_default_five_views_have_safe_positions_and_framing(self):
        validate(self.plan)
        self.assertEqual([c["kind"] for c in self.plan["cameras"]],["master","ots","ots","close","close"])
        for c in self.plan["cameras"]:
            self.assertFalse(warnings(self.plan,c),(c["name"],warnings(self.plan,c)))
            self.assertGreater(side_distance(self.plan,c["x"],c["y"]),.1)
            self.assertTrue(.1<c["x"]<7.9 and .1<c["y"]<5.9)
        master=self.plan["cameras"][0]
        for a in self.plan["actors"]:
            for z in (0,a["height"]):
                p=project_point(self.plan,master,(a["x"],a["y"],z))
                self.assertTrue(0<p[0]<1 and 0<p[1]<1)

    def test_axis_rotations_and_both_sides_keep_subject_order(self):
        for angle in (0,.5,1,1.5,2.5,3.1,4.5,5.5):
            for side in (-1,1):
                plan=copy.deepcopy(self.plan)
                plan.update(width=10,depth=10,side=side)
                for a,sign in zip(plan["actors"],(-1,1)):
                    a.update(x=5+sign*math.cos(angle),y=5+sign*math.sin(angle))
                plan["cameras"]=propose(plan)
                for c in plan["cameras"]:
                    self.assertFalse(warnings(plan,c),(angle,side,c["name"],warnings(plan,c)))
                    pa,pb=[project_point(plan,c,(a["x"],a["y"],a["height"]*.91)) for a in plan["actors"]]
                    if pa and pb: self.assertGreater((pa[0]-pb[0])*side,0)

    def test_wall_adjacent_proposals_never_select_unusable_framing(self):
        self.plan["actors"][0].update(x=6.448447,y=1.746133)
        self.plan["actors"][1].update(x=7.601071,y=.544362)
        self.plan["cameras"]=propose(self.plan)
        self.assertTrue(any(c["selected"] for c in self.plan["cameras"]))
        for camera in self.plan["cameras"]:
            if camera["selected"]: self.assertFalse(warnings(self.plan,camera))
        self.assertTrue(storyboard_shots(self.board,self.plan))

    def test_projection_aim_is_center_and_behind_is_hidden(self):
        c=self.plan["cameras"][-1]
        aim=target(self.plan,c)
        self.assertEqual(tuple(round(v,6) for v in project_point(self.plan,c,aim)[:2]),(.5,.5))
        forward,_,_=camera_basis(self.plan,c)
        behind=[c[k]-forward[i] for i,k in enumerate(("x","y","z"))]
        self.assertIsNone(project_point(self.plan,c,behind))

    def test_crossing_axis_is_visible_and_blocks_storyboard(self):
        self.plan["cameras"][0]["y"]=1
        self.assertIn("handlingsaksen",warnings(self.plan,self.plan["cameras"][0])[0])
        with self.assertRaisesRegex(ValueError,"handlingsaksen"): storyboard_shots(self.board,self.plan)
        self.plan["cameras"][0]["selected"]=False
        self.assertEqual(len(storyboard_shots(self.board,self.plan)),4)

    def test_coincident_axis_and_empty_plan_cannot_propose(self):
        self.plan["actors"][1].update(x=3.1,y=3)
        with self.assertRaisesRegex(ValueError,"40 cm"): propose(self.plan)
        plan=new_plan({"characters":[self.board["characters"][0]]},"room")
        self.assertEqual(len(propose(plan)),2)
        plan.update(actors=[],axis=[])
        with self.assertRaisesRegex(ValueError,"én figur"): propose(plan)

    def test_invalid_nonfinite_types_bounds_and_references_rejected(self):
        cases=[("width",float("nan")),("side",True),("depth",1),("aspect",4),("axis",["missing","actor-1"])]
        for key,value in cases:
            plan=copy.deepcopy(self.plan);plan[key]=value
            with self.assertRaises(ValueError): validate(plan)
        for key,value in (("lens",500),("z",0),("aimIds",["missing"]),("selected",1)):
            plan=copy.deepcopy(self.plan);plan["cameras"][0][key]=value
            with self.assertRaises(ValueError): validate(plan)

    def test_new_shots_are_separate_groups_with_cast_and_no_media_mutation(self):
        self.board["shots"][0].update(outputs=["retained.mp4"],status="done")
        before=copy.deepcopy(self.board)
        shots=storyboard_shots(self.board,self.plan)
        self.assertEqual(self.board,before)
        self.assertEqual([s["directorScene"] for s in shots],[8,9,10,11,12])
        self.assertTrue(all(s["frames"]%8==1 and s["status"]=="draft" and not s["outputs"] for s in shots))
        self.assertTrue(all(s["locationId"]=="room" and s["characterIds"] for s in shots))
        self.assertTrue(all("screen directions" in s["camera"] for s in shots))

    def test_shot_ids_are_idempotent_but_new_geometry_gets_new_drafts(self):
        shots=storyboard_shots(self.board,self.plan)
        self.board["shots"]+=shots
        self.assertEqual(storyboard_shots(self.board,self.plan),[])
        self.plan["cameras"][-1]["selected"]=False
        self.assertEqual(storyboard_shots(self.board,self.plan),[])
        self.plan["cameras"][0]["x"]+=.1
        self.assertEqual(len(storyboard_shots(self.board,self.plan)),1)

    def test_placeholders_and_missing_bible_links_block_application(self):
        plan=new_plan({})
        plan["cameras"]=propose(plan)
        with self.assertRaisesRegex(ValueError,"sted"): storyboard_shots(self.board,plan)
        plan["locationId"]="room"
        with self.assertRaisesRegex(ValueError,"karakterer"): storyboard_shots(self.board,plan)

    def test_persistent_history_cas_and_redo(self):
        initial=self.store.load()
        doc=self.store.commit(initial,{"room":self.plan})
        changed=copy.deepcopy(self.plan);changed["actors"][0]["x"]+=.2
        doc=self.store.commit(doc,{"room":changed})
        self.assertEqual(CoverageStore(self.workspace,"film").load(),doc)
        doc=self.store.commit(doc,doc["plans"],history="undo")
        self.assertEqual(doc["plans"]["room"],self.plan)
        doc=self.store.commit(doc,doc["plans"],history="redo")
        self.assertEqual(doc["plans"]["room"],changed)
        before=self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError,"annet vindu"): self.store.commit(initial,{"room":self.plan})
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_malformed_history_and_symlink_refused(self):
        doc=self.store.load();doc["undo"]=None
        self.store.path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError,"historikk"): self.store.load()
        self.store.path.unlink()
        outside=Path(self.temporary.name)/"outside.json";outside.write_text("{}")
        self.store.path.symlink_to(outside)
        with self.assertRaises(ValueError): self.store.load()
        self.assertEqual(outside.read_text(),"{}")

    def test_history_is_bounded_and_export_keeps_plans(self):
        doc=self.store.load()
        for i in range(35):
            plan=copy.deepcopy(self.plan);plan["title"]=str(i)
            doc=self.store.commit(doc,{"room":plan})
        self.assertEqual(len(doc["undo"]),30)
        archive=self.workspace.export("film")
        with zipfile.ZipFile(archive) as zipped:
            saved=next(n for n in zipped.namelist() if n.endswith("Script/coverage.json"))
            self.assertEqual(json.loads(zipped.read(saved)),doc)

    def test_layout_is_real_png_and_changes_with_camera_and_actor_positions(self):
        c=self.plan["cameras"][0]
        path=Path(self.temporary.name)/"layout.png"
        write_layout(path,self.plan,c,width=120)
        data=path.read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(struct.unpack(">II",data[16:24]),(120,68))
        a,_,_=render_pixels(self.plan,c,width=120)
        empty,_,_=render_pixels(self.plan,c,width=120,empty=True)
        self.assertNotEqual(a,empty)
        b,_,_=render_pixels(self.plan,self.plan["cameras"][-1],width=120)
        self.assertNotEqual(a,b)
        plan=copy.deepcopy(self.plan);plan["actors"][0]["x"]+=.5
        moved,_,_=render_pixels(plan,c,width=120)
        self.assertNotEqual(a,moved)

    def test_stale_board_fails_before_write(self):
        class Fake:
            def get(_,path): return {"board":self.board}
            def put(*args,**kwargs): raise AssertionError("Unexpected write")
        with self.assertRaisesRegex(ValueError,"Manuset er endret"):
            apply_coverage(self.workspace,Fake(),"film","0"*64,self.plan)


class CoverageRealApiTests(unittest.TestCase):
    def test_actual_cas_apply_reopen_layout_upload_and_script(self):
        from backend import Backend, BackendError
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory));backend=Backend(workspace.root)
            try:
                backend.start()
                created=backend.post("/api/boards",{"name":"Coverage test"});slug=created["slug"]
                board=created["board"];fixture=board_fixture()
                board.update(characters=fixture["characters"],filmBible=fixture["filmBible"],shots=fixture["shots"])
                board=backend.put(f"/api/boards/{slug}",board)["board"]
                plan=new_plan(board,"room");plan["cameras"]=propose(plan)
                actual,count=apply_coverage(workspace,backend,slug,authoring_digest(board),plan)
                self.assertEqual(count,5)
                self.assertEqual(len(actual["shots"]),6)
                again,count=apply_coverage(workspace,backend,slug,authoring_digest(actual),plan)
                self.assertEqual(count,0)
                self.assertEqual(again["shots"],actual["shots"])
                self.assertIn("Totalbilde",(workspace.ensure_project(slug)/"Script/manus.md").read_text())
                ref,prompt=layout_reference(workspace,backend,slug,plan,plan["cameras"][0])
                self.assertTrue(workspace.local_media(ref["path"]).read_bytes().startswith(b"\x89PNG"))
                self.assertIn("reference 1",prompt)
                self.assertEqual(backend.get(f"/api/boards/{slug}")["board"]["shots"],actual["shots"])
            finally:
                backend.close()

from pathlib import Path
import tempfile
import unittest

from conversations import ConversationStore
from project_removal import ProjectRemovalMixin
from workspace import Workspace, _atomic_text
import json


class Fixture(ProjectRemovalMixin):
    def __init__(self,root):
        self.workspace=Workspace(root); self.workspace.ensure()
        self.slug="film"; self.board={"name":"Film","shots":[]}
        project=self.workspace.ensure_project(self.slug)
        _atomic_text(project/"storyboard.json",json.dumps(self.board))
        self.summaries=self.workspace.project_summaries()
        self.conversations=ConversationStore(self.workspace)
        self.current_conversation=self.conversations.create(self.slug)
        self.history=[]; self.ready=True; self.closing=False; self.dirty=False
        self.pending={}; self.epoch=1; self.busy=False; self.submitted=None
        self.backend=self
    def get(self,path):
        if path=="/api/status": return {"busy":self.busy}
        if path.startswith("/api/boards/"): return {"board":self.board}
        return {}
    def post(self,path,payload):
        if self.busy: raise RuntimeError("Vent til aktivt arbeid er avsluttet")
        if path.endswith("/restore"):
            return {"slug":self.workspace.restore_project(path.split("/")[-2])}
        if payload.get("confirmName")!=self.board["name"]: raise ValueError("Navnet er endret")
        return {"entry":str(self.workspace.trash("film"))}
    def capture_drafts(self): pass
    def show_modal(self,title,body,*args,**kwargs): self.modal_kind=title
    def modal_close(self): self.modal_kind=""
    def mode_set(self,mode): self.mode=mode
    def toast(self,message): self.notice=message
    def submit(self,key,operation,done,**kwargs): self.submitted=(key,operation,done)
    def _free_project(self): self.board=None; self.slug=""
    def open_project(self,slug): self.opened=slug
    def complete(self):
        key,operation,done=self.submitted
        done(operation())


class ProjectRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.studio=Fixture(Path(self.temp.name))
    def tearDown(self): self.temp.cleanup()
    def test_busy_production_refuses_deletion(self):
        studio=self.studio
        studio.show_delete_project(); studio.busy=True
        studio.delete_project(); studio.complete()
        self.assertTrue((studio.workspace.projects_dir/"film/storyboard.json").is_file())
        self.assertEqual(studio.workspace.trashed_projects(),[])
        self.assertEqual(studio.modal_kind,"Prosjektet kunne ikke slettes")
    def test_delete_rebinds_history_clears_selection_and_restore_opens_project(self):
        studio=self.studio
        studio.show_delete_project()
        self.assertEqual(studio.modal_kind,"Slett prosjekt?")
        studio.delete_project(); studio.complete()
        self.assertIsNone(studio.board)
        self.assertEqual(studio.summaries,[])
        self.assertIsNone(studio.conversations.load(studio.current_conversation["id"])["projectSlug"])
        entry=studio.workspace.trashed_projects()[0]
        studio.restore_deleted_project(entry["id"]); studio.complete()
        self.assertEqual(studio.opened,"film")
        self.assertEqual(studio.summaries[0]["slug"],"film")
    def test_confirmation_never_deletes(self):
        studio=self.studio
        studio.show_delete_project()
        self.assertTrue((studio.workspace.projects_dir/"film").is_dir())
        self.assertIsNone(studio.submitted)

    def test_inflight_authoring_or_upload_cannot_race_deletion(self):
        for key in ["chat","save","upload","director.execute"]:
            with self.subTest(key=key):
                self.studio.pending={key:object()}
                with self.assertRaises(ValueError): self.studio.show_delete_project()
                self.assertTrue((self.studio.workspace.projects_dir/"film").is_dir())

    def test_committed_delete_reports_success_when_conversation_refresh_fails(self):
        studio=self.studio
        studio.show_delete_project()
        def unavailable(*args,**kwargs): raise OSError("temporary metadata failure")
        studio.conversations.rebind_project=unavailable
        studio.delete_project(); studio.complete()
        self.assertFalse((studio.workspace.projects_dir/"film").exists())
        self.assertIsNone(studio.board)
        self.assertEqual(studio.modal_kind,"Prosjektet er flyttet til papirkurven")
        self.assertIsNone(studio.current_conversation["projectSlug"])

    def test_committed_restore_reports_success_when_local_index_refresh_fails(self):
        studio=self.studio
        studio.show_delete_project(); studio.delete_project(); studio.complete()
        entry=studio.workspace.trashed_projects()[0]
        def unavailable(): raise OSError("temporary index failure")
        studio.workspace.project_summaries=unavailable
        studio.restore_deleted_project(entry["id"]); studio.complete()
        self.assertTrue((studio.workspace.projects_dir/"film/storyboard.json").is_file())
        self.assertEqual(studio.modal_kind,"Prosjektet er gjenopprettet")

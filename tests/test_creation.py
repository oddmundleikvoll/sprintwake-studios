import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

sys.modules.setdefault('raven',types.SimpleNamespace(R_INVALID_ID=-1))
from creation_panel import CreationMixin, creation_kind, prepare_creation
from workspace import Workspace


class CreationTests(unittest.TestCase):
    def test_open_studios_with_optional_fields_never_requests_generation(self):
        for kind in ('image','clip'):
            for name,prompt in (('',''),('Mitt arbeid',''),('','En stille skog.'),('Mitt arbeid','En stille skog.')):
                with self.subTest(kind=kind,name=name,prompt=prompt),tempfile.TemporaryDirectory() as tmp:
                    panel=CreationMixin();panel.workspace=Workspace(Path(tmp));panel.workspace.ensure()
                    panel.ready=True;panel.opening=False;panel.pending={};panel.creation_mode=kind
                    panel.connection_settings={'selected':{'video':'ltx-2.5'}}
                    panel.drafts={};panel.opened=[];panel.saved={};requests=[]
                    panel.text=lambda key:{'creation.title':name,'creation.prompt':prompt}[key]
                    def post(path,payload):
                        self.assertEqual(path,'/api/boards')
                        requests.append(payload)
                        return {'slug':'new-work','board':{'name':payload['name'],'shots':[],'defaults':{}}}
                    def put(path,board):
                        panel.saved=copy.deepcopy(board)
                        return {'board':board}
                    panel.backend=types.SimpleNamespace(post=post,put=put,get=lambda _: {'boards':[]})
                    panel.submit=lambda key,operation,done,**_:done(operation())
                    panel.open_project=lambda slug:panel.opened.append((slug,panel.project_destination))
                    panel.set_attr=lambda *_:None;panel.toast=lambda *_:None
                    panel.create_work()
                    self.assertEqual(panel.opened,[('new-work','images' if kind=='image' else 'clip')])
                    self.assertEqual(panel.saved['sceneDescription'],prompt)
                    self.assertEqual(requests,[{'name':name or prompt or ('Nytt bilde' if kind=='image' else 'Nytt klipp')}])
                    self.assertEqual(creation_kind(panel.workspace,'new-work'),kind)
                    if kind=='image':self.assertEqual(panel.drafts['new-work']['imagePrompt'],prompt)
                    else:self.assertEqual(panel.saved['shots'][0]['prompt'],prompt)

    def test_image_room_retains_canonical_defaults_and_creates_no_shot(self):
        board={'name':'Bilde','shots':[],'defaults':{'model':'ltx-2.5','resolution':'384x256'},'filmBible':{'locations':[]}}
        before=copy.deepcopy(board)
        created=prepare_creation(board,'image','En skog i morgenlys.','ltx-2.5')
        self.assertEqual(board,before)
        self.assertEqual(created['shots'],[])
        self.assertEqual(created['defaults'],board['defaults'])
        self.assertEqual(created['sceneDescription'],'En skog i morgenlys.')

    def test_clip_room_has_one_legal_independent_native_shot(self):
        created=prepare_creation({'shots':[],'defaults':{'steps':8}},'clip','A bird lands.','ltx-2.5')
        self.assertEqual(len(created['shots']),1)
        shot=created['shots'][0]
        self.assertEqual(shot['prompt'],'A bird lands.')
        self.assertEqual((shot['frames']-1)%8,0)
        self.assertEqual(shot['dialogueSource'],'native')
        self.assertIsNone(shot['startRef'])
        self.assertEqual(created['defaults']['steps'],8)
        with self.assertRaises(ValueError):prepare_creation({},'other','','')

    def test_reopen_room_is_local_metadata_and_invalid_metadata_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace=Workspace(Path(tmp));root=workspace.ensure_project('work')
            path=root/'Script/creation.json'
            self.assertEqual(creation_kind(workspace,'work'),'studio')
            for kind in ('image','clip'):
                path.write_text(json.dumps({'version':1,'kind':kind}))
                self.assertEqual(creation_kind(workspace,'work'),kind)
            for value in ('[]','null','broken','{"version":2,"kind":"clip"}'):
                path.write_text(value)
                self.assertEqual(creation_kind(workspace,'work'),'studio')

    def clip(self):
        panel=CreationMixin()
        panel.ready=True;panel.slug='one';panel.raw_shot={'id':'s','prompt':'A bird lands.'}
        panel.pending={};panel.sync_clip=lambda:None;panel._busy=lambda:False;panel.clip_profile=lambda:None
        panel.calls=[];panel.waiter=None
        panel.backend=types.SimpleNamespace(post=lambda path,payload:panel.calls.append((path,payload)))
        panel.submit=lambda key,operation,done:operation()
        panel.save=lambda after:setattr(panel,'waiter',after)
        panel.toast=lambda *_:None
        return panel

    def test_one_clip_waits_for_save_and_requests_only_one_take(self):
        panel=self.clip();panel.generate_clip()
        self.assertEqual(panel.calls,[])
        panel.waiter()
        self.assertEqual(panel.calls,[('/api/render-seeds',{'slug':'one','shotId':'s','count':1})])

    def test_busy_or_empty_clip_never_schedules_inference(self):
        panel=self.clip();panel._busy=lambda:True
        with self.assertRaises(ValueError):panel.generate_clip()
        panel._busy=lambda:False;panel.raw_shot['prompt']=' '
        with self.assertRaises(ValueError):panel.generate_clip()
        self.assertIsNone(panel.waiter)
        self.assertFalse(panel.calls)

    def test_external_clip_uses_existing_explicit_workflow_confirmation(self):
        panel=self.clip();panel.clip_profile=lambda:{'id':'external'}
        calls=[];panel.workflow_action=calls.append
        panel.generate_clip()
        self.assertEqual(calls,['video.generate'])
        self.assertFalse(panel.calls)
        self.assertIsNone(panel.waiter)

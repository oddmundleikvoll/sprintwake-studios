"""Portable model identity survives the legacy LTX project migration."""
import copy,tempfile,unittest
from pathlib import Path
from server.store import Store,default_shot,render_fingerprint
from server.backends.base import ShotPaths
from server.backends.configured import ConfiguredBackend
from server.comfy_workflow import profile_digest

def profile():
    return {'version':1,'id':'own-video','label':'Own video','kind':'video','workflow':{'1':{'class_type':'Video','inputs':{'text':''}}},'bindings':{'prompt':{'node':'1','input':'text'}},'output':{'node':'1','key':'videos'},'capabilities':{'fps':24,'maxFrames':241,'frameRule':{'kind':'any','minimum':1},'sizeAlign':64}}

class ConfiguredModelTests(unittest.TestCase):
    def test_explicit_model_and_recipe_survive_reopen_and_change_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);backend=ConfiguredBackend({'version':1,'image':'none','video':profile()},root,'http://127.0.0.1:1')
            store=Store(root,root/'Projects');slug,board=store.create('Portable')
            board['defaults'].update(backend.project_defaults());shot=default_shot(board['defaults']);shot.update(backend.project_defaults());shot.update(prompt='A test',frames=33,resolution='384x256');board['shots']=[shot]
            saved=store.save(slug,board);reopened=store.load(slug)
            self.assertEqual(reopened['defaults']['model'],'own-video');self.assertEqual(reopened['shots'][0]['model'],'own-video')
            self.assertEqual(reopened['shots'][0]['modelProfileDigest'],profile_digest(profile()))
            before=render_fingerprint(reopened['shots'][0],reopened);changed=copy.deepcopy(reopened);changed['shots'][0]['modelProfileDigest']='a'*64
            self.assertNotEqual(before,render_fingerprint(changed['shots'][0],changed))
            paths=ShotPaths(root,root/'take','take',data_dir=root)
            self.assertEqual(backend.prepare(reopened['shots'][0],reopened,paths).expected_frames,33)

    def test_previous_model_or_changed_profile_requires_explicit_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);backend=ConfiguredBackend({'version':1,'image':'none','video':profile()},root,'http://127.0.0.1:1');paths=ShotPaths(root,root/'take','take',data_dir=root)
            with self.assertRaisesRegex(ValueError,'eksplisitt'):backend.prepare({'id':'s','prompt':'x'},{'defaults':{'model':'ltx-2.5'}},paths)
            requested=backend.project_defaults();requested['modelProfileDigest']='b'*64
            with self.assertRaisesRegex(ValueError,'endret'):backend.prepare({'id':'s','prompt':'x',**requested},{},paths)
            self.assertFalse(paths.abs_dir.exists())

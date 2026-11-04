import copy,unittest
from types import SimpleNamespace
from workflow_panel import WorkflowPanelMixin

def panel():
    p=WorkflowPanelMixin();p.ready=True;p.slug='demo';p.raw_shot={'id':'s','model':'ltx-2.5','frames':121};p.board={}
    p.connection_settings={'selected':{'video':'own'},'workflowProfiles':[{'id':'own','label':'Own','kind':'video','capabilities':{'fps':24,'frameRule':{'kind':'affine','step':8,'offset':1,'minimum':25},'maxFrames':241,'sizeAlign':64}}]}
    p.connection_settings['workflowProfiles'][0].update(version=1,workflow={'1':{'class_type':'Video','inputs':{'text':''}}},bindings={'prompt':{'node':'1','input':'text'}},output={'node':'1','key':'videos'})
    p._busy=lambda:False;p.text=lambda key:{'video.frames':'121','video.resolution':'768x512'}[key]
    p.set_dirty=lambda *_:None;p.toast=lambda *_:None;p.modal_close=lambda:None
    return p

class WorkflowPanelTests(unittest.TestCase):
    def test_restricted_profile_prefills_supported_size_without_changing_saved_take(self):
        p=panel();p.raw_shot['resolution']='384x256'
        p.connection_settings['workflowProfiles'][0]['capabilities']['resolutions']=['128x128']
        before=copy.deepcopy(p.raw_shot);p.show_modal=lambda *args:setattr(p,'modal',args)
        p.workflow_action('video.generate')
        self.assertIn('Text="128x128"',p.modal[1])
        self.assertEqual(p.raw_shot,before)

    def test_open_confirmation_does_not_edit_shot_or_generate(self):
        p=panel();before=copy.deepcopy(p.raw_shot);p.show_modal=lambda *args:setattr(p,'modal',args)
        p.workflow_action('video.generate');self.assertEqual(p.raw_shot,before);self.assertIn('Tidligere opptak beholdes',p.modal[1])

    def test_invalid_contract_does_not_save_or_launch(self):
        p=panel();p.text=lambda key:'120' if key=='video.frames' else '768x512';p.save=lambda **_:self.fail('must not save')
        before=copy.deepcopy(p.raw_shot)
        with self.assertRaises(ValueError):p.workflow_action('video.confirm')
        self.assertEqual(p.raw_shot,before)

    def test_confirm_saves_explicit_parameters_then_requests_one_separate_take(self):
        p=panel();calls=[];p.backend=SimpleNamespace(post=lambda path,doc:calls.append((path,doc)))
        p.submit=lambda key,op,done:op();p.save=lambda after:after()
        p.workflow_action('video.confirm')
        self.assertEqual(p.raw_shot['model'],'own');self.assertEqual(p.raw_shot['frames'],121);self.assertEqual(p.raw_shot['resolution'],'768x512')
        self.assertEqual(p.raw_shot['modelProvider'],'comfy-workflow');self.assertEqual(len(p.raw_shot['modelProfileDigest']),64)
        self.assertEqual(calls,[('/api/render-seeds',{'slug':'demo','shotId':'s','count':1})])

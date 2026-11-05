# SPDX-License-Identifier: MIT
"""Actual repository + decoded synthetic media through production approval gates."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from PIL import Image
from engine.server.store import Store, Conflict, default_shot
from engine.server.jobs import Scheduler
from engine.server.film import FilmController

class Provider:
    def __init__(self,root): self.root=root; self.calls=[]; self.fail=False; self.hook=None
    def generate(self,profile,prompt,seed=None,frames=None,width=None,height=None,**kwargs):
        self.calls.append(('take',seed))
        if self.fail: raise RuntimeError('Synthetic provider failed')
        return {'media':[self.root/f'{frames or 97}.mp4'],'recipe':{'seed':seed}}
    def generate_group(self,shots,**kwargs):
        self.calls.append(('group',len(shots),kwargs))
        if self.hook: self.hook()
        return {'media':[self.root/f'{s["frames"]}.mp4' for s in shots],'recipe':{'fixture':True}}

class FilmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media=tempfile.TemporaryDirectory(); cls.root=Path(cls.media.name)
        for frames in (97,96):
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=64x64:rate=24',
                '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-frames:v',str(frames),
                '-t',str(frames/24),'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(cls.root/f'{frames}.mp4')],check=True,timeout=30)
    @classmethod
    def tearDownClass(cls): cls.media.cleanup()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name); self.store=Store(self.path/'Projects'); self.slug,self.board=self.store.create('Film')
        image=self.path/'opening.png'; Image.new('RGB',(64,64),'orange').save(image)
        self.ref=self.store.import_media(self.slug,image,'refs/opening.png')
        self.provider=Provider(self.root); self.scheduler=Scheduler()
        self.controller=FilmController(self.store,self.scheduler,self.provider)
        self.set_shots([(1,97),(1,96),(2,97)])
    def set_shots(self,groups):
        board=self.store.get(self.slug); board['shots']=[]
        for index,(group,frames) in enumerate(groups):
            shot=default_shot(); shot.update(id=f's{index}',directorScene=group,frames=frames,startRef=self.ref,
                resolution='64x64',start='An opening',action='Move slowly',end='A quiet ending',camera='Fixed camera',prompt='A scene')
            board['shots'].append(shot)
        self.store.put(self.slug,board)
    def act(self,**action):
        action.setdefault('revision',self.controller.status(self.slug)['revision'])
        return self.controller.action({'slug':self.slug,'action':action})
    def wait(self):
        job=self.scheduler.wait(self.slug,15)
        self.assertFalse(job.thread.is_alive())
        return job
    def prepared(self):
        self.act(prepareOnly=True); self.assertIsNone(self.wait().error)
        status=self.controller.status(self.slug)
        self.assertTrue(status['imageApprovalRequired']); self.assertEqual(len(self.provider.calls),0)
        return status
    def approved(self):
        status=self.prepared(); self.act(acceptImagePlan=status['imagePlan']['digest'])
    def test_pixel_approval_and_per_group_video_audio_review_including_final_group(self):
        with self.assertRaises(ValueError): self.act()
        self.approved(); self.act(); self.assertIsNone(self.wait().error)
        status=self.controller.status(self.slug)
        self.assertTrue(status['reviewRequired']); self.assertEqual(status['reviewPreview']['shotIds'],['s0','s1'])
        self.assertTrue(self.store.media_path(status['reviewPreview']['tail']).is_file())
        with self.assertRaises(ValueError): self.act()
        with self.assertRaises(Conflict): self.controller.assert_assembly_ready(self.slug)
        self.act(acceptReview=True); self.act(); self.assertIsNone(self.wait().error)
        self.assertTrue(self.controller.status(self.slug)['reviewRequired'])
        with self.assertRaises(Conflict): self.controller.assert_assembly_ready(self.slug)
        self.act(acceptReview=True); self.controller.assert_assembly_ready(self.slug)
        self.assertEqual([call[1] for call in self.provider.calls],[2,1])
    def test_changed_reference_pixels_invalidate_existing_approval(self):
        status=self.prepared(); Image.new('RGB',(64,64),'blue').save(self.store.media_path(self.ref))
        with self.assertRaises(Conflict): self.controller.action({'slug':self.slug,'action':{'revision':status['revision'],'acceptImagePlan':status['imagePlan']['digest']}})
        self.assertTrue(self.controller.status(self.slug)['imagePlanStale'])
    def test_script_change_during_render_does_not_attach_old_clip(self):
        self.approved()
        def edit():
            board=self.store.get(self.slug); board['shots'][0]['prompt']='A different action'; self.store.put(self.slug,board)
        self.provider.hook=edit; self.act(); self.assertIn('changed',self.wait().error)
        self.assertEqual(self.store.get(self.slug)['shots'][0]['outputs'],[])
        self.assertTrue(list(self.store.project_dir(self.slug).rglob('clip.mp4')))
    def test_group_cap_and_discontinuous_groups_are_rejected(self):
        for groups in ([(1,97)]+[(1,96)]*6,[(1,97),(2,97),(1,97)]):
            self.set_shots(groups)
            with self.assertRaises(ValueError): self.act(prepareOnly=True)
        self.assertEqual(self.provider.calls,[])
    def test_failed_later_take_retains_selected_media_and_recipe(self):
        self.set_shots([(1,97)])
        self.controller.render_seeds({'slug':self.slug,'shotId':'s0','count':1}); self.assertIsNone(self.wait().error)
        take=self.controller.takes(self.slug,'s0')[0]; self.controller.adopt_take(self.slug,'s0',take['seed'])
        board=self.store.get(self.slug); path=self.store.media_path(board['shots'][0]['outputs'][0]); before=path.read_bytes(); recipe=path.with_name('recipe.json').read_bytes()
        self.provider.fail=True; self.controller.render_seeds({'slug':self.slug,'shotId':'s0','count':1}); self.assertIsNotNone(self.wait().error)
        self.assertEqual(self.store.get(self.slug),board); self.assertEqual(path.read_bytes(),before); self.assertEqual(path.with_name('recipe.json').read_bytes(),recipe)
        self.assertEqual([t['status'] for t in self.controller.takes(self.slug,'s0')],['review','failed'])
        source=self.store.project_dir(self.slug)/take['recipe']; source.unlink()
        with self.assertRaises(ValueError): self.controller.adopt_take(self.slug,'s0',take['seed'])
        self.assertEqual(path.read_bytes(),before)
    def test_forged_or_stale_review_does_not_unlock_assembly(self):
        self.approved(); self.act(); self.wait(); status=self.controller.status(self.slug)
        clip=self.store.media_path(status['reviewPreview']['clips'][0]['video']); clip.write_bytes(b'changed')
        with self.assertRaises(Conflict): self.act(acceptReview=True)
        with self.assertRaises(Conflict): self.controller.assert_assembly_ready(self.slug)

    def test_manifest_without_controller_checkpoint_is_explicitly_resumed(self):
        self.approved(); state=self.controller._read(self.slug)
        selected={shot['id']:shot['outputs'] for shot in self.store.get(self.slug)['shots'][:2]}
        state.update(phase='rendering',groupRuns={'1':{'runId':'a'*32,'selected':selected}})
        self.controller._save(self.slug,state)
        self.provider.resume_available=lambda run_id:run_id=='a'*32
        self.assertTrue(self.controller.status(self.slug)['canResume'])
        self.assertEqual(self.provider.calls,[])
        self.act(); self.assertIsNone(self.wait().error)
        self.assertEqual(self.provider.calls[0][2]['resume_run_id'],'a'*32)
        self.assertIsNone(self.provider.calls[0][2]['run_id'])

    def test_selected_retained_take_can_be_reviewed_without_regenerating_film(self):
        self.set_shots([(1,97)]); self.approved(); self.act(); self.assertIsNone(self.wait().error)
        self.act(acceptReview=True); self.controller.assert_assembly_ready(self.slug)
        self.controller.render_seeds({'slug':self.slug,'shotId':'s0','count':1}); self.assertIsNone(self.wait().error)
        take=self.controller.takes(self.slug,'s0')[0]; calls=len(self.provider.calls)
        self.controller.adopt_take(self.slug,'s0',take['seed'])
        self.assertEqual(len(self.provider.calls),calls)
        self.assertTrue(self.controller.status(self.slug)['reviewRequired'])
        with self.assertRaises(Conflict): self.controller.assert_assembly_ready(self.slug)
        self.act(acceptReview=True); self.controller.assert_assembly_ready(self.slug)

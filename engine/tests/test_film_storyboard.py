"""Saved-board production: real store/controller/media, inert model stand-ins."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

from PIL import Image
from server import film_chat
from server import assemble
from server.film import FilmController
from server.film_storyboard import storyboard_plan
from server.ltx_media import concat_media
from server.store import default_shot, default_character
from server.backends.render_helpers import _resolved_prompt
from tests import test_film as fixtures
from tests.test_film_chat import NoInferenceThread


class SavedComfy(fixtures.FakeComfy):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.cancel_second = False

    def director_scene(self, prompts, references, dest_dir, **kwargs):
        self.calls.append((list(prompts), kwargs))
        if self.cancel_second and dest_dir.name == 'scene-002':
            self.cancel_second = False
            raise InterruptedError('inert interruption')
        dest_dir.mkdir(parents=True, exist_ok=True)
        records = []
        for i in range(len(prompts)):
            frames = kwargs['continuation_frames'] if i else kwargs['first_frames']
            video, audio = dest_dir/f'{i}.mp4', dest_dir/f'{i}.wav'
            if not video.exists():
                subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=red:size=64x64:rate=24',
                    '-f','lavfi','-i','sine=frequency=220:sample_rate=48000','-frames:v',str(frames),'-t',str(frames/24),
                    '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-ac','2',str(video)],check=True)
                subprocess.run(['ffmpeg','-y','-v','error','-i',str(video),'-vn','-ar','48000','-ac','2',str(audio)],check=True)
            records.append({'video':video,'audio':audio,'frames':frames})
        movie = dest_dir/'movie.mp4'
        if not movie.exists():
            concat_media([r['video'] for r in records],movie,frames=[r['frames'] for r in records])
        return {'shots':records,'run_id':'a'*32,'movie':movie}


class PlannedFilmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.ctx = fixtures.FilmTests().context(Path(self.temp.name))
        self.service = fixtures.FakeSplash()
        self.service.complete = Mock(side_effect=AssertionError('Saved board must not be replanned'))
        self.ctx.llm_services = lambda: {'splash-local':self.service}
        self.ctx.backend.resolved_prompt = lambda shot,board: _resolved_prompt(shot,board,model='ltx-2.5')
        self.comfy = SavedComfy()
        self.film = self.ctx.film = FilmController(self.ctx,self.comfy)
        self.slug, self.board = self.ctx.store.create('Robot og orc')
        char = default_character(); char.update(name='Ragnar',description='Large amber robot.')
        self.board.update(characters=[char],sceneDescription='A rocky mountain.',renderStyle='Animation.',soundscape='Wind.',
                          filmBible={'locations': [{'id': 'mountain', 'description': 'A rocky mountain.'}], 'props': []})
        self.board['shots'] = []
        for n, count in enumerate((3,3,1),1):
            for i in range(count):
                shot=default_shot(self.board['defaults'])
                shot.update(title=f'Scene {n} · Klipp {i+1}',prompt=f'Ragnar moves in scene {n}, beat {i+1}.',
                            start='Ragnar waits.', action='Ragnar moves.', end='Ragnar stops.', camera='Wide shot.',
                            locationId='mountain', stateBefore='Ragnar waits.', stateAfter='Ragnar stops.',
                            frames=97,characterIds=[char['id']])
                self.board['shots'].append(shot)
        self.ctx.store.save(self.slug,self.board)

    def action(self):
        state=film_chat.context(self.film,self.slug)
        return film_chat.clean_action({'tool':'render_storyboard','slug':self.slug,'revision':state['anchorRevision']},state)

    def approve_images_and_reviews(self):
        for _ in range(4):
            if self.film.status().get('busy'):
                self.ctx.orch._thread.join(20)
            board = self.ctx.store.load(self.slug)
            settings = board.get('filmAutomation') or {}
            phase = self.film.status().get('phase')
            if phase == 'prepared':
                self.film.start({'resumeSlug': self.slug, 'expectedRevision': settings.get('revision'),
                                 'acceptImagePlan': (settings.get('imagePlan') or {}).get('digest')})
            elif phase == 'needs_review':
                self.film.start({'resumeSlug': self.slug, 'expectedRevision': settings.get('revision'),
                                 'acceptReview': True})
            else:
                return
            self.ctx.orch._thread.join(20)
        self.fail(self.film.status())

    def test_preview_keeps_seven_cards_and_single_shot_scene(self):
        plan=storyboard_plan(self.board)
        self.assertEqual([len(s['shots']) for s in plan['scenes']],[3,3,1])
        self.assertEqual(plan['frames'],675)
        self.assertEqual(len(plan['frameAdjustments']),4)
        self.assertEqual([s['id'] for g in plan['scenes'] for s in g['shots']],[s['id'] for s in self.board['shots']])
        reply=film_chat.local_reply('start filmen',film_chat.context(self.film,self.slug))
        self.assertTrue(reply['localControl'])
        self.assertEqual(reply['actions'][0]['tool'],'render_storyboard')
        self.assertEqual(reply['actions'][0]['plannedClips'],7)

    def test_strict_production_rejects_unstructured_legacy_cards(self):
        legacy = copy.deepcopy(self.board)
        for shot in legacy['shots']:
            for key in ('start', 'action', 'end', 'camera', 'locationId', 'stateBefore', 'stateAfter'):
                shot.pop(key, None)
        with self.assertRaisesRegex(ValueError, 'mangler eksplisitt start'):
            storyboard_plan(legacy, strict=True)
        board = copy.deepcopy(self.board)
        board['sceneDescription'] = ''
        board['filmBible'] = {'locations': [{'id': 'mountain', 'description': 'A rocky mountain.'}], 'props': []}
        for shot in board['shots']:
            shot.update(start='Ragnar waits.', action='Ragnar moves.', end='Ragnar stops.',
                        camera='Wide shot.', locationId='mountain')
        plan = storyboard_plan(board, strict=True)
        self.assertTrue(all(card['stateBefore'] and card['stateAfter'] for scene in plan['scenes'] for card in scene['shots']))

    def test_stale_cards_and_old_new_film_cards_cannot_start(self):
        action=self.action()
        board=self.ctx.store.load(self.slug); board['shots'][0]['prompt']='A changed action.'
        self.ctx.store.save(self.slug,board)
        with self.assertRaisesRegex(ValueError,'endret'):
            film_chat.execute(self.film,self.slug,action)
        with self.assertRaisesRegex(ValueError,'Start planlagt film'):
            film_chat.execute(self.film,self.slug,{'tool':'start_film','description':'different','duration':30})
        self.assertFalse(self.film.status()['busy'])

    def test_explicit_scene_numbers_and_chains_survive_descriptive_titles(self):
        board=copy.deepcopy(self.board)
        for i,shot in enumerate(board['shots']):
            shot.update(title=f'Beat {i}',directorScene=1 if i<3 else 2)
        self.assertEqual([len(s['shots']) for s in storyboard_plan(board)['scenes']],[3,4])
        board['shots'][3]['startRef']={'kind':'chain','from':board['shots'][2]['id']}
        self.assertEqual(len(storyboard_plan(board)['scenes']),1)
        for shot in board['shots']:
            shot.pop('directorScene')
        self.assertEqual(len(storyboard_plan(board)['scenes']),1)

    def test_unique_named_cast_links_are_reviewable_and_existing_links_win(self):
        board=copy.deepcopy(self.board)
        board['characters'][0]['name']='Ragnar, the robot'
        for shot in board['shots']:shot['characterIds']=[]
        plan=storyboard_plan(board)
        self.assertEqual(len(plan['characterAssignments']),7)
        self.assertEqual(plan['scenes'][0]['characterKeys'],['cast_0'])
        self.assertEqual(plan['scenes'][0]['shots'][0]['characterIds'],[board['characters'][0]['id']])
        board['shots'][0]['characterIds']=[board['characters'][0]['id']]
        self.assertEqual(len(storyboard_plan(board)['characterAssignments']),6)
        duplicate=copy.deepcopy(board['characters'][0]);duplicate['id']='other'
        board['characters'].append(duplicate)
        self.assertEqual(storyboard_plan(board)['characterAssignments'],[])

    def test_empty_locked_invalid_anchors_and_unequal_durations(self):
        with self.assertRaises(ValueError): storyboard_plan({'shots':[]})
        bad=copy.deepcopy(self.board);bad['shots'][0]['locked']=True
        with self.assertRaisesRegex(ValueError,'låst'): storyboard_plan(bad)
        bad=copy.deepcopy(self.board);bad['shots'][0]['imageAnchors']=[None]
        with self.assertRaises(ValueError): storyboard_plan(bad)
        last=copy.deepcopy(self.board)
        last['shots'][0]['imageAnchors']=[{'ref':{'kind':'upload','path':'refs/a.png'},'frame':-1,'strength':1}]
        anchor=storyboard_plan(last)['scenes'][0]['shots'][0]['imageAnchors'][0]
        self.assertEqual(anchor['frame'],'last')
        self.assertIs(type(anchor['strength']),float)
        bad=copy.deepcopy(self.board);bad['shots'][2]['frames']=145
        plan=storyboard_plan(bad)
        self.assertEqual(len(plan['scenes']),4)
        self.assertEqual(sum(len(g['shots']) for g in plan['scenes']),7)

    def test_admission_uses_same_project_and_resume_after_restart_without_model(self):
        before=self.ctx.store.load(self.slug)
        with patch('server.film.threading.Thread',NoInferenceThread):
            result=film_chat.execute(self.film,self.slug,self.action())
        self.assertEqual(result['slug'],self.slug)
        self.assertEqual(result['operation'],'planned')
        self.assertEqual(len(self.ctx.store.list_boards()),1)
        after=self.ctx.store.load(self.slug)
        self.assertEqual(after['shots'],before['shots'])
        self.assertEqual(after['filmAutomation']['plannedClips'],7)
        self.film= self.ctx.film=FilmController(self.ctx,self.comfy)
        state=film_chat.context(self.film,self.slug)
        self.assertTrue(state['project']['canResume'])
        with patch('server.film.threading.Thread',NoInferenceThread):
            film_chat.execute(self.film,self.slug,{'tool':'resume_film','slug':self.slug,'revision':state['project']['revision']})
        self.service.complete.assert_not_called()

    @patch('server.film.assert_other_models_idle')
    @patch('server.film.prepare_language_memory')
    def test_recording_voice_and_existing_finite_tts_mix_are_preserved(self,*_):
        board=self.ctx.store.load(self.slug)
        board['shots']=board['shots'][:1]
        board['shots'][0].update(dialogue='Hei.',dialogueSource='recording',dialogueVoice='stable-voice',dubMode='replace')
        board['defaults']['tts']='configured-tts'
        self.ctx.store.save(self.slug,board)
        voices=[]
        def prepare(slug,sid):
            current=self.ctx.store.load(slug)
            self.assertEqual(current['defaults']['tts'],'configured-tts')
            shot=current['shots'][0]
            directory=self.ctx.data_dir/self.ctx.store.shot_rel_dir(slug,1)
            directory.mkdir(parents=True,exist_ok=True)
            wav = directory/'dialogue.wav'
            if wav.exists() and shot.get('dialogueSpokenText') == shot.get('dialogue'):
                return
            voices.append(shot['dialogueVoice'])
            subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=500:sample_rate=48000',
                            '-t','1','-ac','2',str(wav)],check=True)
            shot.update(dialogueSpokenText='Hei.',dialogueSpokenStyle='')
            self.ctx.store.save(slug,current)
        self.ctx.orch.prepare_dialogue=prepare
        film_chat.execute(self.film,self.slug,self.action())
        self.approve_images_and_reviews()
        self.assertEqual(self.film.status()['phase'],'done',self.film.status()['error'])
        final=self.ctx.store.load(self.slug)['shots'][0]
        self.assertEqual(voices,['stable-voice'])
        self.assertEqual(final['dialogueSource'],'recording')
        self.assertTrue(final['dubUrl'])
        self.assertEqual(final['frames'],97)
        self.assertEqual(self.ctx.store.load(self.slug)['finalVideo']['parts'][0]['file'],'clip-dubbed.mp4')

    @patch('server.film.assert_other_models_idle')
    @patch('server.film.prepare_language_memory')
    def test_saved_cut_honors_trims_mute_and_transitions(self,*_):
        board=self.ctx.store.load(self.slug);board['shots']=board['shots'][:2]
        board['shots'][0].update(trimIn=.5,muteAudio=True)
        board['assembly']={'transitionSeconds':.25,'fadeInBlackSeconds':.1}
        self.ctx.store.save(self.slug,board)
        film_chat.execute(self.film,self.slug,self.action())
        self.approve_images_and_reviews()
        self.assertEqual(self.film.status()['phase'],'done',self.film.status()['error'])
        final=self.ctx.store.load(self.slug)['finalVideo']
        self.assertAlmostEqual(final['seconds'],(97+96)/24-.5-.25,delta=.08)
        movie=self.ctx.store.project_dir(self.slug)/'final.mp4'
        # First clip's audio really is muted in the delivered cut.
        import av
        with av.open(str(movie)) as media:
            first=next(media.decode(audio=0))
            self.assertLess(abs(first.to_ndarray()).max(),1e-4)

    def test_cancelled_assembly_keeps_previous_final_and_removes_partial(self):
        out=Path(self.temp.name)/'final.mp4';out.write_bytes(b'previous final')
        def interrupted(*args,**kwargs):
            out.with_name('final.assembling.mp4').write_bytes(b'partial')
            raise InterruptedError('cancelled')
        with patch('server.assemble.cut_timings',return_value=([(0,2)],2)), \
             patch('server.assemble._has_audio',return_value=True), \
             patch('server.ltx_media.run_ffmpeg',side_effect=interrupted):
            with self.assertRaises(InterruptedError):
                assemble.assemble([('one',Path('fake.mp4'))],out,64,64,cancel=lambda:True)
        self.assertEqual(out.read_bytes(),b'previous final')
        self.assertFalse(out.with_name('final.assembling.mp4').exists())

    @patch('server.film.assert_other_models_idle')
    @patch('server.film.prepare_language_memory')
    def test_stop_during_soundtrack_preserves_finished_clips_without_publishing(self,*_):
        board=self.ctx.store.load(self.slug);board['shots']=board['shots'][:1]
        self.ctx.store.save(self.slug,board)
        def prepare(slug,cancel=None):
            self.assertIsNotNone(cancel)
            self.film.stop(slug,self.film.status()['jobId'])
            self.assertTrue(cancel())
            raise InterruptedError('stopped during soundtrack')
        self.ctx.orch.prepare_soundtrack=prepare
        film_chat.execute(self.film,self.slug,self.action())
        self.approve_images_and_reviews()
        self.assertEqual(self.film.status()['phase'],'cancelled')
        final=self.ctx.store.load(self.slug)
        self.assertEqual(final['shots'][0]['status'],'done')
        self.assertFalse(final.get('finalVideo'))
        self.assertTrue(film_chat.context(self.film,self.slug)['project']['canResume'])

    @patch('server.film.assert_other_models_idle')
    @patch('server.film.prepare_language_memory')
    @patch('server.orchestrator.mux_speech',side_effect=RuntimeError('inert failed mux'))
    def test_failed_speech_mix_cannot_publish_a_film_with_only_ambient_audio(self,*_):
        board=self.ctx.store.load(self.slug);board['shots']=board['shots'][:1]
        board['shots'][0].update(dialogue='Hei.',dialogueSource='recording')
        self.ctx.store.save(self.slug,board)
        def prepare(slug,sid):
            directory=self.ctx.data_dir/self.ctx.store.shot_rel_dir(slug,1)
            directory.mkdir(parents=True,exist_ok=True)
            subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=500:sample_rate=48000',
                            '-t','1','-ac','2',str(directory/'dialogue.wav')],check=True)
        self.ctx.orch.prepare_dialogue=prepare
        film_chat.execute(self.film,self.slug,self.action())
        self.approve_images_and_reviews()
        self.assertEqual(self.film.status()['phase'],'failed')
        self.assertIn('dialog',self.film.status()['error'])
        self.assertFalse(self.ctx.store.load(self.slug).get('finalVideo'))

    @patch('server.film.assert_other_models_idle')
    @patch('server.film.prepare_language_memory')
    def test_worker_uses_saved_prompts_anchors_and_resumes_finished_clips(self,*_):
        path=self.ctx.data_dir/self.slug/'refs/start.png';path.parent.mkdir(exist_ok=True)
        Image.new('RGB',(64,64),'blue').save(path)
        ref={'kind':'upload','path':path.relative_to(self.ctx.data_dir).as_posix()}
        board=self.ctx.store.load(self.slug)
        board['shots'][1]['imageAnchors']=[{'ref':ref,'frame':0,'strength':.4},{'ref':ref,'frame':96,'strength':.6}]
        board['shots'][1]['resolution']='960x544'
        board['shots'][1]['seed']=99
        board['characters'][0]['image']=ref
        self.ctx.store.save(self.slug,board)
        original=copy.deepcopy(board)
        self.comfy.cancel_second=True
        film_chat.execute(self.film,self.slug,self.action())
        self.approve_images_and_reviews()
        self.assertFalse(self.ctx.orch.busy)
        self.assertEqual(self.film.status()['phase'],'failed')
        done=self.ctx.store.load(self.slug)
        self.assertEqual([s['status'] for s in done['shots'][:3]],['done']*3,self.film.status()['error'])
        clip=self.ctx.data_dir/self.ctx.store.shot_rel_dir(self.slug,1)/'clip.mp4'
        stamp=clip.stat().st_mtime_ns
        self.film=self.ctx.film=FilmController(self.ctx,self.comfy)
        state=film_chat.context(self.film,self.slug)
        film_chat.execute(self.film,self.slug,{'tool':'resume_film','slug':self.slug,'revision':state['project']['revision']})
        self.approve_images_and_reviews()
        self.assertFalse(self.ctx.orch.busy)
        self.assertEqual(self.film.status()['phase'],'done',self.film.status()['error'])
        final=self.ctx.store.load(self.slug)
        self.assertEqual([s['prompt'] for s in final['shots']],[s['prompt'] for s in original['shots']])
        self.assertEqual([s['id'] for s in final['shots']],[s['id'] for s in original['shots']])
        self.assertEqual(clip.stat().st_mtime_ns,stamp)
        self.assertEqual(sum(s['frames'] for s in final['shots']),675)
        self.assertEqual({s['resolution'] for s in final['shots']},{'384x256'})
        self.assertEqual([s['seed'] for s in final['shots']],[0,1,2,0,1,2,0])
        self.assertEqual(self.comfy.calls[0][1]['seed'],0)
        self.assertEqual(final['shots'][1]['imageAnchors'][1]['frame'],'last')
        self.assertIn('Ragnar',self.comfy.calls[0][0][0])
        self.assertIn('Wind',self.comfy.calls[0][0][0])
        self.assertEqual(self.comfy.calls[0][1]['image_anchors'][1][0]['strength'],.4)
        self.service.complete.assert_not_called()

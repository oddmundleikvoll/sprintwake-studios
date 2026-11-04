"""Qwen film controls: fake language/inference, real controller/store/HTTP routes."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from server import film_chat
from server.app import Handler
from server.film import FilmController, GuardedLLM, validate_plan
from server.film_comfy import ComfyFilmClient
from server.ltx_media import atomic_json
from server.store import default_shot
from server.storyboard_chat import chat, validate_actions
from tests import test_film as fixture


class FakeQwen:
    label, model = 'Fake Qwen', 'fake-no-inference'
    def __init__(self, reply):
        self.reply, self.seen = reply, None
    def health(self):
        return True, ''
    def complete(self, system, user, **kwargs):
        self.seen = (system, user)
        return json.dumps(self.reply)


class NoInferenceThread:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
    def start(self):
        pass
    def is_alive(self):
        return False


class FilmChatTests(unittest.TestCase):
    def test_closed_comfy_start_routes_are_actionable_and_leave_projects_unchanged(self):
        self.film.comfy = ComfyFilmClient()
        before = self.ctx.store.board_path(self.slug).read_bytes()
        before_job = (self.project / 'film-job.json').read_bytes()
        before_boards = self.ctx.store.list_boards()
        action = {'tool': 'start_film', 'newProject': True, 'description': 'A robot fights an orc.',
                  'duration': 30, 'imageEngine': 'qwen-image-2.1-turbo', 'continuationAnchor': False}
        class TestHandler(Handler):
            ctx = self.ctx
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), TestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch('server.film_comfy.urlopen', side_effect=URLError('connection refused')):
                for route, payload in (('/api/film/action', {'slug': self.slug, 'action': action}),
                                       ('/api/film', action)):
                    with self.subTest(route=route):
                        request = Request(f'http://127.0.0.1:{server.server_port}'+route,
                            data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
                        with self.assertRaises(HTTPError) as failure:
                            urlopen(request)
                        self.assertEqual(failure.exception.code, 409)
                        self.assertIn('Comfy Desktop', json.load(failure.exception)['error'])
            self.assertEqual(self.ctx.store.board_path(self.slug).read_bytes(), before)
            self.assertEqual((self.project / 'film-job.json').read_bytes(), before_job)
            self.assertEqual(self.ctx.store.list_boards(), before_boards)
            self.assertFalse(self.film.status()['busy'])
            self.assertIsNone(self.ctx.orch._thread)
        finally:
            server.shutdown(); thread.join(); server.server_close()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ctx = fixture.FilmTests().context(self.root)
        self.slug, board = self.ctx.store.create('Testfilm')
        board['filmAutomation'] = {'engine': 'ltx-2.5', 'revision': 'revision-1', 'plannedClips': 2,
            'description': 'A robot waves.', 'imageEngine': 'qwen-image-2.1-turbo', 'requestedSeconds': 7, 'continuationAnchor': False}
        board['shots'] = [default_shot(board['defaults']), default_shot(board['defaults'])]
        for index, shot in enumerate(board['shots']):
            shot.update(directorScene=1, title=f'Clip {index+1}', prompt='A robot waves.', status='interrupted',
                        frames=73 if index == 0 else 96)
        self.ctx.store.save(self.slug, board)
        self.project = self.ctx.store.project_dir(self.slug)
        atomic_json(self.project/'film-plan.json', validate_plan(fixture.PLAN, 2))
        atomic_json(self.project/'film-job.json', {'slug': self.slug, 'jobId': 'job-1', 'busy': False,
            'phase': 'interrupted', 'completed': 1, 'total': 2, 'detail': 'Interrupted during clip 2'})
        self.film = self.ctx.film = FilmController(self.ctx, fixture.FakeComfy())
        self.film.comfy.output_dir = self.root/'output'
        self.ctx.search_url = lambda: ''
        self.ctx.llm = lambda sid: self.fail('A local film control must not load Qwen')

    def state(self):
        return film_chat.context(self.film, self.slug)

    def test_failed_initial_planning_can_retry_in_same_project_without_checkpoint(self):
        board = self.ctx.store.load(self.slug)
        board['shots'] = []
        self.ctx.store.save(self.slug, board)
        (self.project / 'film-plan.json').unlink()
        state = self.state()
        self.assertTrue(state['project']['canRetryPlanning'])
        self.assertTrue(state['project']['canResume'])
        reply = film_chat.local_reply('fortsett filmen', state)
        self.assertIn('planleggingen igjen', reply['message'])
        self.assertEqual(reply['actions'][0]['tool'], 'resume_film')
        before = self.ctx.store.list_boards()
        with patch('server.film.threading.Thread', NoInferenceThread):
            result = film_chat.execute(self.film, self.slug, reply['actions'][0])
        self.assertEqual(result['slug'], self.slug)
        self.assertEqual(result['status']['phase'], 'plan')
        self.assertEqual(len(self.ctx.store.list_boards()), len(before))
        self.assertNotEqual(result['status']['revision'], 'revision-1')
        with self.assertRaises(ValueError):
            film_chat.execute(self.film, self.slug, reply['actions'][0])

    def test_missing_plan_with_existing_shots_is_not_silently_replanned(self):
        (self.project / 'film-plan.json').unlink()
        state = self.state()
        self.assertFalse(state['project']['canRetryPlanning'])
        self.assertFalse(state['project']['canResume'])

    def anchor_action(self):
        from PIL import Image
        path = self.ctx.store.refs_dir(self.slug) / 'chosen.png'
        Image.new('RGB', (16, 16), 'red').save(path)
        board = self.ctx.store.load(self.slug)
        board['styleRefs'] = [{'path': path.relative_to(self.root).as_posix(), 'label': 'Chosen image'}]
        self.ctx.store.save(self.slug, board)
        state = self.state()
        return {'tool': 'set_shot_anchors', 'slug': self.slug, 'revision': state['anchorRevision'],
                'shotId': board['shots'][1]['id'], 'anchors': [
                    {'imageId': state['imageCatalog'][0]['imageId'], 'frame': 0, 'strength': .3},
                    {'imageId': state['imageCatalog'][0]['imageId'], 'frame': 'last', 'strength': .7}]}

    def test_qwen_can_propose_and_apply_multiple_anchors_without_starting_any_job(self):
        action = self.anchor_action()
        state = self.state()
        self.assertNotIn(str(self.root), json.dumps(state))
        qwen = FakeQwen({'message': 'To ankere, ingen generering.', 'actions': [action]})
        board = self.ctx.store.load(self.slug)
        reply = chat(qwen, board, 'Bruk bildet ved start og slutt av videreføringen', slug=self.slug, film_state=state)
        self.assertEqual(reply['actions'][0]['anchors'][0]['imageLabel'], 'Chosen image')
        self.assertIn('set_shot_anchors', qwen.seen[0])
        self.assertIn('first NEW frame', qwen.seen[0])
        with patch.object(self.film, 'start', side_effect=AssertionError('anchor edit must never start inference')):
            result = self.handler({'slug': self.slug, 'action': reply['actions'][0]})._api_post('/api/film/action')
        self.assertEqual(result['operation'], 'shot_anchors')
        saved = self.ctx.store.load(self.slug)
        anchors = saved['shots'][1]['imageAnchors']
        self.assertEqual([a['frame'] for a in anchors], [0, 'last'])
        self.assertEqual([a['strength'] for a in anchors], [.3, .7])
        self.assertNotEqual(saved['filmAutomation']['revision'], 'revision-1')
        self.assertFalse(self.film.status().get('busy'))
        self.assertFalse(self.ctx.orch._cancel.is_set())
        with self.assertRaises(ValueError):
            film_chat.execute(self.film, self.slug, action)  # old proposal cannot overwrite

    def test_anchor_proposals_reject_unknown_images_bad_frames_and_mixed_render(self):
        action = self.anchor_action()
        state, board = self.state(), self.ctx.store.load(self.slug)
        for patch_ in ({'imageId': '/etc/passwd'}, {'frame': 96}, {'strength': 0}, {'strength': True}):
            bad = copy.deepcopy(action)
            bad['anchors'][0].update(patch_)
            self.assertEqual(validate_actions([bad], board, film_state=state), [])
        bad = copy.deepcopy(action)
        bad['anchors'][1]['frame'] = 0
        self.assertEqual(validate_actions([bad], board, film_state=state), [])
        self.assertEqual(validate_actions([action, {'tool': 'start_render'}], board, film_state=state), [])
        self.active()
        with self.assertRaises(ValueError):
            film_chat.execute(self.film, self.slug, action)

    def test_anchor_edit_on_ordinary_board_and_stale_page_guard(self):
        action = self.anchor_action()
        board = self.ctx.store.load(self.slug)
        board.pop('filmAutomation')
        self.ctx.store.save(self.slug, board)
        action['revision'] = self.state()['anchorRevision']
        stale = self.ctx.store.load(self.slug)
        film_chat.execute(self.film, self.slug, action)
        with self.assertRaisesRegex(RuntimeError, 'Bildeankrene'):
            self.handler({})._save_from_page(self.slug, stale)
        state = self.state()
        action.update(revision=state['anchorRevision'], anchors=[])
        film_chat.execute(self.film, self.slug, action)
        self.assertEqual(self.ctx.store.load(self.slug)['shots'][1]['imageAnchors'], [])

    def test_desktop_anchor_edits_rotate_tokens_and_reject_a_second_stale_tab(self):
        action = self.anchor_action()
        # This also has to work before the first Qwen anchor action exists.
        first = self.ctx.store.load(self.slug)
        stale = copy.deepcopy(first)
        first['shots'][1]['imageAnchors'] = [{'ref': first['styleRefs'][0], 'frame': 24, 'strength': .5}]
        saved = self.handler({})._save_from_page(self.slug, first)
        self.assertTrue(saved['anchorsRevision'])
        self.assertNotEqual(saved['filmAutomation']['revision'], stale['filmAutomation']['revision'])
        stale['name'] = 'Other tab'
        with self.assertRaisesRegex(RuntimeError, 'Bildeankrene'):
            self.handler({})._save_from_page(self.slug, stale)
        stale = copy.deepcopy(saved)
        saved['shots'][1]['imageAnchors'][0]['strength'] = .8
        current = self.handler({})._save_from_page(self.slug, saved)
        self.assertNotEqual(current['anchorsRevision'], stale['anchorsRevision'])
        with self.assertRaisesRegex(RuntimeError, 'Bildeankrene'):
            self.handler({})._save_from_page(self.slug, stale)
        self.assertEqual(self.ctx.store.load(self.slug)['shots'][1]['imageAnchors'][0]['strength'], .8)

    def test_status_survives_malformed_saved_anchor_metadata(self):
        board = self.ctx.store.load(self.slug)
        board['shots'][1]['imageAnchors'] = [None]
        self.ctx.store.board_path(self.slug).write_text(json.dumps(board))
        state = self.state()
        self.assertIn('Ugyldige', state['project']['clips'][1]['anchorError'])

    def active(self):
        self.film._update(busy=True, phase='director', jobId='job-1')

    def handler(self, payload):
        handler = object.__new__(Handler)
        handler.ctx = self.ctx
        handler._read_json = lambda: payload
        handler._send_json = lambda obj, status=200: obj
        handler._send_events = lambda run: run(lambda event: None)
        return handler

    def test_qwen_receives_live_film_state_and_validated_action(self):
        board = self.ctx.store.load(self.slug)
        state = self.state()
        reply = {'message': 'Fortsett bruker lagrede klipp.', 'actions': [{
            'tool': 'resume_film', 'slug': self.slug, 'revision': 'revision-1', 'continuationAnchor': True}]}
        qwen = FakeQwen(reply)
        result = chat(qwen, board, 'Gjenoppta med sceneankeret på', slug=self.slug, film_state=state)
        self.assertEqual(result['actions'], reply['actions'])
        self.assertIn('FILM STATE (live;', qwen.seen[1])
        self.assertIn('job-1', qwen.seen[1])
        self.assertIn('checkpointMeaning', qwen.seen[1])
        self.assertNotIn(str(self.root), qwen.seen[1])
        self.assertIn('900s', qwen.seen[0])
        self.assertIn('long films/larger sizes remain unqualified', qwen.seen[0])
        self.assertIn('dubbing is not proof', qwen.seen[0])
        self.assertIn('new-frame index', qwen.seen[0])

    def test_start_count_is_derived_and_inputs_are_allowlisted(self):
        raw = {'tool': 'start_film', 'newProject': True, 'description': 'Robot waves', 'duration': 30,
               'continuationAnchor': True, 'plannedClips': 900, 'runtime': '/evil', 'model': 'other'}
        clean = film_chat.clean_action(raw, self.state())
        self.assertEqual(clean['plannedClips'], 6)
        self.assertEqual(clean['imageEngine'], 'qwen-image-2.1-turbo')
        self.assertNotIn('runtime', clean)
        self.assertNotIn('model', clean)
        for changes in ({'duration': None}, {'duration': True}, {'duration': float('inf')}, {'duration': 7201},
                        {'imageEngine': 'unknown'}, {'continuationAnchor': 'yes'}, {'description': ''}):
            with self.assertRaises(ValueError):
                film_chat.clean_action({**raw, **changes}, self.state())

    @patch('server.film.threading.Thread', NoInferenceThread)
    @patch('server.film.assert_other_models_idle')
    @patch('server.film.prepare_language_memory')
    def test_resume_preserves_old_clip_count_and_frame_defaults(self, *_):
        board = self.ctx.store.load(self.slug)
        board['defaults']['frames'] = 73
        board['filmAutomation'].update(plannedClips=8, requestedSeconds=30)
        self.ctx.store.save(self.slug, board)
        old_plan = copy.deepcopy(fixture.PLAN)
        scene = old_plan['scenes'][0]
        scene.update(firstFrames=73, continuationFrames=96)
        scene['shots'] = [copy.deepcopy(scene['shots'][0]) for _ in range(4)]
        old_plan['scenes'].append(copy.deepcopy(scene))
        atomic_json(self.project/'film-plan.json', validate_plan(old_plan, 8))
        self.film.start({'resumeSlug': self.slug})
        resumed = self.ctx.store.load(self.slug)
        self.assertEqual(self.film.status()['total'], 8)
        self.assertEqual(resumed['filmAutomation']['plannedClips'], 8)
        self.assertEqual(resumed['defaults']['frames'], 73)
        saved = json.loads((self.project/'film-plan.json').read_text())
        self.assertEqual([(s['firstFrames'], s['continuationFrames']) for s in saved['scenes']], [(73, 96), (73, 96)])

    def test_stale_foreign_and_mixed_film_proposals_are_rejected(self):
        state, board = self.state(), self.ctx.store.load(self.slug)
        valid = {'tool': 'resume_film', 'slug': self.slug, 'revision': 'revision-1'}
        for raw in ({**valid, 'slug': '../elsewhere'}, {**valid, 'revision': 'stale'},
                    {**valid, 'continuationAnchor': 1}, {'tool': []}):
            self.assertEqual(validate_actions([raw], board, film_state=state), [])
        notes = []
        self.assertEqual(validate_actions([valid, {'tool': 'assemble'}], board, film_state=state, notes=notes), [])
        self.assertTrue(notes)
        self.assertEqual(validate_actions([valid], board), [])

    def test_status_and_stop_proposal_work_while_active_without_model_or_cancellation(self):
        self.active()
        for stream in (False, True):
            status = self.handler({'slug': self.slug, 'message': 'filmstatus', 'stream': stream})._api_post('/api/chat')
            self.assertTrue(status['localControl'])
            self.assertIn('1/2 klipp', status['message'])
            proposal = self.handler({'slug': self.slug, 'message': 'stopp filmen', 'stream': stream})._api_post('/api/chat')
            self.assertEqual(proposal['actions'], [{'tool': 'stop_film', 'slug': self.slug, 'jobId': 'job-1'}])
            self.assertFalse(self.ctx.orch._cancel.is_set())
        waiting = self.handler({'slug': self.slug, 'message': 'Endre kostymet'})._api_post('/api/chat')
        self.assertTrue(waiting['localControl'])
        self.assertEqual(waiting['actions'], [])

    def test_applying_stop_is_scoped_to_job_and_stale_stop_cannot_cancel_next_job(self):
        self.active()
        action = {'tool': 'stop_film', 'slug': self.slug, 'jobId': 'job-1'}
        self.film._update(jobId='job-2')
        with self.assertRaises(ValueError):
            film_chat.execute(self.film, self.slug, action)
        self.assertFalse(self.ctx.orch._cancel.is_set())
        action['jobId'] = 'job-2'
        result = self.handler({'slug': self.slug, 'action': action})._api_post('/api/film/action')
        self.assertEqual(result['operation'], 'stop')
        self.assertTrue(self.ctx.orch._cancel.is_set())
        self.assertTrue(result['status']['busy'])  # request, not a false worker-exit acknowledgement

    def test_dialog_stop_requires_identity_and_cannot_cancel_a_replacement_job(self):
        self.active()
        for payload in ({}, {'slug': self.slug}, {'slug': self.slug, 'jobId': 'old-job'}):
            with self.assertRaises(ValueError):
                self.handler(payload)._api_post('/api/film/stop')
            self.assertFalse(self.ctx.orch._cancel.is_set())
        result = self.handler({'slug': self.slug, 'jobId': 'job-1'})._api_post('/api/film/stop')
        self.assertTrue(result['cancelled'])

    def test_anchor_update_rotates_revision_and_is_refused_during_active_film(self):
        action = {'tool': 'set_film_anchor', 'slug': self.slug, 'revision': 'revision-1', 'enabled': True}
        self.active()
        with self.assertRaises(ValueError):
            film_chat.execute(self.film, self.slug, action)
        self.film._update(busy=False)
        result = film_chat.execute(self.film, self.slug, action)
        project = result['filmState']['project']
        self.assertTrue(project['continuationAnchor'])
        self.assertNotEqual(project['revision'], 'revision-1')
        with self.assertRaises(ValueError):
            film_chat.execute(self.film, self.slug, action)

    def test_apply_start_and_resume_use_real_controller_but_never_start_inference(self):
        with patch('server.film.threading.Thread', NoInferenceThread):
            resumed = film_chat.execute(self.film, self.slug, {'tool': 'resume_film', 'slug': self.slug,
                'revision': 'revision-1', 'continuationAnchor': True})
            self.assertEqual(resumed['operation'], 'resume')
            board = self.ctx.store.load(self.slug)
            self.assertTrue(board['filmAutomation']['continuationAnchor'])
            self.assertEqual(resumed['slug'], self.slug)
            self.assertNotEqual(resumed['status']['jobId'], 'job-1')
            self.film._update(busy=False)
            started = film_chat.execute(self.film, self.slug, {'tool': 'start_film', 'newProject': True, 'description': 'Robot waves',
                'duration': 7, 'imageEngine': 'qwen-image-2.1-turbo', 'continuationAnchor': False})
            self.assertNotEqual(started['slug'], self.slug)
            self.assertEqual(self.ctx.store.load(started['slug'])['filmAutomation']['imageEngine'], 'qwen-image-2.1-turbo')
            self.assertEqual(list(self.root.rglob('*.mp4')), [])

    def test_chat_action_cannot_acknowledge_video_review(self):
        board = self.ctx.store.load(self.slug)
        board['filmAutomation']['reviewRequired'] = 'exact-receipt'
        self.ctx.store.save(self.slug, board)
        before = self.ctx.store.board_path(self.slug).read_bytes()
        with patch.object(self.film, 'start', side_effect=AssertionError('Chat must only open the review')):
            for accepted in (False, True):
                with self.subTest(accepted=accepted), self.assertRaisesRegex(ValueError, 'Filmproduksjon'):
                    film_chat.execute(self.film, self.slug, {'tool':'resume_film', 'slug':self.slug,
                        'revision':'revision-1', 'acceptReview':accepted})
        self.assertEqual(self.ctx.store.board_path(self.slug).read_bytes(), before)

    def test_resume_controller_rechecks_revision_after_action_validation(self):
        revision = self.state()['project']['revision']
        self.film.set_anchor(self.slug, True, revision)
        with self.assertRaisesRegex(ValueError, 'endret'):
            self.film.start({'resumeSlug': self.slug, 'expectedRevision': revision})
        self.assertFalse(self.film.status()['busy'])

    def test_checkpoint_status_reads_only_owned_metadata_and_never_claims_validation(self):
        state_dir = self.project/'director/scene-001'
        state_dir.mkdir(parents=True)
        run = self.film.comfy.output_dir/'ltx25_mlx/scene_runs'/('a'*32)
        run.mkdir(parents=True)
        atomic_json(state_dir/'scene-state.json', {'run_id': run.name, 'signature': 'request-1'})
        atomic_json(run/'recipe.json', {'request_fingerprint': 'request-1'})
        atomic_json(run/'manifest.json', {'state': 'running', 'active_clip': 2, 'attempt': 1,
                                         'segments': [{'frames': 73, 'file': 'unread.mp4'}]})
        with patch('server.ltx_media.validate_video', side_effect=AssertionError('status must stay lightweight')):
            state = self.state()
        self.assertEqual(state['project']['clips'][0]['status'], 'checkpoint')
        self.assertEqual(state['project']['scenes'][0]['recordedClips'], 1)
        self.assertIn('revalidated on resume', state['checkpointMeaning'])
        atomic_json(state_dir/'scene-state.json', {'run_id': '../escape', 'signature': 'request-1'})
        self.assertEqual(self.state()['project']['scenes'][0]['recordedClips'], 0)

    def test_status_recovers_checkpoint_count_after_crash_and_keeps_legacy_scene_summary(self):
        self.assertEqual(self.state()['project']['scenes'][0]['totalClips'], 2)
        state_dir = self.project/'director/scene-001'
        state_dir.mkdir(parents=True)
        run = self.film.comfy.output_dir/'ltx25_mlx/scene_runs'/('b'*32)
        run.mkdir(parents=True)
        atomic_json(state_dir/'scene-state.json', {'run_id': run.name, 'signature': 'request'})
        atomic_json(run/'recipe.json', {'request_fingerprint': 'request'})
        atomic_json(run/'manifest.json', {'segments': [{'frames': 73}, {'frames': 96}]})
        self.assertEqual(self.state()['project']['completed'], 2)
        self.assertEqual(self.state()['project']['scenes'][0]['recordedClips'], 2)

    def test_local_command_matching_is_exact_and_never_starts_from_a_question(self):
        for message in ('ikke stopp filmen', 'hva skjer hvis jeg stopper filmen?', 'lag en film om stoppknappen'):
            self.assertIsNone(film_chat.local_reply(message, self.state()))
        resume = film_chat.local_reply('/film resume', self.state())
        self.assertEqual(resume['actions'][0]['tool'], 'resume_film')
        anchor = film_chat.local_reply('/film anchor on', self.state())
        self.assertTrue(anchor['actions'][0]['enabled'])
        self.assertFalse(film_chat.local_reply('Slå av videreføringsankeret', self.state())['actions'][0]['enabled'])
        self.active()
        self.assertEqual(film_chat.local_reply('Kan du stoppe filmen?', self.state())['actions'][0]['tool'], 'stop_film')

    def test_regular_chat_route_supplies_film_state_to_qwen(self):
        qwen = FakeQwen({'message': 'En film er lagret.', 'actions': []})
        self.ctx.llm = lambda sid: qwen
        result = self.handler({'slug': self.slug, 'message': 'Forklar ankrene'})._api_post('/api/chat')
        self.assertEqual(result['model'], qwen.model)
        self.assertIn('revision-1', qwen.seen[1])

    def test_http_status_and_streaming_control_do_not_touch_language_service(self):
        self.active()
        class TestHandler(Handler):
            ctx = self.ctx
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), TestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            with urlopen(base+'/api/film/status?slug='+self.slug) as response:
                self.assertTrue(json.load(response)['project']['busy'])
            payload = {'slug': self.slug, 'message': '/film stop', 'stream': True}
            request = Request(base+'/api/chat', data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
            with urlopen(request) as response:
                result = json.loads(response.read())
                self.assertEqual(result['type'], 'result')
                self.assertEqual(result['result']['actions'][0]['jobId'], 'job-1')
            self.assertFalse(self.ctx.orch._cancel.is_set())
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_http_chat_thinking_uses_real_guard_without_recursion_or_model_inference(self):
        seen = []
        owner = self
        class ThinkingQwen(FakeQwen):
            thinking = 'none'
            def complete(self, *args, **kwargs):
                seen.append((self.thinking, owner.ctx.orch._llm_active))
                return super().complete(*args, **kwargs)
        inner = ThinkingQwen({'message': 'Bare sceneoppsett; ingen generering.', 'actions': []})
        self.ctx.llm = lambda sid: GuardedLLM(inner, self.ctx.orch)
        class TestHandler(Handler):
            ctx = self.ctx
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), TestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/api/chat'
            for stream in (False, True):
                payload = {'slug': self.slug, 'message': 'Sett opp scener uten generering',
                           'thinking': 'medium', 'stream': stream}
                request = Request(url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
                with urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                    body = response.read().decode()
                result = next(e['result'] for e in map(json.loads, body.splitlines()) if e.get('type') == 'result') if stream else json.loads(body)
                self.assertEqual(result['message'], 'Bare sceneoppsett; ingen generering.')
                self.assertEqual(result['actions'], [])
                self.assertEqual(inner.thinking, 'none')
                self.assertEqual(self.ctx.orch._llm_active, 0)
                self.assertFalse(self.film.status().get('busy'))
            self.assertEqual(seen, [('medium', 1), ('medium', 1)])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == '__main__':
    unittest.main()

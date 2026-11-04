"""Active render input contracts: python3 tests/scene_layers.py."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.backends.render_helpers import _resolved_prompt
from server.backends.ltx_backend import LTXBackend
from server.backends.base import FrameRule, ModelCapability, ShotPaths
from server.store import default_shot, render_fingerprint
from server.llm import _parse_character_result

class SceneLayers(unittest.TestCase):
    def setUp(self):
        self.board = dict(sceneDescription='Shared corridor.', soundscape='Ventilation.', characters=[
            dict(id='c1', name='Robot', description='Brushed steel plating.', image={'path':'steel.png'}, voice={'path':'voice.wav'})], defaults={})
        self.shot = dict(id='s1', prompt='Walk through @corridor.', characterIds=['c1'],
            referenceImages=[dict(path='room.png',tag='corridor',role='environment')],
            dialogue='Yes, sir.', dialogueStyle='Quietly.', dialogueSource='native', soundNote='Metal footsteps.')

    def test_ltx_prose_keeps_cast_scene_and_sound_without_portrait_tokens(self):
        text = _resolved_prompt(self.shot, self.board, model='ltx-2.5')
        for part in ['Shared corridor.', 'Brushed steel plating.', 'Walk through corridor.', 'Ventilation.', 'Metal footsteps.', 'Quietly.']:
            self.assertIn(part, text)
        self.assertNotIn('<Picture', text)
        self.assertNotIn('reference voice clip', text)
        self.assertNotIn('portrait', text)

    def test_native_dialogue_has_priority_and_delivery(self):
        self.board['soundscape'] = 'No speech. Quiet room tone.'
        text = _resolved_prompt(self.shot, self.board, model='ltx-2.5')
        self.assertIn('saying exactly: "Yes, sir."', text)
        self.assertIn('Dialogue priority:', text)
        self.assertIn('Voice direction for Robot', text)
        self.assertIn('Quietly.', text)

    def test_recorded_dialogue_is_not_generated_again(self):
        self.shot['dialogueSource'] = 'recording'
        text = _resolved_prompt(self.shot, self.board, model='ltx-2.5')
        self.assertIn('natural jaw and lip movement', text)
        self.assertIn('dubbed separately', text)
        self.assertNotIn('Dialogue priority:', text)
        self.assertNotIn('speaks only this one line', text)
        self.assertEqual(default_shot()['dialogueSource'], 'recording')

    def test_silent_video_and_stills_omit_audio_instructions(self):
        text = _resolved_prompt(self.shot, self.board, with_audio=False, model='ltx-2.5')
        self.assertNotIn('Ventilation', text)
        self.assertNotIn('Yes, sir', text)
        self.assertNotIn('speaks', text)

    def test_anchor_prompt_has_no_reference_list(self):
        self.shot.update(startRef={'path':'opening.png'}, endRef={'path':'closing.png'})
        text = _resolved_prompt(self.shot, self.board, model='ltx-2.5')
        self.assertIn('supplied Start image', text)
        self.assertIn('supplied End image', text)
        self.assertNotIn('<Picture', text)

    def test_unselected_character_not_added(self):
        self.shot['characterIds'] = []
        self.assertNotIn('Brushed steel plating', _resolved_prompt(self.shot, self.board, model='ltx-2.5'))

    def test_preview_uses_ltx_prompt_and_exposes_anchors(self):
        from server.app import Handler
        self.shot['startRef'] = {'path':'opening.png'}
        payload = dict(board=self.board, shot=self.shot)
        fake = SimpleNamespace(ctx=SimpleNamespace(backend=SimpleNamespace(capability=lambda model: SimpleNamespace(supports_audio=True))),
            _read_json=lambda: payload, _send_json=lambda data: data)
        result = Handler._api_post(fake, '/api/render-preview')
        self.assertEqual(result['prompt'], _resolved_prompt(self.shot, self.board, model='ltx-2.5'))
        self.assertEqual(result['dialogueSource'], 'LTX native speech')
        self.assertEqual(result['references'][0]['name'], 'Start frame')
        self.shot['dialogueSource'] = 'recording'
        self.assertEqual(Handler._api_post(fake, '/api/render-preview')['dialogueSource'], 'Dialogue-window recording')

    def test_qwen_still_ignores_video_dialogue_validation(self):
        from server.backends.qwen_backend import WithQwenImage
        backend = WithQwenImage(LTXBackend(Path('/tmp')))
        with tempfile.TemporaryDirectory() as tmp:
            paths = ShotPaths(Path('/tmp'), Path(tmp), 'test', Path('/tmp'))
            shot = {**self.shot, 'model':'qwen-image-2.1-turbo', 'dialogueSource':'recording',
                    'characterIds':[], 'referenceImages':[]}
            spec = backend.prepare(shot, self.board, paths)
            self.assertEqual(spec.payload['engine'], 'qwen-image')
            self.assertNotIn('Yes, sir.', spec.payload['prompt'])

    def test_changed_render_inputs(self):
        original = render_fingerprint(self.shot, self.board)
        for (field, value) in [('dialogueStyle', 'Loud'), ('dialogueSource', 'recording'), ('speakerId', 'c2')]:
            shot = {**self.shot, field: value}
            self.assertNotEqual(original, render_fingerprint(shot, self.board))
        board = copy.deepcopy(self.board)
        board['characters'][0]['voice']['path'] = 'new.wav'
        self.assertNotEqual(original, render_fingerprint(self.shot, board))
        shot = copy.deepcopy(self.shot)
        shot['referenceImages'][0]['role'] = 'style'
        self.assertNotEqual(original, render_fingerprint(shot, self.board))

    def test_character_result_is_split_into_character_and_environment(self):
        result = _parse_character_result('CHARACTER:\nA person with dark curly hair and a red jacket.\n\nENVIRONMENT:\nA bright kitchen with wooden cabinets.')
        self.assertEqual(result['character'], 'A person with dark curly hair and a red jacket.')
        self.assertEqual(result['environment'], 'A bright kitchen with wooden cabinets.')

    def test_character_result_accepts_json(self):
        result = _parse_character_result('{"character": "A silver helmet.", "environment": "A hangar."}')
        self.assertEqual(result, {'character': 'A silver helmet.', 'environment': 'A hangar.'})

    def test_character_result_keeps_legacy_plain_text_as_character_only(self):
        result = _parse_character_result('A person in a blue coat.')
        self.assertEqual(result, {'character': 'A person in a blue coat.', 'environment': ''})

if __name__ == '__main__':
    unittest.main()

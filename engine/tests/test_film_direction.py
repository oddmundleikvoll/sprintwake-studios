# SPDX-License-Identifier: MIT
"""Prompt-boundary regressions; no inference or external providers."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest

from engine.server.film import FilmController
from engine.server.film_direction import clip_prompt, directed_shot, image_prompt


class DirectionTests(unittest.TestCase):
    def setUp(self):
        self.board = {
            'sceneDescription': 'PRIVATE GLOBAL PLOT: the entire ending must not enter an opening image',
            'renderStyle': 'Natural film grain', 'soundscape': 'Quiet rain',
            'soundtrackPrompt': 'One continuous restrained score',
            'characters': [
                {'id': 'c-ada', 'name': 'Ada', 'description': 'Short black hair and a worn blue coat', 'image': 'ada.png'},
                {'id': 'c-bo', 'name': 'Bo', 'description': 'Tall, grey curls, red scarf', 'image': 'bo.png'},
                {'id': 'c-other', 'name': 'Unused character', 'description': 'Must not appear'},
            ],
            'filmBible': {
                'locations': [{'id': 'loc-room', 'title': 'Waiting room', 'description': 'A bench beside the east door',
                               'timeOfDay': 'Night', 'lighting': 'Warm lamp on the left', 'referenceImage': 'room.png'}],
                'props': [{'id': 'p-key', 'title': 'Brass key', 'description': 'A triangular bow', 'referenceImage': 'key.png'}],
            },
        }
        self.shot = {'prompt': 'Ada makes a quiet decision', 'start': 'Ada sits beside the door',
                     'action': 'Ada places the key on the bench', 'end': 'Ada rests both hands in her lap',
                     'camera': 'Locked medium shot', 'characterIds': ['c-ada'], 'locationId': 'loc-room',
                     'propIds': ['p-key'], 'assetStates': {'c-ada': 'The blue coat is wet', 'p-key': 'Dull brass'},
                     'stateBefore': 'Ada holds the key in her right hand',
                     'stateAfter': 'The key lies on the bench beside Ada', 'soundNote': 'A soft metal tap'}

    def test_video_keeps_states_props_location_and_group_cast_without_mutating_inputs(self):
        original = copy.deepcopy((self.board, self.shot))
        continuation = dict(self.shot, characterIds=['c-bo'],prompt='Bo watches the key',start='Bo stands beside the bench',
                            action='Bo looks down',end='Bo keeps watching',stateBefore='The key lies on the bench',
                            stateAfter='The key lies on the bench beside Bo')
        prompt = clip_prompt(directed_shot(self.board, continuation, [self.shot, continuation]))
        for value in ('Short black hair', 'red scarf', 'Waiting room', 'Warm lamp on the left',
                      'triangular bow', 'blue coat is wet', 'key lies on the bench', 'Quiet rain', 'soft metal tap'):
            self.assertIn(value, prompt)
        for value in ('Unused character', 'PRIVATE GLOBAL PLOT', 'c-ada', 'p-key', 'Stable cast:', 'Opening:', 'Action:'):
            self.assertNotIn(value, prompt)
        self.assertIn('musical score is added separately', prompt)
        self.assertIn('visible characters in this shot are Bo', prompt)
        self.assertIn('remain offscreen in this shot: Ada', prompt)
        self.assertEqual((self.board, self.shot), original)

    def test_native_dialogue_keeps_exact_words_speaker_language_and_delivery(self):
        self.shot.update(dialogue='Der er du!', dialogueSource='native', speakerId='c-ada',
                         dialogueStyle='in Norwegian, softly')
        prompt = clip_prompt(directed_shot(self.board, self.shot))
        self.assertIn('Ada says in Norwegian, softly: "Der er du!"', prompt)
        self.assertLess(prompt.index('Ada says'),prompt.index('The shot ends with'))
        self.shot['dialogueSource'] = 'recording'
        recorded = clip_prompt(directed_shot(self.board, self.shot))
        self.assertNotIn('Der er du!', recorded)
        self.assertIn('without generating speech', recorded)

    def test_native_speaker_fallback_is_unambiguous_and_invalid_sources_fail(self):
        self.shot.update(dialogue='Hello',dialogueSource='native')
        self.assertIn('Ada says: "Hello"',clip_prompt(directed_shot(self.board,self.shot)))
        self.shot['characterIds']=['c-ada','c-bo']
        with self.assertRaisesRegex(ValueError,'speakerId'):
            directed_shot(self.board,self.shot)
        self.shot['speakerId']='unknown'
        with self.assertRaisesRegex(ValueError,'speakerId'):
            directed_shot(self.board,self.shot)
        self.shot.update(dialogueSource='typo',speakerId='c-ada')
        with self.assertRaisesRegex(ValueError,'native or recording'):
            clip_prompt(directed_shot(self.board,self.shot))
        with self.assertRaisesRegex(ValueError,'native or recording'):
            image_prompt(self.board,self.shot)

    def test_state_only_prop_is_not_dropped_from_text_or_pixel_references(self):
        self.shot['propIds']=[]
        self.shot['assetStates']={'p-key':'On the bench'}
        prompt=clip_prompt(directed_shot(self.board,self.shot))
        self.assertIn('Brass key: On the bench',prompt)
        store=SimpleNamespace(media_path=lambda ref:Path('/references')/ref)
        paths,roles=FilmController(store,None,None)._reference_bindings(self.board,self.shot)
        self.assertIn('key.png',[p.name for p in paths])
        self.assertTrue(any('Brass key' in role for role in roles))

    def test_scene_frame_uses_current_staging_and_explicit_reference_roles(self):
        prompt = image_prompt(self.board, self.shot, ['the identity of Ada', 'the room geography'])
        for value in ('Reference image 1 supplies the identity of Ada', 'Reference image 2 supplies the room geography',
                      'Ada sits beside the door', 'right hand', 'triangular bow'):
            self.assertIn(value, prompt)
        for value in ('PRIVATE GLOBAL PLOT', 'places the key', 'key lies on the bench', 'Unused character', 'Bo:'):
            self.assertNotIn(value, prompt)
        ending = image_prompt(self.board, self.shot, closing=True)
        self.assertIn('key lies on the bench', ending)
        self.assertNotIn('holds the key in her right hand', ending)
        self.shot['assetStates']['p-key']='Held in Ada\'s right hand'
        self.assertNotIn('Held in Ada',image_prompt(self.board,self.shot,closing=True))

    def test_deduplicated_reference_roles_match_the_actual_pixel_order(self):
        store = SimpleNamespace(media_path=lambda ref: Path('/references') / ref)
        controller = FilmController(store, None, None)
        self.shot['referenceImages'] = ['ada.png']
        self.board['styleRefs'] = ['look.png']
        paths, roles = controller._reference_bindings(self.board, self.shot)
        self.assertEqual([p.name for p in paths], ['ada.png', 'look.png', 'room.png', 'key.png'])
        self.assertIn('identity, build and costume of Ada', roles[0])
        self.assertIn('visual style', roles[1])
        self.assertIn('Waiting room', roles[2])
        self.assertIn('Brass key', roles[3])
        self.assertEqual(len(paths), len(roles))
        self.shot['referenceImages'] = [str(i)+'.png' for i in range(11)]
        with self.assertRaisesRegex(ValueError, 'ten selected references'):
            controller._reference_bindings(self.board, self.shot)

    def test_appearance_alone_cannot_replace_missing_shot_direction(self):
        with self.assertRaises(ValueError):
            clip_prompt({'castDescription': 'Ada in a blue coat'})
        with self.assertRaises(ValueError):
            image_prompt(self.board, {})

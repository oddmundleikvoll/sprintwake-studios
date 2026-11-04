"""LTX drafts preserve duration and seed with fixed distilled steps."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.backends.base import ShotPaths
from server.backends.ltx_backend import LTXBackend
from server.store import Store, default_board, default_shot

class DraftSteps(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = LTXBackend(self.root)
        self.paths = ShotPaths(self.root, self.root/'shot', 'shot', data_dir=self.root)
        self.board = default_board('Draft')
        self.board['defaults'].update(resolution='832x480', draft=True)
        self.shot = default_shot(self.board['defaults'])
        self.shot.update(prompt='A person waves', frames=97, seed=123, steps=16)
        self.board['shots'] = [self.shot]

    def test_draft_shrinks_pixels_but_keeps_duration_and_seed(self):
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        self.assertEqual((spec.payload['width'], spec.payload['height']), (384,256))
        self.assertEqual((spec.payload['frames'], spec.payload['seed']), (97,123))
        self.assertEqual(spec.payload['steps'], 8)

    def test_full_render_snaps_to_two_stage_grid(self):
        self.board['defaults']['draft'] = False
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        self.assertEqual((spec.payload['width'], spec.payload['height']), (832,512))
        self.assertEqual(spec.payload['steps'], 8)

    def test_sketch_uses_short_silent_generation_but_retains_target_length(self):
        self.board['defaults']['sketch'] = True
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        self.assertEqual(spec.payload['frames'], 25)
        self.assertFalse(spec.payload['with_audio'])
        self.assertAlmostEqual(spec.payload['sketchStretchFactor'], 97/25)

    def test_sketch_keeps_frames_for_a_downstream_chain(self):
        self.board['defaults']['sketch'] = True
        self.board['shots'].append(dict(id='next',startRef={'kind':'chain','from':self.shot['id']}))
        spec = self.backend.prepare(self.shot, self.board, self.paths)
        self.assertIsNotNone(spec.frames_dir)
        self.assertEqual(spec.expected_frames, 97)

    def test_retired_draft_toggle_is_removed_on_load(self):
        self.board['defaults']['draftFullSteps'] = True
        migrated = Store(workspace=self.root, data_dir=self.root).migrate(self.board)
        self.assertNotIn('draftFullSteps', migrated['defaults'])

if __name__ == '__main__':
    unittest.main()

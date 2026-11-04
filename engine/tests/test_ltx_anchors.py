"""CPU-only image-anchor contract; no model imports or inference."""
import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from server.ltx_anchors import clean_anchors, frame_index, image_path, image_catalog
from server.store import Store, default_shot, render_fingerprint, stale_reason, _collect_media_usages
from tests.test_ltx_resume import scene, BRIDGE
from server import ltx_media


class AnchorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store(workspace=self.root, data_dir=self.root)
        self.slug, self.board = self.store.create('Anchors')
        path = self.store.refs_dir(self.slug) / 'red.png'
        Image.new('RGB', (16, 16), 'red').save(path)
        self.ref = {'path': path.relative_to(self.root).as_posix(), 'label': 'Red reference'}
        self.shot = default_shot(self.board['defaults'])
        self.shot.update(frames=73, imageAnchors=[{'ref': self.ref, 'frame': 24, 'strength': .4}])
        self.board['shots'] = [self.shot]

    def test_frame_strength_and_duplicate_validation(self):
        self.assertEqual(frame_index('last', 96), 95)
        for frame in (-2, True, 1.5, 96, '24', None):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                frame_index(frame, 96)
        for strength in (0, -1, 1.01, float('nan'), float('inf'), True, '0.5'):
            with self.subTest(strength=strength), self.assertRaises(ValueError):
                clean_anchors([{'ref': self.ref, 'frame': 0, 'strength': strength}], 96)
        for anchors in ([{'ref': self.ref, 'frame': 0, 'strength': .5}] * 2,
                        [{'ref': self.ref, 'frame': 'last', 'strength': .5},
                         {'ref': self.ref, 'frame': 95, 'strength': .5}]):
            with self.assertRaises(ValueError):
                clean_anchors(anchors, 96)
        with self.assertRaises(ValueError):
            clean_anchors([{}] * 33, 96)

    def test_media_is_confined_and_readable(self):
        self.assertTrue(image_path(self.ref, self.root, verify=True).is_file())
        for ref in ({'path': '/etc/passwd'}, {'path': '../outside.png'}, {'path': 'https://example.com/a.png'},
                    {'kind': 'chain', 'path': self.ref['path']}, {'path': 'missing.png'}):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                image_path(ref, self.root, verify=True)
        broken = self.root / 'broken.png'
        broken.write_text('partial image')
        with self.assertRaises(OSError):
            image_path({'path': 'broken.png'}, self.root, verify=True)
        link = self.root / 'escape.png'
        link.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            image_path({'path': 'escape.png'}, self.root)

    def test_roundtrip_content_timing_strength_and_media_usage(self):
        self.store.save(self.slug, self.board)
        board = self.store.load(self.slug)
        shot = board['shots'][0]
        self.assertTrue(shot['imageAnchors'][0]['ref']['contentHash'])
        self.assertIn(self.ref['path'], _collect_media_usages(board))
        shot.update(status='done', outputs=['accepted.mp4'])
        shot['renderFingerprint'] = render_fingerprint(shot, board)
        self.store.save(self.slug, board)
        self.assertFalse(stale_reason(shot, board))
        for patch_ in ({'frame': 25}, {'strength': .7}):
            changed = copy.deepcopy(shot)
            changed['imageAnchors'][0].update(patch_)
            self.assertTrue(stale_reason(changed, board))
        Image.new('RGB', (16, 16), 'blue').save(self.root / self.ref['path'])
        changed = self.store.load(self.slug)
        self.assertTrue(stale_reason(changed['shots'][0], changed))

    def test_empty_list_keeps_legacy_fingerprint_and_missing_ref_fails(self):
        self.shot.pop('imageAnchors')
        before = render_fingerprint(self.shot, self.board)
        self.shot['imageAnchors'] = []
        self.assertEqual(before, render_fingerprint(self.shot, self.board))
        self.assertEqual(image_catalog(self.board, self.root), {})
        self.shot['imageAnchors'] = [{'ref': self.ref, 'frame': 73, 'strength': .5}]
        with self.assertRaises(ValueError):
            self.store.save(self.slug, self.board)

    def test_malformed_persisted_anchors_remain_readable_and_can_be_cleared(self):
        self.store.save(self.slug, self.board)
        original = self.store.load(self.slug)
        for value in ([None], ['bad'], None, {'bad': True}, [{'ref': {'path': 3}, 'frame': 0, 'strength': .4}]):
            board = copy.deepcopy(original)
            board['shots'][0]['imageAnchors'] = value
            self.store.board_path(self.slug).write_text(json.dumps(board))
            loaded = self.store.load(self.slug)
            self.assertEqual(loaded['shots'][0]['imageAnchors'], value)
            with self.assertRaises(ValueError):
                self.store.save(self.slug, loaded)
            loaded['shots'][0]['imageAnchors'] = []
            self.store.save(self.slug, loaded)

    def test_catalog_drops_foreign_url_and_worker_identity_covers_anchor_helper(self):
        self.shot['imageAnchors'][0]['ref']['url'] = 'https://unwanted.example/image.png'
        catalog = image_catalog(self.board, self.root)
        choice = next(iter(catalog.values()))
        self.assertEqual(choice['ref']['url'], '/media/' + self.ref['path'])
        runtime, model, bridge = (self.root/name for name in ('runtime', 'model', 'bridge'))
        for path in (runtime/'packages', model, bridge): path.mkdir(parents=True)
        (runtime/'uv.lock').write_text('pinned')
        (model/'.ltx-installed.json').write_text(json.dumps({'files': {}}))
        media = self.root/'ltx_media.py'; media.write_text('media helper')
        anchors = self.root/'ltx_anchors.py'; anchors.write_text('anchors v1')
        with patch.object(ltx_media, '__file__', str(media)):
            before = ltx_media.runtime_identity(runtime, model, bridge)
            anchors.write_text('anchors v2')
            self.assertNotEqual(before, ltx_media.runtime_identity(runtime, model, bridge))

    def config(self):
        return {'prompts': ['first', 'continuation'], 'width': 64, 'height': 64,
                'first_frames': 73, 'continuation_frames': 96, 'seed': 3, 'low_ram': False,
                'start_image': 'legacy.png', 'continuation_image': 'scene.png',
                'image_anchors': [[{'path': 'first.png', 'frame': 0, 'strength': .6},
                                   {'path': 'middle.png', 'frame': 24, 'strength': .4}],
                                  [{'path': 'face.png', 'frame': 0, 'strength': .3},
                                   {'path': 'end.png', 'frame': 'last', 'strength': .7}]]}

    @unittest.skipUnless(scene, 'external LTX Comfy worker unavailable; set RAVEN_LTX_BRIDGE')
    def test_cli_multiple_anchors_override_defaults_and_extend_uses_new_indices(self):
        config = self.config()
        scene.validate_config(config)
        first = scene.ltx_command(config, 0, 'out.mp4')
        extended = scene.ltx_command(config, 1, 'out.mp4', 'tail.mp4')
        images = lambda cmd: [cmd[i+1:i+4] for i, word in enumerate(cmd) if word == '--image']
        self.assertEqual(images(first), [['first.png', '0', '0.6'], ['middle.png', '24', '0.4']])
        self.assertEqual(images(extended), [['face.png', '0', '0.3'], ['end.png', '95', '0.7']])
        self.assertNotIn('25', extended)  # CLI itself adds the carried prefix.
        config['image_anchors'][1][1]['frame'] = 96
        with self.assertRaises(ValueError):
            scene.validate_config(config)

    @unittest.skipUnless(scene, 'external LTX Comfy worker unavailable; set RAVEN_LTX_BRIDGE')
    def test_recipe_hash_covers_every_anchor_content_and_position(self):
        config = self.config()
        config.pop('start_image'); config.pop('continuation_image')
        for group in config['image_anchors']:
            for anchor in group:
                path = self.root / anchor['path']
                path.write_text('original')
                anchor['path'] = str(path)
        with patch.object(scene, 'runtime_identity', return_value={'revision': 'pinned'}):
            before = scene.recipe_for(config)
            for patch_ in ({'frame': 3}, {'strength': .8}):
                changed = copy.deepcopy(config)
                changed['image_anchors'][1][0].update(patch_)
                self.assertNotEqual(before, scene.recipe_for(changed))
            Path(config['image_anchors'][1][1]['path']).write_text('changed')
            self.assertNotEqual(before, scene.recipe_for(config))

    @unittest.skipUnless((BRIDGE / 'nodes.py').is_file(), 'external LTX Comfy nodes unavailable; set RAVEN_LTX_BRIDGE')
    def test_comfy_node_serializes_chained_images_without_running_worker(self):
        # Execute the real node classes with only CPU image/Comfy API stubs.
        tree = ast.parse((BRIDGE / 'nodes.py').read_text())
        tree.body = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in ('LTX25MLXScene', 'LTX25MLXImageAnchor')]
        seen = []
        env = dict(Path=Path, json=json, io=io, hashlib=hashlib, re=re, np=np, Image=Image,
                   clean_anchors=clean_anchors, release_memory=lambda: None,
                   sys=SimpleNamespace(executable='unused'), __file__=str(BRIDGE/'nodes.py'),
                   run_worker=lambda *args, **kwargs: seen.append(args),
                   mm=SimpleNamespace(throw_exception_if_processing_interrupted=lambda: None),
                   comfy=SimpleNamespace(utils=SimpleNamespace(ProgressBar=lambda count: SimpleNamespace(update_absolute=lambda *a: None))),
                   InputImpl=SimpleNamespace(VideoFromFile=lambda path: path))
        exec(compile(tree, 'nodes.py', 'exec'), env)
        image = [SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: np.zeros((16, 16, 3))))]
        anchor = env['LTX25MLXImageAnchor']()
        first = anchor.anchor(image, 0, 24, .4)[0]
        both = anchor.anchor(image, 1, -1, .7, first)[0]
        env['LTX25MLXScene']()._generate(self.root, 'one\n---\ntwo', 64, 64, 73, 96,
                1, False, None, None, None, 0, .35, 'signature', both)
        request = json.loads((self.root/'request.json').read_text())
        self.assertEqual(len(seen), 1)
        self.assertEqual(request['image_anchors'][0][0]['frame'], 24)
        self.assertEqual(request['image_anchors'][1][0]['frame'], 'last')
        self.assertTrue(Path(request['image_anchors'][1][0]['path']).is_file())


if __name__ == '__main__':
    unittest.main()

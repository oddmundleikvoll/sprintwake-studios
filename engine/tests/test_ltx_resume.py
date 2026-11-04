"""CPU-only interruption/resume tests: inference is replaced by tiny ffmpeg fixtures."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from server import ltx_media

BRIDGE = Path(os.environ.get('RAVEN_LTX_BRIDGE', str(Path(__file__).resolve().parents[3] / 'ComfyUI-Film/ComfyUI/custom_nodes/ComfyUI-LTX25-MLX')))
scene = None
if (BRIDGE / 'render_scene.py').is_file():
    spec = importlib.util.spec_from_file_location('resume_scene', BRIDGE / 'render_scene.py')
    scene = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scene)


@unittest.skipUnless(scene, 'external LTX Comfy worker unavailable; set RAVEN_LTX_BRIDGE')
class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.config = {'prompts': ['one', 'two', 'three'], 'width': 64, 'height': 64,
                       'first_frames': 25, 'continuation_frames': 8, 'seed': 3, 'low_ram': False}
        self.sources = {}
        for count in (25, 33):
            output = self.root / f'fixture-{count}.mp4'
            scene.ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=24', '-f', 'lavfi', '-i',
                'sine=sample_rate=48000', '-frames:v', count, '-t', count/24,
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ac', 2, output)
            self.sources[count] = output
        self.calls = []
        identity = patch.object(scene, 'runtime_identity', return_value={'revision': 'test-pinned'})
        identity.start()
        self.addCleanup(identity.stop)

    def infer(self, command, **kwargs):
        self.calls.append(command)
        self.assertLessEqual(kwargs['timeout'], 900)
        if 'extend' in command:
            tail = Path(command[command.index('--video') + 1])
            ltx_media.validate_video(tail, 25, 64, 64)
        shutil.copy2(self.sources[33 if 'extend' in command else 25], command[command.index('-o') + 1])

    def manifest(self):
        return json.loads((self.run / 'manifest.json').read_text())

    def interrupt_after_two(self):
        def interrupted(command, **kwargs):
            if command[command.index('--prompt') + 1] == 'three':
                raise InterruptedError('simulated app interruption')
            self.infer(command, **kwargs)
        with patch.object(scene.subprocess, 'run', side_effect=interrupted):
            with self.assertRaises(InterruptedError):
                scene.render(self.config, self.run)
        self.assertEqual(len(self.manifest()['segments']), 2)

    def test_resume_uses_valid_prefix_and_full_window_for_short_extensions(self):
        self.interrupt_after_two()
        before = copy.deepcopy(self.manifest()['segments'])
        stamps = [(self.run / s['file']).stat().st_mtime_ns for s in before]
        self.calls.clear()
        with patch.object(scene.subprocess, 'run', side_effect=self.infer):
            scene.render(self.config, self.run)
        result = self.manifest()
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['reused'], 2)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result['segments'][:2], before)
        self.assertEqual([(self.run / s['file']).stat().st_mtime_ns for s in before], stamps)
        ltx_media.validate_video(self.run / 'movie.mp4', 41, 64, 64)

    def test_resume_revalidates_multiple_continuation_anchors_without_touching_prefix(self):
        image = self.root/'reference.png'
        image.write_bytes(b'image-v1')
        self.config['image_anchors'] = [[], [{'path': str(image), 'frame': 0, 'strength': .4},
                                            {'path': str(image), 'frame': 'last', 'strength': .7}], []]
        self.interrupt_after_two()
        before = (self.run/'manifest.json').read_bytes()
        for patch_ in ({'strength': .6}, {'frame': 3}):
            config = copy.deepcopy(self.config)
            config['image_anchors'][1][0].update(patch_)
            with self.assertRaisesRegex(ValueError, 'changed'):
                scene.render(config, self.run)
        image.write_bytes(b'image-v2')
        with self.assertRaisesRegex(ValueError, 'changed'):
            scene.render(self.config, self.run)
        self.assertEqual((self.run/'manifest.json').read_bytes(), before)
        image.write_bytes(b'image-v1')
        self.calls.clear()
        with patch.object(scene.subprocess, 'run', side_effect=self.infer):
            scene.render(self.config, self.run)
        self.assertEqual(self.manifest()['reused'], 2)
        self.assertEqual(len(self.calls), 1)

    def test_corrupt_clip_restarts_at_transition_preserving_valid_prefix_and_old_files(self):
        self.interrupt_after_two()
        before = self.manifest()['segments']
        (self.run / before[1]['audio']).write_bytes(b'partial')
        self.calls.clear()
        with patch.object(scene.subprocess, 'run', side_effect=self.infer):
            scene.render(self.config, self.run)
        result = self.manifest()
        self.assertEqual(result['reused'], 1)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(result['segments'][0], before[0])
        self.assertNotEqual(result['segments'][1]['file'], before[1]['file'])
        self.assertTrue((self.run / before[1]['file']).exists())

    def test_changed_prompt_seed_reference_and_runtime_refuse_old_checkpoint(self):
        anchor = self.root / 'anchor.png'
        anchor.write_bytes(b'anchor-v1')
        self.config['continuation_image'] = str(anchor)
        self.interrupt_after_two()
        saved = (self.run / 'manifest.json').read_bytes()
        for config in ({**self.config, 'seed': 4}, {**self.config, 'prompts': ['one', 'changed', 'three']},
                       {**self.config, 'low_ram': True}):
            with self.assertRaisesRegex(ValueError, 'changed'):
                scene.render(config, self.run)
        anchor.write_bytes(b'anchor-v2')
        with self.assertRaisesRegex(ValueError, 'changed'):
            scene.render(self.config, self.run)
        anchor.write_bytes(b'anchor-v1')
        with patch.object(scene, 'runtime_identity', return_value={'revision': 'new'}):
            with self.assertRaisesRegex(ValueError, 'changed'):
                scene.render(self.config, self.run)
        self.assertEqual((self.run / 'manifest.json').read_bytes(), saved)

    def test_uncommitted_window_is_never_adopted_and_timeout_is_not_retried(self):
        def timeout(command, **kwargs):
            self.infer(command, **kwargs)
            raise subprocess.TimeoutExpired(command, 900)
        with patch.object(scene.subprocess, 'run', side_effect=timeout):
            with self.assertRaises(subprocess.TimeoutExpired):
                scene.render(self.config, self.run)
        self.assertEqual(self.manifest()['segments'], [])
        self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        with patch.object(scene.subprocess, 'run', side_effect=self.infer):
            scene.render(self.config, self.run)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.manifest()['reused'], 0)

    def test_invalid_media_gets_two_attempts_and_no_checkpoint(self):
        def invalid(command, **kwargs):
            self.calls.append(command)
            shutil.copy2(self.sources[33], command[command.index('-o') + 1])
        with patch.object(scene.subprocess, 'run', side_effect=invalid):
            with self.assertRaisesRegex(ValueError, 'length'):
                scene.render(self.config, self.run)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.manifest()['segments'], [])
        self.assertEqual(self.manifest()['state'], 'failed')

    def test_anchor_new_frame_indices_are_passed_without_carry_offset(self):
        config = {**self.config, 'continuation_image': 'anchor.png', 'continuation_frame': 7,
                  'continuation_strength': .35}
        command = scene.ltx_command(config, 1, self.root/'out.mp4', self.root/'carry.mp4')
        self.assertEqual(command[command.index('--image')+1:command.index('--image')+4], ['anchor.png', '7', '0.35'])
        with self.assertRaisesRegex(ValueError, 'new frame'):
            scene.validate_config({**config, 'continuation_frame': 8})

    def test_validation_rejects_silent_truncated_short_audio_and_wrong_rate(self):
        for args in (['-an'], ['-af', 'atrim=duration=0.1'], ['-ar', '32000'], ['-af', 'asetpts=PTS+0.5/TB']):
            bad = self.root / 'bad.mp4'
            scene.ffmpeg('-i', self.sources[25], '-c:v', 'copy', *args, bad)
            with self.assertRaises((ValueError, OSError)):
                ltx_media.validate_video(bad, 25, 64, 64)
        bad.write_bytes(bad.read_bytes()[:100])
        with self.assertRaises(Exception):
            ltx_media.validate_video(bad, 25, 64, 64)

    def test_final_concat_cancellation_retains_accepted_film(self):
        target = self.root / 'movie.mp4'
        target.write_bytes(b'accepted take')
        with self.assertRaises(InterruptedError):
            ltx_media.concat_media([self.sources[25]] * 10, target, frames=[25] * 10, cancel=lambda: True)
        self.assertEqual(target.read_bytes(), b'accepted take')
        self.assertEqual(list(self.root.glob('*.pending.mp4')), [])

    def test_many_clip_concat_keeps_frame_and_audio_timing(self):
        output = self.root / 'many.mp4'
        ltx_media.concat_media([self.sources[25]] * 18, output, frames=[25] * 18)
        ltx_media.validate_video(output, 450, 64, 64)

    def test_atomic_checkpoint_failure_keeps_previous_json(self):
        target = self.root / 'checkpoint.json'
        ltx_media.atomic_json(target, {'complete': 1})
        with patch.object(Path, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                ltx_media.atomic_json(target, {'complete': 2})
        self.assertEqual(json.loads(target.read_text()), {'complete': 1})
        self.assertEqual(list(self.root.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()

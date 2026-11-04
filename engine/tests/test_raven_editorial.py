import json
import tempfile
import unittest
from pathlib import Path

from server.raven_editorial import editorial_context


class RavenEditorialProjectionTests(unittest.TestCase):
    def test_absent_is_explicit_and_bounded(self):
        with tempfile.TemporaryDirectory() as value:
            result = editorial_context(value, "demo")
        self.assertEqual(result["cut"]["status"], "absent")

    def test_projects_semantic_state_without_paths_or_history(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            script = root / "demo" / "Script"
            script.mkdir(parents=True)
            (script / "cut.json").write_text(json.dumps({
                "format": "raven-cut", "version": 1, "fps": 24, "revision": 2,
                "clips": [{"shotId": "s1", "source": {"id": "take-1", "label": "Take 1", "video": "Input/a.mp4", "image": "Input/a.png"}, "inFrame": 0, "outFrame": 24, "sourceFrames": 24, "included": True}],
                "undo": [{"label": "secret must not leak", "clips": []}], "redo": []}), encoding="utf-8")
            result = editorial_context(root, "demo")
        cut = result["cut"]
        self.assertEqual(cut["status"], "ok")
        self.assertEqual(cut["runtimeFrames"], 24)
        self.assertEqual(cut["clips"][0]["takeId"], "take-1")
        self.assertNotIn("Input/a.mp4", json.dumps(result))
        self.assertNotIn("secret", json.dumps(result))

    def test_future_format_is_explicitly_unsupported(self):
        with tempfile.TemporaryDirectory() as value:
            script = Path(value) / "demo" / "Script"
            script.mkdir(parents=True)
            (script / "sound.json").write_text(json.dumps({"format": "raven-sound", "version": 99}), encoding="utf-8")
            result = editorial_context(value, "demo")
        self.assertEqual(result["sound"]["status"], "unsupported")


if __name__ == "__main__":
    unittest.main()


class RavenEditorialBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'Projects'
        self.script = self.root / 'demo' / 'Script'
        self.script.mkdir(parents=True)

    def write(self, section, **fields):
        doc = {'format': 'raven-' + section, 'version': 1, 'revision': 3, 'undo': [], 'redo': [], **fields}
        path = self.script / (section + '.json')
        path.write_text(json.dumps(doc))
        return path

    def cut(self, count=1):
        return [{'shotId': str(i), 'source': None, 'inFrame': 0, 'outFrame': 24,
                 'sourceFrames': 48, 'included': True} for i in range(count)]

    def test_malformed_fields_fail_explicitly_instead_of_crashing_or_claiming_empty(self):
        cases = [('cut', {'fps': 24, 'clips': 'wrong'}),
                 ('cut', {'fps': 24, 'clips': [{'shotId': 's', 'inFrame': 'x'}]}),
                 ('cut', {'fps': 25, 'clips': self.cut()}),
                 ('coverage', {'plans': {'r': {'actors': None}}}),
                 ('structure', {'state': {'targetFrames': 24, 'sequences': [{'id': 's'}]}}),
                 ('sound', {'fps': 24, 'state': {'tracks': [None] * 4, 'clips': []}})]
        for section, fields in cases:
            with self.subTest(section=section, fields=fields):
                self.write(section, **fields)
                result = editorial_context(self.root, 'demo')[section]
                self.assertEqual(result['status'], 'invalid')
                self.assertNotIn('runtimeFrames', result)

    def test_project_script_and_file_symlinks_cannot_read_other_projects(self):
        outside = self.root / 'other'
        outside.mkdir()
        external = outside / 'cut.json'
        external.write_text(json.dumps({'format': 'raven-cut', 'version': 1, 'fps': 24,
                                       'revision': 99, 'clips': self.cut()}))
        self.script.joinpath('cut.json').symlink_to(external)
        self.assertEqual(editorial_context(self.root, 'demo')['cut']['status'], 'invalid')
        self.script.joinpath('cut.json').unlink()
        self.script.rmdir(); self.script.symlink_to(outside, target_is_directory=True)
        self.assertEqual(editorial_context(self.root, 'demo')['cut']['status'], 'invalid')
        self.script.unlink()
        self.script.parent.rmdir(); self.script.parent.symlink_to(outside, target_is_directory=True)
        self.assertEqual(editorial_context(self.root, 'demo')['cut']['status'], 'invalid')
        self.assertEqual(editorial_context(self.root, '../other')['cut']['status'], 'invalid')

    def test_nested_injected_values_and_duplicate_shots_are_invalid(self):
        clips = self.cut()
        clips[0]['source'] = {'id': {'path': '/private/source'}, 'label': 'Take'}
        self.write('cut', fps=24, clips=clips)
        self.assertEqual(editorial_context(self.root, 'demo')['cut']['status'], 'invalid')
        self.write('cut', fps=24, clips=self.cut() * 2)
        self.assertEqual(editorial_context(self.root, 'demo')['cut']['status'], 'invalid')
        self.write('sound', version={'path': '/private/version'})
        result = editorial_context(self.root, 'demo')
        self.assertEqual(result['sound']['status'], 'unsupported')
        self.assertNotIn('/private/', json.dumps(result))

    def test_context_and_input_limits_are_explicit_and_reads_do_not_write(self):
        from unittest.mock import patch
        clips = self.cut(100)
        for clip in clips:
            clip['source'] = {'id': 'take-' + clip['shotId'], 'label': 'L' * 900, 'video': '', 'image': ''}
        path = self.write('cut', fps=24, clips=clips, undo=[{'label': 'private', 'clips': []}])
        before = path.read_bytes()
        result = editorial_context(self.root, 'demo')['cut']
        self.assertEqual(result['status'], 'truncated')
        self.assertEqual(result['clipCount'], 100)
        self.assertEqual(result['runtimeFrames'], 2400)
        self.assertNotIn('clips', result)
        self.assertEqual(path.read_bytes(), before)
        with patch('server.raven_editorial.MAX_FILE_BYTES', 10):
            limited = editorial_context(self.root, 'demo')['cut']
        self.assertEqual(limited, {'status': 'truncated', 'reason': 'file_limit'})
        self.assertNotIn('runtimeFrames', limited)

    def test_hidden_malformed_history_and_external_source_are_not_reported_ok(self):
        self.write('cut', fps=24, clips=self.cut(), undo=[{'secret': 'malformed'}])
        self.assertEqual(editorial_context(self.root, 'demo')['cut']['status'], 'invalid')
        clips = self.cut()
        clips[0]['source'] = {'id': 't', 'label': 'T', 'video': '/outside/a.mov', 'image': ''}
        self.write('cut', fps=24, clips=clips)
        self.assertEqual(editorial_context(self.root, 'demo')['cut']['status'], 'invalid')

    def test_nonfinite_timing_and_directory_are_invalid(self):
        clips = self.cut(); clips[0]['outFrame'] = float('nan')
        self.write('cut', fps=24, clips=clips)
        self.assertEqual(editorial_context(self.root, 'demo')['cut']['status'], 'invalid')
        (self.script / 'cut.json').unlink(); (self.script / 'cut.json').mkdir()
        self.assertEqual(editorial_context(self.root, 'demo')['cut']['status'], 'invalid')

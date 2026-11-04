"""Actual native journal stores through the shared director read boundary."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from coverage import CoverageStore, new_plan, propose
from cut import CutStore, choose_take, trim
from director_contract import PANEL_NAMES, editorial_context
from director_editorial import EDITORIAL_TOOLS, inspection_result
from director_executor import DirectorExecutor
from library import MediaLibrary
from project import Project, Shot
from sequence import SequenceStore, edit_group
from sound import SoundStore, default_state
from tests.test_director_executor import Backend
from workspace import Workspace


class DirectorEditorialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name) / 'Studio')
        self.workspace.ensure()
        self.backend = Backend()
        self.board = {'name': 'Film', 'shots': [{'id': 'a'}, {'id': 'b'}],
                      'characters': [{'id': 'ada', 'name': 'Ada'}, {'id': 'bea', 'name': 'Bea'}],
                      'filmBible': {'locations': [{'id': 'room', 'name': 'Stuen'}]}}
        self.backend.boards['film'] = copy.deepcopy(self.board)
        self.executor = DirectorExecutor(self.backend, self.workspace, MediaLibrary(self.workspace))

    def snapshot(self):
        return {str(p.relative_to(self.workspace.root)): p.read_bytes()
                for p in self.workspace.root.rglob('*') if p.is_file()}

    def journals(self):
        cut = CutStore(self.workspace, 'film')
        project = Project('film', 'Film', '', [Shot('a', 'A', 5), Shot('b', 'B', 5)])
        doc = cut.initialize(project)
        source = {'id': 'take-7', 'label': 'Valgt take', 'video': 'Input/private.mov', 'image': ''}
        doc = cut.commit(doc, choose_take(doc['clips'], 'a', source, 180), 'Velg take')
        doc = cut.commit(doc, trim(doc['clips'], 'a', 24, 96), 'Trim')
        structure = SequenceStore(self.workspace, 'film')
        sd = structure.initialize(self.board, doc['clips'])
        structure.commit(sd, edit_group(sd['state'], 'film', 'film', targetFrames=240), 'Tidsmål')
        coverage = CoverageStore(self.workspace, 'film')
        plan = new_plan(self.board, 'room'); plan['cameras'] = propose(plan)
        coverage.commit(coverage.load(), {'room': plan})
        sound = SoundStore(self.workspace, 'film')
        state = default_state()
        state['original']['enabled'] = False
        state['tracks'][1].update(gainDb=-6, solo=True)
        state['clips'] = [{'id': 'music-1', 'track': 'music', 'label': 'Tema',
                           'path': 'Input/private.wav', 'sourceFrames': 480, 'inFrame': 24,
                           'outFrame': 264, 'atFrame': 48, 'gainDb': -3,
                           'fadeIn': 12, 'fadeOut': 24, 'muted': False}]
        sound.commit(sound.load(), state, 'Legg til tema')

    def test_reads_real_stores_exactly_without_any_writes_or_inference(self):
        self.journals()
        before = self.snapshot()
        result = self.executor.execute([{'tool': tool} for tool in EDITORIAL_TOOLS], 'film')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(any(call[0] != 'GET' for call in self.backend.calls))
        data = {row['tool']: row['editorial'] for row in result['results']}
        self.assertTrue(all(row['status'] == 'ok' for row in data.values()), data)
        cut = data['inspect_cut']
        self.assertEqual(cut['runtimeFrames'], 192)
        self.assertEqual((cut['clips'][0]['takeId'], cut['clips'][0]['inFrame'], cut['clips'][0]['outFrame']), ('take-7', 24, 96))
        self.assertEqual(data['inspect_structure']['targetFrames'], 240)
        plan = data['inspect_coverage']['plans'][0]
        self.assertEqual((plan['width'], plan['depth'], plan['axis']), (8, 6, ['actor-1', 'actor-2']))
        self.assertTrue(plan['cameras'][0]['aim'])
        sound = data['inspect_sound']
        self.assertEqual(sound['clips'][0]['atFrame'], 48)
        self.assertEqual(sound['clips'][0]['outFrame'], 264)
        self.assertFalse(sound['original']['enabled'])
        self.assertTrue(sound['tracks'][1]['solo'])
        encoded = json.dumps(data)
        for hidden in ('Input/', 'private.mov', 'private.wav', 'undo', 'redo', 'Velg take'):
            self.assertNotIn(hidden, encoded)

    def test_inspection_does_not_initialize_a_project_or_manuscript(self):
        before = self.snapshot()
        result = self.executor.execute([{'tool': 'inspect_sound'}, {'tool': 'status'}], 'film')
        self.assertEqual(result['results'][0]['editorial']['status'], 'absent')
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.workspace.projects_dir / 'film').exists())

    def test_re_reads_current_journal_when_executing_and_refuses_mutation_fields(self):
        self.journals()
        old = editorial_context(self.workspace.projects_dir, 'film')['sound']['revision']
        store = SoundStore(self.workspace, 'film')
        doc = store.load()
        changed = copy.deepcopy(doc['state']); changed['tracks'][1]['muted'] = True
        store.commit(doc, changed, 'Stille')
        result = self.executor.execute([{'tool': 'inspect_sound'}], 'film')
        self.assertGreater(result['results'][0]['editorial']['revision'], old)
        self.assertTrue(result['results'][0]['editorial']['tracks'][1]['muted'])
        with self.assertRaises(ValueError):
            self.executor.execute([{'tool': 'inspect_sound', 'gainDb': 3}], 'film')
        with self.assertRaises(ValueError):
            self.executor.execute([{'tool': 'inspect_cut'}, {'tool': 'edit_project', 'fields': {'name': 'Oops'}}], 'film')

    def test_malformed_hidden_history_and_blank_native_fields_are_invalid(self):
        self.journals()
        script = self.workspace.projects_dir / 'film' / 'Script'
        changes = [('cut', lambda doc: doc.update(undo=[{}])),
                   ('coverage', lambda doc: doc['plans']['room']['cameras'][0].update(name='')),
                   ('structure', lambda doc: doc['state']['sequences'][0].update(title=' ')),
                   ('sound', lambda doc: doc['state']['tracks'][0].update(name=''))]
        for section, change in changes:
            path = script / (section + '.json')
            saved = path.read_bytes()
            doc = json.loads(saved); change(doc); path.write_text(json.dumps(doc))
            self.assertEqual(editorial_context(self.workspace.projects_dir, 'film')[section]['status'], 'invalid', section)
            path.write_bytes(saved)

    def test_new_panels_are_validated_and_route_to_native_pages(self):
        path = Path(__file__).parents[1] / 'director_workspace.py'
        spec = importlib.util.spec_from_file_location('editorial_workspace_fixture', path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'raven': types.ModuleType('raven')}):
            spec.loader.exec_module(module)
        fixture = types.SimpleNamespace(history=[], mode_set=lambda page: opened.append(page))
        for panel in ('scenes', 'cut', 'coverage', 'structure', 'sound'):
            opened = []
            result = self.executor.execute([{'tool': 'open_panel', 'panel': panel}], 'film')
            module.DirectorWorkspaceMixin.route_director_result(fixture, result)
            self.assertEqual(opened, [panel])
            self.assertIn(panel, PANEL_NAMES)
        for tool, (panel, title) in EDITORIAL_TOOLS.items():
            result = {'results': [inspection_result(tool, {panel: {'status': 'absent'}})]}
            fixture.history = [{'execution': {'result': result}}]
            fixture.show_modal = lambda title, body: rendered.append(body)
            rendered = []
            module.DirectorWorkspaceMixin.show_director_result(fixture, 0)
            self.assertIn('page.' + panel, rendered[0])
            self.assertIn('ingen lagrede valg', rendered[0])

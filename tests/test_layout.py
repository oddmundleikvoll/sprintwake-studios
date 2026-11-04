"""Shared-editor identity, information hierarchy and native XML constraints."""
import copy
from pathlib import Path
import unittest
import xml.etree.ElementTree as E

from layout_model import outline, panel_sizes
from project import Project, Shot


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.board={'shots':[{'id':'a','directorScene':1},{'id':'b','directorScene':1},{'id':'c','directorScene':2}]}
        self.project=Project('film','Film','',[Shot('c','C',3),Shot('a','A',2)])

    def test_outline_uses_visible_take_order_and_never_mutates_canonical_cards(self):
        before=copy.deepcopy(self.board)
        groups=outline(self.project,self.board)
        self.assertEqual([sid for seq in groups for scene in seq['scenes'] for sid in scene['shotIds']],['c','a'])
        self.assertEqual([scene['frames'] for scene in groups[0]['scenes']],[72,48])
        self.assertEqual(self.board,before)

    def test_narrative_groups_retain_empty_scenes_and_collect_unassigned_once(self):
        state={'sequences':[{'id':'seq','title':'Act','scenes':[{'id':'scene','title':'Scene','shotIds':['a','b']},{'id':'empty','title':'Empty','shotIds':[]}]}]}
        before=copy.deepcopy(state)
        groups=outline(self.project,self.board,state)
        self.assertEqual(groups[0]['scenes'][0]['shotIds'],['a'])
        self.assertEqual(groups[0]['scenes'][1]['shotIds'],[])
        self.assertEqual(groups[1]['scenes'][0]['shotIds'],['c'])
        self.assertEqual(state,before)

    def test_layout_keeps_useful_player_width_at_supported_sizes(self):
        for width,height in [(1440,920),(1180,760),(1920,1080)]:
            for sound in (False,True):
                left,right,bottom=panel_sizes(width,height,sound)
                self.assertGreaterEqual(width-left-right-38,560)
                self.assertGreaterEqual(left,210);self.assertGreaterEqual(right,260)
                self.assertLess(bottom,height*.38)

    def test_native_shell_has_one_player_and_both_timelines_inside_common_workspace(self):
        root=E.parse(Path(__file__).parents[1]/'assets/studio.xml').getroot()
        names=[x.get('Name') for x in root.iter() if x.get('Name')]
        self.assertEqual(len(names),len(set(names)))
        shell=next(x for x in root.iter() if x.get('Name')=='film.page')
        contained={x.get('Name') for x in shell.iter()}
        self.assertTrue({'workspace.sidebar','workspace.viewer','workspace.details','timeline.panel','timeline','sound.timeline','scene.details','structure.content','sound.inspector'} <= contained)
        self.assertEqual(names.count('viewer'),1)
        # Native Border is a single-child decorator: two children silently lose
        # the first one, even when both names exist in the source XML.
        for border in root.iter('Border'):
            self.assertEqual(len(border),1,border.attrib)
        self.assertEqual(next(x for x in root.iter() if x.get('Name')=='project.title').get('Font'),'/System/Library/Fonts/NewYork.ttf')

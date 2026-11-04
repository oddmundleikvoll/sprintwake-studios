import unittest

from director_panel import DirectorAuthoringMixin
from director_state import authoring_turn, normalize_turn


class StudioFixture(DirectorAuthoringMixin):
    def __init__(self):
        self.board = {"shots":[]}
        self.slug = "film"
        self.epoch = 1
        self.pending = {"save":object()}
        self.saved_callbacks = []
        self.submissions = []
        self.workspace = None
        self.backend = None
    def text(self,name): return {"author.brief":"En maur finner hjem.","author.count":"12"}[name]
    def _busy(self): return False
    def save(self,after=None): self.saved_callbacks.append(after)
    def show_modal(self,*args,**kwargs): pass
    def set_label(self,*args): pass
    def submit(self,key,operation,done):
        self.submissions.append((key,operation,done))
        self.pending[key]=operation
        return True


class DirectorPanelTests(unittest.TestCase):
    def test_double_start_during_save_keeps_single_task_and_cancel_event(self):
        studio=StudioFixture()
        studio.start_authoring()
        event=studio.author_cancel
        studio.start_authoring()
        self.assertEqual(len(studio.saved_callbacks),1)
        self.assertIs(studio.author_cancel,event)
        studio.saved_callbacks[0]()
        self.assertEqual(len(studio.submissions),1)
        studio.start_authoring()
        studio.cancel_authoring()
        self.assertTrue(event.is_set())
        self.assertIs(studio.author_cancel,event)

    def test_setup_commit_is_not_reported_as_no_changes(self):
        turn=authoring_turn({"status":"cancelled","completed":0,"total":12,"setupDone":True,"error":"Stoppet"})
        self.assertTrue(turn["applied"])
        self.assertEqual(turn["proposalStatus"],"applied")
        self.assertIn("film bible are saved",turn["content"])
        self.assertEqual(normalize_turn(turn),turn)

    def test_success_without_actions_is_a_saved_authoring_turn(self):
        turn=authoring_turn({"status":"done","completed":12,"total":12,"setupDone":True})
        self.assertTrue(turn["applied"])
        self.assertEqual(turn["proposalStatus"],"applied")
        self.assertIn("12 new shots",turn["content"])

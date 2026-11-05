import copy
import tempfile
import unittest
from pathlib import Path

from director_authoring import author_project
from studio_state import authoring_digest
from workspace import Workspace


class FakeBackend:
    def __init__(self, board, replies):
        self.board = copy.deepcopy(board)
        self.replies = list(replies)
        self.gets = []
        self.puts = []
        self.chats = []

    def get(self, path):
        self.gets.append(path)
        return {"slug": "film", "board": copy.deepcopy(self.board)}

    def put(self, path, board, **kwargs):
        self.puts.append((path, copy.deepcopy(board)))
        self.board = copy.deepcopy(board)
        return {"board": copy.deepcopy(self.board)}

    def chat(self, payload):
        self.chats.append(payload)
        return copy.deepcopy(self.replies.pop(0))


def shots(count, start=1):
    return [{"tool": "add_shot", "shot": {
        "title": f"Klipp {i}", "prompt": "Ada stands by the gate and looks toward the light.",
        "start": "Ada stands.", "action": "Ada looks.", "end": "Ada remains still.",
        "stateBefore": "Ada stands at the gate, facing the light.",
        "stateAfter": "Ada stays at the gate, looking toward the light.",
        "camera": "Wide locked camera.", "directorScene": i, "locationId": "loc-gate",
        "frames": 121,
        "characterIds": ["c1"],
    }} for i in range(start, start + count)]


class DirectorAuthoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.tmp.name) / "Raven Studio")
        self.board = {"schema": 1, "name": "Film", "shots": [], "characters": [],
                      "filmBible": {}, "revision": "r1"}
        self.setup = {"proposalStatus": "ready", "actions": [
            {"tool": "set_board_fields", "fields": {"sceneDescription": "En kort historie.",
             "filmBible": {"storyArc": "Et møte.", "acts": [{"id": "act-1", "title": "Møte"}],
                            "locations": [{"id": "loc-gate", "title": "Porten", "description": "En port."}],
                            "props": []}}},
            {"tool": "add_character", "character": {"id": "c1", "name": "Ada", "description": "Rolig."}},
        ]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_setup_and_twelve_shots_commit_in_four_three_card_batches(self):
        backend = FakeBackend(self.board, [self.setup,
            {"proposalStatus": "ready", "actions": shots(3)},
            {"proposalStatus": "ready", "actions": shots(3, 4)},
            {"proposalStatus": "ready", "actions": shots(3, 7)},
            {"proposalStatus": "ready", "actions": shots(3, 10)}])
        progress = []
        result = author_project(backend, self.workspace, "film", "Ada møter lyset", 12,
                                on_progress=progress.append)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["completed"], 12)
        self.assertEqual(len(result["board"]["shots"]), 12)
        self.assertEqual(len(backend.chats), 5)
        self.assertEqual(len(backend.puts), 5)
        self.assertTrue((self.workspace.projects_dir / "film" / "Script" / "manus.md").is_file())
        self.assertEqual(progress[-1]["completed"], 12)
        self.assertTrue(all(not any(a.get("tool") in {"start_render", "render_storyboard"}
                                   for a in reply.get("actions", [])) for reply in [self.setup]))

    def test_truncated_batch_and_correction_keep_smaller_slot_plan(self):
        import json
        from unittest.mock import patch
        invalid = shots(3)
        invalid[0]["shot"]["frames"] = 120
        backend = FakeBackend(self.board, [self.setup,
            {"proposalStatus": "incomplete", "actions": []},
            {"proposalStatus": "ready", "actions": invalid},
            {"proposalStatus": "ready", "actions": shots(3)},
            {"proposalStatus": "ready", "actions": shots(3, 4)}])
        with patch("director_authoring.BATCH_SIZE", 6):
            result = author_project(backend, self.workspace, "film", "Ada møter lyset", 6)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["completed"], 6)
        for payload in backend.chats[2:4]:
            prompt = payload["message"]
            slots, _ = json.JSONDecoder().raw_decode(prompt[prompt.index("["):])
            self.assertEqual([s["position"] for s in slots], [1, 2, 3])
            self.assertIn("exactly 3 new", prompt)
            self.assertIn("Ada møter lyset", prompt)
        self.assertEqual(len(backend.puts), 3)

    def test_later_failure_preserves_first_committed_batch(self):
        backend = FakeBackend(self.board, [self.setup,
            {"proposalStatus": "ready", "actions": shots(3)},
            {"proposalStatus": "rejected", "actions": []}])
        result = author_project(backend, self.workspace, "film", "Kort historie", 12)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["completed"], 3)
        self.assertEqual(len(result["board"]["shots"]), 3)
        self.assertNotIn("complete", result.get("error", "").lower())

    def test_explicit_resume_rejects_stale_board_before_model_or_put(self):
        backend = FakeBackend(self.board, [self.setup,
            {"proposalStatus": "rejected", "actions": [], "errors": ["Invalid camera"]}])
        failed = author_project(backend, self.workspace, "film", "Kort historie", 6)
        self.assertEqual(failed["status"], "failed")
        chat_count, put_count = len(backend.chats), len(backend.puts)
        backend.board["sceneDescription"] = "Brukeren endret idéen"
        with self.assertRaises(ValueError) as raised:
            author_project(backend, self.workspace, "film", "Kort historie", 6,
                           resume_only=True, expected_digest=authoring_digest(backend.board))
        self.assertIn("changed", str(raised.exception))
        self.assertEqual(len(backend.chats), chat_count)
        self.assertEqual(len(backend.puts), put_count)

    def test_native_empty_note_defaults_do_not_block_explicit_resume(self):
        backend = FakeBackend(self.board, [self.setup,
            {"actions": shots(3)}, {"proposalStatus": "rejected", "actions": []}])
        failed = author_project(backend, self.workspace, "film", "Kort historie", 6)
        self.assertEqual(failed["completed"], 3)
        for shot in backend.board["shots"]:
            shot.update(ravenNotes="", ravenReviewed=False)
        backend.replies.append({"actions": shots(3, 4)})
        result = author_project(backend, self.workspace, "film", "Kort historie", 6,
                                resume_only=True, expected_digest=authoring_digest(backend.board))
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(result["board"]["shots"]), 6)

    def test_resume_uses_checkpoint_and_reaches_exact_total(self):
        backend = FakeBackend(self.board, [self.setup,
            {"proposalStatus": "ready", "actions": shots(3)},
            {"proposalStatus": "rejected", "actions": []}])
        first = author_project(backend, self.workspace, "film", "Kort historie", 6)
        self.assertEqual(first["completed"], 3)
        backend.replies.append({"proposalStatus": "ready", "actions": shots(3, 4)})
        resumed = author_project(backend, self.workspace, "film", "Kort historie", 6)
        self.assertEqual(resumed["status"], "done")
        self.assertEqual(resumed["completed"], 6)
        self.assertEqual(len(resumed["board"]["shots"]), 6)
        # Setup and the first six-card batch were not duplicated.
        self.assertEqual(len(backend.puts), 3)

    def test_resume_reconciles_crash_after_put_before_checkpoint(self):
        class CrashBackend(FakeBackend):
            def __init__(self, board, replies):
                super().__init__(board, replies)
                self.crash = True
            def put(self, path, board, **kwargs):
                super().put(path, board, **kwargs)
                if self.crash:
                    self.crash = False
                    raise SystemExit("simulated crash after PUT")
        backend = CrashBackend(self.board, [self.setup, {"proposalStatus": "ready", "actions": shots(3)}])
        with self.assertRaises(SystemExit):
            author_project(backend, self.workspace, "film", "Kort historie", 6)
        backend.replies.append({"proposalStatus": "ready", "actions": shots(3, 4)})
        # The pending journal is reconciled; no duplicate three-card batch is sent.
        resumed = author_project(backend, self.workspace, "film", "Kort historie", 6,
                                resume_only=True, expected_digest=authoring_digest(backend.board))
        self.assertEqual(resumed["status"], "done")
        self.assertEqual(resumed["completed"], 6)
        self.assertEqual(len(resumed["board"]["shots"]), 6)

    def test_crash_after_setup_put_reconciles_server_character_defaults(self):
        class NormalizingCrashBackend(FakeBackend):
            def put(self, path, board, **kwargs):
                board = copy.deepcopy(board)
                for character in board.get("characters", []):
                    character["private"] = {"serverDefault": True}
                super().put(path, board, **kwargs)
                if len(self.puts) == 1:
                    raise SystemExit("simulated setup crash")
        backend = NormalizingCrashBackend(self.board, [self.setup, {"proposalStatus": "ready", "actions": shots(1)}])
        with self.assertRaises(SystemExit):
            author_project(backend, self.workspace, "film", "Kort historie", 1)
        resumed = author_project(backend, self.workspace, "film", "Kort historie", 1)
        self.assertEqual(resumed["status"], "done")
        self.assertEqual(len(resumed["board"]["characters"]), 1)
        self.assertEqual(len(resumed["board"]["shots"]), 1)

    def test_stale_edit_refuses_to_overwrite(self):
        class StaleBackend(FakeBackend):
            def get(self, path):
                if len(self.gets) == 1:
                    self.board["sceneDescription"] = "Brukerens endring"
                return super().get(path)
        backend = StaleBackend(self.board, [self.setup])
        result = author_project(backend, self.workspace, "film", "Historie", 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(backend.puts), 0)
        self.assertIn("changed", result["error"])
        self.assertEqual(result["board"]["sceneDescription"], "Brukerens endring")

    def test_cancellation_returns_actual_partial_board(self):
        class Cancel:
            def __init__(self): self.calls = 0
            def is_set(self):
                self.calls += 1
                return self.calls > 1
        backend = FakeBackend(self.board, [self.setup, {"proposalStatus": "ready", "actions": shots(6)}])
        result = author_project(backend, self.workspace, "film", "Historie", 6, cancel=Cancel())
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["completed"], 0)
        self.assertEqual(len(result["board"]["shots"]), 0)

    def test_forbidden_media_action_cannot_update_the_board(self):
        forbidden = copy.deepcopy(self.setup)
        forbidden["actions"].append({"tool": "start_render"})
        backend = FakeBackend(self.board, [forbidden])
        result = author_project(backend, self.workspace, "film", "Historie", 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(backend.puts), 0)
        self.assertEqual(result["board"]["shots"], [])

    def test_opening_and_continuation_frames_share_one_camera_group(self):
        continuation = shots(1, 2)[0]
        continuation["shot"]["directorScene"] = 1
        continuation["shot"]["frames"] = 120
        backend = FakeBackend(self.board, [self.setup,
            {"proposalStatus": "ready", "actions": [shots(1)[0], continuation]}])
        result = author_project(backend, self.workspace, "film", "Historie", 2)
        self.assertEqual(result["status"], "done")
        self.assertEqual([shot["frames"] for shot in result["board"]["shots"]], [121, 120])

    def test_continuation_cannot_switch_camera(self):
        first = shots(1)[0]
        second = shots(1, 2)[0]
        second["shot"]["directorScene"] = 1
        second["shot"]["frames"] = 120
        second["shot"]["camera"] = "Close-up camera."
        backend = FakeBackend(self.board, [self.setup,
            {"proposalStatus": "ready", "actions": [first, second]}])
        result = author_project(backend, self.workspace, "film", "Historie", 2)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["completed"], 0)
        self.assertEqual(len(backend.puts), 1)

    def test_invalid_frame_gets_one_model_correction_with_full_brief(self):
        bad = shots(1)[0]
        bad["shot"]["frames"] = 1
        backend = FakeBackend(self.board, [self.setup,
            {"proposalStatus": "ready", "actions": [bad]},
            {"proposalStatus": "ready", "actions": shots(1)}])
        brief = "Maursoldaten vokter en forlatt port i regnet"
        result = author_project(backend, self.workspace, "film", brief, 1)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(backend.puts), 2)
        self.assertEqual(backend.chats[0]["authoringStage"], "setup")
        self.assertEqual(backend.chats[1]["authoringStage"], "shots")
        self.assertIn("actual validation error", backend.chats[2]["message"])
        self.assertIn(brief, backend.chats[2]["message"])

    def test_invalid_camera_gets_one_model_correction_without_duplicate_put(self):
        first = shots(1)[0]
        bad_second = shots(1, 2)[0]
        bad_second["shot"]["directorScene"] = 1
        bad_second["shot"]["frames"] = 120
        bad_second["shot"]["camera"] = "Conflicting close-up"
        good_second = shots(1, 2)[0]
        good_second["shot"]["directorScene"] = 1
        good_second["shot"]["frames"] = 120
        backend = FakeBackend(self.board, [self.setup,
            {"proposalStatus": "ready", "actions": [first, bad_second]},
            {"proposalStatus": "ready", "actions": [first, good_second]}])
        result = author_project(backend, self.workspace, "film", "To korte scener", 2)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(backend.puts), 2)
        self.assertEqual(len(result["board"]["shots"]), 2)

    def test_camera_and_frame_feedback_identifies_all_bad_shots(self):
        from director_authoring import _validated_shot_candidate
        first=shots(1)[0]
        second=shots(1,2)[0]
        second["shot"].update(directorScene=1,frames=121,camera="A different angle")
        with self.assertRaises(ValueError) as failure:
            _validated_shot_candidate(self.board,{"actions":[first,second]},2)
        text=str(failure.exception)
        self.assertIn("Shot 2",text)
        self.assertIn("continuation frame",text)
        self.assertIn("continuous camera group",text)
        self.assertIn("NEW directorScene",text)

    def test_new_manuscript_cards_require_continuity_and_registered_props(self):
        from director_authoring import _validated_shot_candidate
        board=copy.deepcopy(self.board)
        board['characters']=[{'id':'c1','name':'Ada'}]
        board['filmBible']={'locations':[{'id':'loc-gate'}], 'props':[{'id':'key','title':'Brass key'}]}
        card=shots(1)[0]
        card['shot'].update(propIds=['unknown'],assetStates={'unknown':'In Ada\'s hand'})
        card['shot'].pop('stateAfter')
        with self.assertRaises(ValueError) as failure:
            _validated_shot_candidate(board,{'actions':[card]},1)
        for message in ('stateBefore and stateAfter','propIds','assetStates'):
            self.assertIn(message,str(failure.exception))
        card['shot'].update(stateAfter='The brass key lies beside Ada.',propIds=['key'],assetStates={'key':'Dull brass'})
        _,candidate,_=_validated_shot_candidate(board,{'actions':[card]},1)
        self.assertEqual(candidate['shots'][0]['assetStates'],{'key':'Dull brass'})

    def test_batch_prompt_carries_recent_planned_handoff_without_media_or_global_repetition(self):
        from director_authoring import _shot_prompt
        board=copy.deepcopy(self.board)
        board['shots']=[dict(shots(1)[0]['shot'],outputs=['PRIVATE_MOVIE.mp4'],recipe={'key':'PRIVATE_RECIPE'})]
        prompt=_shot_prompt(board,'The original story',12,3,3)
        self.assertIn('Ada stays at the gate',prompt)
        self.assertIn('RECENT PLANNED HANDOFF (not evidence of rendered action)',prompt)
        self.assertIn('CINEMATIC COVERAGE',prompt)
        self.assertIn('The original story',prompt)
        self.assertNotIn('PRIVATE_MOVIE',prompt)
        self.assertNotIn('PRIVATE_RECIPE',prompt)

    def test_writer_rejects_noncontiguous_group_and_location_change(self):
        from director_authoring import _validated_shot_candidate
        board=copy.deepcopy(self.board)
        board['characters']=[{'id':'c1','name':'Ada'}]
        board['filmBible']={'locations':[{'id':'loc-gate'},{'id':'loc-room'}]}
        board['shots']=[dict(shots(1)[0]['shot'],id='s1'),dict(shots(1,2)[0]['shot'],id='s2')]
        card=shots(1,3)[0]
        card['shot'].update(directorScene=1,frames=120,locationId='loc-room')
        with self.assertRaises(ValueError) as failure:
            _validated_shot_candidate(board,{'actions':[card]},1)
        self.assertIn('contiguous',str(failure.exception))
        self.assertIn('location or resolution',str(failure.exception))

    def test_writer_native_dialogue_requires_a_known_or_unambiguous_speaker(self):
        from director_authoring import _validated_shot_candidate
        board=copy.deepcopy(self.board)
        board['characters']=[{'id':'c1','name':'Ada'},{'id':'c2','name':'Bo'}]
        board['filmBible']={'locations':[{'id':'loc-gate'}]}
        card=shots(1)[0]
        card['shot'].update(characterIds=['c1','c2'],dialogue='Hello',dialogueSource='native')
        with self.assertRaisesRegex(ValueError,'speakerId'):
            _validated_shot_candidate(board,{'actions':[card]},1)
        card['shot']['speakerId']='c1'
        _validated_shot_candidate(board,{'actions':[card]},1)
        card['shot']['dialogueSource']='typo'
        with self.assertRaisesRegex(ValueError,'dialogueSource'):
            _validated_shot_candidate(board,{'actions':[card]},1)

    def test_empty_film_bible_gets_one_setup_correction_then_commits(self):
        bad_setup = {"proposalStatus": "ready", "actions": [
            {"tool": "set_board_fields", "fields": {"sceneDescription": "Ufullstendig"}}
        ]}
        backend = FakeBackend(self.board, [bad_setup, self.setup,
            {"proposalStatus": "ready", "actions": shots(1)}])
        result = author_project(backend, self.workspace, "film", "En maursoldat", 1)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(backend.puts), 2)
        self.assertIn("filmBible act", backend.chats[1]["message"])
        self.assertIn("En maursoldat", backend.chats[1]["message"])




class BeforeCommitRecoveryTests(unittest.TestCase):
    def test_crash_before_setup_put_retries_setup_on_resume(self):
        class Crash(BaseException): pass
        class BeforePut(FakeBackend):
            crash = True
            def put(self,path,board,**kwargs):
                if self.crash:
                    self.crash = False
                    raise Crash()
                return super().put(path,board,**kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            workspace=Workspace(Path(tmp)/"Raven")
            board={"name":"Film","characters":[{"id":"c1","name":"Ada"}],"shots":[],"filmBible":{}}
            setup={"proposalStatus":"ready","actions":[{"tool":"set_board_fields","fields":{"sceneDescription":"En hage.","filmBible":{"acts":[{"id":"act-1","title":"Møte"}],"locations":[{"id":"loc-gate","title":"Port","description":"En gammel port."}]}}}]}
            backend=BeforePut(board,[setup,setup,{"proposalStatus":"ready","actions":shots(1)}])
            with self.assertRaises(Crash):
                author_project(backend,workspace,"film","En hage.",1)
            self.assertFalse(backend.board.get("sceneDescription"))
            result=author_project(backend,workspace,"film","En hage.",1)
            self.assertEqual(result["status"],"done",result.get("error"))
            self.assertEqual(len(backend.chats),3)
            self.assertEqual(len(result["board"]["shots"]),1)


if __name__ == "__main__":
    unittest.main()

import unittest

from director_state import normalize_turn, response_turn, proposal_summary


class DirectorStateTests(unittest.TestCase):
    def test_actual_legacy_truncation_does_not_claim_created_shots(self):
        raw = {"role":"assistant", "content":"Jeg har satt opp et fullt manus med 28 klipp. The proposal was incomplete, so no changes are available to apply.", "actions":[],"source":"version"}
        turn = normalize_turn(raw)
        self.assertEqual(turn["proposalStatus"], "incomplete")
        self.assertNotIn("28 klipp", turn["content"])
        self.assertEqual(turn["originalContent"], raw["content"])
        self.assertFalse(turn["applied"])
        self.assertEqual(normalize_turn(turn),turn)
        self.assertIn("28 klipp",raw["content"])

    def test_ready_is_pending_until_explicit_apply(self):
        actions=[{"tool":"add_shot","shot":{"title":f"Shot {i}"}} for i in range(6)]
        turn=response_turn({"message":"Her er første del.","proposalStatus":"ready","actions":actions},"source")
        self.assertEqual(turn["proposalStatus"],"ready")
        self.assertIn("6 new shots",proposal_summary(turn["actions"]))
        self.assertFalse(turn.get("applied"))
        self.assertEqual(normalize_turn(dict(turn,applied=True))["proposalStatus"],"applied")

    def test_no_action_is_not_a_saved_manuscript(self):
        turn=response_turn({"message":"En idé til reisen.","actions":[]},"source")
        self.assertEqual(turn["proposalStatus"],"no_changes")
        rejected=response_turn({"message":"Model claim","proposalStatus":"rejected","actions":[]},"source")
        self.assertEqual(rejected["proposalStatus"],"rejected")
        self.assertNotIn("Model claim", rejected["content"])


class DirectorDiagnosticsTests(unittest.TestCase):
    def test_rejection_preserves_actual_reason_and_trace(self):
        from director_state import response_turn
        turn=response_turn({"message":"Forslag","actions":[],"proposalStatus":"rejected","errors":["action 0: unknown fields"],"traceId":"trace-1","attempts":2,"traceWriteError":"disk full"},None)
        self.assertEqual(turn["errors"],["action 0: unknown fields"])
        self.assertEqual(turn["traceId"],"trace-1")
        self.assertEqual(turn["attempts"],2)
        self.assertEqual(turn["traceWriteError"],"disk full")
        self.assertFalse(turn["applied"])


class DirectorExecutionRebindTests(unittest.TestCase):
    def test_partial_creation_receipt_survives_bind_and_can_show_resume(self):
        from director_state import rebase_created_execution
        result={"slug":"maur","board":{"name":"Maur","shots":[]},"results":[{"tool":"create_project","slug":"maur"},{"tool":"write_manuscript","brief":"Original brief","count":12}]}
        session={"messages":[{"role":"assistant","sourceSlug":None,"stale":True,"proposalStatus":"stale","actions":[{"tool":"create_project"},{"tool":"write_manuscript"}],"execution":{"status":"failed","result":result}}, {"role":"assistant","stale":True,"actions":[{"tool":"add_shot"}]}]}
        self.assertTrue(rebase_created_execution(session,0,result))
        turn=session["messages"][0]
        self.assertEqual(turn["sourceSlug"],"maur")
        self.assertFalse(turn.get("stale"))
        self.assertEqual(turn["proposalStatus"],"ready")
        self.assertTrue(session["messages"][1]["stale"])
        self.assertFalse(rebase_created_execution(session,0,result))

    def test_no_create_receipt_cannot_rebase_a_proposal(self):
        from director_state import rebase_created_execution
        session={"messages":[{"sourceSlug":None,"stale":True}]}
        self.assertFalse(rebase_created_execution(session,0,{"slug":"other","results":[]}))
        self.assertTrue(session["messages"][0]["stale"])

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from server.raven_chat import chat


class Store:
    data_dir = Path(tempfile.gettempdir())
    def __init__(self, boards, images=None): self.boards, self.images = boards, images or {}
    def list_boards(self): return [{"slug": k, "name": v.get("name", k), "shots": len(v.get("shots", []))} for k, v in self.boards.items()]
    def load(self, slug): return self.boards[slug]
    def image_history(self, slug): return self.images.get(slug, [])


class Service:
    def __init__(self, response): self.response, self.seen = response, None; self.thinking = "medium"; self.last_thinking = None
    def __copy__(self):
        other = type(self)(self.response); other.seen = self.seen; other.thinking = self.thinking; self.last_thinking = other; return other
    def complete(self, system, user, **kwargs): self.seen = (system, user); return self.response


class SequenceService(Service):
    def __init__(self, responses): super().__init__(responses[0]); self.responses = list(responses); self.calls = 0
    def __copy__(self):
        other = type(self)(self.responses); other.calls = self.calls; other.thinking = self.thinking; return other
    def complete(self, system, user, **kwargs):
        self.seen = (system, user); self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


class Ctx:
    def __init__(self, response, boards): self.store, self.service = Store(boards), Service(response); self.film = object()
    def llm(self, _): return self.service


class RavenChatTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix="raven-chat-test-")
        self.addCleanup(temporary.cleanup)
        projects=Path(temporary.name)/"Projects"
        projects.mkdir()
        replacement=patch.object(Store,"data_dir",projects)
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_no_project_can_create(self):
        ctx = Ctx(json.dumps({"message": "start", "actions": [{"tool": "create_project", "name": "Nordlys"}]}), {})
        out = chat(ctx, {"slug": None, "message": "Start Nordlys"})
        self.assertEqual(out["proposalStatus"], "pending")
        self.assertEqual(out["actions"][0]["tool"], "create_project")
        self.assertIn("projects", ctx.service.seen[1])

    def test_no_project_can_open_known(self):
        ctx = Ctx(json.dumps({"message": "open", "actions": [{"tool": "open_project", "slug": "demo"}]}), {"demo": {}})
        self.assertEqual(chat(ctx, {"slug": None, "message": "Åpne demo"})["proposalStatus"], "pending")

    def test_full_context_is_sent(self):
        board = {"name": "Film", "filmBible": {"theme": "hope"}, "characters": [{"id": "c1", "name": "Ada"}],
                 "shots": [{"id": "s1", "start": "door", "action": "walk", "end": "room", "camera": "wide"}]}
        ctx = Ctx('{"message":"ok","actions":[]}', {"demo": board})
        chat(ctx, {"slug": "demo", "message": "Hva mangler?"})
        prompt = ctx.service.seen[1]
        self.assertIn("hope", prompt); self.assertIn("Ada", prompt); self.assertIn("door", prompt)

    def test_editorial_state_reaches_live_prompt_without_sources_or_history(self):
        script = Store.data_dir / 'demo' / 'Script'
        script.mkdir(parents=True)
        doc = {'format': 'raven-cut', 'version': 1, 'fps': 24, 'revision': 7,
               'clips': [{'shotId': 's1', 'source': {'id': 'take-9', 'label': 'Chosen',
                          'video': 'Input/private.mov', 'image': ''}, 'inFrame': 24,
                          'outFrame': 72, 'sourceFrames': 120, 'included': True}],
               'undo': [{'label': 'Hidden history label', 'clips': []}], 'redo': []}
        path = script / 'cut.json'; path.write_text(json.dumps(doc))
        before = path.read_bytes()
        ctx = Ctx('{"message":"ok","actions":[{"tool":"inspect_cut"}]}', {'demo': {'shots': [{'id': 's1'}]}})
        out = chat(ctx, {'slug': 'demo', 'message': 'Hvordan er filmen klippet?'})
        self.assertEqual(out['proposalStatus'], 'pending')
        prompt = ctx.service.seen[1]
        context = json.loads(prompt.split('\n', 1)[1].split('\n\nUSER MESSAGE:', 1)[0])
        cut = context['project']['editorial']['cut']
        self.assertEqual((cut['revision'], cut['runtimeFrames'], cut['clips'][0]['takeId']), (7, 48, 'take-9'))
        self.assertEqual(context['project']['editorial']['sound']['status'], 'absent')
        self.assertNotIn('private.mov', prompt)
        self.assertNotIn('Hidden history label', prompt)
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_id_rejects_whole_batch(self):
        ctx = Ctx('{"message":"x","actions":[{"tool":"edit_shot","shotId":"bad"},{"tool":"status"}]}', {"demo": {"shots": []}})
        out = chat(ctx, {"slug": "demo", "message": "x"})
        self.assertEqual(out["proposalStatus"], "rejected"); self.assertEqual(out["actions"], [])

    def test_nested_approval_is_rejected(self):
        ctx = Ctx('{"message":"x","actions":[{"tool":"status","approve":true}]}', {"demo": {}})
        self.assertEqual(chat(ctx, {"slug": "demo", "message": "x"})["proposalStatus"], "rejected")

    def test_malformed_does_not_echo_claim(self):
        ctx = Ctx("DONE: film rendered", {})
        out = chat(ctx, {"slug": None, "message": "x"})
        self.assertEqual(out["proposalStatus"], "incomplete"); self.assertNotIn("rendered", out["message"])

    def test_thinking_is_applied(self):
        ctx = Ctx('{"message":"ok","actions":[]}', {})
        chat(ctx, {"slug": None, "message": "x", "thinking": "high"})
        self.assertEqual(ctx.service.last_thinking.thinking, "high")

    def test_invalid_reply_is_retried_with_feedback_and_trace(self):
        class RetryCtx(Ctx):
            def __init__(self):
                self.store, self.service = Store({}), SequenceService([
                    '{"message":"bad","actions":[{"tool":"create_project","args":{"name":"Maur"},"approve":false}]}',
                    '{"message":"klar","actions":[{"tool":"create_project","name":"Maur"}]}',
                ])
                self.film = object()
            def llm(self, _): return self.service
        ctx = RetryCtx()
        out = chat(ctx, {"slug": None, "message": "Lag prosjekt", "authoringStage": "combined"})
        self.assertEqual(out["proposalStatus"], "pending")
        self.assertEqual(out["attempts"], 2)
        self.assertTrue(out["repaired"])
        self.assertEqual(ctx.service.calls, 2)
        trace = Path(ctx.store.data_dir).parent / "System" / "director-traces" / (out["traceId"] + ".json")
        self.assertTrue(trace.exists())
        self.assertIn("rawReply", trace.read_text(encoding="utf-8"))

    def test_stage_rejects_generation_tool(self):
        ctx = Ctx('{"message":"x","actions":[{"tool":"generate_image","prompt":"x"}]}', {"demo": {}})
        out = chat(ctx, {"slug": "demo", "message": "x", "authoringStage": "shots"})
        self.assertEqual(out["proposalStatus"], "rejected")
        self.assertEqual(out["actions"], [])

    def test_authoring_has_separate_bounded_inference_budget(self):
        ctx = Ctx('{"message":"ok","actions":[]}', {"demo": {}})
        with patch.object(ctx.service, "complete", return_value=ctx.service.response) as complete:
            chat(ctx, {"slug": "demo", "message": "Skriv klipp", "authoringStage": "shots"})
        self.assertEqual(complete.call_args.kwargs["timeout"], 600)

    def test_model_failure_retains_earlier_attempt_and_diagnostic_trace(self):
        ctx = Ctx("not JSON", {})
        with patch.object(ctx.service, "complete", side_effect=["not JSON", RuntimeError("service unavailable")]):
            out = chat(ctx, {"slug": None, "message": "Lag prosjekt"})
        self.assertEqual(out["proposalStatus"], "rejected")
        self.assertEqual(out["actions"], [])
        self.assertEqual(out["attempts"], 2)
        self.assertIn("service unavailable", out["errors"][0])
        trace_path = Path(ctx.store.data_dir).parent / "System/director-traces" / (out["traceId"] + ".json")
        trace = json.loads(trace_path.read_text())
        self.assertEqual(trace["attempts"][0]["rawReply"], "not JSON")
        self.assertIn("service unavailable", trace["attempts"][1]["errors"][0])


if __name__ == "__main__": unittest.main()

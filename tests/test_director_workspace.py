"""Render chat receipts without initializing a native window or model."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from director_state import rebase_created_execution


class DirectorWorkspaceRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "director_workspace_render_fixture", Path(__file__).parents[1] / "director_workspace.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"raven": types.ModuleType("raven")}):
            spec.loader.exec_module(module)

        class Fixture(module.DirectorWorkspaceMixin):
            def __init__(self, turn):
                self.current_conversation = {"messages": [turn]}
                self.history = [turn]
                self.slug = "maur"
                self.rendered = ""

            def render_conversation_sidebar(self): pass
            def replace_host(self, name, xml): self.rendered = xml

        cls.fixture = Fixture

    def test_created_partial_receipt_renders_resume_after_project_bind(self):
        result = {"slug": "maur", "board": {"name": "Maur", "shots": []}, "results": [
            {"tool": "create_project", "slug": "maur"},
            {"tool": "write_manuscript", "brief": "En maur finner hjem", "count": 12}]}
        turn = {"role": "assistant", "sourceSlug": None, "stale": True,
                "proposalStatus": "stale", "actions": [{"tool": "create_project"}, {"tool": "write_manuscript"}],
                "execution": {"status": "failed", "result": result}}
        session = {"messages": [turn]}
        rebase_created_execution(session, 0, result)
        studio = self.fixture(turn)
        studio.render_conversation()
        self.assertIn('OnClicked="director.resume.0"', studio.rendered)
        self.assertNotIn('OnClicked="director.apply.0"', studio.rendered)

    def test_valid_proposal_exposes_trace_write_failure(self):
        turn = {"role": "assistant", "sourceSlug": "maur", "proposalStatus": "ready",
                "actions": [{"tool": "write_manuscript", "brief": "En maur finner hjem"}],
                "traceWriteError": "disk full"}
        studio = self.fixture(turn)
        studio.render_conversation()
        self.assertIn('OnClicked="director.diagnostics.0"', studio.rendered)
        self.assertIn("Review diagnostic log", studio.rendered)

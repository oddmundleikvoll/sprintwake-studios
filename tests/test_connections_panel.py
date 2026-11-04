import copy
import tempfile
import unittest
import sys
import types
from pathlib import Path
from unittest.mock import patch

from connections import ConnectionStore, defaults, credential_account
sys.modules.setdefault("raven", types.SimpleNamespace(R_INVALID_ID=-1))
from connections_panel import ConnectionsMixin


class Credentials:
    def __init__(self): self.saved = {}
    def get(self, account): return self.saved.get(account, "")
    def set(self, account, value): self.saved[account] = value


class Panel(ConnectionsMixin):
    def __init__(self, path):
        self.connections = ConnectionStore(path)
        self.connection_settings = self.connections.load()
        self.connection_draft = copy.deepcopy(self.connection_settings)
        self.credentials = Credentials()
        self.player = type("Player", (), {"choose_project": lambda self: None})()
        self.fields = {}
        self.modal_kind = "Modelltilkoblinger"
        self.modal = []
        self.connection_modal_token = 0
        self.connection_comfy_key_draft = ""
        self.connection_status = ""
        self.connection_form = None

    def find(self, name): return 1
    def text(self, name): return self.fields.get(name, "")
    def selected(self, name): return 0
    def checked(self, name): return False
    def show_modal(self, *args, **kwargs): self.modal.append((args, kwargs)); self.modal_kind = args[0]
    def submit(self, key, operation, done=None, **kwargs):
        if done: done(operation())
        return True


class ConnectionPanelTests(unittest.TestCase):
    def test_main_draft_survives_opening_add_form_and_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel = Panel(Path(tmp) / "connections.json")
            panel.show_connections()
            panel.fields["connections.root"] = "/tmp/other-production"
            panel.connections_action("connections.llm.add")
            self.assertEqual(panel.connection_draft["paths"]["root"], "/tmp/other-production")
            panel.connections_action("connections.llm.cancel")
            self.assertEqual(panel.connection_draft["paths"]["root"], "/tmp/other-production")

    def test_show_action_and_new_profile_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel = Panel(Path(tmp) / "connections.json")
            self.assertTrue(panel.connections_action("connections.show"))
            panel.connection_llm_edit = None
            panel.fields.update({
                "connections.llm.id": "remote", "connections.llm.label": "Remote",
                "connections.llm.url": "https://example.test", "connections.llm.model": "model",
            })
            with patch.object(panel, "selected", return_value=0), patch.object(panel, "checked", return_value=False):
                panel._save_llm()
            self.assertEqual(panel.connection_draft["selected"]["llm"], "remote")

    def test_explicit_save_keeps_active_config_and_never_writes_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel = Panel(Path(tmp) / "connections.json")
            active = copy.deepcopy(panel.connection_settings)
            panel.show_connections()
            panel.fields["connections.comfy.key"] = "secret-value"
            panel.connections_action("connections.save")
            self.assertEqual(panel.connection_settings, active)
            self.assertNotIn("secret-value", Path(tmp, "connections.json").read_text())
            self.assertEqual(panel.credentials.saved[credential_account(panel.connection_draft,"comfy")], "secret-value")

    def test_probe_receives_validated_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel = Panel(Path(tmp) / "connections.json")
            seen = []
            module = type("Checks", (), {"probe_connections": staticmethod(lambda doc, get: seen.append(doc) or [])})
            panel.show_connections()
            with patch.dict("sys.modules", {"connection_checks": module}):
                panel.connections_action("connections.test")
            self.assertEqual(len(seen), 1)
            seen[0]["paths"]["root"] = "changed"
            self.assertNotEqual(panel.connection_draft["paths"]["root"], "changed")

    def test_background_probe_preserves_edits_made_while_waiting(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel=Panel(Path(tmp)/'connections.json');panel.modal_kind='Models and connections';panel.connection_test_token=0
            panel.fields['connections.root']='/tmp/latest-edited-root'
            panel._connection_test_done([])
            self.assertEqual(panel.connection_draft['paths']['root'],'/tmp/latest-edited-root')
            panel.modal_kind='Et annet skjema';panel.modal=[]
            panel.connection_test_token=panel.connection_modal_token
            panel._connection_test_done([]);self.assertFalse(panel.modal)

    def test_hidden_draft_key_is_discarded_when_endpoint_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel=Panel(Path(tmp)/'connections.json')
            panel.fields.update({'connections.comfy':'https://one.example','connections.comfy.key':'one-secret'})
            panel._read_main();self.assertEqual(panel.connection_comfy_key_draft,'one-secret')
            panel.fields.update({'connections.comfy':'https://two.example','connections.comfy.key':''})
            panel._read_main();self.assertEqual(panel.connection_comfy_key_draft,'')


if __name__ == "__main__":
    unittest.main()

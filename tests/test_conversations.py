import json
import os
from pathlib import Path
import tempfile
import unittest

from conversations import ConversationStore
from workspace import Workspace


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name) / "Raven Studio")
        self.workspace.ensure()
        self.store = ConversationStore(self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def test_persistence_scoping_and_composer(self):
        session = self.store.create("film", "Arbeidsøkt")
        session["composer"] = {"text": "Skriv neste scene", "thinking": True, "model": "expert"}
        session["messages"] = [{"role": "user", "content": "rå tekst", "proposalResult": {"ok": True}}]
        saved = self.store.save(session)
        loaded = self.store.load(saved["id"])
        self.assertEqual(loaded["composer"]["text"], "Skriv neste scene")
        self.assertEqual(loaded["messages"][0]["proposalResult"]["ok"], True)
        self.assertEqual([x["id"] for x in self.store.list("film")], [saved["id"]])
        self.assertEqual(self.store.list("other"), [])

    def test_unbound_conversation_binds_to_created_project_same_id(self):
        session = self.store.create(None, "Start med regissøren")
        session["messages"].append({"role": "assistant", "content": "Oppretter prosjekt."})
        self.store.save(session)
        bound = self.store.bind(session["id"], "new-film")
        self.assertEqual(bound["id"], session["id"])
        self.assertEqual(bound["projectSlug"], "new-film")
        self.assertEqual(len(bound["messages"]), 1)
        self.assertEqual(bound["events"][0]["type"], "project_bound")

    def test_project_move_marks_pending_proposals_stale(self):
        session = self.store.create("first")
        session["messages"] = [{"role": "assistant", "proposalStatus": "ready", "content": "keep raw"}]
        session["pendingProposal"] = {"action": "create_project"}
        self.store.save(session)
        moved = self.store.bind(session["id"], "second")
        self.assertTrue(moved["messages"][0]["stale"])
        self.assertEqual(moved["messages"][0]["content"], "keep raw")
        self.assertTrue(moved["pendingProposal"]["stale"])
        self.assertEqual(moved["messages"][0]["proposalStatus"], "stale")

    def test_independent_new_sessions(self):
        first = self.store.create()
        second = self.store.create()
        self.assertNotEqual(first["id"], second["id"])
        first["messages"].append({"role": "user", "content": "første"})
        self.store.save(first)
        self.assertEqual(self.store.load(second["id"])["messages"], [])
        self.assertEqual(len(self.store.list()), 2)

    def test_english_and_legacy_defaults_adopt_the_untranslated_first_message(self):
        for title in ("New conversation", "Ny samtale"):
            with self.subTest(title=title):
                session = self.store.create("film", title)
                session["messages"] = [{"role": "user", "content": "Planlegg en åpningsscene"}]
                saved = self.store.save(session)
                self.assertEqual(saved["title"], "Planlegg en åpningsscene")
                self.assertEqual(saved["messages"], session["messages"])

    def test_legacy_import_is_idempotent_and_preserves_original(self):
        legacy = self.workspace.ensure_project("legacy-film") / "System" / "conversation.json"
        original = [{"role": "user", "content": "Lag en åpningsscene"}, {"role": "assistant", "content": "Klart."}]
        legacy.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
        before = legacy.read_bytes()
        imported = self.store.migrate_legacy("legacy-film")
        imported_again = self.store.migrate_legacy("legacy-film")
        self.assertEqual(imported["id"], imported_again["id"])
        self.assertEqual([{k:v for k,v in m.items() if k != "sourceSlug"} for m in imported["messages"]], original)
        self.assertEqual(imported["messages"][1]["sourceSlug"],"legacy-film")
        self.assertEqual(legacy.read_bytes(), before)
        legacy.write_text(json.dumps(original + [{"role": "user", "content": "Ny replikk"}], ensure_ascii=False), encoding="utf-8")
        changed = self.store.migrate_legacy("legacy-film")
        self.assertNotEqual(changed["id"], imported["id"])

    def test_archive_is_soft_and_recoverable(self):
        session = self.store.create("film")
        archived = self.store.archive(session["id"])
        self.assertTrue(archived["archived"])
        self.assertEqual(self.store.load(session["id"])["id"], session["id"])
        self.assertEqual(self.store.list("film"), [])
        self.assertEqual(len(self.store.list("film", include_archived=True)), 1)

    def test_restart_marks_unknown_execution_for_fresh_review(self):
        session=self.store.create("film")
        session["messages"]=[{"role":"assistant","actions":[{"tool":"generate_image"}],"execution":{"status":"running"}}]
        self.store.save(session)
        self.assertEqual(self.store.recover_interrupted(),1)
        recovered=self.store.load(session["id"])["messages"][0]
        self.assertTrue(recovered["stale"])
        self.assertEqual(recovered["execution"]["status"],"interrupted")
        self.assertEqual(self.store.recover_interrupted(),0)

    def test_project_rename_and_archive_keep_sessions_reachable(self):
        first=self.store.create("old")
        second=self.store.create("old")
        self.store.rebind_project("old","new")
        self.assertEqual(len(self.store.list("new")),2)
        self.store.rebind_project("new",None)
        self.assertEqual(len(self.store.list(None)),2)
        self.assertEqual(self.store.load(first["id"])["id"],first["id"])

    def test_rejects_path_traversal_and_symlink_ancestry(self):
        with self.assertRaises(ValueError):
            self.store.load("../escape")
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        conversations = self.workspace.system_dir / "Conversations"
        moved = self.workspace.system_dir / "Conversations-real"
        conversations.rename(moved)
        try:
            os.symlink(moved, conversations, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.store.create()
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable")
        finally:
            if conversations.is_symlink():
                conversations.unlink()
            if moved.exists():
                moved.rename(conversations)


if __name__ == "__main__":
    unittest.main()

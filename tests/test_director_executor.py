import copy
from pathlib import Path
import tempfile
import unittest

from director_contract import TOOLS
from director_executor import DirectorExecutor
from library import MediaLibrary
from studio_state import authoring_digest
from workspace import Workspace


class Backend:
    def __init__(self):
        self.boards = {}
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        if path == "/api/boards":
            return {"boards": [{"slug": key, "name": value.get("name", key)} for key, value in self.boards.items()]}
        if path.startswith("/api/boards/"):
            return copy.deepcopy(self.boards[path.split("/")[3]])
        return {}

    def post(self, path, payload=None, **kwargs):
        self.calls.append(("POST", path, payload, kwargs))
        if path == "/api/boards":
            slug = payload["name"].lower().replace(" ", "-")
            board = {"name": payload["name"], "sceneDescription": "", "shots": [], "characters": [], "filmBible": {}}
            self.boards[slug] = board
            return {"slug": slug, "board": copy.deepcopy(board)}
        if path.endswith("/rename"):
            slug = path.split("/")[3]
            self.boards[slug]["name"] = payload["name"]
            return {"slug": slug, "board": copy.deepcopy(self.boards[slug])}
        return {"accepted": True, "jobId": "job-1"}

    def put(self, path, payload=None, **kwargs):
        self.calls.append(("PUT", path, payload, kwargs))
        slug = path.split("/")[3]
        self.boards[slug] = copy.deepcopy(payload)
        return {"slug": slug, "board": copy.deepcopy(payload)}

    def upload(self, slug, source):
        return {"ref": {"path": f"/media/{slug}/{Path(source).name}"}, "path": f"/media/{slug}/{Path(source).name}"}


class DirectorExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name) / "Raven Studio")
        self.workspace.ensure()
        self.backend = Backend()
        self.library = MediaLibrary(self.workspace)
        self.executor = DirectorExecutor(self.backend, self.workspace, self.library)

    def tearDown(self):
        self.temp.cleanup()

    def test_contract_is_loaded_and_has_all_canonical_tools(self):
        self.assertGreaterEqual(len(TOOLS), 36)
        self.assertEqual(len({tool["name"] for tool in TOOLS}), len(TOOLS))

    def test_start_without_project_creates_project_and_returns_slug(self):
        result = self.executor.execute([{"tool": "create_project", "name": "Min film", "brief": "En rolig åpning"}])
        self.assertEqual(result["slug"], "min-film")
        self.assertEqual(self.backend.boards["min-film"]["sceneDescription"], "En rolig åpning")

    def test_board_edit_requires_fresh_digest_and_uses_cas(self):
        self.backend.boards["film"] = {"name": "Film", "sceneDescription": "", "shots": [], "characters": [], "filmBible": {}}
        board = self.backend.boards["film"]
        digest = authoring_digest(board)
        result = self.executor.execute([{"tool": "add_shot", "title": "Åpning", "prompt": "Regn"}], "film", digest)
        self.assertEqual(len(result["board"]["shots"]), 1)
        put = next(call for call in self.backend.calls if call[0] == "PUT")
        self.assertEqual(put[3]["expected_digest"], digest)
        with self.assertRaises(ValueError):
            self.executor.execute([{"tool": "add_shot", "title": "Ny", "prompt": "x"}], "film", "0" * 64)

    def test_approval_bypass_and_arbitrary_paths_are_rejected(self):
        self.backend.boards["film"] = {"name": "Film", "sceneDescription": "", "shots": [], "characters": [], "filmBible": {}}
        with self.assertRaises(ValueError):
            self.executor.execute([{"tool": "approve_review"}], "film", authoring_digest(self.backend.boards["film"]))
        with self.assertRaises(ValueError):
            self.executor.execute([{"tool": "generate_image", "prompt": "x", "referenceIds": ["/tmp/a.png"]}], "film")

    def test_real_temp_project_export(self):
        self.backend.boards["film"] = {"name": "Film", "sceneDescription": "", "shots": [], "characters": [], "filmBible": {}}
        self.workspace.ensure_project("film")
        result = self.executor.execute([{"tool": "export_project"}], "film")
        self.assertTrue(Path(result["results"][0]["artifact"]).is_file())

    def test_assign_image_uses_canonical_ref_and_bible_entity(self):
        source = Path(self.temp.name) / "ref.png"
        source.write_bytes(b"png-bytes")
        self.library.import_images({"test": source})
        image_id = self.library.catalogue()[0]["id"]
        self.backend.boards["film"] = {"name": "Film", "sceneDescription": "", "shots": [], "characters": [],
                                        "filmBible": {"locations": [{"id": "loc1"}], "props": []}}
        digest = authoring_digest(self.backend.boards["film"])
        result = self.executor.execute([{"tool": "assign_image", "imageId": "shared:" + image_id,
                                         "target": "location", "entityId": "loc1"}], "film", digest)
        location = result["board"]["filmBible"]["locations"][0]
        self.assertIsInstance(location["referenceImage"], dict)
        self.assertIn("path", location["referenceImage"])

    def test_film_action_is_nested_and_revision_bound(self):
        class FilmBackend(Backend):
            def get(self, path, **kwargs):
                if path.startswith("/api/film/status"):
                    return {"anchorRevision": "rev1", "project": {"revision": "rev1", "isFilm": True}, "active": None}
                return super().get(path, **kwargs)
        backend = FilmBackend()
        backend.boards["film"] = {"name": "Film", "sceneDescription": "", "shots": [], "characters": [], "filmBible": {}}
        executor = DirectorExecutor(backend, self.workspace, self.library)
        result = executor.execute([{"tool": "set_film_anchor", "enabled": True, "revision": "rev1"}], "film")
        call = next(item for item in backend.calls if item[0] == "POST" and item[1] == "/api/film/action")
        self.assertEqual(call[2]["slug"], "film")
        self.assertEqual(call[2]["action"]["revision"], "rev1")
        self.assertTrue(result["results"])


if __name__ == "__main__":
    unittest.main()


class DirectorCreateWriteTests(unittest.TestCase):
    setUp=DirectorExecutorTests.setUp
    tearDown=DirectorExecutorTests.tearDown
    def test_chat_chain_creates_real_board_before_authoring_with_new_digest(self):
        from unittest.mock import patch
        def write(backend,workspace,slug,brief,count,**kwargs):
            self.assertEqual(slug,"maursoldaten")
            self.assertEqual(kwargs["expected_digest"],authoring_digest(backend.boards[slug]))
            self.assertEqual(brief,"En ensom maursoldat finner hjem.")
            board=copy.deepcopy(backend.boards[slug])
            board["shots"]=[{"id":"shot","title":"Oppvåkning","prompt":"An ant wakes up."}]
            board["filmBible"]={"acts":[{"id":"act","title":"Hjemreisen"}]}
            backend.put("/api/boards/"+slug,board)
            return {"status":"done","board":board,"completed":1,"total":1}
        actions=[{"tool":"create_project","name":"Maursoldaten","brief":"En ensom maursoldat finner hjem."},{"tool":"write_manuscript","brief":"En ensom maursoldat finner hjem.","count":1}]
        with patch("director_authoring.author_project",side_effect=write) as writer:
            result=self.executor.execute(actions)
        self.assertEqual(writer.call_count,1)
        self.assertEqual(result["operationStatus"],"done")
        self.assertEqual(len(result["board"]["shots"]),1)
        self.assertIn("1 nye klipp",result["message"])
        self.assertTrue((self.workspace.projects_dir/"maursoldaten/Script/manus.md").is_file())
        self.assertFalse(any(c[0]=="POST" and c[1] in {"/api/images","/api/film/action"} for c in self.backend.calls))

    def test_partial_manuscript_failure_retains_created_project_and_brief(self):
        from unittest.mock import patch
        def fail(backend,workspace,slug,brief,count,**kwargs):
            return {"status":"failed","board":backend.boards[slug],"completed":0,"total":count,"error":"Invalid camera"}
        actions=[{"tool":"create_project","name":"Maursoldaten"},{"tool":"write_manuscript","brief":"En maur finner hjem.","count":12}]
        with patch("director_authoring.author_project",side_effect=fail): result=self.executor.execute(actions)
        self.assertEqual(result["slug"],"maursoldaten")
        self.assertEqual(result["operationStatus"],"failed")
        self.assertIn("Invalid camera",result["message"])
        self.assertEqual(result["results"][-1]["brief"],actions[-1]["brief"])

    def test_model_action_cannot_enable_internal_resume_intent(self):
        from unittest.mock import patch
        self.backend.boards["maur"] = {"name": "Maur", "sceneDescription": "",
                                        "shots": [], "characters": [], "filmBible": {}}
        seen = {}
        def write(backend, workspace, slug, brief, count, **kwargs):
            seen.update(kwargs)
            return {"status": "done", "board": copy.deepcopy(backend.boards[slug]),
                    "completed": 0, "total": count}
        action = {"tool": "write_manuscript", "brief": "En maur", "count": 1,
                  "resume_only": True}
        with patch("director_authoring.author_project", side_effect=write) as writer:
            with self.assertRaises(ValueError):
                self.executor.execute([action], "maur", authoring_digest(self.backend.boards["maur"]))
        writer.assert_not_called()
        self.assertNotIn("resume_only", seen)

"""Integration-shaped executor checks using real Raven workspace/library code.

No model or renderer is started: the backend below is a small API capture
double, while files, manifests, snapshots, refs and exports are real.
"""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from director_executor import DirectorExecutor
from library import MediaLibrary
from studio_state import authoring_digest
from workspace import Workspace


class CaptureBackend:
    def __init__(self):
        self.boards = {}
        self.images = {}
        self.posts = []

    def get(self, path, **kwargs):
        if path == "/api/boards":
            return {"boards": [{"slug": slug, "name": board.get("name", slug)} for slug, board in self.boards.items()]}
        if path.startswith("/api/boards/"):
            return copy.deepcopy(self.boards[path.split("/")[3]])
        if path.startswith("/api/images?"):
            slug = path.split("slug=", 1)[1]
            return {"images": copy.deepcopy(self.images.get(slug, [])), "job": None, "busy": False}
        if path.startswith("/api/film/status?"):
            return {"project": {"revision": "rev-1", "isFilm": True}, "anchorRevision": "anchor-1", "active": None}
        return {}

    def post(self, path, payload=None, **kwargs):
        self.posts.append((path, copy.deepcopy(payload), kwargs))
        if path == "/api/boards":
            slug = payload["name"].lower().replace(" ", "-")
            self.boards[slug] = {"name": payload["name"], "sceneDescription": "", "renderStyle": "",
                                 "shots": [], "characters": [], "filmBible": {"locations": [], "props": []}}
            return {"slug": slug, "board": copy.deepcopy(self.boards[slug])}
        if path.endswith("/rename"):
            slug = path.split("/")[3]
            self.boards[slug]["name"] = payload["name"]
            return {"slug": slug, "board": copy.deepcopy(self.boards[slug])}
        if path.endswith("/seed-takes/7/adopt"):
            return {"board": copy.deepcopy(self.boards[path.split("/")[3]])}
        return {"accepted": True, "job": {"jobId": "job-1"}}

    def put(self, path, payload=None, **kwargs):
        slug = path.split("/")[3]
        self.boards[slug] = copy.deepcopy(payload)
        return {"slug": slug, "board": copy.deepcopy(payload)}

    def upload(self, slug, source):
        ref = {"path": f"/media/{slug}/{Path(source).name}"}
        return {"ref": ref, "path": ref["path"]}


class DirectorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.tmp.name) / "Raven Studio")
        self.workspace.ensure()
        self.backend = CaptureBackend()
        self.library = MediaLibrary(self.workspace)
        self.executor = DirectorExecutor(self.backend, self.workspace, self.library)

    def tearDown(self):
        self.tmp.cleanup()

    def _board(self, slug="film"):
        self.backend.boards[slug] = {"name": "Film", "sceneDescription": "brief", "renderStyle": "",
                                     "shots": [{"id": "shot-1", "title": "Opening", "prompt": "", "locked": False}],
                                     "characters": [{"id": "char-1", "name": "Hero"}],
                                     "filmBible": {"locations": [{"id": "loc-1"}], "props": []}}
        return self.backend.boards[slug]

    def test_create_syncs_manuscript_then_rename_and_archive(self):
        result = self.executor.execute([{"tool": "create_project", "name": "Ny Film", "brief": "En åpning"}])
        slug = result["slug"]
        self.assertTrue((self.workspace.projects_dir / slug / "Script" / "manus.md").is_file())
        digest = authoring_digest(self.backend.boards[slug])
        self.executor.execute([{"tool": "rename_project", "name": "Ny Film 2"}], slug, digest)
        self.assertTrue((self.workspace.projects_dir / slug).is_dir())
        archived = self.executor.execute([{"tool": "archive_project"}], slug, authoring_digest(self.backend.boards[slug]))
        self.assertIsNone(archived["results"][0]["slug"])
        self.assertTrue(Path(archived["results"][0]["artifact"]).is_dir())

    def test_shared_assignment_writes_canonical_location_ref(self):
        self._board()
        source = Path(self.tmp.name) / "location.png"
        source.write_bytes(b"pixels")
        self.library.import_images({"shared": source})
        image_id = self.library.catalogue()[0]["id"]
        digest = authoring_digest(self.backend.boards["film"])
        result = self.executor.execute([{"tool": "assign_image", "imageId": "shared:" + image_id,
                                         "target": "location", "entityId": "loc-1"}], "film", digest)
        ref = result["board"]["filmBible"]["locations"][0]["referenceImage"]
        self.assertIsInstance(ref, dict)
        self.assertIn("path", ref)

    def test_generated_assignment_uses_completed_history_record(self):
        self._board()
        target = self.workspace.ensure_project("film") / "Output" / "Images" / "generated.png"
        target.write_bytes(b"generated")
        self.backend.images["film"] = [{"id": "image-1", "status": "done", "ref": {"path": "film/Output/Images/generated.png"}}]
        digest = authoring_digest(self.backend.boards["film"])
        result = self.executor.execute([{"tool": "assign_image", "imageId": "generated:image-1", "target": "opening", "shotId": "shot-1"}], "film", digest)
        self.assertIsInstance(result["board"]["shots"][0]["startRef"], dict)
        self.assertEqual(result["board"]["shots"][0]["startRef"]["path"], "film/Output/Images/generated.png")

    def test_stale_digest_is_rejected_before_put(self):
        self._board()
        with self.assertRaises(ValueError):
            self.executor.execute([{"tool": "edit_project", "fields": {"sceneDescription": "changed"}}], "film", "0" * 64)
        self.assertEqual(self.backend.boards["film"]["sceneDescription"], "brief")

    def test_film_payload_is_nested_and_anchor_revision_bound(self):
        self._board()
        result = self.executor.execute([{"tool": "set_film_anchor", "revision": "rev-1", "enabled": True}], "film")
        call = next(item for item in self.backend.posts if item[0] == "/api/film/action")
        self.assertEqual(call[1]["slug"], "film")
        self.assertEqual(call[1]["action"]["revision"], "rev-1")
        self.assertTrue(result["results"])


if __name__ == "__main__":
    unittest.main()


class DirectorDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.workspace=Workspace(Path(self.tmp.name)); self.workspace.ensure()
        class FilmCapture(CaptureBackend):
            review=False
            def get(self,path,**kwargs):
                if path.startswith("/api/film/status"):
                    return {"project":{"slug":"film","isFilm":True,"revision":"film-r","reviewRequired":self.review,"continuationAnchor":False},"anchorRevision":"board-r","active":{"slug":"film","jobId":"own-job"}}
                return super().get(path,**kwargs)
        self.backend=FilmCapture()
        self.backend.boards["film"]={"name":"Film","shots":[{"id":"shot","title":"Åpning","frames":121}],"characters":[],"filmBible":{}}
        self.executor=DirectorExecutor(self.backend,self.workspace,MediaLibrary(self.workspace))
    def tearDown(self): self.tmp.cleanup()
    def run_action(self,action):
        return self.executor.execute([action],"film",authoring_digest(self.backend.boards["film"]))
    def test_render_refine_dialogue_payloads_are_flat_and_keep_shot(self):
        for action,path in [({"tool":"render_seed_takes","shotId":"shot","count":3},"/api/render-seeds"),({"tool":"refine_shot","shotId":"shot","requirement":"Rolig kamera"},"/api/refine"),({"tool":"prepare_dialogue"},"/api/prepare-dialogue"),({"tool":"dub_dialogue","shotId":"shot"},"/api/boards/film/shots/shot/dub")]:
            with self.subTest(tool=action["tool"]):
                self.run_action(action)
                request=self.backend.posts[-1]
                self.assertEqual(request[0],path)
                self.assertEqual(request[1]["slug"],"film")
                self.assertNotIn("action",request[1])
                if action.get("shotId"): self.assertEqual(request[1]["shotId"],"shot")
    def test_prepare_resume_and_stop_are_revision_bound(self):
        self.run_action({"tool":"prepare_film","revision":"film-r"})
        path,payload,_=self.backend.posts[-1]
        self.assertEqual(path,"/api/film/action")
        self.assertTrue(payload["action"]["prepareOnly"])
        self.assertEqual(payload["action"]["revision"],"film-r")
        self.backend.review=True
        before=len(self.backend.posts)
        result=self.run_action({"tool":"resume_film","revision":"film-r"})
        self.assertEqual(result["operationStatus"],"review_required")
        self.assertEqual(len(self.backend.posts),before)
        self.run_action({"tool":"stop_film","jobId":"own-job"})
        self.assertEqual(self.backend.posts[-1][1],{"slug":"film","jobId":"own-job"})
        with self.assertRaises(ValueError): self.run_action({"tool":"stop_film","jobId":"foreign-job"})
    def test_music_prompt_is_committed_before_request(self):
        self.run_action({"tool":"generate_soundtrack","prompt":"Varm akustisk musikk"})
        self.assertEqual(self.backend.boards["film"]["soundtrackPrompt"],"Varm akustisk musikk")
        self.assertEqual(self.backend.posts[-1][0],"/api/boards/film/soundtrack")
    def test_continuation_anchor_uses_film_revision(self):
        self.run_action({"tool":"set_film_anchor","revision":"film-r","enabled":True})
        self.assertEqual(self.backend.posts[-1][1]["action"]["revision"],"film-r")
        with self.assertRaises(ValueError): self.run_action({"tool":"set_film_anchor","revision":"board-r","enabled":True})



class ActualDirectorEndpointTests(unittest.TestCase):
    def test_canonical_project_and_generated_refs_manuscript_anchors(self):
        import base64
        import json
        from backend import Backend
        with tempfile.TemporaryDirectory() as temp:
            workspace=Workspace(Path(temp))
            workspace.ensure()
            backend=Backend(workspace.root)
            library=MediaLibrary(workspace)
            executor=DirectorExecutor(backend,workspace,library)
            try:
                backend.start()
                result=executor.execute([{"tool":"create_project","name":"Real director flow","brief":"En fugl ved en dør."}])
                slug=result["slug"]
                def run(action):
                    board=backend.get("/api/boards/"+slug)["board"]
                    return executor.execute([action],slug,authoring_digest(board))
                run({"tool":"edit_bible","fields":{"locations":[{"id":"door","title":"Døren","description":"En blå dør"}]}})
                run({"tool":"add_shot","title":"Første klipp","prompt":"A bird by a blue door, still camera and quiet wind.","locationId":"door","characterIds":[],"frames":121,"directorScene":1})
                source=workspace.root/"small.png"
                source.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
                library.import_images({"test":source})
                image="shared:"+library.catalogue()[0]["id"]
                run({"tool":"assign_image","imageId":image,"target":"location","entityId":"door"})
                run({"tool":"add_character","name":"Fuglen","description":"En liten blå fugl","imageId":image})
                board=backend.get("/api/boards/"+slug)["board"]
                ref=board["characters"][0]["image"]
                self.assertTrue(workspace.local_media(ref["path"]).is_file())
                state=backend.get("/api/film/status?slug="+slug)
                project_id="project:"+state["imageCatalog"][0]["imageId"]
                shot_id=board["shots"][0]["id"]
                run({"tool":"assign_image","imageId":project_id,"target":"opening","shotId":shot_id})
                manifest=workspace.projects_dir/slug/"images.json"
                manifest.write_text(json.dumps([{"id":"retained","status":"done","ref":ref}]))
                run({"tool":"assign_image","imageId":"generated:retained","target":"closing","shotId":shot_id})
                published=run({"tool":"publish_image","imageId":"generated:retained"})
                self.assertTrue(Path(published["results"][0]["artifact"]).is_file())
                state=backend.get("/api/film/status?slug="+slug)
                project_id="project:"+state["imageCatalog"][0]["imageId"]
                run({"tool":"set_shot_anchors","shotId":shot_id,"anchorRevision":state["anchorRevision"],"anchors":[{"imageId":project_id,"frame":"last","strength":0.65}]})
                board=backend.get("/api/boards/"+slug)["board"]
                self.assertEqual(board["shots"][0]["imageAnchors"][0]["strength"],0.65)
                run({"tool":"rewrite_text","scope":"shot","shotId":shot_id,"text":"A bird opens its wings beside the blue door."})
                script=(workspace.projects_dir/slug/"Script/manus.md").read_text()
                self.assertIn("opens its wings",script)
            finally:
                backend.close()

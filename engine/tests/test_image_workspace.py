"""CPU/inert coverage for the standalone Images workspace (no model/Comfy)."""
import tempfile
import time
import unittest
import threading
import io
import json
from types import SimpleNamespace
import zipfile
import urllib.parse
from pathlib import Path

from PIL import Image

from server.backends.base import Backend, JobSpec, ModelCapability, RunResult
from server.orchestrator import Orchestrator
from server.store import Store
from server.app import Handler
from server.update_lifecycle import UpdateLifecycle


class FakeImageBackend(Backend):
    id = "fake"
    label = "fake"

    def capabilities(self):
        return [ModelCapability(id="qwen-image-2.1-turbo", label="fake", kind="image")]

    def prepare(self, shot, project, paths):
        paths.ensure()
        return JobSpec(shot_id=shot["id"], expected_outputs=[paths.abs_dir / "image.png"],
                       payload={"prompt": shot["prompt"]})

    def run(self, spec, on_event, should_cancel):
        if should_cancel():
            return RunResult(exit_code=1, cancelled=True, error="cancelled")
        Image.new("RGB", (64, 64), "red").save(spec.expected_outputs[0])
        return RunResult(exit_code=0)

    def health(self):
        return True, ""


class BlockingImageBackend(FakeImageBackend):
    def __init__(self):
        self.started = threading.Event()

    def run(self, spec, on_event, should_cancel):
        self.started.set()
        while not should_cancel():
            time.sleep(0.005)
        return RunResult(exit_code=1, cancelled=True, error="cancelled")


class ImageWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = Store(root, root)
        self.slug, _ = self.store.create("Image test")
        self.orch = Orchestrator(FakeImageBackend(), self.store, root, root)

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self):
        deadline = time.time() + 2
        while self.orch.image_busy and time.time() < deadline:
            time.sleep(0.01)

    def test_direct_prompt_is_durable_without_shot(self):
        accepted = self.orch.create_image(self.slug, "  A red cabin\nwith snow  ")
        self.assertTrue(accepted["busy"])
        self.wait()
        history = self.orch.image_history(self.slug)
        self.assertEqual(history[-1]["status"], "done")
        self.assertEqual(history[-1]["prompt"], "A red cabin\nwith snow")
        self.assertTrue((self.store.root / history[-1]["ref"]["path"]).is_file())
        self.assertEqual(self.store.load(self.slug)["shots"], [])

    def test_random_seed_is_chosen_for_null_and_zero_is_preserved(self):
        from unittest.mock import patch
        with patch("server.orchestrator.random.randint", return_value=12345):
            first = self.orch.create_image(self.slug, "random")
        self.wait()
        self.assertEqual(self.orch.image_history(self.slug)[-1]["seed"], 12345)
        second = self.orch.create_image(self.slug, "explicit", seed=0, inherit_style=False)
        self.wait()
        record = next(item for item in self.orch.image_history(self.slug) if item.get("prompt") == "explicit")
        self.assertEqual(record["seed"], 0)
        self.assertFalse(record["inheritStyle"])

    def test_adopt_uses_content_hash_for_same_name_collisions(self):
        first, _ = self.store.create("Source one")
        second, _ = self.store.create("Source two")
        p1 = self.store.refs_dir(first) / "same.png"
        p2 = self.store.refs_dir(second) / "same.png"
        p1.parent.mkdir(parents=True, exist_ok=True); p2.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "red").save(p1)
        Image.new("RGB", (8, 8), "blue").save(p2)
        copied1 = self.store.adopt(self.slug, f"{first}/refs/same.png")
        copied2 = self.store.adopt(self.slug, f"{second}/refs/same.png")
        self.assertNotEqual(copied1["path"], copied2["path"])
        self.assertEqual(Image.open(self.store.root / copied1["path"]).getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(Image.open(self.store.root / copied2["path"]).getpixel((0, 0)), (0, 0, 255))
        copied3 = self.store.adopt(self.slug, f"{first}/refs/same.png")
        self.assertEqual(copied1["path"], copied3["path"])

    def test_reference_limit_and_stale_stop_are_safe(self):
        ref = self.store.refs_dir(self.slug) / "ref.png"
        ref.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "blue").save(ref)
        refs = [{"path": f"{self.slug}/refs/ref.png"} for _ in range(11)]
        with self.assertRaisesRegex(ValueError, "maksimalt 10"):
            self.orch.create_image(self.slug, "x", refs)
        accepted = self.orch.create_image(self.slug, "x")
        self.wait()
        stale = self.orch.stop_image(self.slug, "old-job")
        self.assertTrue(stale["stale"])
        self.assertEqual(self.orch.image_history(self.slug)[-1]["status"], "done")

    def test_completed_stop_cannot_cancel_replacement_job(self):
        first = self.orch.create_image(self.slug, "first")
        self.wait()
        replacement = BlockingImageBackend()
        self.orch.backend = replacement
        second = self.orch.create_image(self.slug, "second")
        self.assertTrue(replacement.started.wait(1))
        stale = self.orch.stop_image(self.slug, first["job"]["jobId"])
        self.assertTrue(stale["stale"])
        self.assertTrue(self.orch.image_busy)
        self.orch.stop_image(self.slug, second["job"]["jobId"])
        self.wait()

    def test_restart_marks_interrupted_records(self):
        self.store.save_image_record(self.slug, {"id": "old", "status": "running", "prompt": "x"})
        records = self.orch.image_history(self.slug)
        self.assertEqual(records[0]["status"], "interrupted")

    def test_reference_is_snapshotted_and_owned_result_can_be_deleted_after_board_delete(self):
        foreign, _ = self.store.create("Foreign")
        source = self.store.refs_dir(foreign) / "source.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "blue").save(source)
        result = self.orch.create_image(self.slug, "with ref", [{"path": f"{foreign}/refs/source.png"}])
        self.wait()
        record = self.orch.image_history(self.slug)[-1]
        self.assertEqual(record["status"], "done")
        self.assertNotEqual(record["references"][0]["path"], f"{foreign}/refs/source.png")
        self.assertEqual(record["references"][0]["sourceContentHash"], record["references"][0]["contentHash"])
        image_path = record["ref"]["path"]
        self.store.delete(self.slug)
        deleted = self.store.delete_library_item(image_path)
        self.assertEqual(deleted["deletedResults"], 1)
        self.assertFalse((self.store.root / image_path).exists())
        history = self.orch.image_history(self.slug)
        self.assertEqual(history[-1]["status"], "deleted")
        self.assertIsNone(history[-1].get("url"))
        self.assertIsNone(history[-1].get("path"))

    def _handler(self, orch, path, payload=None):
        handler = Handler.__new__(Handler)
        ctx = SimpleNamespace(store=self.store, orch=orch,
                              film=SimpleNamespace(owns=lambda _slug: False))
        ctx.update_lifecycle = UpdateLifecycle(ctx)
        orch.update_lifecycle = ctx.update_lifecycle
        handler.ctx = ctx
        handler.path = path
        handler._send_json = lambda value, status=200: value
        if payload is not None:
            handler._read_json = lambda: payload
        return handler

    def _delete_request(self, orch, path, payload=None):
        handler = self._handler(orch, path, payload)
        handler._send_json = lambda value, status=200: {"status": status, "value": value}
        handler._err = lambda status, message: {"status": status, "error": message}
        return handler.do_DELETE()

    def test_http_job_scope_and_rename_guard(self):
        backend = BlockingImageBackend()
        orch = Orchestrator(backend, self.store, Path(self.tmp.name), Path(self.tmp.name))
        accepted = orch.create_image(self.slug, "blocking")
        self.assertTrue(backend.started.wait(1))
        stale = self._handler(orch, f"/api/images?slug={self.slug}&jobId=old")._api_get("/api/images")
        self.assertTrue(stale["stale"])
        self.assertIsNone(stale["job"])
        renamed = self._handler(orch, f"/api/boards/{self.slug}/rename", {"name": "Moved"})._api_post(f"/api/boards/{self.slug}/rename")
        self.assertEqual(renamed.get("error"), "A render is running. Stop it, or wait for it to finish, before renaming the project.")
        orch.stop_image(self.slug, accepted["job"]["jobId"])
        self.wait()

    def test_library_skips_temp_workspace_and_export_contains_manifest(self):
        work = self.store.project_dir(self.slug) / "image-work" / "job"
        work.mkdir(parents=True)
        Image.new("RGB", (8, 8), "red").save(work / "image.png")
        self.store.save_image_record(self.slug, {"id": "i", "status": "failed", "prompt": "x"})
        self.assertFalse(any(item["path"].endswith("image-work/job/image.png") for item in self.store.library()))
        out = io.BytesIO()
        self.store.export_zip(self.slug, out)
        with zipfile.ZipFile(io.BytesIO(out.getvalue())) as archive:
            names = archive.namelist()
        self.assertIn(f"{self.slug}/images.json", names)

    def test_library_delete_rejects_recipe_reference_and_reports_reason(self):
        source = self.store.refs_dir(self.slug) / "source.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "blue").save(source)
        self.store.save_image_record(self.slug, {
            "id": "recipe", "status": "done", "ref": None,
            "references": [{"path": f"{self.slug}/refs/source.png"}],
        })
        item = next(i for i in self.store.library() if i["path"] == f"{self.slug}/refs/source.png")
        self.assertFalse(item["canDelete"])
        before = source.read_bytes()
        with self.assertRaisesRegex(ValueError, "reference"):
            self.store.delete_library_item(item["path"])
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(self.store.image_history(self.slug)[0]["status"], "done")

    def test_project_delete_media_rejects_other_project_reference(self):
        other, _ = self.store.create("Other")
        source = self.store.refs_dir(self.slug) / "shared.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "green").save(source)
        board = self.store.load(other)
        from server.store import default_shot
        shot = default_shot(board["defaults"])
        shot["outputs"] = [f"/media/{self.slug}/refs/shared.png"]
        shot["stills"] = {"start": {"url": f"/media/{self.slug}/refs/shared.png"}}
        board["shots"] = [shot]
        self.store.save(other, board)
        with self.assertRaisesRegex(ValueError, "another project"):
            self.store.delete(self.slug, keep_outputs=False)
        self.assertTrue(self.store.project_dir(self.slug).exists())

    def test_delete_library_http_rejects_busy_and_language_work(self):
        image = self.store.refs_dir(self.slug) / "busy.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "red").save(image)
        path = "/api/library?kind=image&path=" + urllib.parse.quote(f"{self.slug}/refs/busy.png")
        self.orch._llm_active = 1
        response = self._delete_request(self.orch, path)
        self.assertEqual(response["status"], 409)
        self.assertTrue(image.exists())
        self.orch._llm_active = 0

        backend = BlockingImageBackend()
        busy_orch = Orchestrator(backend, self.store, Path(self.tmp.name), Path(self.tmp.name))
        accepted = busy_orch.create_image(self.slug, "busy")
        self.assertTrue(backend.started.wait(1))
        response = self._delete_request(busy_orch, path)
        self.assertEqual(response["status"], 409)
        busy_orch.stop_image(self.slug, accepted["job"]["jobId"])
        deadline = time.time() + 2
        while busy_orch.image_busy and time.time() < deadline:
            time.sleep(0.01)

    def test_project_delete_media_requires_boolean_true_and_symlink_is_rejected(self):
        image = self.store.project_dir(self.slug) / "keep.png"
        Image.new("RGB", (8, 8), "red").save(image)
        path = f"/api/boards/{self.slug}"
        response = self._delete_request(self.orch, path, {"confirmName": "Image test", "deleteMedia": "true"})
        self.assertEqual(response["status"], 200)
        self.assertTrue(image.exists())

        self.store.create("Symlink target")
        alias = self.store.project_dir("Alias")
        alias.symlink_to(self.store.project_dir("Symlink target"), target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            self.store.delete("Alias", keep_outputs=False)
        self.assertTrue(alias.is_symlink())

    def test_library_delete_rolls_back_when_file_unlink_fails(self):
        result = self.orch.create_image(self.slug, "rollback")
        self.wait()
        record = self.orch.image_history(self.slug)[-1]
        image_path = self.store.root / record["ref"]["path"]
        before = image_path.read_bytes()
        from unittest.mock import patch
        original_unlink = Path.unlink
        def fail_temp_unlink(target, *args, **kwargs):
            if target.name.startswith("." + image_path.name + ".delete-"):
                raise OSError("injected unlink failure")
            return original_unlink(target, *args, **kwargs)
        with patch.object(Path, "unlink", fail_temp_unlink):
            with self.assertRaisesRegex(OSError, "injected"):
                self.store.delete_library_item(record["ref"]["path"])
        self.assertEqual(image_path.read_bytes(), before)
        self.assertEqual(self.store.image_history(self.slug)[-1]["status"], "done")

    def test_library_delete_rolls_back_when_manifest_save_fails(self):
        result = self.orch.create_image(self.slug, "manifest rollback")
        self.wait()
        record = self.orch.image_history(self.slug)[-1]
        image_path = self.store.root / record["ref"]["path"]
        before = image_path.read_bytes()
        from unittest.mock import patch
        with patch.object(Store, "save_image_record", side_effect=OSError("injected manifest failure")):
            with self.assertRaisesRegex(OSError, "injected"):
                self.store.delete_library_item(record["ref"]["path"])
        self.assertEqual(image_path.read_bytes(), before)
        self.assertEqual(self.store.image_history(self.slug)[-1]["status"], "done")

    def test_invalid_input_never_handoffs_or_creates_snapshot_and_resolved_is_ignored(self):
        orch = self.orch
        handoffs = []
        orch.before_gpu_start = lambda: handoffs.append(True)
        with self.assertRaises(ValueError):
            orch.create_image(self.slug, "", seed="bad")
        with self.assertRaises(ValueError):
            orch.create_image(self.slug, "valid", seed=2**31)
        self.assertEqual(handoffs, [])
        ref = self.store.refs_dir(self.slug) / "safe.png"
        ref.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "green").save(ref)
        orch.create_image(self.slug, "safe", [{"path": f"{self.slug}/refs/safe.png", "resolved": "/etc/passwd"}])
        self.wait()
        record = orch.image_history(self.slug)[-1]
        self.assertNotEqual(record["references"][0].get("resolved"), "/etc/passwd")
        self.assertTrue(record["references"][0]["path"].startswith(f"{self.slug}/refs/"))

    def test_legacy_shot_stills_are_read_only_gallery_entries(self):
        image = self.store.project_dir(self.slug) / "shots" / "01" / "stills" / "start.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "purple").save(image)
        board = self.store.load(self.slug)
        from server.store import default_shot
        shot = default_shot(board["defaults"])
        shot["id"], shot["stills"] = "old-shot", {"start": {"url": "/media/" + str(image.relative_to(self.store.root)), "seed": 7, "steps": 8}}
        board["shots"] = [shot]
        self.store.save(self.slug, board)
        before = self.store.board_path(self.slug).read_bytes()
        entries = self.orch.image_history(self.slug)
        legacy = next(item for item in entries if item.get("source") == "legacy-still")
        self.assertFalse(legacy["recipeAvailable"])
        self.assertEqual(legacy["seed"], 7)
        self.assertEqual(legacy["ref"]["path"], str(image.relative_to(self.store.root)))
        self.assertEqual(before, self.store.board_path(self.slug).read_bytes())

    def test_live_workspace_job_blocks_other_admission_and_has_workspace_status(self):
        backend = BlockingImageBackend()
        orch = Orchestrator(backend, self.store, Path(self.tmp.name), Path(self.tmp.name))
        accepted = orch.create_image(self.slug, "blocking image")
        self.assertTrue(backend.started.wait(1))
        self.assertTrue(orch.image_busy)
        self.assertTrue(orch.stills_busy)  # shared admission signal for film/Fable
        state = orch.status()
        self.assertEqual(state["stills"]["kind"], "workspace")
        self.assertEqual(state["stills"]["jobId"], accepted["job"]["jobId"])
        with self.assertRaisesRegex(RuntimeError, "one image or video job"):
            orch.create_image(self.slug, "second")
        orch.stop_image(self.slug, accepted["job"]["jobId"])
        deadline = time.time() + 1
        while orch.image_busy and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(orch.image_busy)


if __name__ == "__main__":
    unittest.main()

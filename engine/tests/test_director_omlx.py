"""Director migration contracts; no weights or external servers are loaded."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.director_omlx import DirectorOMLX, MODEL, validate_checkpoint
from server.film import assert_other_models_idle


class DirectorTests(unittest.TestCase):
    def test_release_waits_for_idle_eviction_without_aborting_a_request(self):
        client = DirectorOMLX(key="test-only")
        states = [{"loaded": True, "is_loading": True},
                  {"loaded": True, "is_loading": False},
                  {"loaded": False, "is_loading": False}]
        with patch.object(client, "request") as request, \
             patch.object(client, "entry", side_effect=states), \
             patch("server.director_omlx.time.sleep") as sleep:
            client.release()
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual([(c.args, c.kwargs) for c in request.call_args_list], [
            ((f"/admin/api/models/{MODEL}/settings", {"ttl_seconds": 1}), {"method": "PUT"}),
            ((f"/admin/api/models/{MODEL}/settings", {"ttl_seconds": 300}), {"method": "PUT"})])

    def test_failed_release_restores_idle_policy_and_does_not_claim_ack(self):
        client = DirectorOMLX(key="test-only")
        with patch.object(client, "request") as request, \
             patch.object(client, "entry", return_value={"loaded": True, "is_loading": False}):
            with self.assertRaises(TimeoutError):
                client.release(timeout=0)
        self.assertEqual(request.call_args.args[1], {"ttl_seconds": 300})

    def test_checkpoint_rejects_oq4e_missing_shards_and_missing_vision(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            config = {"quantization": {"bits": 4, "group_size": 64, "mode": "affine"},
                      "vision_config": {"depth": 27}}
            (path / "config.json").write_text(json.dumps(config))
            (path / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"weight": "shard.safetensors"}}))
            with self.assertRaisesRegex(RuntimeError, "ufullstendige"):
                validate_checkpoint(path)
            (path / "shard.safetensors").touch()
            (path / "preprocessor_config.json").write_text("{}")
            validate_checkpoint(path)
            config["quantization"]["mode"] = "oQ4e"
            (path / "config.json").write_text(json.dumps(config))
            with self.assertRaisesRegex(RuntimeError, "affine"):
                validate_checkpoint(path)
            config["quantization"]["mode"] = "affine"
            (path / "config.json").write_text(json.dumps(config))
            (path / "preprocessor_config.json").unlink()
            with self.assertRaisesRegex(RuntimeError, "synsmodul"):
                validate_checkpoint(path)

    def test_language_reuse_allows_only_own_model_but_gpu_requires_empty_pool(self):
        def status(url):
            if "8000" in url:
                return {"engine_pool": {"loaded_count": 1}}
            return {"data": []}
        own = {"id": MODEL, "loaded": True}
        with patch("server.film._local_json", side_effect=status), \
             patch.object(DirectorOMLX, "models", return_value=[own]) as models:
            assert_other_models_idle(allow_director=True)
            with self.assertRaisesRegex(RuntimeError, "oMLX"):
                assert_other_models_idle()
            models.return_value = [own, {"id": "foreign", "is_loading": True}]
            with self.assertRaisesRegex(RuntimeError, "oMLX"):
                assert_other_models_idle(allow_director=True)

    def test_gpu_handoff_rejects_loading_model_even_when_loaded_count_is_zero(self):
        def status(url):
            return {"engine_pool": {"loaded_count": 0}} if "8000" in url else {"data": []}
        with patch("server.film._local_json", side_effect=status), \
             patch.object(DirectorOMLX, "models", return_value=[{"id": MODEL, "loaded": False, "is_loading": True}]):
            with self.assertRaisesRegex(RuntimeError, "oMLX"):
                assert_other_models_idle()
            assert_other_models_idle(allow_director=True)


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.__main__ import parse_args
from server.gpu_handoff import gpu_admission
from server.local_config import ensure_local_configs


class PrivateConfigTests(unittest.TestCase):
    def test_config_dir_is_optional_and_parsed(self):
        self.assertIsNone(parse_args([]).config_dir)
        value = parse_args(["--config-dir", "/tmp/sprintwake-settings"]).config_dir
        self.assertEqual(value, Path("/tmp/sprintwake-settings"))

    def test_private_root_is_seeded_from_shipped_samples(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as shipped:
            private = Path(root)
            assets = Path(shipped)
            (assets / "tts-services-sample.json").write_text(json.dumps({"services": []}))
            ensure_local_configs(private, assets)
            self.assertEqual(
                json.loads((private / "tts-services.json").read_text()), {"services": []}
            )
            self.assertFalse((assets / "tts-services.json").exists())

    def test_gpu_lock_can_live_outside_read_only_bundle(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "Locks" / "gpu.lock"
            with patch.dict(os.environ, {"RAVEN_GPU_LOCK": str(target)}):
                with gpu_admission():
                    self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()

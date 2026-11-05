import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("install_comfy_plugin", ROOT / "scripts/install-comfy-plugin.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

class ComfyPluginSetupTests(unittest.TestCase):
    def test_dry_run_is_complete_and_does_not_start_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ComfyUI"
            (root / "custom_nodes").mkdir(parents=True)
            planned = MODULE.install(root, dry_run=True)
            self.assertEqual({path.name for path in planned}, set(MODULE.FILES))
            self.assertFalse((root / "custom_nodes" / "SprintwakeStudio").exists())

    def test_install_is_idempotent_and_refuses_conflicting_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ComfyUI"
            (root / "custom_nodes").mkdir(parents=True)
            MODULE.install(root)
            MODULE.install(root)
            destination = root / "custom_nodes" / "SprintwakeStudio"
            self.assertEqual((destination / "memory.py").read_text(),
                             (ROOT / "engine/comfy_nodes/memory.py").read_text())
            (destination / "memory.py").write_text("different")
            with self.assertRaises(ValueError): MODULE.install(root)

    def test_symlinked_destination_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ComfyUI"
            custom = root / "custom_nodes"
            custom.mkdir(parents=True)
            outside = Path(directory) / "outside"
            outside.mkdir()
            (custom / "SprintwakeStudio").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError): MODULE.install(root)

if __name__ == "__main__": unittest.main()

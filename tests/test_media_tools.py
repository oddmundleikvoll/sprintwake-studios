import os
from pathlib import Path
import tempfile
import unittest

from media_tools import executable


class MediaToolResolutionTests(unittest.TestCase):
    def test_owned_tools_precede_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); tools = root / "runtime/tools"; tools.mkdir(parents=True)
            owned = tools / "ffmpeg"; owned.write_text("owned"); owned.chmod(0o755)
            pathbin = root / "path"; pathbin.mkdir(); fallback = pathbin / "ffmpeg"; fallback.write_text("path"); fallback.chmod(0o755)
            old_root, old_path = os.environ.get("RAVEN_BUNDLE_ROOT"), os.environ.get("PATH")
            try:
                os.environ["RAVEN_BUNDLE_ROOT"] = str(root); os.environ["PATH"] = str(pathbin)
                self.assertEqual(executable("ffmpeg"), str(owned))
            finally:
                if old_root is None: os.environ.pop("RAVEN_BUNDLE_ROOT", None)
                else: os.environ["RAVEN_BUNDLE_ROOT"] = old_root
                if old_path is None: os.environ.pop("PATH", None)
                else: os.environ["PATH"] = old_path

    def test_explicit_tool_path_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            tool = Path(temp) / "ffprobe"; tool.write_text("explicit"); tool.chmod(0o755)
            old = os.environ.get("RAVEN_FFPROBE")
            try:
                os.environ["RAVEN_FFPROBE"] = str(tool)
                self.assertEqual(executable("ffprobe"), str(tool))
            finally:
                if old is None: os.environ.pop("RAVEN_FFPROBE", None)
                else: os.environ["RAVEN_FFPROBE"] = old

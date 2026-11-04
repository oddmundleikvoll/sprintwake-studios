"""Exercise source setup before any network/native build and protect private data."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SourceSetupTests(unittest.TestCase):
    def check_engine_selection(self, override=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checkout = root / "source with spaces"
            scripts = checkout / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(ROOT / "scripts/setup.sh", scripts / "setup.sh")
            helper = scripts / "setup-bundle-runtime.sh"
            helper.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$RAVEN_SETUP_CAPTURE"\nexit 73\n')
            helper.chmod(0o755)
            tools = root / "tools"
            tools.mkdir()
            uv = tools / "uv"
            uv.write_text("#!/bin/sh\nexit 0\n")
            uv.chmod(0o755)
            capture = root / "selected-engine.txt"
            env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"],
                       RAVEN_SETUP_CAPTURE=str(capture))
            env.pop("RAVEN_BUNDLE_ENGINE", None)
            expected = checkout / "engine"
            if override:
                expected = root / override
                env["RAVEN_BUNDLE_ENGINE"] = str(expected)
            (expected / "server").mkdir(parents=True)
            (expected / "server/__main__.py").touch()
            result = subprocess.run(["bash", str(scripts / "setup.sh")], cwd=root,
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 73, result.stderr)
            self.assertEqual(capture.read_text().splitlines(),
                             [str(expected), str(checkout / "runtime/bundle-venv")])
            self.assertFalse((checkout.parent / "storyboard").exists())

    def test_setup_uses_included_engine_without_sibling_checkout(self):
        self.check_engine_selection()

    def test_setup_preserves_explicit_external_engine(self):
        self.check_engine_selection("external engine")

    def test_private_files_ignored_but_engine_helpers_and_samples_publishable(self):
        private = [
            "engine/server-config.json", "engine/llm-services.json",
            "engine/tts-services.json", "engine/soundtrack-services.json",
            "engine/voice-presets.json", "engine/mflux-engines.json",
            "engine/film-timings.json", "engine/.local-config-backup/llm-services.json.1",
            "engine/server-config.json.tmp", "engine/llm-services.json.tmp",
            "engine/tts-services.json.tmp", "engine/soundtrack-services.json.tmp",
            "engine/local/.gpu-admission.lock", "engine/local/bin/ffmpeg",
            "engine/workspace/project.json", "engine/projects/private/storyboard.json",
            "engine/models/weights.safetensors", ".env", ".env.local", "signing.p12",
            "signing.p8", "signing.pem", "signing.key",
        ]
        public = [
            "engine/server/__main__.py", "engine/local/start_fable.py",
            "engine/local/stop_fable.py", "engine/llm-services-sample.json",
            "engine/tts-services-sample.json", "engine/soundtrack-services-sample.json",
            "engine/mflux-engines-sample.json", ".env.example", "release.json",
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copy2(ROOT / ".gitignore", root / ".gitignore")
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"],
                                    cwd=root, input="\n".join(private + public) + "\n",
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(result.stdout.splitlines()), set(private))

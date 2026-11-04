import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("build_bundle", Path(__file__).parents[1] / "scripts/build_bundle.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class BundleTests(unittest.TestCase):
    def test_runtime_setup_is_owned_and_non_destructive(self):
        script = (Path(__file__).parents[1] / "scripts/setup-bundle-runtime.sh").read_text()
        self.assertIn(".raven-bundle-runtime-v1", script)
        self.assertNotIn("--clear", script)
        self.assertIn("uv pip compile", script)
        self.assertIn("--generate-hashes", script)

    def test_build_isolated_bundle_with_engine_runtime_and_release_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            engine = root / "engine"
            runtime = root / "python"
            for path in (source, engine / "server", engine / "local/bin", runtime / "bin"):
                path.mkdir(parents=True)
            (source / "release.json").write_text(json.dumps({
                "version": "0.9.0", "build": 13,
                "repository": "oddmundleikvoll/sprintwake-studios",
                "feedURL": "https://updates.test/appcast.xml",
                "publicKey": "public-key", "automaticChecks": False,
            }))
            (source / "studio.py").write_text("ROOT = 'source'\n")
            (source / "assets").mkdir()
            (source / "assets/theme.xml").write_text("theme")
            (engine / "server/__main__.py").write_text("# server\n")
            (engine / "local/bin/ffmpeg").write_text("#!/bin/sh\necho ffmpeg version test\n")
            (engine / "local/bin/ffmpeg").chmod(0o755)
            (engine / "local/bin/ffprobe").write_text("#!/private/dev/python\nprint('ok')\n")
            (runtime / "bin/python3.12").write_text("python\n")
            (runtime / "bin/pip3").write_text("#!/bin/sh\n")
            for path in (
                source / "runtime/build/libraven.dylib",
                source / "runtime/Raven/bindings/python/raven.py",
                source / "runtime/Raven/bindings/python/raven_xmlpy.py",
                source / "runtime/lib/libstoryboard_media.dylib",
                source / "runtime/lib/libstoryboard_update.dylib",
                source / "runtime/MoltenVK/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib",
            ):
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text("dylib")
            sparkle = source / "Sparkle.framework"; sparkle.mkdir(); (sparkle / "Sparkle").write_text("sparkle")
            (root / "launcher").write_text("launcher\n")
            output = root / "Sprintwake Studios.app"
            result = build.build_bundle(source, output, engine=engine, python_runtime=runtime,
                                        launcher=root / "launcher", sparkle=sparkle)
            self.assertEqual(result, output.resolve())
            self.assertEqual((output / "Contents/Resources/Studio/studio.py").read_text(), "ROOT = 'source'\n")
            self.assertTrue((output / "Contents/Resources/Studio/engine/server/__main__.py").is_file())
            self.assertTrue((output / "Contents/Resources/Studio/runtime/Raven/bindings/python/raven.py").is_file())
            self.assertTrue((output / "Contents/Resources/Python/bin/python3.12").is_file())
            self.assertFalse((output / "Contents/Resources/Python/bin/pip3").exists())
            manifest = json.loads((output / "Contents/Resources/Studio/runtime/python.json").read_text())
            self.assertEqual(manifest["base"], "../Python")
            self.assertEqual(manifest["executable"], "../Python/bin/python3.12")
            self.assertEqual((output / "Contents/Resources/release.json").read_text().find("sprintwake-studios") >= 0, True)
            plist = (output / "Contents/Info.plist").read_text()
            self.assertIn("Sprintwake Studios", plist)
            self.assertIn("SUFeedURL", plist)
            self.assertIn("SURequireSignedFeed", plist)
            wrapper = (output / "Contents/Resources/Studio/engine/local/bin/ffprobe").read_text()
            self.assertIn("../../../../Python/bin/python3.12", wrapper)
            self.assertIn('" -I -B ', wrapper)
            self.assertFalse("/private/dev/python" in wrapper)
            self.assertFalse((output / "Contents/Resources/StudioRoot.txt").exists())

    def test_external_ffmpeg_symlink_is_materialized(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; engine = root / "engine"; runtime = root / "python"
            for path in (source, engine / "server", engine / "local/bin", runtime / "bin"):
                path.mkdir(parents=True)
            (source / "release.json").write_text(json.dumps({"version":"0.9.0","build":13,"repository":"oddmundleikvoll/sprintwake-studios","feedURL":"x","publicKey":"k","automaticChecks":False}))
            (engine / "server/__main__.py").write_text(""); (engine / "local/bin/ffprobe").write_text("#!/bin/sh\n")
            external = root / "external-ffmpeg"; external.write_text("#!/bin/sh\necho ffmpeg\n"); external.chmod(0o755)
            (engine / "local/bin/ffmpeg").symlink_to(external)
            (runtime / "bin/python3.12").write_text("python")
            for path in (source / "runtime/Raven/bindings/python/raven.py", source / "runtime/Raven/bindings/python/raven_xmlpy.py", source / "runtime/build/libraven.dylib", source / "runtime/lib/libstoryboard_media.dylib", source / "runtime/lib/libstoryboard_update.dylib", source / "runtime/MoltenVK/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib"):
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text("dylib")
            sparkle = source / "Sparkle.framework"; sparkle.mkdir(); (sparkle / "Sparkle").write_text("sparkle")
            launcher = root / "launcher"; launcher.write_text("launcher")
            output = root / "out.app"
            build.build_bundle(source, output, engine=engine, python_runtime=runtime, sparkle=sparkle, launcher=launcher)
            copied = output / "Contents/Resources/Studio/engine/local/bin/ffmpeg"
            self.assertFalse(copied.is_symlink()); self.assertIn("echo ffmpeg", copied.read_text())

    def test_release_bundle_rejects_missing_runtime_or_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"; engine = root / "engine"
            (source).mkdir(); (engine / "server").mkdir(parents=True)
            (engine / "server/__main__.py").write_text("# server")
            with self.assertRaises((ValueError, FileNotFoundError)):
                build.build_bundle(source, root / "out.app", engine=engine, python_runtime=root / "missing", launcher=root / "launcher")

    def test_existing_output_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "existing.app"
            output.mkdir()
            sentinel = output / "do-not-delete.txt"
            sentinel.write_text("preserve")
            with self.assertRaises(FileExistsError):
                build.build_bundle(Path(temp) / "missing-source", output)
            self.assertEqual(sentinel.read_text(), "preserve")

    def test_output_must_be_app_bundle_path(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                build.build_bundle(Path(temp) / "missing-source", Path(temp) / "output")

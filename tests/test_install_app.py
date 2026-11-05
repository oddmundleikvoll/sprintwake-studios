import importlib.util
from pathlib import Path
from unittest.mock import patch
import plistlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
import shutil
import uuid

spec=importlib.util.spec_from_file_location("install_app",Path(__file__).parents[1]/"scripts/install_app.py")
install=importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.retired = []
        def move(path, *, keep, receipt_dir):
            self.assertNotIn(Path(path).resolve(), [Path(p).resolve() for p in keep])
            destination = Path(receipt_dir).parent / 'test-trash' / uuid.uuid4().hex
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), destination)
            self.retired.append(destination)
            return destination
        patcher = patch.object(install, 'trash_bundle', side_effect=move)
        self.trash = patcher.start()
        self.addCleanup(patcher.stop)

    def bundle(self,path,identifier=install.BUNDLE_ID):
        (path/"Contents").mkdir(parents=True)
        (path/"Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier":identifier,"CFBundleVersion":"13"}))
        return path

    def test_finalization_registers_before_returning_validated_legacy_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"source"; root.mkdir()
            old=self.bundle(root/"Storyboard Raven.app")
            target=Path(temp)/"Applications/Storyboard Raven.app"; target.parent.mkdir()
            target.symlink_to(old,target_is_directory=True)
            install.prepare_destination(root,target)
            self.assertFalse(target.is_symlink())
            self.bundle(target)
            retired=install.finalize_install(root,target,register=False)
            self.assertEqual(retired, old)
            self.assertTrue(old.exists())
            self.assertTrue(target.is_dir())
            self.trash.assert_not_called()

    def test_unknown_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); target=self.bundle(root/"other.app","foreign.app")
            with self.assertRaises(ValueError): install.prepare_destination(root,target)
            self.assertTrue(target.is_dir())
            link=root/"link.app"; link.symlink_to(target,target_is_directory=True)
            with self.assertRaises(ValueError): install.prepare_destination(root,link)
            self.assertTrue(link.is_symlink())

    def test_failed_install_keeps_old_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); old=self.bundle(root/"Storyboard Raven.app")
            with self.assertRaises(ValueError): install.finalize_install(root,root/"missing.app",register=False)
            self.assertTrue(old.exists())

    def test_sprintwake_name_is_the_default_bundle_destination(self):
        self.assertEqual(install.APP_NAME, "Sprintwake Studios.app")

    def test_generic_storyboard_bundle_is_never_archived(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); old=self.bundle(root/"Storyboard.app")
            target=root/"Applications/Sprintwake Studios.app";target.parent.mkdir()
            install.prepare_destination(root,target)
            self.bundle(target)
            retired=install.finalize_install(root,target,register=False)
            self.assertIsNone(retired)
            self.assertTrue(old.exists())

    def test_transactional_install_rejects_symlink_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staged = self.bundle(root / "staged.app")
            foreign = self.bundle(root / "foreign.app", "foreign.app")
            target = root / "Applications/Sprintwake Studios.app"
            target.parent.mkdir()
            target.symlink_to(foreign, target_is_directory=True)
            with self.assertRaises(ValueError):
                install.transactional_install(root, staged, target)
            self.assertTrue(target.is_symlink())
            self.assertTrue(staged.exists())

    def test_reader_process_blocks_install_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); lock_root = root / "locks"; ready = root / "ready"
            old = self.bundle(root / "Applications/Sprintwake Studios.app")
            staged = self.bundle(root / "staged.app")
            code = ("import sys,time; sys.path.insert(0, sys.argv[1]); "
                    "from contracts.app_lifetime import AppLifetime; "
                    "life=AppLifetime(root=sys.argv[2],bundle='old',build=1).enter_engine(); "
                    "open(sys.argv[3],'w').close(); time.sleep(20)")
            proc = subprocess.Popen([sys.executable, "-c", code, str(Path(__file__).parents[1]), str(lock_root), str(ready)])
            try:
                for _ in range(100):
                    if ready.exists(): break
                    time.sleep(0.02)
                previous = os.environ.get("SPRINTWAKE_UPDATE_LOCK_ROOT")
                os.environ["SPRINTWAKE_UPDATE_LOCK_ROOT"] = str(lock_root)
                try:
                    with self.assertRaises(RuntimeError):
                        install.transactional_install(root, staged, old)
                finally:
                    if previous is None: os.environ.pop("SPRINTWAKE_UPDATE_LOCK_ROOT", None)
                    else: os.environ["SPRINTWAKE_UPDATE_LOCK_ROOT"] = previous
                self.assertTrue(old.exists())
                self.assertTrue(staged.exists())
            finally:
                proc.terminate(); proc.wait(timeout=5)

    def test_failed_staging_preserves_installed_app_and_clears_reservation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=self.bundle(root/'Sprintwake Studios.app');source=self.bundle(root/'incoming.app')
            (target/'keep.txt').write_text('working app')
            def fail_copy(src,dest,**kw):
                dest.mkdir();(dest/'partial.txt').write_text('partial');raise OSError('disk full')
            with patch.dict(os.environ,{'SPRINTWAKE_UPDATE_LOCK_ROOT':str(root/'locks')}),patch.object(install,'_verify_signature'),patch.object(install.shutil,'copytree',side_effect=fail_copy):
                with self.assertRaisesRegex(OSError,'disk full'):install.transactional_install(root,source,target)
            self.assertEqual((target/'keep.txt').read_text(),'working app');self.assertTrue(source.exists())
            self.assertFalse((root/'locks/pending-update.json').exists());self.assertFalse(list(root.glob('.*.staged-*')))

    def test_success_consumes_source_and_retires_previous_only_after_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=self.bundle(root/'Sprintwake Studios.app');source=self.bundle(root/'incoming.app')
            (target/'marker').write_text('old');(source/'marker').write_text('new')
            with patch.dict(os.environ,{'SPRINTWAKE_UPDATE_LOCK_ROOT':str(root/'locks')}),patch.object(install,'_verify_signature') as verify:
                install.transactional_install(root,source,target)
                self.assertEqual(verify.call_count,2)
            self.assertEqual((target/'marker').read_text(),'new');self.assertFalse(source.exists())
            self.assertEqual([p.joinpath('marker').read_text() for p in self.retired],['old','new'])
            self.assertFalse((root/'qa/retired-apps.noindex').exists())
            self.assertTrue((root/'locks/pending-update.json').exists())
            with self.assertRaises(RuntimeError):
                install.AppLifetime(root=root/'locks',bundle='old',build=12).enter_engine()
            updated=install.AppLifetime(root=root/'locks',bundle=target,build=13).enter_app()
            updated.close()
            self.assertFalse((root/'locks/pending-update.json').exists())

    def test_success_retires_legacy_launcher_as_well_as_build_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);legacy=self.bundle(root/'Storyboard Raven.app')
            source=self.bundle(root/'incoming.app');target=root/'Sprintwake Studios.app'
            with patch.dict(os.environ,{'SPRINTWAKE_UPDATE_LOCK_ROOT':str(root/'locks')}),patch.object(install,'_verify_signature'):
                moved=install.transactional_install(root,source,target)
            self.assertFalse(legacy.exists());self.assertFalse(source.exists())
            self.assertTrue(target.exists());self.assertEqual(moved,self.retired[0])

    def test_cleanup_failure_preserves_committed_app_and_handoff(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=self.bundle(root/'Sprintwake Studios.app');source=self.bundle(root/'incoming.app')
            (target/'marker').write_text('old');(source/'marker').write_text('new')
            self.trash.side_effect=OSError('Trash unavailable')
            with patch.dict(os.environ,{'SPRINTWAKE_UPDATE_LOCK_ROOT':str(root/'locks')}),patch.object(install,'_verify_signature'):
                with self.assertRaisesRegex(RuntimeError,'new app is installed'):
                    install.transactional_install(root,source,target)
            self.assertEqual((target/'marker').read_text(),'new')
            self.assertTrue(source.exists());self.assertTrue((root/'locks/pending-update.json').exists())
            self.assertEqual([p.joinpath('marker').read_text() for p in root.glob('*.previous-*')],['old'])

    def test_same_bundle_or_symlink_source_is_never_consumed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=self.bundle(root/'Sprintwake Studios.app')
            with self.assertRaises(ValueError):install.transactional_install(root,target,target)
            source=root/'source.app';source.symlink_to(target,target_is_directory=True)
            with self.assertRaises(ValueError):install.transactional_install(root,source,root/'other.app')
            self.assertTrue(target.exists());self.assertTrue(source.is_symlink())
            self.trash.assert_not_called()

    def test_live_bundled_worker_blocks_install_before_any_rename(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=self.bundle(root/'Sprintwake Studios.app');source=self.bundle(root/'incoming.app')
            output=f'{os.getpid()} /usr/bin/python\n123 {target.resolve()}/Contents/Resources/Python/bin/python3.12\n'
            result=type('R',(),{'stdout':output,'returncode':0})()
            with patch('app_bundles.subprocess.run',return_value=result),patch.object(install,'_verify_signature') as verify:
                with self.assertRaisesRegex(RuntimeError,'bundled worker'):
                    install.transactional_install(root,source,target)
            verify.assert_not_called();self.trash.assert_not_called()
            self.assertTrue(target.exists());self.assertTrue(source.exists())
            self.assertFalse(list(root.glob('*.previous-*')))

    def test_registration_failure_restores_previous_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=self.bundle(root/'Sprintwake Studios.app');source=self.bundle(root/'incoming.app')
            (target/'marker').write_text('old');(source/'marker').write_text('new')
            with patch.dict(os.environ,{'SPRINTWAKE_UPDATE_LOCK_ROOT':str(root/'locks')}),patch.object(install,'_verify_signature'),patch.object(install,'finalize_install',side_effect=OSError('registration failed')):
                with self.assertRaises(OSError):install.transactional_install(root,source,target,register=True)
            self.assertEqual((target/'marker').read_text(),'old');self.assertTrue(source.exists())
            self.assertFalse((root/'locks/pending-update.json').exists())
            self.trash.assert_not_called()

    def test_failed_registration_never_archives_legacy_app(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);legacy=self.bundle(root/'Storyboard Raven.app');target=self.bundle(root/'Sprintwake Studios.app')
            with patch.object(install.subprocess,'run',side_effect=OSError('registration failed')):
                with self.assertRaises(OSError):install.finalize_install(root,target,register=True)
            self.assertTrue(legacy.exists());self.assertTrue(target.exists())

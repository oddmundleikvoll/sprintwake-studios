import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import call
import uuid
import shutil
import zipfile
import stat

import app_bundles


class AppBundlesTests(unittest.TestCase):
    def setUp(self):
        if self._testMethodName!='test_real_foundation_trash_and_registration_probe':
            temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
            patcher=patch.object(app_bundles,'_cleanup_root',return_value=Path(temporary.name)/'cleanup')
            patcher.start();self.addCleanup(patcher.stop)

    def make_bundle(self, root, identifier=app_bundles.BUNDLE_ID, name="Sprintwake Studios.app"):
        bundle = Path(root) / name
        (bundle / "Contents/MacOS").mkdir(parents=True)
        (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": identifier, "CFBundleExecutable": "Sprintwake Studios"}))
        return bundle

    def test_bundle_id_and_foreign(self):
        with tempfile.TemporaryDirectory() as d:
            own = self.make_bundle(d)
            foreign = self.make_bundle(d, "com.example.other", "Other.app")
            self.assertEqual(app_bundles.bundle_id(own), app_bundles.BUNDLE_ID)
            self.assertEqual(app_bundles.bundle_id(foreign), "com.example.other")

    def test_trash_unregisters_actual_resulting_url_and_keeps_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = self.make_bundle(root); trash = root / ".Trash"; trash.mkdir(); receipt_dir = root / "receipts"
            actual = trash / source.name
            recovery=trash/'recovery.zip'
            def helper(src, receipt):
                source.rename(actual); receipt.write_text(json.dumps({"source": str(src), "trash": str(actual)})); return actual
            with patch.object(app_bundles, "_canonical", return_value=root / "Canonical.app"), patch.object(app_bundles, "_running_process", return_value=False), patch.object(app_bundles, "_trash_with_helper", side_effect=helper), patch.object(app_bundles, "_archive_trash_bundle",return_value=(recovery,{})),patch.object(app_bundles,"_remove_archived_bundle") as remove,patch.object(app_bundles, "_unregister") as unregister:
                self.assertEqual(app_bundles.trash_bundle(source, receipt_dir=receipt_dir), recovery)
            self.assertEqual(unregister.call_args_list,[call(app_bundles._resolved(source)),call(actual),call(actual)])
            remove.assert_called_once_with(actual,{})
            self.assertTrue(list(receipt_dir.glob("*.json")))

    def test_rejects_symlink_running_and_canonical(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = self.make_bundle(root)
            with patch.object(app_bundles, "_canonical", return_value=source):
                with self.assertRaises(ValueError): app_bundles.trash_bundle(source, receipt_dir=root / "r")
            source = self.make_bundle(root, name="Other.app"); (root / "link.app").symlink_to(source, target_is_directory=True)
            with self.assertRaises(ValueError): app_bundles.trash_bundle(root / "link.app", receipt_dir=root / "r")
            with patch.object(app_bundles, "_running_process", return_value=True):
                with self.assertRaises(RuntimeError): app_bundles.trash_bundle(source, receipt_dir=root / "r")

    def test_clear_is_trash_only_and_exact_scope(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); trash = root / ".Trash"; trash.mkdir(); own = self.make_bundle(trash); qa = self.make_bundle(trash, app_bundles.BUNDLE_ID + ".qa." + "a" * 32, "QA.app"); foreign = self.make_bundle(trash, "com.vendor.tool", "Vendor.app")
            canonical = root / "Applications" / app_bundles.APP_NAME; canonical.parent.mkdir(); self.make_bundle(canonical.parent, app_bundles.BUNDLE_ID)
            dump = "\n".join([f"path: {p} (0x58c0)\nidentifier: {i}\n" for p, i in [(own, app_bundles.BUNDLE_ID), (own, app_bundles.BUNDLE_ID), (qa, app_bundles.BUNDLE_ID + ".qa." + "a" * 32), (foreign, "com.vendor.tool"), (canonical, app_bundles.BUNDLE_ID)]])
            with patch.object(app_bundles.Path, "home", return_value=root), patch.object(app_bundles, "_canonical", return_value=canonical), patch.object(app_bundles.subprocess, "run", return_value=type("R", (), {"stdout": dump, "returncode": 0, "stderr": ""})()),patch.object(app_bundles,'_archive_trash_bundle',return_value=(root/'backup.zip',{})) as archive,patch.object(app_bundles,'_remove_archived_bundle') as remove,patch.object(app_bundles, "_unregister_many") as unregister:
                self.assertEqual(app_bundles.clear_trash_registrations(app_bundles.BUNDLE_ID), 1)
            self.assertEqual(unregister.call_args.args[0],[app_bundles._resolved(own)])
            archive.assert_called_once();remove.assert_called_once_with(app_bundles._resolved(own),{})

    def test_unregistered_qa_trash_apps_are_archived_only_when_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);trash=home/'.Trash';trash.mkdir()
            qa=self.make_bundle(trash,app_bundles.BUNDLE_ID+'.qa.'+'b'*32,'Unregistered QA.app')
            vendor=self.make_bundle(trash,'org.sparkle-project.Test','Vendor.app')
            with patch.object(app_bundles.Path,'home',return_value=home),patch.object(app_bundles,'_dump_records',return_value=[]),patch.object(app_bundles,'_archive_trash_bundle',return_value=(home/'backup.zip',{})) as archive,patch.object(app_bundles,'_remove_archived_bundle') as remove,patch.object(app_bundles,'_unregister_many'):
                self.assertEqual(app_bundles.clear_trash_registrations(app_bundles.BUNDLE_ID),0)
                self.assertEqual(app_bundles.clear_trash_registrations(app_bundles.BUNDLE_ID,include_qa=True),1)
            archive.assert_called_once();remove.assert_called_once_with(qa.resolve(),{})
            self.assertTrue(vendor.exists())

    def test_unregister_failure_preserves_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = self.make_bundle(root); trash = root / ".Trash"; trash.mkdir(); receipt_dir = root / "receipts"; actual = trash / source.name
            def helper(src, receipt):
                source.rename(actual); receipt.write_text(json.dumps({"source": str(src), "trash": str(actual)})); return actual
            with patch.object(app_bundles, "_canonical", return_value=root / "Canonical.app"), patch.object(app_bundles, "_running_process", return_value=False), patch.object(app_bundles, "_trash_with_helper", side_effect=helper),patch.object(app_bundles,'_archive_trash_bundle',return_value=(root/'backup.zip',{})),patch.object(app_bundles,'_remove_archived_bundle') as remove,patch.object(app_bundles, "_unregister", side_effect=[None,RuntimeError("register failure")]):
                with self.assertRaises(RuntimeError): app_bundles.trash_bundle(source, receipt_dir=receipt_dir)
                remove.assert_not_called()
            self.assertTrue(list(receipt_dir.glob("*.json")))

    def test_incomplete_or_wrong_mode_archive_preserves_original_app(self):
        for corruption in ('missing','mode'):
            with self.subTest(corruption=corruption),tempfile.TemporaryDirectory() as temp:
                home=Path(temp);trash=home/'.Trash';trash.mkdir();source=self.make_bundle(trash)
                outside=home/'outside.txt';outside.write_text('external data is not copied')
                (source/'Contents/reference').symlink_to(outside)
                def incomplete(command,**kwargs):
                    with zipfile.ZipFile(command[-1],'w') as archive:
                        if corruption=='mode':
                            for relative,entry in app_bundles._bundle_entries(source).items():
                                name=source.name+('/'+relative if relative!='.' else '')
                                if stat.S_ISDIR(entry['mode']):name+='/'
                                info=zipfile.ZipInfo(name);info.create_system=3
                                info.external_attr=(entry['mode']^(0o100 if 'link' in entry else 0))<<16
                                data=entry.get('link','').encode() if 'link' in entry else (source/relative).read_bytes() if stat.S_ISREG(entry['mode']) else b''
                                archive.writestr(info,data)
                    return type('R',(),{'returncode':0})()
                with patch.object(app_bundles.Path,'home',return_value=home),patch.object(app_bundles,'assert_bundle_idle'),patch.object(app_bundles.subprocess,'run',side_effect=incomplete):
                    with self.assertRaises(RuntimeError):app_bundles._archive_trash_bundle(source,home/'receipt.json')
                self.assertTrue((source/'Contents/Info.plist').exists())
                self.assertEqual(outside.read_text(),'external data is not copied')
                self.assertFalse((home/'receipt.json').exists())

    def test_unregister_always_rechecks_inventory(self):
        path = Path("/tmp/Sprintwake.app")
        result = type("R", (), {"stdout": "", "returncode": 0, "stderr": ""})()
        with patch.object(app_bundles.subprocess, "run", return_value=result), patch.object(app_bundles, "_dump_paths", return_value=[path]):
            with self.assertRaises(RuntimeError): app_bundles._unregister(path)
        result = type("R", (), {"stdout": "", "returncode": 1, "stderr": "already absent"})()
        with patch.object(app_bundles.subprocess, "run", return_value=result), patch.object(app_bundles, "_dump_paths", return_value=[]):
            app_bundles._unregister(path)

    def test_real_dump_suffix_preserves_apostrophes_in_bundle_paths(self):
        path=Path("/Users/example/.Trash/Oddmund's Sprintwake.app")
        result=type('R',(),{'stdout':f'path: {path} (0x58c0)\nidentifier: {app_bundles.BUNDLE_ID}\n','returncode':0})()
        with patch.object(app_bundles.subprocess,'run',return_value=result):
            self.assertEqual(app_bundles._dump_paths(app_bundles.BUNDLE_ID),[path])

    def test_keep_alias_is_protected_and_ps_failure_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = self.make_bundle(root); alias_parent = root / "alias"; alias_parent.symlink_to(root, target_is_directory=True)
            with patch.object(app_bundles, "_running_process", return_value=False), patch.object(app_bundles, "_canonical", return_value=root / "Canonical.app"), patch.object(app_bundles, "_trash_with_helper") as helper:
                with self.assertRaises(ValueError): app_bundles.trash_bundle(source, keep=(alias_parent / source.name,), receipt_dir=root / "r")
            with patch.object(app_bundles.subprocess, "run", side_effect=OSError("ps unavailable")):
                with self.assertRaises(RuntimeError): app_bundles._running_process(source)

    def test_late_trash_registration_is_retried_until_two_fresh_checks_agree(self):
        path=Path.home()/'.Trash/probe.app'
        result=type('R',(),{'returncode':0})()
        with patch.object(app_bundles.subprocess,'run',return_value=result) as command,patch.object(app_bundles,'_dump_paths',side_effect=[[],[path],[],[]]),patch.object(app_bundles.time,'sleep'):
            app_bundles._unregister(path)
            self.assertEqual(command.call_count,4)

    def test_many_trash_registrations_share_inventory_checks(self):
        paths=[Path.home()/'.Trash'/f'probe-{n}.app' for n in range(49)]
        result=type('R',(),{'returncode':0})()
        with patch.object(app_bundles.subprocess,'run',return_value=result) as command,patch.object(app_bundles,'_dump_paths',return_value=[]) as inventory,patch.object(app_bundles.time,'sleep'):
            app_bundles._unregister_many(paths)
            self.assertEqual(inventory.call_count,2)
            self.assertEqual(command.call_count,98)

    def test_bundled_python_helper_blocks_retirement_and_empty_process_table_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            source=self.make_bundle(temp)
            output=f'{os.getpid()} /usr/bin/python\n123 {source.resolve()}/Contents/Resources/Python/bin/python3.12\n'
            result=type('R',(),{'stdout':output,'returncode':0})()
            with patch.object(app_bundles.subprocess,'run',return_value=result):
                self.assertTrue(app_bundles._running_process(source))
            result.stdout=''
            with patch.object(app_bundles.subprocess,'run',return_value=result):
                with self.assertRaises(RuntimeError):app_bundles._running_process(source)

    def test_build_output_rejects_parent_and_symlink_escapes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);staging=root/'Build.noindex';staging.mkdir()
            self.assertEqual(app_bundles.validate_build_output(staging/'Sprintwake Studios.app'),(staging/'Sprintwake Studios.app').resolve())
            with self.assertRaises(ValueError):app_bundles.validate_build_output(staging/'../Other.app')
            alias=root/'Alias.noindex';alias.symlink_to(root,target_is_directory=True)
            with self.assertRaises(ValueError):app_bundles.validate_build_output(alias/'Other.app')

    @unittest.skipUnless(sys.platform == "darwin" and os.environ.get("RAVEN_RUN_REAL_APP_BUNDLE_PROBE") == "1", "explicit macOS Foundation probe")
    def test_real_foundation_trash_and_registration_probe(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); identifier=app_bundles.BUNDLE_ID+'.qa.'+uuid.uuid4().hex
            source = self.make_bundle(root,identifier=identifier,name="FoundationProbe.app")
            (source / "Contents/MacOS/Sprintwake Studios").write_text("#!/bin/sh\nexit 0\n")
            (source / "Contents/MacOS/Sprintwake Studios").chmod(0o755)
            (source/'Contents/Empty').mkdir()
            (source/'Contents/launcher-link').symlink_to('MacOS/Sprintwake Studios')
            ls = app_bundles.LSREGISTER
            subprocess.run([ls, "-f", str(source)], check=True, capture_output=True)
            actual=app_bundles.trash_bundle(source,receipt_dir=root/'receipts')
            self.assertTrue(actual.exists());self.assertFalse(source.exists())
            self.assertEqual(actual.suffix,'.zip')
            receipt=json.loads(next((root/'receipts').glob('*.json')).read_text())
            self.assertEqual(receipt['backup'],str(actual));self.assertFalse(Path(receipt['trash']).exists())
            with zipfile.ZipFile(actual) as backup:
                self.assertIsNone(backup.testzip())
            self.assertEqual(app_bundles._dump_paths(identifier),[])


if __name__ == "__main__":
    unittest.main()

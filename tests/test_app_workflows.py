"""Disposable app workflows must clean up on both success and failure."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def script(name):
    spec=importlib.util.spec_from_file_location(name.replace('-','_'),ROOT/'scripts'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

check=script('check-bundle')
sparkle=script('test-sparkle-update')

class WorkflowCleanupTests(unittest.TestCase):
    def test_standalone_check_retires_build_after_success_and_failure(self):
        for exit_code in (0,1):
            with self.subTest(exit_code=exit_code),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);bundle=root/'Build.noindex/Sprintwake Studios.app';output=root/'report'
                events=[]
                def wait(timeout):
                    events.append('exit')
                    (output/'production-smoke.json').write_text(json.dumps({'passed':['real window'],'models_started':False}))
                    return exit_code
                process=SimpleNamespace(wait=wait,poll=lambda:exit_code)
                def retire(path,**kwargs):events.append('retire');self.assertEqual(path,bundle)
                with patch.object(check,'bundle_id',return_value=check.BUNDLE_ID),patch.object(check.subprocess,'run'),patch.object(check.subprocess,'Popen',return_value=process),patch.object(check,'trash_bundle',side_effect=retire):
                    if exit_code:
                        with self.assertRaises(RuntimeError):check.check_bundle(bundle,output)
                    else:self.assertEqual(len(check.check_bundle(bundle,output)['passed']),1)
                self.assertEqual(events,['exit','retire'])

    def test_standalone_check_refuses_installed_app_before_launch_or_cleanup(self):
        canonical=Path.home()/'Applications/Sprintwake Studios.app'
        with patch.object(check.subprocess,'Popen') as launch,patch.object(check,'trash_bundle') as retire:
            with self.assertRaises(ValueError):check.check_bundle(canonical,Path('/tmp/unused-report'))
            launch.assert_not_called();retire.assert_not_called()

    def test_standalone_signature_failure_still_retires_disposable_build(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle=Path(temp)/'Build.noindex/Sprintwake Studios.app'
            with patch.object(check,'bundle_id',return_value=check.BUNDLE_ID),patch.object(check.subprocess,'run',side_effect=OSError('invalid signature')),patch.object(check,'trash_bundle') as retire:
                with self.assertRaises(OSError):check.check_bundle(bundle,Path(temp)/'report')
                retire.assert_called_once()

    def test_sparkle_finally_cleans_all_owned_copies_when_scenario_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'Input.app';destination=root/'scenario'
            identifier=sparkle.BUNDLE_ID+'.qa.'+'a'*32
            def fail(*args):
                for group in ('Installed','payload'):
                    (destination/'Apps.noindex'/group/'Sprintwake Studios.app').mkdir(parents=True)
                raise RuntimeError('signed feed rejected')
            removed=[]
            with patch.object(sparkle,'_scenario',side_effect=fail),patch.object(sparkle,'bundle_id',return_value=identifier),patch.object(sparkle,'trash_bundle',side_effect=lambda p,**kw:removed.append(p)):
                with self.assertRaisesRegex(RuntimeError,'signed feed rejected'):
                    sparkle.scenario(source,destination,root/'tools',None,True)
            self.assertEqual({path.parent.name for path in removed},{'Installed','payload'})

    def test_existing_scenario_and_symlink_escape_are_never_cleaned(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);destination=root/'existing';destination.mkdir()
            with patch.object(sparkle,'trash_bundle') as retire:
                with self.assertRaises(FileExistsError):sparkle.scenario(root/'source.app',destination,root/'tools',None,False)
                retire.assert_not_called()
            external=root/'external';(external/'Installed/Sprintwake Studios.app').mkdir(parents=True)
            (destination/'Apps.noindex').symlink_to(external,target_is_directory=True)
            with self.assertRaises(ValueError):sparkle.cleanup_scenario(root/'source.app',destination)
            self.assertTrue((external/'Installed/Sprintwake Studios.app').exists())

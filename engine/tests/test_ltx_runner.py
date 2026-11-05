import os, stat, tempfile, textwrap, unittest, json, subprocess, sys, shutil
from pathlib import Path
from unittest.mock import Mock, patch

from engine.server.ltx_runner import LTXRunner


class LTXRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.cmd = root / "ltx"
        self.cmd.write_text("#!/bin/sh\nwhile [ \"$1\" != \"-o\" ]; do shift; done\nshift\nprintf fake > \"$1\"\n")
        self.cmd.chmod(self.cmd.stat().st_mode | stat.S_IXUSR)
        self.model = root / "model"; self.model.mkdir()
        (self.model/'weights.bin').write_bytes(b'weights')
        self.work = root / "work"

    def tearDown(self): self.tmp.cleanup()

    def test_requires_offline_local_cli_model(self):
        runner = LTXRunner(self.cmd, self.model, self.work)
        self.assertEqual(runner.model, self.model)
        with self.assertRaises(ValueError): LTXRunner(self.work / "missing", self.model, self.work)

    def test_frame_alignment_and_resume_fingerprint(self):
        runner = LTXRunner(self.cmd, self.model, self.work)
        with self.assertRaises(ValueError): runner.run(["x"], 64, 64, 96, [96], 1)
        # CLI output is intentionally not decoded here; this test exercises the
        # deterministic request/resume rejection before media assembly.
        with self.assertRaises(ValueError): runner.run(["x"], 64, 64, 97, [96], 1, resume_run_id="missing")

    def test_uuid_and_resume_tamper_are_rejected(self):
        runner=LTXRunner(self.cmd,self.model,self.work)
        with self.assertRaises(ValueError): runner.run(['x'],64,64,97,[],1,resume_run_id='../escape')
        with self.assertRaises(ValueError): runner.run(['x'],64,64,97,[],1,resume_run_id='not-a-uuid')

    def test_synthetic_media_path_validates_exact_extension_window(self):
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg: self.skipTest('ffmpeg unavailable')
        script=self.cmd
        script.write_text(f'''#!{sys.executable}
import sys,subprocess,json
a=sys.argv[1:]; out=a[a.index('-o')+1]; n=int(a[a.index('-f')+1]) if '-f' in a else 25+8*int(a[a.index('--extend-frames')+1])
subprocess.run([{ffmpeg!r},'-y','-f','lavfi','-i','testsrc=size=64x64:rate=24','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-frames:v',str(n),'-t',str(n/24),'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-ar','48000',out],check=True)
'''); script.chmod(script.stat().st_mode|stat.S_IXUSR)
        runner=LTXRunner(script,self.model,self.work)
        result=runner.run(['first','second'],64,64,97,[96],1)
        from engine.server.media import validate_media
        validate_media(result['media'][0],kind='video',expected_frames=97,fps=24,audio_hz=48000)
        validate_media(result['media'][1],kind='video',expected_frames=96,fps=24,audio_hz=48000)
        stamp=result['media'][0].stat().st_mtime_ns
        resumed=runner.run(['first','second'],64,64,97,[96],1,resume_run_id=result['run_id'])
        self.assertEqual(stamp,resumed['media'][0].stat().st_mtime_ns)
        original=resumed['media'][0].read_bytes(); resumed['media'][0].write_bytes(original+b'tampered')
        with self.assertRaises(ValueError): runner.run(['first','second'],64,64,97,[96],1,resume_run_id=result['run_id'])

    def test_verbose_process_and_surviving_owned_child_are_drained(self):
        import signal
        runner=LTXRunner(self.cmd,self.model,self.work)
        record=self.work/'processes.json'
        code="import os,sys,subprocess,json; child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); open(sys.argv[1],'w').write(json.dumps({'group':os.getpid(),'child':child.pid})); print('x'*300000)"
        try:
            runner._run_process([sys.executable,'-c',code,str(record)],lambda:False)
            data=json.loads(record.read_text())
            self.assertTrue(runner._group_gone(data['group']))
            self.assertFalse(runner._active.exists())
            self.assertGreater(sum(path.stat().st_size for path in self.work.glob('*.log')),300000)
        finally:
            if record.exists():
                try: os.killpg(json.loads(record.read_text())['group'],signal.SIGKILL)
                except ProcessLookupError: pass
                except PermissionError:
                    if not runner._group_gone(json.loads(record.read_text())['group']): raise

    def test_group_permission_requires_positive_process_table(self):
        group=2**30
        own=f'{os.getpid()} {os.getpgrp()} S\n'
        with patch('engine.server.ltx_runner.os.killpg',side_effect=PermissionError), \
                patch('engine.server.ltx_runner.subprocess.run') as snapshot:
            for state,drained in [('Z',True),('Z+',True),('S',False),('R+',False)]:
                with self.subTest(state=state):
                    snapshot.return_value=subprocess.CompletedProcess([],0,own+f'123 {group} {state}\n','')
                    self.assertEqual(LTXRunner._group_gone(group),drained)
            snapshot.return_value=subprocess.CompletedProcess([],0,own,'')
            self.assertTrue(LTXRunner._group_gone(group))

    def test_unconfirmed_group_snapshot_keeps_journal(self):
        runner=LTXRunner(self.cmd,self.model,self.work)
        runner._active.write_text(json.dumps({'pid':2**30,'phase':'running'}))
        own=f'{os.getpid()} {os.getpgrp()} S\n'
        with patch('engine.server.ltx_runner.os.killpg',side_effect=PermissionError), \
                patch('engine.server.ltx_runner.subprocess.run') as snapshot:
            for output in ['',own+'invalid\n',own+f'123 {2**30} S\n']:
                snapshot.return_value=subprocess.CompletedProcess([],0,output,'')
                with self.assertRaises(RuntimeError): runner.assert_drained()
                self.assertTrue(runner._active.exists())
            snapshot.side_effect=subprocess.TimeoutExpired('/bin/ps',5)
            with self.assertRaises(RuntimeError): runner.assert_drained()
            self.assertTrue(runner._active.exists())
            snapshot.side_effect=None
            snapshot.return_value=subprocess.CompletedProcess([],0,own+f'123 {2**30} Z\n','')
            runner.assert_drained()
            self.assertFalse(runner._active.exists())

    def test_signal_permission_race_only_accepts_confirmed_drain(self):
        runner=LTXRunner(self.cmd,self.model,self.work)
        process=Mock(pid=2**30)
        with patch.object(runner,'_group_gone',side_effect=[False,True,True]), \
                patch('engine.server.ltx_runner.os.killpg',side_effect=PermissionError):
            runner._drain(process)
        with patch.object(runner,'_group_gone',return_value=False), \
                patch('engine.server.ltx_runner.os.killpg',side_effect=PermissionError):
            with self.assertRaisesRegex(RuntimeError,'unconfirmed'): runner._drain(process)

    def test_process_journal_blocks_after_workspace_change(self):
        import signal
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
        try:
            journal=self.work/'shared-process.json'; journal.parent.mkdir(exist_ok=True)
            journal.write_text(json.dumps({'pid':process.pid,'phase':'running'}))
            other=LTXRunner(self.cmd,self.model,self.work/'another-workspace',process_journal=journal)
            with self.assertRaises(RuntimeError): other.assert_drained()
            self.assertTrue(journal.exists())
        finally: os.killpg(process.pid,signal.SIGTERM); process.wait(timeout=5)
        other.assert_drained(); self.assertFalse(journal.exists())


if __name__ == "__main__": unittest.main()

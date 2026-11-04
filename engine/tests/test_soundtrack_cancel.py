"""Soundtrack cancellation uses an inert Python sleeper, never a model."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from server.soundtrack.sa3_mlx import Sa3MlxEngine


class SoundtrackCancelTests(unittest.TestCase):
    def test_stop_reaps_only_owned_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            engine=Sa3MlxEngine('test','test',root)
            children=[]
            original=subprocess.Popen
            def spawn(*args,**kwargs):
                child=original(*args,**kwargs);children.append(child)
                return child
            started=time.monotonic()
            with patch.object(engine,'health',return_value=(True,'')), \
                 patch.object(engine,'command',return_value=[sys.executable,'-c','import time; time.sleep(30)']), \
                 patch('server.soundtrack.sa3_mlx.subprocess.Popen',side_effect=spawn):
                with self.assertRaises(InterruptedError):
                    engine.generate('inert',root/'temporary.wav',seconds=2,model='sm-music',seed=0,
                                    cancel=lambda:time.monotonic()-started>.2)
            self.assertLess(time.monotonic()-started,3)
            self.assertEqual(len(children),1)
            self.assertIsNotNone(children[0].poll())
            with self.assertRaises(ProcessLookupError):os.killpg(children[0].pid,0)

    def test_already_cancelled_never_starts_process(self):
        with patch('server.soundtrack.sa3_mlx.subprocess.Popen') as spawn:
            with self.assertRaises(InterruptedError):
                Sa3MlxEngine('test','test',Path('/unused')).generate('inert',Path('/unused.wav'),
                    seconds=2,model='sm-music',seed=0,cancel=lambda:True)
            spawn.assert_not_called()

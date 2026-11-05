import tempfile,unittest,subprocess,sys,time,json,os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]))
from engine.server.app_lifetime import AppLifetime
class LifetimeTests(unittest.TestCase):
 def test_pending_marker_and_reader_guard(self):
  with tempfile.TemporaryDirectory() as t:
   a=AppLifetime(Path(t),bundle='a',build=1).enter_app(); a.reserve(2); self.assertTrue(a.writer_ready()); self.assertTrue((Path(t)/'pending-update.json').exists()); a.cancel_update(); a.close()
 def test_pending_marker_fails_closed_even_for_matching_engine(self):
  with tempfile.TemporaryDirectory() as t:
   a=AppLifetime(Path(t),bundle='new',build=2).enter_app(); a.reserve(2); a.close()
   with self.assertRaises(RuntimeError): AppLifetime(Path(t),bundle='new',build=2).enter_engine()
   a=AppLifetime(Path(t),bundle='new',build=2).enter_app(); self.assertFalse((Path(t)/'pending-update.json').exists()); a.close()
 def test_uncertain_marker_and_symlink_fail_closed(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t); (root/'pending-update.json').write_text('{bad')
   with self.assertRaises(RuntimeError): AppLifetime(root,bundle='x',build=1).enter_engine()
  with tempfile.TemporaryDirectory() as t:
   parent=Path(t)/'parent'; parent.mkdir(); root=parent/'locks'; root.mkdir(); outside=Path(t)/'outside'; outside.mkdir()
   (parent/'locks').rename(parent/'real-locks'); (parent/'locks').symlink_to(outside,target_is_directory=True)
   with self.assertRaises(RuntimeError): AppLifetime(root,bundle='x',build=1).enter_engine()
 def test_pending_marker_rechecked_under_entry_fence(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t); owner=AppLifetime(root,bundle='new',build=2).enter_app(); owner.reserve(2)
   with self.assertRaises(RuntimeError): AppLifetime(root,bundle='old',build=1).enter_engine()
   with self.assertRaises(RuntimeError): AppLifetime(root,bundle='old',build=1).enter_app()
   self.assertTrue((root/'pending-update.json').exists()); owner.cancel_update(); owner.close()
  with tempfile.TemporaryDirectory() as t:
   root=Path(t); outside=Path(t).parent/'outside-lock'; outside.write_text('x')
   (root/'pending-update.json').symlink_to(outside)
   with self.assertRaises(RuntimeError): AppLifetime(root,bundle='x',build=1).enter_engine()
 def test_app_instance_is_exclusive(self):
  with tempfile.TemporaryDirectory() as t:
   first=AppLifetime(Path(t),bundle='a',build=1).enter_app()
   with self.assertRaises(RuntimeError): AppLifetime(Path(t),bundle='b',build=2).enter_app()
   first.close()
 def test_reserve_and_cancel_require_marker_ownership(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t); outsider=AppLifetime(root,bundle='x',build=1)
   with self.assertRaises(RuntimeError): outsider.reserve(1)
   owner=AppLifetime(root,bundle='x',build=1).enter_app(); owner.reserve(2); owner.close()
   with self.assertRaises(RuntimeError): outsider.cancel_update()
   with self.assertRaises(RuntimeError): AppLifetime(root,bundle='x',build=1).enter_engine()
 def test_peer_reader_drains_after_reservation_before_writer_upgrade(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t); ready=root/'ready'; code="import sys,time; from server.app_lifetime import AppLifetime; x=AppLifetime(sys.argv[1],bundle='child',build=1).enter_engine(); open(sys.argv[2],'w').close(); time.sleep(20)"
   env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1]))
   proc=subprocess.Popen([sys.executable,'-c',code,str(root),str(ready)],env=env)
   try:
    for _ in range(100):
     if ready.exists(): break
     time.sleep(.01)
    owner=AppLifetime(root,bundle='new',build=2).enter_app(); owner.reserve(2)
    self.assertFalse(owner.writer_ready()); proc.terminate(); proc.wait(timeout=3)
    self.assertTrue(owner.writer_ready()); owner.cancel_update(); owner.close()
   finally:
    if proc.poll() is None: proc.terminate(); proc.wait(timeout=3)

 def test_legacy_published_handoff_uses_same_lock_and_marker(self):
  import fcntl
  with tempfile.TemporaryDirectory() as t:
   root=Path(t); marker=root/'pending-update.json'
   marker.write_text(json.dumps({'bundle':'new','build':17,'pid':1234}))
   with (root/'code-reader.lock').open('a+') as old_reader:
    fcntl.flock(old_reader,fcntl.LOCK_SH)
    with self.assertRaises(RuntimeError): AppLifetime(root,bundle='new',build=17).enter_app()
    self.assertTrue(marker.exists())
   gate=AppLifetime(root,bundle='new',build='17').enter_app()
   self.assertFalse(marker.exists()); gate.close()

import json,tempfile,threading,unittest
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from engine.server.language_lifecycle import managed_language,LanguageLease,LifecycleError
class H(BaseHTTPRequestHandler):
 calls=[]; loaded=set(); fail_unload=False
 def log_message(self,*a): pass
 def _reply(self,data): raw=json.dumps(data).encode(); self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
 def do_GET(self): H.calls.append(('GET',self.path)); self._reply({'models':[{'name':x,'loaded':True} for x in H.loaded]})
 def do_POST(self):
  H.calls.append(('POST',self.path)); n=self.path.rsplit('/',1)[-1]
  if self.path=='/api/chat':
   body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')))); model=body['model'];
   if body.get('keep_alive')==0 and not H.fail_unload: H.loaded.discard(model)
   else: H.loaded.add(model)
  elif self.path.endswith('/load'): H.loaded.add(unquote(self.path.split('/')[-2]))
  elif self.path.endswith('/unload'):
   if not H.fail_unload: H.loaded.discard(unquote(self.path.split('/')[-2]))
  self._reply({'ok':True})
class LifecycleTests(unittest.TestCase):
 def setUp(self):
  H.calls=[]; H.loaded=set(); H.fail_unload=False; self.s=ThreadingHTTPServer(('127.0.0.1',0),H); threading.Thread(target=self.s.serve_forever,daemon=True).start(); self.addCleanup(self.s.server_close); self.addCleanup(self.s.shutdown)
 def profile(self,kind='ollama',model='small'): return {'kind':'openai-compatible','lifecycle':kind,'model':model,'url':f'http://127.0.0.1:{self.s.server_port}','lifecycleTimeout':1}
 def test_load_completion_failure_still_unloads(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):
    with managed_language(self.profile(),store_path=Path(d)/'j.json'): raise ValueError('completion failed')
   self.assertIn(('POST','/api/chat'),H.calls); self.assertEqual(H.loaded,set())
 def test_only_selected_model_is_touched_and_journal_drains(self):
  with tempfile.TemporaryDirectory() as d:
   with managed_language(self.profile(model='chosen'),store_path=Path(d)/'j.json'): pass
   self.assertEqual(json.loads((Path(d)/'j.json').read_text())['state'],'drained'); self.assertNotIn('other',H.loaded)
 def test_failed_unload_blocks_following_media(self):
  with tempfile.TemporaryDirectory() as d:
   H.fail_unload=True
   with self.assertRaises(LifecycleError):
    with managed_language(self.profile(),store_path=Path(d)/'j.json'): pass
   with self.assertRaises(LifecycleError): LanguageLease(self.profile(),store_path=Path(d)/'j.json').assert_drained()
 def test_cloud_and_unmanaged_profiles_make_no_requests(self):
  with tempfile.TemporaryDirectory() as d:
   with managed_language({'kind':'openai-compatible','url':'http://invalid','model':'cloud'},store_path=Path(d)/'j.json'): pass
   self.assertEqual(H.calls,[])
 def test_27b_requires_long_timeout(self):
  with self.assertRaises(LifecycleError):
   with managed_language(self.profile(model='local-27B'),store_path=Path(tempfile.mkdtemp())/'j.json'): pass
 def test_omlx_documented_status_and_v1_endpoint_normalization(self):
  with tempfile.TemporaryDirectory() as d:
   profile=self.profile('omlx','Qwen/model'); profile['url']=f'http://127.0.0.1:{self.s.server_port}/v1'; profile['lifecycleTimeout']=1
   with managed_language(profile,store_path=Path(d)/'j.json'): pass
   self.assertIn(('POST','/v1/models/Qwen%2Fmodel/load'),H.calls)
 def test_unsafe_journal_blocks_independent_drain_check(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'j.json'; p.write_text(json.dumps({'state':'uncertain','model':'x'}))
   with self.assertRaises(LifecycleError): LanguageLease(self.profile(),store_path=p).assert_drained()

 def test_incomplete_drained_record_does_not_authorize_media(self):
  from engine.server.language_lifecycle import assert_drained
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'journal.json'
   for invalid in ({'state':'drained'},{'state':'drained','lifecycle':'unknown','model':'x','updatedAt':1}):
    path.write_text(json.dumps(invalid))
    with self.assertRaises(LifecycleError): assert_drained(path)

import json, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory
from pathlib import Path
from engine.server.comfy_workflow import WorkflowClient, validate_profile, profile_digest, AdmissionUncertain
PROFILE={'version':1,'id':'demo','label':'Demo','kind':'image','workflow':{'1':{'class_type':'SaveImage','inputs':{'text':''}}},'bindings':{'prompt':{'node':'1','input':'text'}},'output':{'node':'1','key':'images'},'capabilities':{}}
class H(BaseHTTPRequestHandler):
 calls=[]; ack=True
 def log_message(self,*a): pass
 def do_GET(self):
  H.calls.append(('GET',self.path)); data={'/object_info':{'SaveImage':{}},'/queue':{'queue_pending':[],'queue_running':[]}}
  raw=json.dumps(data.get(self.path,{})).encode(); self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
 def do_POST(self):
  H.calls.append(('POST',self.path)); self.rfile.read(int(self.headers.get('Content-Length','0')))
  raw=b'{"prompt_id":"job"}' if H.ack else b'{}'; self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
class WorkflowTests(unittest.TestCase):
 def setUp(self):
  H.calls=[]; H.ack=True; self.s=ThreadingHTTPServer(('127.0.0.1',0),H); threading.Thread(target=self.s.serve_forever,daemon=True).start(); self.addCleanup(self.s.server_close); self.addCleanup(self.s.shutdown)
 def test_profile_declared_bindings_and_normalization(self):
  normalized=validate_profile(PROFILE); self.assertEqual(normalized['capabilities']['sizeAlign'],1); self.assertEqual(profile_digest(PROFILE),profile_digest(json.loads(json.dumps(PROFILE))))
  bad=json.loads(json.dumps(PROFILE)); bad['bindings']['seed']={'node':'1','input':'missing'}
  with self.assertRaises(ValueError): validate_profile(bad)
  bad=json.loads(json.dumps(PROFILE)); bad['workflow']['1']['inputs']['api_key']='secret'
  with self.assertRaises(ValueError): validate_profile(bad)
 def test_submit_lost_ack_blocks_retry_and_recover_is_dict(self):
  with TemporaryDirectory() as d:
   H.ack=False; c=WorkflowClient(f'http://127.0.0.1:{self.s.server_port}',state_dir=d)
   with self.assertRaises(AdmissionUncertain): c.submit({'1':{}})
   self.assertTrue(c.has_unresolved()); H.ack=True
   with self.assertRaises(AdmissionUncertain): c.submit({'1':{}})
   self.assertIsInstance(c.recover(),dict)
 def test_cancel_is_owned_and_scoped(self):
  with TemporaryDirectory() as d:
   c=WorkflowClient(f'http://127.0.0.1:{self.s.server_port}',state_dir=d); pid,cid=c.submit({'1':{}})
   with self.assertRaises(ValueError): c.cancel('foreign')
   c.cancel(pid); self.assertIn(('POST','/sprintwake/cancel-pending'),H.calls); self.assertNotIn(('POST','/interrupt'),H.calls); self.assertNotIn(('POST','/free'),H.calls)
 def test_endpoint_rejects_credentials_and_queries(self):
  with self.assertRaises(ValueError): WorkflowClient('http://u:p@example.test')
  with self.assertRaises(ValueError): WorkflowClient('http://example.test/?token=x')

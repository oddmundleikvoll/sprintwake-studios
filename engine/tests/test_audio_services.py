import io,json,os,tempfile,threading,unittest,wave
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from engine.server.audio import AudioServices
from engine.server.jobs import Scheduler
from engine.server.store import Store

def wav():
 b=io.BytesIO()
 with wave.open(b,'wb') as w: w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000); w.writeframes(b'\0\0'*96000)
 return b.getvalue()
class H(BaseHTTPRequestHandler):
 mode='ok'; seen={}
 def log_message(self,*a): pass
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length','0')))
  H.seen=dict(self.headers)
  if H.mode=='redirect': self.send_response(302); self.send_header('Location','/other'); self.end_headers(); return
  raw=b'x'*(AudioServices.MAX_RESPONSE+1) if H.mode=='oversize' else wav()
  self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); root=Path(self.tmp.name); self.store=Store(root/'Projects'); self.slug,self.board=self.store.create('Film'); self.cfg=root/'cfg'; self.cfg.mkdir()
  H.mode='ok'; self.http=ThreadingHTTPServer(('127.0.0.1',0),H); threading.Thread(target=self.http.serve_forever,daemon=True).start(); self.addCleanup(self.http.server_close); self.addCleanup(self.http.shutdown)
  url=f'http://127.0.0.1:{self.http.server_port}'
  (self.cfg/'tts-services.json').write_text(json.dumps({'services':[{'id':'tts','kind':'openai-compatible','url':url}]})); (self.cfg/'soundtrack-services.json').write_text(json.dumps({'services':[{'id':'music','kind':'http-audio','url':url}]}))
  self.sched=Scheduler(); self.audio=AudioServices(self.store,self.sched,self.cfg,credential_loader=lambda _: 'secret-token'); os.environ['RAVEN_FFMPEG']=str(Path(__file__).parents[2]/'runtime/tools/ffmpeg'); os.environ['RAVEN_FFPROBE']=str(Path(__file__).parents[2]/'runtime/tools/ffprobe')
 def tearDown(self): os.environ.pop('RAVEN_FFMPEG',None); os.environ.pop('RAVEN_FFPROBE',None); self.tmp.cleanup()
 def done(self,r): return self.sched.wait(self.slug,20)
 def test_dialogue_derives_text_and_writes_immutable_audio(self):
  b=dict(self.board); b['shots']=[{'id':'s1','dialogue':'Hello'}]; self.store.put(self.slug,b); r=self.audio.prepare_dialogue({'slug':self.slug,'service':'tts'}); j=self.done(r); self.assertEqual(j.status,'completed',j.error); self.assertTrue((self.store.project_dir(self.slug)/j.result['path']).is_file()); self.assertNotIn('secret-token',json.dumps(j.result))
 def test_soundtrack_failure_preserves_board(self):
  before=self.store.get(self.slug); r=self.audio.soundtrack(self.slug,{'service':'music','prompt':'quiet'}); job=self.done(r); self.assertEqual(job.status,'completed',job.error); self.assertTrue(self.store.get(self.slug).get('soundtrackUrl')); H.mode='redirect'; self.addCleanup(lambda:setattr(H,'mode','ok')); r=self.audio.soundtrack(self.slug,{'service':'music','prompt':'retry'}); self.assertEqual(self.done(r).status,'failed'); self.assertTrue(self.store.get(self.slug).get('soundtrackUrl'))
 def test_oversize_fails_without_output(self):
  H.mode='oversize'; self.addCleanup(lambda:setattr(H,'mode','ok')); r=self.audio.prepare_dialogue({'slug':self.slug,'service':'tts','text':'hello'}); self.assertEqual(self.done(r).status,'failed'); self.assertFalse(list((self.store.project_dir(self.slug)/'Output/Audio').glob('*')))
 def test_dub_requires_picture_take(self):
  b=dict(self.board); b['shots']=[{'id':'s1','dialogue':'hello'}]; self.store.put(self.slug,b)
  with self.assertRaisesRegex(ValueError,'picture take'): self.audio.dub(self.slug,'s1',{'service':'tts'})

 def test_actual_dub_preserves_all_picture_packets_and_attaches_audio(self):
  import av,hashlib,subprocess
  from unittest.mock import patch
  source=Path(self.tmp.name)/'fixture.mp4'
  subprocess.run([os.environ['RAVEN_FFMPEG'],'-v','error','-y','-f','lavfi','-i','testsrc2=size=64x64:rate=24','-frames:v','24','-c:v','libx264',str(source)],check=True,timeout=20)
  ref=self.store.import_media(self.slug,source,'shots/s1/clip.mp4'); board=self.store.get(self.slug)
  board['shots']=[{'id':'s1','dialogue':'Hello','outputs':[ref['url']]}]; self.store.put(self.slug,board)
  before=source.read_bytes(); errors=[]; run=subprocess.run
  def capture(*args,**kwargs):
   try: return run(*args,**kwargs)
   except subprocess.CalledProcessError as error:
    errors.append(error.stderr.decode('utf-8','replace') if isinstance(error.stderr,bytes) else str(error.stderr)); raise
  with patch('engine.server.audio.subprocess.run',side_effect=capture):
   result=self.audio.dub(self.slug,'s1',{'service':'tts'}); job=self.done(result)
  self.assertEqual(job.status,'completed',str(job.error)+' '+str(errors))
  saved=self.store.get(self.slug); dubbed=self.store.media_path(saved['shots'][0]['dubUrl'])
  def packets(path):
   with av.open(str(path)) as container: return [hashlib.sha256(bytes(packet)).hexdigest() for packet in container.demux(video=0) if packet.size]
  self.assertEqual(packets(source),packets(dubbed)); self.assertEqual(source.read_bytes(),before)
  from engine.server.media import validate_decoded
  validate_decoded(dubbed,kind='video',frames=24,fps=24,audio_hz=48000)

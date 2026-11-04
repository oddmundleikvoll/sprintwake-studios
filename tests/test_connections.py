"""Portable defaults and actual owned engine + inert OpenAI/Comfy endpoints."""
import copy,json,os,shutil,struct,subprocess,sys,tempfile,threading,time,unittest,zlib
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from backend import Backend,BackendError
from connections import ConnectionStore,defaults,validate,credential_account


def image_bytes():
    def chunk(name,data):return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data))
    raw=b''.join(b'\0'+bytes(v for x in range(128) for v in (x*2,y*2,(x*y)%256)) for y in range(128))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',128,128,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

class Provider(BaseHTTPRequestHandler):
    calls=[]
    video=None
    failed=False
    def log_message(self,*_):pass
    def reply(self,data,kind='application/json'):
        raw=json.dumps(data).encode() if kind=='application/json' else data
        self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        type(self).calls.append(('GET',self.path,self.headers.get('Authorization')))
        if self.path=='/v1/models':return self.reply({'data':[{'id':'my-text-model'}]})
        if self.path=='/system_stats':return self.reply({'system':{}})
        if self.path=='/object_info':return self.reply({'TestImage':{'input':{'required':{'text':['STRING',{}]}}},'TestVideo':{}})
        if self.path=='/queue':return self.reply({'queue_running':[],'queue_pending':[]})
        if self.path.startswith('/history/'):
            ident=self.path.rsplit('/',1)[1]
            key,name=('videos','clip.mp4') if type(self).video else ('images','generated.png')
            return self.reply({ident:{'status':{'completed':True,'status_str':'error' if type(self).failed else 'success'},'outputs':{'1':{key:[{'filename':name,'subfolder':'','type':'output'}]}}}})
        if self.path.startswith('/view?'):return self.reply(type(self).video or image_bytes(),'video/mp4' if type(self).video else 'image/png')
        self.send_error(404)
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).calls.append(('POST',self.path,self.headers.get('Authorization'),body))
        if self.path=='/v1/chat/completions':
            content='{"message":"Tilkoblingen virker.","actions":[]}'
            if body.get('stream'):
                return self.reply(('data: '+json.dumps({'choices':[{'delta':{'content':content}}]})+'\n\ndata: [DONE]\n\n').encode(),'text/event-stream')
            return self.reply({'choices':[{'message':{'content':content}}]})
        if self.path=='/prompt':return self.reply({'prompt_id':'fixture-job'})
        self.send_error(404)

class ConnectionTests(unittest.TestCase):
    def test_setup_contracts_import_from_a_standalone_checkout_without_engine(self):
        source=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            checkout=Path(tmp)/'Raven';checkout.mkdir()
            shutil.copytree(source/'contracts',checkout/'contracts',ignore=shutil.ignore_patterns('__pycache__'))
            for name in ('connections.py','director_contract.py','workspace.py','backend.py'):shutil.copy2(source/name,checkout/name)
            code='import sys;sys.path.insert(0,'+repr(str(checkout))+');import connections,director_contract,workspace,backend;assert director_contract.load_contract().CATALOG_VERSION;assert connections.defaults()["selected"]["image"]=="qwen-local"'
            result=subprocess.run([sys.executable,'-I','-B','-c',code],cwd=tmp,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_bundled_contracts_match_included_engine_source(self):
        source=Path(__file__).resolve().parents[1];engine=source/'engine/server'
        for path in (source/'contracts').iterdir():
            if path.name!='__init__.py' and (engine/path.name).is_file():self.assertEqual(path.read_bytes(),(engine/path.name).read_bytes(),path.name)

    def test_saved_credentials_are_bound_to_profile_and_endpoint(self):
        doc=defaults();doc['llmProfiles']=[{'id':'mine','url':'https://example.test/v1'}]
        original=credential_account(doc,'llm:mine');comfy=credential_account(doc,'comfy')
        doc['llmProfiles'][0]['url']='https://another.test/v1';doc['comfyUrl']='https://another.test/comfy'
        self.assertNotEqual(original,credential_account(doc,'llm:mine'));self.assertNotEqual(comfy,credential_account(doc,'comfy'))

    def test_native_endpoint_change_does_not_reuse_inherited_provider_keys(self):
        doc=defaults();doc['llmProfiles']=[{'id':'mine','url':'https://new.example.test/v1'}];doc['selected']['llm']='mine'
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'RAVEN_LLM_API_KEY':'old-llm-key','RAVEN_COMFY_API_KEY':'old-comfy-key'}):
            backend=Backend(Path(temp),connection_settings=doc,credential_loader=lambda _:'')
            env=backend._child_environment()
            self.assertNotIn('RAVEN_LLM_API_KEY',env);self.assertNotIn('RAVEN_COMFY_API_KEY',env)

    def test_defaults_use_another_users_home_without_an_engine_checkout(self):
        with tempfile.TemporaryDirectory() as tmp,patch('connections.ROOT',Path(tmp)/'Apps/Raven'),patch('pathlib.Path.home',return_value=Path(tmp)/'home'):
            doc=defaults()
            self.assertNotIn('/Users/oddmund',json.dumps(doc))
            self.assertEqual(doc['paths']['root'],str(Path(tmp)/'home/Movies/Raven Studio'))
            self.assertEqual(doc['selected'],{'llm':'local','image':'qwen-local','video':'ltx-local'})
    def test_settings_private_strict_and_separate_from_projects(self):
        self.assertTrue(ConnectionStore('relative-connections.json').path.is_absolute())
        with tempfile.TemporaryDirectory() as tmp:
            store=ConnectionStore(Path(tmp)/'prefs/connections.json');doc=defaults();store.save(doc)
            self.assertEqual(store.load(),validate(doc));self.assertEqual(store.path.stat().st_mode&0o777,0o600)
            bad=copy.deepcopy(doc);bad['comfyUrl']='https://name:secret@example.com'
            with self.assertRaises(ValueError):store.save(bad)
            bad=copy.deepcopy(doc);bad['apiKey']='secret'
            with self.assertRaises(ValueError):store.save(bad)
            bad=copy.deepcopy(doc);bad['selected']['video']=[]
            with self.assertRaises(ValueError):store.save(bad)
            self.assertEqual(store.load(),validate(doc))

class EngineConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        Provider.calls=[];Provider.video=None;Provider.failed=False;self.server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
    def test_passive_connection_probe_only_reads_provider_metadata(self):
        from connection_checks import probe_connections
        url=f'http://127.0.0.1:{self.server.server_port}'
        doc=defaults();doc['comfyUrl']=url;doc['paths']['python']=str(self.root/'missing-python');doc['paths']['engine']=str(self.root/'missing-engine')
        doc['llmProfiles']=[{'id':'my-llm','label':'Mine','kind':'openai-compatible','url':url+'/v1','model':'my-text-model','vision':False}]
        doc['selected']={'llm':'my-llm','image':'none','video':'none'}
        result={row['name']:row for row in probe_connections(doc,lambda _:'probe-key')}
        self.assertEqual(result['Språkmodell']['status'],'klar');self.assertEqual(result['ComfyUI']['status'],'klar')
        self.assertTrue(Provider.calls);self.assertTrue(all(call[0]=='GET' for call in Provider.calls))
        self.assertTrue(all(call[2]=='Bearer probe-key' for call in Provider.calls))
    def test_own_llm_and_image_use_real_engine_apis_without_global_config_or_secrets(self):
        source=Path(__file__).resolve().parents[1];engine=source/'engine';python=source/'runtime/bundle-venv/bin/python'
        if not python.is_file():self.skipTest('matching production runtime required for owned-engine integration')
        url=f'http://127.0.0.1:{self.server.server_port}'
        doc=defaults();doc['paths'].update(root=str(self.root/'Studio'),workspace=str(self.root/'work'),engine=str(engine),python=str(python))
        doc['comfyUrl']=url;doc['llmProfiles']=[{'id':'my-llm','label':'My text model','kind':'openai-compatible','url':url+'/v1','model':'my-text-model','vision':False}]
        doc['workflowProfiles']=[{'version':1,'id':'My.Image','label':'My image','kind':'image','workflow':{'1':{'class_type':'TestImage','inputs':{'text':''}}},'bindings':{'prompt':{'node':'1','input':'text'}},'output':{'node':'1','key':'images'},'capabilities':{'sizeAlign':1}}]
        doc['selected']={'llm':'my-llm','image':'My.Image','video':'none'};doc=validate(doc)
        originals={p:p.read_bytes() for p in (engine/'llm-services.json',engine/'server-config.json') if p.exists()}
        backend=Backend(Path(doc['paths']['root']),engine_root=engine,runtime_python=python,workspace=Path(doc['paths']['workspace']),comfyui_url=url,llm='my-llm',connection_settings=doc,credential_loader=lambda _:'fixture-secret')
        self.addCleanup(backend.close);info=backend.start()
        self.assertEqual(info['llm']['default'],'my-llm');self.assertFalse(info['connections']['nativeFilm'])
        result=backend.raven_chat({'slug':None,'message':'Svar kort'})
        self.assertIn('Tilkoblingen virker',result['message'])
        created=backend.post('/api/boards',{'name':'Portable'});slug=created['slug']
        backend.post('/api/images',{'slug':slug,'prompt':'A test image','width':128,'height':128,'inheritStyle':False})
        end=time.monotonic()+15
        while time.monotonic()<end:
            images=backend.get('/api/images?slug='+slug)
            job=images.get('job') or {}
            if job.get('status') in {'done','failed','interrupted'}:break
            time.sleep(.05)
        self.assertEqual(job.get('status'),'done',job)
        records=images['images'];self.assertEqual(records[0]['model'],'My.Image')
        self.assertIn('workflowRecipe',records[0])
        self.assertTrue(any(call[:3]==('POST','/v1/chat/completions','Bearer fixture-secret') for call in Provider.calls))
        for path,data in originals.items():self.assertEqual(path.read_bytes(),data)
        for p in Path(doc['paths']['root']).rglob('*.json'):
            self.assertNotIn('fixture-secret',p.read_text(),str(p))
        self.assertFalse(any(call[1] in {'/interrupt','/free'} for call in Provider.calls))

    def test_relocated_engine_video_takes_keep_selected_media_when_next_job_fails(self):
        root=Path(__file__).resolve().parents[1];source=root/'engine';python=root/'runtime/bundle-venv/bin/python'
        if not python.is_file():self.skipTest('matching production runtime required')
        from media_tools import executable
        video=self.root/'fixture.mp4'
        subprocess.run([executable('ffmpeg'),'-v','error','-y','-f','lavfi','-i','testsrc2=size=128x128:rate=24','-frames:v','24',str(video)],check=True,timeout=15)
        Provider.video=video.read_bytes()
        engine=self.root/'Elsewhere/production-engine';shutil.copytree(source/'server',engine/'server',ignore=shutil.ignore_patterns('__pycache__'))
        url=f'http://127.0.0.1:{self.server.server_port}'
        doc=defaults();doc['paths'].update(root=str(self.root/'Studio'),workspace=str(self.root/'SeparateWork'),engine=str(engine),python=str(python));doc['comfyUrl']=url
        doc['selected']={'llm':'none','image':'none','video':'own-video'}
        doc['workflowProfiles']=[{'version':1,'id':'own-video','label':'Own video','kind':'video','workflow':{'1':{'class_type':'TestVideo','inputs':{'text':''}}},'bindings':{'prompt':{'node':'1','input':'text'}},'output':{'node':'1','key':'videos'},'capabilities':{'fps':24,'frameRule':{'kind':'any','minimum':1},'maxFrames':240,'sizeAlign':1,'resolutions':['128x128']}}]
        backend=Backend(Path(doc['paths']['root']),engine_root=engine,runtime_python=python,workspace=Path(doc['paths']['workspace']),comfyui_url=url,llm='none',connection_settings=validate(doc))
        self.addCleanup(backend.close);info=backend.start();self.assertFalse(info['connections']['nativeFilm'])
        slug=backend.post('/api/boards',{'name':'Own video'})['slug'];board=backend.get('/api/boards/'+slug)['board']
        from contracts.comfy_workflow import profile_digest
        digest=profile_digest(doc['workflowProfiles'][0])
        self.assertEqual(board['defaults']['model'],'own-video');self.assertEqual(board['defaults']['modelProfileDigest'],digest)
        board['shots']=[{'id':'s1','title':'Shot','prompt':'A test shot','model':'own-video','modelProvider':'comfy-workflow','modelProfileDigest':digest,'frames':24,'resolution':'128x128','seed':5}]
        backend.put('/api/boards/'+slug,board)
        persisted=backend.get('/api/boards/'+slug)['board']['shots'][0]
        self.assertEqual(persisted['model'],'own-video');self.assertEqual(persisted['modelProfileDigest'],digest)
        def render():
            backend.post('/api/render-seeds',{'slug':slug,'shotId':'s1','count':1})
            end=time.monotonic()+15
            while backend.get('/api/status').get('busy') and time.monotonic()<end:time.sleep(.05)
            return backend.get(f'/api/boards/{slug}/shots/s1/seed-takes')
        takes=render();takes=takes['takes'] if isinstance(takes,dict) else takes
        self.assertEqual(len(takes),1);self.assertIn(takes[0]['status'],{'done','review'},takes)
        seed=takes[0]['seed'];backend.post(f'/api/boards/{slug}/shots/s1/seed-takes/{seed}/adopt',{})
        selected=list((Path(doc['paths']['root'])/'Projects'/slug/'shots').glob('*/clip.mp4'));self.assertEqual(len(selected),1)
        before=selected[0].read_bytes();selected_board=backend.get('/api/boards/'+slug)
        recipe_path=selected[0].with_name('recipe.json');recipe=recipe_path.read_bytes()
        self.assertEqual(json.loads(recipe)['workflowDigest'],digest)
        Provider.failed=True;takes=render();takes=takes['takes'] if isinstance(takes,dict) else takes
        self.assertEqual(len(takes),2);self.assertTrue(any(t['status']=='failed' for t in takes))
        self.assertEqual(selected[0].read_bytes(),before);self.assertEqual(backend.get('/api/boards/'+slug),selected_board)
        self.assertEqual(recipe_path.read_bytes(),recipe)
        source_recipe=next(path for path in (Path(doc['paths']['root'])/'Projects'/slug).rglob('recipe.json') if path!=recipe_path and 'seed-takes' in path.parts)
        source_recipe.unlink()
        still=selected[0].parent/'frames/old.png';still.parent.mkdir(exist_ok=True);still.write_bytes(b'old selected still')
        with self.assertRaises(BackendError):backend.post(f'/api/boards/{slug}/shots/s1/seed-takes/{seed}/adopt',{})
        self.assertEqual(selected[0].read_bytes(),before);self.assertEqual(recipe_path.read_bytes(),recipe);self.assertEqual(still.read_bytes(),b'old selected still')
        self.assertFalse((engine/'server-config.json').exists());self.assertFalse(any(call[1] in {'/interrupt','/free'} for call in Provider.calls))

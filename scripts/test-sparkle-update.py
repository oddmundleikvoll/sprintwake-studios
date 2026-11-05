"""Local signed Sparkle replacement + production-wait + data-preservation test.

Uses only a disposable QA bundle, synthetic media, fake Comfy HTTP and Keychain
signing. No model inference, user settings or GitHub publication.
"""
import argparse,copy,functools,json,os,plistlib,shutil,subprocess,sys,tempfile,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from connections import defaults,ConnectionStore,validate
from release_artifacts import prepare_release

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def reply(self,data,kind='application/json'):
        raw=json.dumps(data).encode() if kind=='application/json' else data
        self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        state=self.server.state;state['calls'].append(('GET',self.path))
        if self.path=='/system_stats':return self.reply({'system':{}})
        if self.path=='/object_info':return self.reply({'FakeVideo':{'input':{'required':{'text':['STRING',{}]}}}})
        if self.path=='/queue':return self.reply({'queue_running':[] if state['finish'] or not state.get('submitted') else [[0,'fixture',{},{}]],'queue_pending':[]})
        if self.path.startswith('/history/'):
            return self.reply({'fixture':{'status':{'completed':state['finish'],'status_str':'success' if state['finish'] else 'running'},'outputs':{'1':{'videos':[{'filename':'clip.mp4','subfolder':'','type':'output'}]}}}})
        if self.path.startswith('/view?'):return self.reply(state['video'],'video/mp4')
        name=self.path.split('?',1)[0].lstrip('/')
        path=state['assets']/name
        if name and '/' not in name and path.is_file():return self.reply(path.read_bytes(),'application/xml' if name.endswith('.xml') else 'application/zip')
        self.send_error(404)
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length','0')));state=self.server.state;state['calls'].append(('POST',self.path))
        if self.path=='/prompt':state['submitted']=True;return self.reply({'prompt_id':'fixture'})
        if self.path=='/_qa/finish':state['finish']=True;return self.reply({'ok':True})
        if self.path in {'/queue','/interrupt','/free'}:state['unexpected'].append(self.path);return self.reply({})
        self.send_error(404)

def sign_bundle(app):
    subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True,capture_output=True)
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True,capture_output=True)

def scenario(source,destination,tools,account,bad_feed,test_key_file=None):
    destination.mkdir(parents=True,exist_ok=False)
    installed=destination/'Installed/Sprintwake Studios.app';installed.parent.mkdir();shutil.copytree(source,installed,symlinks=True)
    # The input may be a sealed read-only qualification bundle. Only this
    # disposable scenario copy becomes writable for its synthetic metadata.
    for entry in (installed,*installed.rglob('*')):
        if not entry.is_symlink(): entry.chmod(entry.stat().st_mode|0o200)
    assets=destination/'feed';assets.mkdir()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);url=f'http://127.0.0.1:{server.server_port}'
    video=destination/'fixture.mp4';ffmpeg=installed/'Contents/Resources/Studio/engine/local/bin/ffmpeg'
    subprocess.run([str(ffmpeg),'-v','error','-y','-f','lavfi','-i','testsrc2=size=128x128:rate=24','-frames:v','24',str(video)],check=True,timeout=20)
    server.state={'finish':False,'calls':[],'unexpected':[],'assets':assets,'video':video.read_bytes()}
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    proc=None
    try:
        public=Path(str(test_key_file)+'.public').read_text().strip() if test_key_file else subprocess.run([str(tools/'generate_keys'),'--account',account,'-p'],check=True,capture_output=True,text=True).stdout.strip()
        ident='no.oddmund.storyboard.raven.qa.'+uuid.uuid4().hex
        info_path=installed/'Contents/Info.plist';info=plistlib.loads(info_path.read_bytes());old_build=int(info['CFBundleVersion']);target_build=old_build+1
        info.update(CFBundleIdentifier=ident,SUFeedURL=url+'/appcast.xml',SUPublicEDKey=public,SUEnableAutomaticChecks=False,SprintwakeUpdateTest=True,SprintwakeQARoot=str(destination),NSAppTransportSecurity={'NSAllowsArbitraryLoads':True})
        info_path.write_bytes(plistlib.dumps(info));sign_bundle(installed)
        payload=destination/'payload/Sprintwake Studios.app';payload.parent.mkdir();shutil.copytree(installed,payload,symlinks=True)
        updated=copy.deepcopy(info);updated['CFBundleVersion']=str(target_build);updated['CFBundleShortVersionString']='0.9.1';(payload/'Contents/Info.plist').write_bytes(plistlib.dumps(updated))
        metadata=payload/'Contents/Resources/release.json';release=json.loads(metadata.read_text());release.update(version='0.9.1',build=target_build,publicKey=public);metadata.write_text(json.dumps(release));sign_bundle(payload)
        prepare_release(payload,assets,tools=tools,account=account,asset_base=url,test_fixture=True,test_key_file=test_key_file)
        if bad_feed:
            # Sparkle permits trailing bytes outside the signed feed length.
            # Change bytes inside the signed XML body to test real tampering.
            feed=assets/'appcast.xml';original=feed.read_bytes()
            changed=original.replace(b'<title>Sprintwake Studios</title>',b'<title>Sprintwake Altered</title>',1)
            assert changed!=original,'Signed feed title was not found'
            feed.write_bytes(changed)
            key_args=['--ed-key-file',str(test_key_file)] if test_key_file else ['--account',account]
            verified=subprocess.run([str(tools/'sign_update'),*key_args,'--verify',str(feed)],capture_output=True)
            assert verified.returncode!=0,'Negative fixture must have an invalid signature'
        profile={'version':1,'id':'qa-video','label':'Synthetic QA video','kind':'video','workflow':{'1':{'class_type':'FakeVideo','inputs':{'text':''}}},'bindings':{'prompt':{'node':'1','input':'text'}},'output':{'node':'1','key':'videos'},'capabilities':{'fps':24,'frameRule':{'kind':'any','minimum':24,'maximum':24},'maxFrames':24,'sizeAlign':1,'resolutions':['128x128']}}
        doc=defaults();doc['paths']={'root':str(destination/'Productions'),'engine':str(installed/'Contents/Resources/Studio/engine'),'python':str(installed/'Contents/Resources/Python/bin/python3.12'),'workspace':str(destination/'Work')};doc['comfyUrl']=url;doc['selected']={'llm':'none','image':'none','video':'qa-video'};doc['workflowProfiles']=[profile]
        ConnectionStore(destination/'connections.json').save(validate(doc))
        (destination/'scenario.json').write_text(json.dumps({'targetBuild':target_build,'provider':url,'badFeed':bad_feed}))
        env=os.environ.copy()
        for name in ('PYTHONHOME','PYTHONPATH','RAVEN_CONFIG','SPRINTWAKE_APP_BUNDLE','SPRINTWAKE_QA_ROOT','SPRINTWAKE_BUNDLED_PYTHON'):env.pop(name,None)
        with (destination/'launch.log').open('wb') as log:
            proc=subprocess.Popen([str(installed/'Contents/MacOS/Sprintwake Studios'),'--update-smoke'],cwd='/',env=env,stdout=log,stderr=log)
            deadline=time.monotonic()+360
            while time.monotonic()<deadline and not (destination/'result.json').exists():
                if (destination/'failure.json').exists():raise AssertionError((destination/'failure.json').read_text())
                if proc.poll() is not None and bad_feed:break
                time.sleep(.25)
            if not (destination/'result.json').exists():raise AssertionError('Sparkle scenario failed: '+str(destination))
        result=json.loads((destination/'result.json').read_text());assert not server.state['unexpected'],server.state['unexpected']
        assert result.get('rejected') if bad_feed else result.get('updated'),result
        print(json.dumps(result));return result
    finally:
        server.state['finish']=True
        if proc and proc.poll() is None:
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=5)
        server.shutdown();server.server_close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--account',help='Optional existing Keychain signing account; default uses a disposable test key');p.add_argument('--scenario',choices=('both','valid','tampered'),default='both');a=p.parse_args()
    tools=Path(__file__).resolve().parents[1]/'runtime/Sparkle/bin'
    with tempfile.TemporaryDirectory(prefix='sprintwake-test-key-') as key_root:
        key_file=None
        if not a.account:
            key_file=Path(key_root)/'test-key'
            subprocess.run(['xcrun','swift',str(Path(__file__).with_name('create-test-key.swift')),str(key_file)],check=True,capture_output=True)
        if a.scenario in {'both','valid'}:scenario(a.bundle.resolve(),a.output.resolve()/'valid',tools,a.account,False,key_file)
        if a.scenario in {'both','tampered'}:scenario(a.bundle.resolve(),a.output.resolve()/'tampered',tools,a.account,True,key_file)

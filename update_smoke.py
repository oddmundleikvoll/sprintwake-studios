"""Real Sparkle update fixture, enabled only in an isolated test bundle."""
from __future__ import annotations
import hashlib,json,os,time
from concurrent.futures import Future
from pathlib import Path
import raven as r

def run(studio,root):
    root=Path(root);scenario=root/'scenario.json';spec=json.loads(scenario.read_text())
    last_status=[None]
    def pump():
        if not r.Rwait_frame(1/60):raise AssertionError('QA window closed')
        studio.tick()
        state=studio.updater.status()
        if state!=last_status[0]:
            (root/'updater-status.json').write_text(json.dumps(state));last_status[0]=state
    def wait(check,message,seconds=60):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline and not check():pump()
        assert check(),message
    wait(lambda:studio.ready,'Bundled engine did not start')
    current=int(os.environ['SPRINTWAKE_APP_BUILD'])
    if current>=spec['targetBuild']:
        for relative,digest in spec['fingerprints'].items():assert hashlib.sha256((root/relative).read_bytes()).hexdigest()==digest,relative
        assert str(studio.backend.engine_root).startswith(os.environ['SPRINTWAKE_APP_BUNDLE'])
        return {'updated':True,'build':current,'productionWaited':spec['productionWaited'],'filesPreserved':len(spec['fingerprints']),'modelsStarted':False}
    created=studio.backend.post('/api/boards',{'name':'Update preservation fixture'});slug=created['slug'];board=created['board']
    board['shots']=[{'id':'s1','title':'Fixture','prompt':'A synthetic test card','frames':24,'resolution':'128x128','seed':2}]
    studio.backend.put('/api/boards/'+slug,board)
    studio.backend.post('/api/render-seeds',{'slug':slug,'shotId':'s1','count':1})
    wait(lambda:studio.backend.get('/api/status')['busy'],'Synthetic workflow did not start')
    (root/'progress.json').write_text(json.dumps({'phase':'choose-update','build':current}))
    studio.updater.check();deadline=time.monotonic()+180
    if spec.get('badFeed'):
        wait(lambda:studio.updater.status()['stage']=='error','Tampered feed was not rejected')
        assert not studio.update_waiting and studio.backend.get('/api/status')['busy']
        from urllib.request import Request,urlopen
        with urlopen(Request(spec['provider']+'/_qa/finish',data=b'{}',headers={'Content-Type':'application/json'}),timeout=5):pass
        wait(lambda:not studio.backend.get('/api/status')['busy'],'Synthetic job did not drain')
        return {'rejected':True,'productionStillActive':True,'build':current,'modelsStarted':False}
    while time.monotonic()<deadline:
        pump()
        if studio.update_waiting:break
    assert studio.update_waiting,studio.updater.status()
    (root/'progress.json').write_text(json.dumps({'phase':'waiting-for-production','build':current}))
    for _ in range(25):pump()
    assert studio.backend.process.poll() is None and studio.backend.get('/api/status')['busy']
    assert not studio.backend.get('/api/desktop/update-status')['quiesced']
    # Keep native admission occupied while the completed take and preservation
    # evidence are filed, without canceling the real synthetic backend job.
    studio.pending['qa.finish']=Future()
    from urllib.request import Request,urlopen
    with urlopen(Request(spec['provider']+'/_qa/finish',data=b'{}',headers={'Content-Type':'application/json'}),timeout=5):pass
    wait(lambda:not studio.backend.get('/api/status')['busy'],'Synthetic video did not finish')
    takes=studio.backend.get(f'/api/boards/{slug}/shots/s1/seed-takes')['takes'];assert takes[0]['status'] in {'done','review'},takes
    studio.backend.post(f"/api/boards/{slug}/shots/s1/seed-takes/{takes[0]['seed']}/adopt",{})
    paths=list((studio.workspace.projects_dir/slug).rglob('*'))+[studio.connections.path]
    fingerprints={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}
    spec.update(fingerprints=fingerprints,productionWaited=True,phase='installing');scenario.write_text(json.dumps(spec))
    studio.pending.pop('qa.finish')
    wait(lambda:studio.update_phase=='installing','Update was not handed to Sparkle',seconds=60)
    # Sparkle terminates this process and relaunches the updated test bundle.
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:pump()
    raise AssertionError('Sparkle did not terminate and relaunch the application')

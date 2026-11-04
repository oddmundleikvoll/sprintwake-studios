"""Explicit Comfy API profiles, bounded transport and recoverable owned jobs."""
from __future__ import annotations
import copy, hashlib, json, os, re, shutil, tempfile, time, uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

PROFILE_VERSION=1
_ID=re.compile(r'^[A-Za-z0-9_.-]{1,80}$')
_BINDINGS={'prompt','seed','width','height','frames','fps','start_image','end_image',*(f'reference_{i}' for i in range(1,11))}
_SECRETS={'apikey','xapikey','token','authorization','proxyauthorization','credential','credentials','secret','password','bearertoken','accesstoken','refreshtoken','authtoken','clientsecret'}
class ComfyWorkflowError(RuntimeError):pass
class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def _require(ok,message):
    if not ok:raise ValueError(message)

def _check_value(value,depth=0):
    _require(depth<30,'profile nesting exceeds limit')
    if isinstance(value,dict):
        for key,item in value.items():
            _require(re.sub(r'[^a-z0-9]','',str(key).lower()) not in _SECRETS,'credential fields are not allowed in workflow profiles')
            _check_value(item,depth+1)
    elif isinstance(value,list):
        for item in value:_check_value(item,depth+1)

def validate_profile(profile):
    _require(isinstance(profile,dict),'profile must be an object');_check_value(profile)
    _require(set(profile)=={'version','id','label','kind','workflow','bindings','output','capabilities'},'unknown or missing profile fields')
    _require(type(profile['version']) is int and profile['version']==1,'unsupported workflow profile version')
    _require(isinstance(profile['id'],str) and _ID.fullmatch(profile['id']) and profile['id'] not in {'.','..'},'invalid profile id')
    _require(isinstance(profile['label'],str) and 0<len(profile['label'])<=300,'invalid profile label')
    _require(isinstance(profile['kind'],str) and profile['kind'] in {'image','video'},'kind must be image or video')
    graph=profile['workflow'];_require(isinstance(graph,dict) and 0<len(graph)<=256,'workflow needs 1..256 API nodes')
    for ident,node in graph.items():
        _require(isinstance(ident,str) and _ID.fullmatch(ident) and isinstance(node,dict) and set(node)<={'class_type','inputs','_meta'},'invalid API node')
        _require(isinstance(node.get('class_type'),str) and 0<len(node['class_type'])<=128 and isinstance(node.get('inputs'),dict),'invalid API node')
    bindings=profile['bindings'];_require(isinstance(bindings,dict) and 'prompt' in bindings,'prompt binding is required')
    seen=set()
    for name,item in bindings.items():
        _require(name in _BINDINGS and isinstance(item,dict) and set(item)=={'node','input'},'invalid binding')
        _require(isinstance(item['node'],str) and item['node'] in graph and isinstance(item['input'],str) and item['input'] in graph[item['node']]['inputs'],'binding input does not exist: '+name)
        target=(item['node'],item['input']);_require(target not in seen,'bindings must target different inputs');seen.add(target)
    output=profile['output'];_require(isinstance(output,dict) and set(output)=={'node','key'} and isinstance(output['node'],str) and output['node'] in graph and isinstance(output['key'],str) and output['key'] in {'images','gifs','videos'},'invalid output declaration')
    caps=profile['capabilities'];_require(isinstance(caps,dict),'capabilities required')
    allowed={'fps','frameRule','maxFrames','sizeAlign','resolutions','defaultSteps','supportsAudio','supportsStartAnchor','supportsEndAnchor','supportsReferences','maxReferences'}
    _require(set(caps)<=allowed,'unknown capabilities')
    for name in ('supportsAudio','supportsStartAnchor','supportsEndAnchor','supportsReferences'):
        _require(type(caps.get(name,False)) is bool,'capability must be boolean: '+name)
    for name,binding in [('supportsStartAnchor','start_image'),('supportsEndAnchor','end_image')]:
        _require(not caps.get(name) or binding in bindings,name+' requires binding')
    refs=caps.get('maxReferences',0);_require(type(refs) is int and 0<=refs<=10,'maxReferences must be 0..10')
    _require(all(f'reference_{i}' in bindings for i in range(1,refs+1)),'each declared reference requires a binding')
    _require(not any(f'reference_{i}' in bindings for i in range(refs+1,11)),'reference binding exceeds maxReferences')
    align=caps.get('sizeAlign',1);_require(type(align) is int and 1<=align<=256,'invalid sizeAlign')
    resolutions=caps.get('resolutions',[]);_require(isinstance(resolutions,list) and len(resolutions)<=30,'invalid resolutions')
    for value in resolutions:
        _require(isinstance(value,str) and re.fullmatch(r'[1-9][0-9]{1,3}x[1-9][0-9]{1,3}',value),'invalid resolution')
        _require(all(64<=int(n)<=4096 and int(n)%align==0 for n in value.split('x')),'resolution violates sizeAlign')
    if 'defaultSteps' in caps:_require(type(caps['defaultSteps']) is int and 1<=caps['defaultSteps']<=1000,'invalid defaultSteps')
    if profile['kind']=='video':
        _require(type(caps.get('fps')) is int and caps['fps']==24,'video profile requires explicit 24 fps')
        maximum=caps.get('maxFrames');_require(type(maximum) is int and 1<=maximum<=14400,'invalid maxFrames')
        rule=caps.get('frameRule');_require(isinstance(rule,dict) and set(rule)<={'kind','step','offset','minimum','maximum','note'},'invalid frameRule')
        _require(isinstance(rule.get('kind'),str) and rule['kind'] in {'any','affine'},'invalid frame rule kind')
        for name,default,low,high in [('step',1,1,1024),('offset',0,0,1023),('minimum',1,1,maximum),('maximum',maximum,1,maximum)]:
            _require(type(rule.get(name,default)) is int and low<=rule.get(name,default)<=high,'invalid frameRule '+name)
        _require(rule.get('offset',0)<rule.get('step',1) and rule.get('minimum',1)<=rule.get('maximum',maximum),'invalid frame grid')
    raw=json.dumps(profile,allow_nan=False,ensure_ascii=False)
    _require(len(raw.encode())<=4_000_000,'profile exceeds limit')
    return copy.deepcopy(profile)

def profile_digest(profile):return hashlib.sha256(json.dumps(validate_profile(profile),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def _atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.raven-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as handle:json.dump(data,handle,ensure_ascii=False,indent=2);handle.flush();os.fsync(handle.fileno())
        os.replace(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)

def publish_pair(media_stage,recipe_stage,target):
    """Publish verified media/recipe together, retaining prior bytes on failure."""
    target=Path(target);recipe=target.with_name('recipe.json')
    prior=[path for path in (target,recipe) if path.exists()]
    archive=None
    if prior:
        archive=target.parent/'workflow-takes'/uuid.uuid4().hex;archive.mkdir(parents=True)
        for old in prior:shutil.copy2(old,archive/old.name)
    committed=[]
    try:
        os.replace(media_stage,target);committed.append(target)
        if recipe_stage is not None:os.replace(recipe_stage,recipe)
        else:recipe.unlink(missing_ok=True)
    except Exception:
        for changed in committed:
            saved=archive/changed.name if archive else None
            if saved and saved.exists():os.replace(saved,changed)
            else:changed.unlink(missing_ok=True)
        raise

@dataclass
class WorkflowOutcome:
    outputs:list[Path]=field(default_factory=list)
    prompt_id:str=''
    workflow_digest:str=''
    cancelled:bool=False
    compiled_workflow:dict=field(default_factory=dict)

class WorkflowClient:
    def __init__(self,base_url,api_key='',timeout=30,deadline=1200,state_dir=None):
        parsed=urlsplit(str(base_url));_require(parsed.scheme in {'http','https'} and parsed.hostname and not(parsed.username or parsed.password or parsed.query or parsed.fragment),'invalid Comfy URL')
        self.base_url=str(base_url).rstrip('/');self.api_key=api_key;self.timeout=float(timeout);self.deadline=float(deadline)
        self.state_dir=Path(state_dir) if state_dir else None;self.pending=None;self.unhealthy=False
    def _open(self,method,path,data=None,headers=None):
        h={'Accept':'application/json',**(headers or {})}
        if self.api_key:h['Authorization']='Bearer '+self.api_key
        body=data if isinstance(data,bytes) else json.dumps(data).encode() if data is not None else None
        if body is not None:h.setdefault('Content-Type','application/json')
        try:return build_opener(_NoRedirect).open(Request(self.base_url+path,data=body,headers=h,method=method),timeout=self.timeout)
        except Exception as exc:
            if isinstance(exc,HTTPError) and method=='POST' and path=='/prompt' and 400<=exc.code<500:
                self._save_pending(None)
            elif self.pending:self.unhealthy=True
            text=str(exc).replace(self.api_key,'[redacted]') if self.api_key else str(exc)
            raise ComfyWorkflowError(text) from None
    def _request(self,method,path,data=None,headers=None):
        with self._open(method,path,data,headers) as response:raw=response.read(8_000_001)
        if len(raw)>8_000_000:raise ComfyWorkflowError('metadata exceeds limit')
        return json.loads(raw) if raw else {}
    def preflight(self,profile):
        p=validate_profile(profile);info=self._request('GET','/object_info');queue=self._request('GET','/queue');missing=[]
        _require(isinstance(info,dict) and isinstance(queue,dict) and all(isinstance(queue.get(k),list) for k in ('queue_running','queue_pending')),'invalid Comfy metadata')
        for node in p['workflow'].values():
            cls=node['class_type']
            if cls not in info:missing.append(cls);continue
            schema=info[cls].get('input',{})
            declared={**schema.get('required',{}),**schema.get('optional',{})}
            if declared:
                for name,value in node['inputs'].items():
                    if name not in declared:missing.append(cls+'.'+name);continue
                    choices=declared[name][0] if isinstance(declared[name],list) and declared[name] else None
                    if isinstance(choices,list) and not(isinstance(value,list) and len(value)==2) and value not in choices:missing.append(cls+'.'+name+' model/value')
        return {'ok':not missing,'profileId':p['id'],'workflowDigest':profile_digest(p),'missingNodes':list(dict.fromkeys(missing)),'queue':queue}
    def _journal(self):
        if self.state_dir is None:return None
        return self.state_dir/(hashlib.sha256(self.base_url.encode()).hexdigest()[:24]+'.json')
    @property
    def uncertain_path(self):return self._journal()
    def _save_pending(self,value):
        self.pending=value;path=self._journal()
        if path:
            if value:_atomic(path,value)
            else:path.unlink(missing_ok=True)
    @staticmethod
    def _terminal(record):
        status=(record or {}).get('status') or {}
        return status.get('completed') or status.get('status_str') in {'success','error','failed'}
    def assert_released(self):
        path=self._journal()
        pending=json.loads(path.read_text()) if path and path.exists() else self.pending
        if not pending:self.unhealthy=False;return
        ident=pending.get('promptId')
        if not ident and pending.get('clientId'):
            # Comfy stores client_id in prompt extra_data. Recover the owned ID
            # after a lost POST response using read-only queue/history metadata.
            try:
                queue=self._request('GET','/queue')
                records=self._request('GET','/history?max_items=100')
                candidates=[row for key in ('queue_running','queue_pending') for row in queue.get(key,[])]
                candidates += [rec.get('prompt') for rec in records.values() if isinstance(rec,dict)]
                for row in candidates:
                    if isinstance(row,list) and len(row)>3 and isinstance(row[3],dict) and row[3].get('client_id')==pending['clientId']:
                        ident=row[1];self._save_pending({**pending,'promptId':ident});break
            except Exception:pass
        if ident:
            try:
                record=self._request('GET','/history/'+ident).get(ident)
                if self._terminal(record):self._save_pending(None);self.unhealthy=False;return
            except Exception:pass
        self.unhealthy=True
        raise ComfyWorkflowError('Previous Comfy job is unresolved; wait for its history before retrying. No new job was submitted.')
    def _upload(self,path):
        path=Path(path).expanduser().resolve();_require(path.is_file() and path.stat().st_size<=32*1024*1024,'invalid image reference')
        from PIL import Image
        with Image.open(path) as image:image.verify()
        boundary='Raven'+uuid.uuid4().hex
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="reference{path.suffix}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+path.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
        result=self._request('POST','/upload/image',body,{'Content-Type':'multipart/form-data; boundary='+boundary})
        name=result.get('name');_require(isinstance(name,str) and name and '/' not in name and '\\' not in name,'invalid uploaded image name')
        return name
    def run(self,profile,values,output_dir,should_cancel=lambda:False,on_progress=lambda *_:None):
        p=validate_profile(profile);values=dict(values)
        if should_cancel():return WorkflowOutcome(cancelled=True)
        out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
        if self.state_dir is None:self.state_dir=out/'.comfy-jobs'
        self.assert_released()
        _require(set(values)<=set(p['bindings']),'unknown workflow values')
        _require(isinstance(values.get('prompt'),str) and values['prompt'].strip(),'prompt value is required')
        if should_cancel():return WorkflowOutcome(cancelled=True)
        ready=self.preflight(p)
        if not ready['ok']:raise ComfyWorkflowError('Missing workflow inputs/models: '+', '.join(ready['missingNodes']))
        graph=copy.deepcopy(p['workflow'])
        for name,value in values.items():
            if name.endswith('_image') or name.startswith('reference_'):value=self._upload(value)
            binding=p['bindings'][name];graph[binding['node']]['inputs'][binding['input']]=value
        if should_cancel():return WorkflowOutcome(cancelled=True)
        client_id=uuid.uuid4().hex;self._save_pending({'clientId':client_id,'promptId':None})
        response=self._request('POST','/prompt',{'prompt':graph,'client_id':client_id});ident=response.get('prompt_id')
        if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',ident):raise ComfyWorkflowError('Comfy returned no valid prompt ID; admission remains unresolved')
        self._save_pending({'clientId':client_id,'promptId':ident});end=time.monotonic()+self.deadline;cancelled=False;deleted=False;record=None
        while time.monotonic()<end:
            cancelled=cancelled or bool(should_cancel())
            if cancelled and not deleted:self._request('POST','/queue',{'delete':[ident]});deleted=True
            history=self._request('GET','/history/'+ident);record=history.get(ident)
            if self._terminal(record):break
            if cancelled and record is None:
                queue=self._request('GET','/queue')
                _require(all(isinstance(queue.get(k),list) for k in ('queue_running','queue_pending')),'invalid queue metadata; admission remains unresolved')
                ids={str(row[1]) for key in ('queue_running','queue_pending') for row in queue.get(key,[]) if isinstance(row,list) and len(row)>1}
                if ident not in ids:self._save_pending(None);return WorkflowOutcome(prompt_id=ident,cancelled=True)
            on_progress({'prompt_id':ident,'status':'stopping' if cancelled else 'running'});time.sleep(.1)
        if not self._terminal(record):
            self.unhealthy=True;raise ComfyWorkflowError('workflow cancellation/deadline timed out; admission remains unresolved')
        self._save_pending(None)
        if cancelled:return WorkflowOutcome(prompt_id=ident,cancelled=True)
        if record.get('status',{}).get('status_str') in {'error','failed'}:raise ComfyWorkflowError('Comfy workflow failed')
        entries=record.get('outputs',{}).get(p['output']['node'],{}).get(p['output']['key'],[])
        if not isinstance(entries,list) or len(entries)!=1:raise ComfyWorkflowError('workflow must return exactly one declared output')
        item=entries[0];name=item.get('filename','');sub=item.get('subfolder','');kind=item.get('type','output')
        _require(isinstance(name,str) and name and '/' not in name and '\\' not in name and name not in {'.','..'},'unsafe output filename')
        _require(isinstance(sub,str) and not Path(sub).is_absolute() and '..' not in Path(sub).parts and '\\' not in sub and kind=='output','unsafe output location')
        suffix=Path(name).suffix.lower();_require(suffix in ({'.png','.jpg','.jpeg','.webp'} if p['kind']=='image' else {'.mp4'}),'unsupported output format (video requires MP4)')
        fd,tmp=tempfile.mkstemp(prefix='.download-',suffix=suffix,dir=out);os.close(fd);temporary=Path(tmp)
        try:
            total=0
            with self._open('GET','/view?'+urlencode({'filename':name,'subfolder':sub,'type':kind})) as response, temporary.open('wb') as handle:
                while chunk:=response.read(1024*1024):
                    total+=len(chunk)
                    if total>(128*1024*1024 if p['kind']=='image' else 4*1024*1024*1024):raise ComfyWorkflowError('media exceeds limit')
                    handle.write(chunk)
                handle.flush();os.fsync(handle.fileno())
            if p['kind']=='image':
                from PIL import Image
                with Image.open(temporary) as image:
                    _require(max(image.width,image.height)<=4096,'image exceeds geometry limit');image.load()
            target=out/name
            if target.exists():raise ComfyWorkflowError('refusing to overwrite existing downloaded output')
            os.replace(temporary,target)
            return WorkflowOutcome([target],ident,profile_digest(p),False,graph)
        except (OSError,ValueError) as exc:
            raise ComfyWorkflowError('Invalid workflow output: '+type(exc).__name__) from None
        finally:temporary.unlink(missing_ok=True)

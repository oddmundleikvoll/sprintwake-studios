# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Explicit Comfy workflows with durable per-endpoint admission and scoped drain.

A lost acknowledgement is never retried as another prompt. An unresolved journal
requires positive queue/history evidence before the endpoint can admit more work.
"""
from __future__ import annotations
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid

from .store import atomic_json, read_json, safe_path

BINDINGS = frozenset('prompt seed width height frames fps start_image end_image'.split()) | {f'reference_{i}' for i in range(1,11)}

class AdmissionUncertain(RuntimeError): pass
class GenerationCancelled(RuntimeError): pass
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def _integer(value, low, high): return type(value) is int and low <= value <= high

def validate_profile(profile):
    if not isinstance(profile,dict) or type(profile.get('version')) is not int or profile['version']!=1:
        raise ValueError('Expected a version-1 workflow profile')
    allowed={'version','id','label','kind','workflow','bindings','output','capabilities'}
    if set(profile)-allowed or not (allowed-{'capabilities'}).issubset(profile):
        raise ValueError('Unexpected or missing workflow profile fields')
    p=copy.deepcopy(profile)
    if not isinstance(p['id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}',p['id']): raise ValueError('Invalid workflow profile ID')
    if not isinstance(p['label'],str) or not p['label'].strip() or len(p['label'])>200: raise ValueError('A workflow label is required')
    if p['kind'] not in {'image','video'}: raise ValueError('Expected image or video workflow')
    graph=p['workflow']
    if not isinstance(graph,dict) or not 1<=len(graph)<=1000: raise ValueError('Expected a bounded API-format graph')
    for ident,node in graph.items():
        if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',ident): raise ValueError('Invalid workflow node ID')
        if not isinstance(node,dict) or not isinstance(node.get('class_type'),str) or not node['class_type'] or not isinstance(node.get('inputs'),dict):
            raise ValueError('A workflow node requires class_type and inputs')
        if set(node)-{'class_type','inputs','_meta'}: raise ValueError('UI-format or unknown node fields are unsupported')
    def secrets(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key.lower().replace('_','') in {'apikey','authorization','accesstoken','password'} and item:
                    raise ValueError('Workflow credentials belong in Keychain or the provider')
                secrets(item)
        elif isinstance(value,list):
            for item in value: secrets(item)
    secrets(graph)
    bindings=p['bindings']; occupied=set()
    if not isinstance(bindings,dict) or not bindings or 'prompt' not in bindings or set(bindings)-BINDINGS:
        raise ValueError('Declare supported workflow bindings including prompt')
    for name,binding in bindings.items():
        if not isinstance(binding,dict) or set(binding)!={'node','input'}: raise ValueError('A binding needs node and input')
        node,key=binding['node'],binding['input']
        if node not in graph or not isinstance(key,str) or key not in graph[node]['inputs']:
            raise ValueError('Binding input is absent from the graph')
        if (node,key) in occupied: raise ValueError('Two values cannot bind the same input')
        occupied.add((node,key))
    output=p['output']
    if not isinstance(output,dict) or set(output)!={'node','key'} or output['node'] not in graph:
        raise ValueError('Declare one output node and key')
    if output['key'] not in ({'images'} if p['kind']=='image' else {'videos','gifs','images'}): raise ValueError('Output kind does not match the workflow')
    caps=p.setdefault('capabilities',{})
    if not isinstance(caps,dict): raise ValueError('Capabilities must be an object')
    caps.setdefault('sizeAlign',1)
    if not _integer(caps['sizeAlign'],1,4096): raise ValueError('Invalid size alignment')
    if 'maxReferences' in caps and not _integer(caps['maxReferences'],0,10): raise ValueError('Invalid reference limit')
    for flag in ('supportsStartAnchor','supportsEndAnchor','supportsAudio'):
        if flag in caps and type(caps[flag]) is not bool: raise ValueError('Invalid capability flag')
    if 'resolutions' in caps:
        if not isinstance(caps['resolutions'],list) or len(caps['resolutions'])>100: raise ValueError('Invalid resolution list')
        for resolution in caps['resolutions']:
            if not isinstance(resolution,str) or not re.fullmatch(r'\d{2,4}x\d{2,4}',resolution): raise ValueError('Invalid resolution')
            if any(not 64<=int(n)<=4096 or int(n)%caps['sizeAlign'] for n in resolution.split('x')): raise ValueError('Resolution violates alignment')
    if p['kind']=='video':
        if caps.get('fps')!=24 or type(caps.get('fps')) is bool or not _integer(caps.get('maxFrames'),1,86400): raise ValueError('Video needs 24 fps and maxFrames')
        rule=caps.setdefault('frameRule',{'kind':'any'})
        if not isinstance(rule,dict) or set(rule)-{'kind','step','offset','minimum','maximum'} or rule.get('kind') not in {'any','affine'}: raise ValueError('Invalid frame rule')
        rule.setdefault('step',1); rule.setdefault('offset',0); rule.setdefault('minimum',1)
        if not _integer(rule['step'],1,86400) or not _integer(rule['offset'],0,rule['step']-1) or not _integer(rule['minimum'],1,caps['maxFrames']): raise ValueError('Invalid frame grid')
        if 'maximum' in rule and not _integer(rule['maximum'],rule['minimum'],caps['maxFrames']): raise ValueError('Invalid maximum frame count')
    if len(json.dumps(p,allow_nan=False).encode())>8_000_000: raise ValueError('Workflow profile is too large')
    return p


def profile_digest(profile):
    return hashlib.sha256(json.dumps(validate_profile(profile),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


class WorkflowClient:
    def __init__(self,url,api_key='',timeout=15,work_dir=None,state_dir=None):
        parts=urlsplit(str(url))
        if parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('Choose an HTTP(S) endpoint without embedded credentials')
        self.url=str(url).rstrip('/'); self.api_key=api_key; self.timeout=max(.1,min(float(timeout),120))
        self.work_dir=Path(work_dir or tempfile.gettempdir())/'sprintwake-workflows'
        self.state_dir=Path(state_dir or self.work_dir/'State')
        self.state_dir.mkdir(parents=True,exist_ok=True)
        self._guard=threading.RLock(); self.opener=build_opener(NoRedirect)
        self._key=hashlib.sha256(self.url.encode()).hexdigest()[:32]
        self._journal=safe_path(self.state_dir,self._key+'.json')
        self._lock=safe_path(self.state_dir,self._key+'.lock')
    @contextmanager
    def _transaction(self):
        with self._guard:
            fd=os.open(self._lock,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
            try: fcntl.flock(fd,fcntl.LOCK_EX); yield
            finally: fcntl.flock(fd,fcntl.LOCK_UN); os.close(fd)
    def _raw(self,method,path,body=None,content_type='application/json',limit=8_000_000):
        if not path.startswith('/') or path.startswith('//'): raise ValueError('Provider path must be relative to its endpoint')
        headers={'Accept':'application/json'}
        if self.api_key: headers['Authorization']='Bearer '+self.api_key
        if body is not None: headers['Content-Type']=content_type
        request=Request(self.url+path,data=body,headers=headers,method=method)
        try:
            with self.opener.open(request,timeout=self.timeout) as response:
                raw=response.read(limit+1)
                if len(raw)>limit: raise RuntimeError('Provider response exceeds the supported limit')
                return raw
        except HTTPError as error: raise RuntimeError(f'Comfy endpoint returned HTTP {error.code}') from error
        except URLError as error: raise RuntimeError('Comfy endpoint could not be reached') from error
    def _request(self,method,path,body=None,timeout=None):
        raw=self._raw(method,path,None if body is None else json.dumps(body,allow_nan=False).encode())
        try: return json.loads(raw)
        except ValueError as error: raise RuntimeError('Comfy endpoint returned invalid JSON') from error
    def preflight(self,profile):
        profile=validate_profile(profile); info=self._request('GET','/object_info'); queue=self._request('GET','/queue')
        if not isinstance(info,dict) or not isinstance(queue,dict): raise ValueError('Invalid Comfy metadata')
        missing=sorted({n['class_type'] for n in profile['workflow'].values()}-set(info))
        return {'ok':not missing,'missingNodes':missing,'profileDigest':profile_digest(profile),'objectInfo':info,'queue':queue}
    def _records(self):
        try: records=read_json(self._journal,{})
        except (ValueError,OSError) as error: raise AdmissionUncertain('Provider admission journal is unreadable; inspect the endpoint before recovery') from error
        if not isinstance(records,dict) or any(not isinstance(v,dict) or v.get('state') not in {'uncertain','active','terminal'} for v in records.values()):
            raise AdmissionUncertain('Provider admission journal has an unknown format')
        return records
    def has_unresolved(self):
        return any(v['state']!='terminal' for v in self._records().values())
    def _terminal(self,record):
        status=record.get('status',{}) if isinstance(record,dict) else {}
        return status.get('completed') is True or status.get('status_str') in {'success','error','interrupted'}
    def _recover(self,records):
        unresolved={k:v for k,v in records.items() if v['state']!='terminal'}
        if not unresolved: return records
        queue=self._request('GET','/queue')
        history=None
        for client,entry in unresolved.items():
            ident=entry.get('promptId')
            if not ident:
                for queued in queue.get('queue_running',[])+queue.get('queue_pending',[]):
                    if isinstance(queued,list) and len(queued)>3 and isinstance(queued[3],dict) and queued[3].get('client_id')==client:
                        ident=entry['promptId']=queued[1]
                if not ident:
                    if history is None: history=self._request('GET','/history?max_items=1000')
                    for found,record in history.items():
                        prompt=record.get('prompt',[]) if isinstance(record,dict) else []
                        if len(prompt)>3 and isinstance(prompt[3],dict) and prompt[3].get('client_id')==client:
                            ident=entry['promptId']=found; break
            if ident:
                record=self._request('GET','/history/'+quote(str(ident),safe='')).get(ident)
                if self._terminal(record): entry['state']='terminal'
                else: entry['state']='active'
        atomic_json(self._journal,records)
        return records
    def recover(self):
        with self._transaction():
            return copy.deepcopy(self._recover(self._records()))
    def submit(self,prompt,client_id=None):
        with self._transaction():
            records=self._recover(self._records())
            if any(e['state']!='terminal' for e in records.values()): raise AdmissionUncertain('An earlier prompt is unresolved; no new prompt was submitted')
            queue=self._request('GET','/queue')
            if queue.get('queue_running') or queue.get('queue_pending'): raise RuntimeError('Comfy has existing work; wait until its queue drains')
            client_id=client_id or uuid.uuid4().hex
            if client_id in records: raise ValueError('A provider client ID cannot be reused')
            records[client_id]={'state':'uncertain','createdAt':time.time()}
            atomic_json(self._journal,records)
            # No retry here: a disconnected POST may already be executing remotely.
            result=self._request('POST','/prompt',{'prompt':prompt,'client_id':client_id})
            ident=result.get('prompt_id') if isinstance(result,dict) else None
            if not isinstance(ident,str) or not ident: raise AdmissionUncertain('Provider acknowledgement omitted its prompt ID')
            records[client_id].update(state='active',promptId=ident)
            atomic_json(self._journal,records)
            return ident,client_id
    def _owned(self,ident,records):
        return next((e for e in records.values() if e.get('promptId')==ident and e['state']!='terminal'),None)
    def cancel(self,ident):
        with self._transaction():
            records=self._records()
            owner=next((client for client,entry in records.items() if entry.get('promptId')==ident and entry['state']!='terminal'),None)
            if owner is None: raise ValueError('Refusing to cancel a foreign prompt')
            try:
                receipt=self._request('POST','/sprintwake/cancel-pending',{'promptId':ident,'clientId':owner})
            except RuntimeError as error:
                if str(error).endswith(('HTTP 403','HTTP 404','HTTP 405')): return False
                raise
            if isinstance(receipt,dict) and receipt.get('promptId')==ident and receipt.get('clientId')==owner and receipt.get('cancelledBeforeStart') is True:
                records[owner].update(state='terminal',cancellation=receipt); atomic_json(self._journal,records)
                return True
            return False
    def _finish(self,ident):
        with self._transaction():
            records=self._records(); entry=self._owned(ident,records)
            if entry: entry['state']='terminal'; atomic_json(self._journal,records)
    def wait_terminal(self,ident,timeout=7200,poll=.2,cancel=lambda:False):
        deadline=time.monotonic()+timeout; cancel_sent=False; ever_running=False
        while True:
            record=self._request('GET','/history/'+quote(ident,safe='')).get(ident)
            if self._terminal(record):
                self._finish(ident)
                if cancel_sent or cancel(): raise GenerationCancelled('The cancelled provider job has drained')
                if record.get('status',{}).get('status_str') in {'error','interrupted'}: raise RuntimeError('Comfy reported a failed job')
                return record
            if cancel() and not cancel_sent:
                if self.cancel(ident): raise GenerationCancelled('The owned pending prompt was cancelled before model execution')
                cancel_sent=True
            if cancel_sent:
                queue=self._request('GET','/queue')
                running=any(isinstance(q,list) and len(q)>1 and q[1]==ident for q in queue.get('queue_running',[]))
                pending=any(isinstance(q,list) and len(q)>1 and q[1]==ident for q in queue.get('queue_pending',[]))
                ever_running |= running
                # Queue disappearance alone does not prove a running worker released its models.
                # Keep the journal blocked until terminal history confirms drain.
            if time.monotonic()>=deadline: raise AdmissionUncertain('Provider completion is unconfirmed; admission remains blocked')
            time.sleep(poll)
    def upload_image(self,source):
        from PIL import Image
        source=Path(source)
        if source.is_symlink() or not source.is_file() or source.stat().st_size>32*1024*1024: raise ValueError('Invalid reference image')
        with Image.open(source) as image: image.load()
        boundary='sprintwake-'+uuid.uuid4().hex; name=uuid.uuid4().hex+source.suffix.lower()
        pieces=[f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),source.read_bytes(),
                f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\nfalse\r\n--{boundary}--\r\n'.encode()]
        result=json.loads(self._raw('POST','/upload/image',b''.join(pieces),'multipart/form-data; boundary='+boundary))
        filename=result.get('name'); folder=result.get('subfolder','')
        if not isinstance(filename,str) or Path(filename).name!=filename or '\\' in filename or any(p in {'.','..'} for p in folder.split('/')): raise ValueError('Provider returned an unsafe uploaded filename')
        return (folder.rstrip('/')+'/' if folder else '')+filename
    def wait_result(self,ident,output_dir,timeout=7200,poll=.2,*,declaration=None,cancel=lambda:False):
        from .media import immutable_output
        record=self.wait_terminal(ident,timeout,poll,cancel)
        outputs=record.get('outputs',{})
        if declaration:
            node=outputs.get(declaration['node'],{}); values=node.get(declaration['key'],[]) if isinstance(node,dict) else []
        else:
            values=[v for node in outputs.values() if isinstance(node,dict) for key in ('images','videos','gifs') for v in node.get(key,[])]
        if not isinstance(values,list) or not values or len(values)>100: raise ValueError('Declared provider output is missing or invalid')
        directory=Path(output_dir)/uuid.uuid4().hex; directory.mkdir(parents=True)
        results=[]
        for index,value in enumerate(values):
            if not isinstance(value,dict) or not isinstance(value.get('filename'),str): raise ValueError('Invalid provider output descriptor')
            filename=value['filename']; folder=value.get('subfolder','')
            if Path(filename).name!=filename or '\\' in filename or any(p in {'.','..'} for p in folder.split('/')): raise ValueError('Unsafe provider output path')
            target=directory/(str(index)+'-'+filename)
            data=self._raw('GET','/view?'+urlencode({'filename':filename,'subfolder':folder,'type':value.get('type','output')}),limit=256*1024*1024)
            target.write_bytes(data); results.append(immutable_output(target))
        return results


class ComfyProvider(WorkflowClient):
    def generate(self,profile,prompt,seed=None,frames=None,width=None,height=None,references=None,start_image=None,end_image=None,cancel=lambda:False,on_progress=lambda _:None):
        from .media import validate_decoded
        profile=validate_profile(profile); caps=profile['capabilities']
        if not self.preflight(profile)['ok']: raise ValueError('Comfy is missing required workflow nodes')
        if profile['kind']=='video' and frames is not None:
            rule=caps['frameRule']
            if not _integer(frames,rule['minimum'],min(caps['maxFrames'],rule.get('maximum',caps['maxFrames']))) or (rule['kind']=='affine' and frames%rule['step']!=rule['offset']): raise ValueError('Requested frames violate the workflow contract')
        if width is not None and height is not None:
            if any(not _integer(n,64,4096) or n%caps['sizeAlign'] for n in (width,height)): raise ValueError('Requested size violates the workflow contract')
            if caps.get('resolutions') and f'{width}x{height}' not in caps['resolutions']: raise ValueError('Choose a declared workflow resolution')
        values={'prompt':prompt,'seed':seed,'frames':frames,'width':width,'height':height,'fps':24}
        references=references or []
        if len(references)>caps.get('maxReferences',10): raise ValueError('Too many image references')
        for name,source in [('start_image',start_image),('end_image',end_image)]+[(f'reference_{i}',ref) for i,ref in enumerate(references,1)]:
            if source is not None:
                if name not in profile['bindings']: raise ValueError('This workflow does not bind '+name)
                values[name]=self.upload_image(source)
        graph=copy.deepcopy(profile['workflow'])
        for name,binding in profile['bindings'].items():
            if values.get(name) is not None: graph[binding['node']]['inputs'][binding['input']]=values[name]
        ident,client=self.submit(graph)
        on_progress({'type':'progress','phase':'submitted','promptId':ident})
        outputs=self.wait_result(ident,self.work_dir/'Outputs',declaration=profile['output'],cancel=cancel)
        for output in outputs: validate_decoded(output['path'],kind=profile['kind'],width=width if 'width' in profile['bindings'] else None,height=height if 'height' in profile['bindings'] else None,fps=24 if profile['kind']=='video' else None,frames=frames if profile['kind']=='video' else None)
        recipe={'workflowDigest':profile_digest(profile),'profileDigest':profile_digest(profile),
                'compiledWorkflowDigest':hashlib.sha256(json.dumps(graph,sort_keys=True).encode()).hexdigest(),
                'promptId':ident,'clientId':client,'outputs':outputs,'fps':24 if profile['kind']=='video' else None}
        return {'media':[Path(o['path']) for o in outputs],'recipe':recipe,'promptId':ident,'clientId':client}

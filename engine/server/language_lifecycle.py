"""Explicit managed lifecycle for local Ollama/oMLX language models."""
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
from __future__ import annotations
from contextlib import contextmanager
import json, os, tempfile, time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

class LifecycleError(RuntimeError): pass
class _NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): return None
class LanguageLease:
 def __init__(self,service,config_dir=None,store_path=None):
  self.profile=dict(getattr(service,'profile',service) or {}); self.api_key=getattr(service,'api_key','') or self.profile.get('api_key',''); self.config_dir=Path(config_dir) if config_dir else Path(tempfile.gettempdir())/'sprintwake-language'; self.store_path=Path(store_path) if store_path else self.config_dir/'lifecycle.json'; self._managed=False; self._confirmed=False
  self.kind=self.profile.get('lifecycle'); self.model=self.profile.get('model'); self.endpoint=str(self.profile.get('url') or self.profile.get('endpoint') or '').rstrip('/')
  if self.kind=='omlx' and self.endpoint.endswith('/v1'): self.endpoint=self.endpoint[:-3].rstrip('/')
  if self.kind in {'ollama','omlx'}:
   parsed=urlsplit(self.endpoint)
   if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment: raise ValueError('local lifecycle endpoint must be an HTTP(S) origin')
 def _read(self):
  if self.store_path.is_symlink() or (self.store_path.exists() and self.store_path.stat().st_size>1024*1024): raise LifecycleError('lifecycle journal is unsafe or oversized')
  if not self.store_path.exists(): return None
  try: data=json.loads(self.store_path.read_text())
  except Exception as e: raise LifecycleError('lifecycle journal is unreadable') from e
  if not isinstance(data,dict) or data.get('state') not in {'uncertain','drained'} or data.get('lifecycle') not in {'ollama','omlx'} or not isinstance(data.get('model'),str) or not data['model'].strip() or type(data.get('updatedAt')) not in {int,float}: raise LifecycleError('lifecycle journal is unknown')
  return data
 def _write(self,data):
  self.store_path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix='.lifecycle-',dir=self.store_path.parent)
  try:
   with os.fdopen(fd,'w') as f: json.dump(data,f); f.flush(); os.fsync(f.fileno())
   os.replace(tmp,self.store_path)
  finally: Path(tmp).unlink(missing_ok=True)
 def _request(self,method,path,body=None):
  headers={'Accept':'application/json'}; data=None
  if self.api_key: headers['Authorization']='Bearer '+str(self.api_key)
  if body is not None: headers['Content-Type']='application/json'; data=json.dumps(body).encode()
  req=Request(self.endpoint+path,data=data,headers=headers,method=method)
  try:
   with build_opener(_NoRedirect).open(req,timeout=min(max(float(self.profile.get('lifecycleTimeout',720)),.1),3600)) as r: return json.loads(r.read(4_000_001))
  except (HTTPError,URLError,ValueError) as e: raise LifecycleError('managed language lifecycle request failed') from e
 def _models(self): return self._request('GET','/api/ps' if self.kind=='ollama' else '/admin/api/models')
 def _loaded(self,data):
  if not isinstance(data,dict) or not isinstance(data.get('models'),list): raise LifecycleError('unknown managed model metadata')
  for item in data['models']:
   if not isinstance(item,dict): raise LifecycleError('unknown managed model metadata')
   name=item.get('name') or item.get('model') or item.get('id')
   if name==self.model:
    if self.kind=='ollama': return True
    loaded=item.get('is_loaded') if isinstance(item.get('is_loaded'),bool) else item.get('loaded') if isinstance(item.get('loaded'),bool) else None
    if loaded is None and item.get('status') in {'loaded','running','unloaded'}: loaded=item['status']!='unloaded'
    if not isinstance(loaded,bool): raise LifecycleError('unknown managed model load state')
    return loaded
  return False
 def _poll(self,expect,timeout):
  deadline=time.monotonic()+timeout
  while time.monotonic()<deadline:
   if self._loaded(self._models())==expect: return True
   time.sleep(.1)
  return False
 def _enter(self):
  if self.kind not in {'ollama','omlx'}: return
  if not isinstance(self.model,str) or not self.model.strip(): raise LifecycleError('managed lifecycle requires a model')
  timeout=max(float(self.profile.get('lifecycleTimeout',720)),.1)
  if ('27b' in self.model.lower() or self.profile.get('parameterCount')==27) and timeout<720: raise LifecycleError('27B local model requires a 720 second lifecycle timeout')
  old=self._read()
  if old and old.get('state')!='drained': raise LifecycleError('previous managed model state is uncertain')
  self._write({'state':'uncertain','lifecycle':self.kind,'model':self.model,'updatedAt':time.time()})
  if self.kind=='ollama': self._request('POST','/api/chat',{'model':self.model,'messages':[],'keep_alive':self.profile.get('keepAlive','5m')})
  else: self._request('POST','/v1/models/'+quote(self.model,safe='')+'/load',{})
  self._managed=True
  try:
   if not self._poll(True,timeout): raise LifecycleError('managed model did not become loaded')
  except Exception:
   try: self._exit()
   except Exception: pass
   raise
 def _exit(self):
  if not self._managed: return
  timeout=max(float(self.profile.get('lifecycleTimeout',720)),.1)
  try:
   if self.kind=='ollama': self._request('POST','/api/chat',{'model':self.model,'messages':[],'keep_alive':0})
   else: self._request('POST','/v1/models/'+quote(self.model,safe='')+'/unload',{})
   if not self._poll(False,timeout): raise LifecycleError('managed model unload was not confirmed')
   self._write({'state':'drained','lifecycle':self.kind,'model':self.model,'updatedAt':time.time()}); self._confirmed=True
  finally: self._managed=False
 def assert_drained(self):
  assert_drained(self.store_path)
def assert_drained(store_path):
 p=Path(store_path)
 if p.is_symlink(): raise LifecycleError('lifecycle journal is unsafe')
 if not p.exists(): return
 if p.stat().st_size>1_000_000: raise LifecycleError('lifecycle journal is oversized')
 try: data=json.loads(p.read_text())
 except Exception as e: raise LifecycleError('lifecycle journal is unreadable') from e
 if not isinstance(data,dict) or data.get('state')!='drained' or data.get('lifecycle') not in {'ollama','omlx'} or not isinstance(data.get('model'),str) or not data['model'].strip() or type(data.get('updatedAt')) not in {int,float}: raise LifecycleError('managed model state is uncertain; media work is blocked')
@contextmanager
def managed_language(service,config_dir=None,store_path=None):
 lease=LanguageLease(service,config_dir,store_path)
 lease._enter()
 try: yield service
 finally: lease._exit()

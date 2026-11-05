"""Read-only, bounded editorial projections for the independent engine."""
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
from pathlib import Path
import copy, json, math

MAX_BYTES=4*1024*1024; MAX_ITEMS=500
def _number(v): return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def _safe_text(v,limit=8000): return isinstance(v,str) and bool(v.strip()) and len(v)<=limit
def _project(root,slug):
 root=Path(root); project=root/slug
 if not isinstance(slug,str) or not slug or any(x in ('','..') for x in Path(slug).parts) or project.is_symlink(): raise ValueError('unsafe project')
 current=root
 for part in project.relative_to(root).parts:
  current=current/part
  if current.is_symlink(): raise ValueError('unsafe project')
 return project
def _clip(c):
 if not isinstance(c,dict) or not isinstance(c.get('shotId'),str) or not c['shotId']: raise ValueError
 if any(type(c.get(k)) is not int or not 0<=c[k]<=86400*24 for k in ('inFrame','outFrame','sourceFrames')) or not 0<=c['inFrame']<c['outFrame']<=c['sourceFrames']: raise ValueError
 if not isinstance(c.get('included'),bool): raise ValueError
 src=c.get('source'); take=src.get('id') if isinstance(src,dict) and isinstance(src.get('id'),str) else None
 return {'shotId':c['shotId'],'takeId':take,'inFrame':c['inFrame'],'outFrame':c['outFrame'],'included':c['included']}
def _validate_history(d,name):
 for key in ('undo','redo'):
  hist=d.get(key,[])
  if not isinstance(hist,list) or len(hist)>50: raise ValueError
  for item in hist:
   if name=='coverage':
    if not isinstance(item,dict): raise ValueError
   elif not isinstance(item,dict) or not _safe_text(item.get('label'),1000): raise ValueError
   if name=='cut':
    if not isinstance(item.get('clips'),list) or len(item['clips'])>MAX_ITEMS: raise ValueError
    for c in item['clips']: _clip(c)
   elif name in ('structure','sound') and not isinstance(item.get('state'),dict): raise ValueError
def _load(script,name):
 p=script/(name+'.json')
 if not p.exists(): return {'status':'absent','revision':0}
 if p.is_symlink() or p.stat().st_size>MAX_BYTES: return {'status':'truncated','reason':'journal exceeds bounded context'}
 try: d=json.loads(p.read_text(encoding='utf-8'))
 except Exception: return {'status':'invalid','reason':'malformed journal'}
 if not isinstance(d,dict) or type(d.get('revision')) is not int or d['revision']<0: return {'status':'invalid','reason':'invalid revision'}
 try:
  _validate_history(d,name)
  if name=='cut':
   if d.get('format')!='raven-cut' or d.get('version')!=1 or d.get('fps')!=24 or not isinstance(d.get('clips'),list) or len(d['clips'])>MAX_ITEMS: raise ValueError
   for c in d['clips']: _clip(c)
  elif name=='sound':
   s=d.get('state');
   if not isinstance(s,dict) or not isinstance(s.get('tracks'),list) or not all(isinstance(t,dict) and _safe_text(t.get('name'),200) for t in s['tracks']): raise ValueError
  elif name=='structure':
   s=d.get('state');
   if not isinstance(s,dict) or not isinstance(s.get('sequences'),list): raise ValueError
   for q in s['sequences']:
    if not isinstance(q,dict) or not _safe_text(q.get('title'),500): raise ValueError
    for scene in q.get('scenes',[]):
     if not isinstance(scene,dict) or not _safe_text(scene.get('title'),500): raise ValueError
  elif name=='coverage':
   plans=d.get('plans');
   if not isinstance(plans,dict) or len(plans)>30: raise ValueError
   for plan in plans.values():
    if not isinstance(plan,dict) or not _safe_text(plan.get('title'),500): raise ValueError
    for cam in plan.get('cameras',[]):
     if not isinstance(cam,dict) or not _safe_text(cam.get('name'),500): raise ValueError
     for k in ('x','y','z','lens'):
      if k in cam and not _number(cam[k]): raise ValueError
 except (ValueError,TypeError,AttributeError): return {'status':'invalid','reason':'malformed journal'}
 d['status']='ok'
 return d
def _cut(d):
 clips=[_clip(c) for c in d['clips']]; return {'status':'ok','revision':d['revision'],'runtimeFrames':sum(c['outFrame']-c['inFrame'] for c in clips if c['included']),'clips':clips}
def _fields(value,names):
 result={}
 for key in names:
  if key in value:
   item=value[key]
   if item is not None and type(item) not in {str,int,float,bool}: raise ValueError('invalid journal field')
   if isinstance(item,float) and not math.isfinite(item): raise ValueError('invalid numeric journal field')
   result[key]=item
 return result
def _geometry(value):
 if isinstance(value,dict):
  if set(value)-{'x','y','z'} or not all(_number(n) for n in value.values()): raise ValueError('invalid geometry')
  return dict(value)
 if isinstance(value,list):
  if len(value)>32: raise ValueError('invalid geometry')
  return [_geometry(item) for item in value]
 if isinstance(value,str) or _number(value) or value is None: return value
 raise ValueError('invalid geometry')
def _coverage(d):
 out=[]
 for p in d['plans'].values():
  keep=_fields(p,('id','title','locationId','width','depth','height','aspect','side'))
  if 'axis' in p: keep['axis']=_geometry(p['axis'])
  keep['actors']=[]
  for actor in p.get('actors',[]):
   item=_fields(actor,('id','characterId','name','x','y','height'))
   if 'lookAt' in actor: item['lookAt']=_geometry(actor['lookAt'])
   keep['actors'].append(item)
  keep['cameras']=[]
  for camera in p.get('cameras',[]):
   item=_fields(camera,('id','name','kind','x','y','z','lens','selected'))
   for key in ('aimIds','aim'):
    if key in camera: item[key]=_geometry(camera[key])
   keep['cameras'].append(item)
  out.append(keep)
 return {'status':'ok','revision':d['revision'],'plans':out}
def _structure(d):
 s=d['state']; sequences=[]
 target=s.get('targetFrames',0) or 0
 if type(target) is not int or not 0<=target<=24*86400: raise ValueError('invalid runtime goal')
 for sequence in s.get('sequences',[]):
  row={k:copy.deepcopy(sequence[k]) for k in ('id','title','description','targetFrames') if k in sequence and isinstance(sequence[k],(str,int,float,bool))}
  row['scenes']=[]
  for scene in sequence.get('scenes',[]):
   item={k:copy.deepcopy(scene[k]) for k in ('id','title','description','targetFrames') if k in scene and isinstance(scene[k],(str,int,float,bool))}
   item['shotIds']=[ident for ident in scene.get('shotIds',[]) if isinstance(ident,str)][:MAX_ITEMS]
   row['scenes'].append(item)
  sequences.append(row)
 return {'status':'ok','revision':d['revision'],'targetFrames':target,'sequences':sequences}
def _sound(d):
 s=d['state']; tracks=[_fields(t,('id','name','gainDb','muted','solo')) for t in s.get('tracks',[])]
 clips=[_fields(c,('id','track','sourceFrames','inFrame','outFrame','atFrame','gainDb','fadeIn','fadeOut','muted')) for c in s.get('clips',[])]
 return {'status':'ok','revision':d['revision'],'original':{k:v for k,v in s.get('original',{}).items() if k in {'enabled','gainDb','muted','solo'} and type(v) in {bool,int,float}},'tracks':tracks,'clips':clips}
def editorial_context(projects_dir,slug,board=None):
 project=_project(Path(projects_dir),slug); script=project/'Script'
 if script.is_symlink(): raise ValueError('unsafe Script directory')
 docs={n:_load(script,n) for n in ('cut','coverage','structure','sound')}; out={}
 for n,d in docs.items():
  if d['status']!='ok': out[n]=d; continue
  try: out[n]={'cut':_cut,'coverage':_coverage,'structure':_structure,'sound':_sound}[n](d)
  except Exception: out[n]={'status':'invalid','reason':'malformed journal'}
 return out

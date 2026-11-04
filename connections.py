"""Private, versioned installation/provider settings; no eager network or models."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
IDS = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
LLM_KINDS = ('openai-compatible','openai-api','ollama','anthropic')


def config_path():
    return Path(os.environ.get('RAVEN_CONFIG', str(Path.home()/'Library/Application Support/Raven Studio/connections.json'))).expanduser()


def endpoint(value):
    if not isinstance(value,str) or any(c.isspace() for c in value): raise ValueError('Invalid service address.')
    parts=urlsplit(value)
    if parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Use an http(s) address without a key, username, or query parameters.')
    try: parts.port
    except ValueError: raise ValueError('Invalid port.') from None
    return value.rstrip('/')


def defaults():
    engine=next((p for p in (ROOT/'engine',ROOT.parent/'storyboard') if (p/'server/__main__.py').is_file()),ROOT.parent/'storyboard')
    production=Path.home()/'Movies/Raven Studio'
    bundled=os.environ.get('SPRINTWAKE_BUNDLED_PYTHON')
    candidates=([Path(bundled)] if bundled else [])+[ROOT/'runtime/bundle-venv/bin/python',engine/'.venv/bin/python',ROOT.parent/'ComfyUI-Film/.venv/bin/python',ROOT/'.venv/bin/python']
    python=next((p for p in candidates if p.is_file()),engine/'.venv/bin/python')
    workspace=production/'System/EngineWork'
    try:
        raw=json.loads((engine/'server-config.json').read_text()).get('workspace')
        if isinstance(raw,str) and raw: workspace=Path(raw).expanduser()
    except (OSError,ValueError,AttributeError): pass
    return {'version':1,'paths':{'root':str(production),'engine':str(engine),'python':str(python),'workspace':str(workspace)},
            'comfyUrl':'http://127.0.0.1:8188','selected':{'llm':'local','image':'qwen-local','video':'ltx-local'},
            'llmProfiles':[],'workflowProfiles':[]}


def validate(doc):
    if not isinstance(doc,dict) or set(doc)!={'version','paths','comfyUrl','selected','llmProfiles','workflowProfiles'} or type(doc['version']) is not int or doc['version']!=1:
        raise ValueError('Unknown connection format.')
    out=copy.deepcopy(doc)
    if not isinstance(out['paths'],dict) or set(out['paths'])!={'root','engine','python','workspace'}: raise ValueError('Invalid installation paths.')
    for name,value in out['paths'].items():
        if not isinstance(value,str) or not value.strip() or len(value)>4096 or '\0' in value: raise ValueError('Choose a valid path for '+name)
        path=Path(value).expanduser()
        if not path.is_absolute(): raise ValueError('Use an absolute path or ~ for '+name)
        out['paths'][name]=str(path)
    out['comfyUrl']=endpoint(out['comfyUrl'])
    if not isinstance(out['llmProfiles'],list) or len(out['llmProfiles'])>30: raise ValueError('Too many language models.')
    llms={'local','none'}
    for item in out['llmProfiles']:
        if not isinstance(item,dict) or set(item)!={'id','label','kind','url','model','vision'}: raise ValueError('Invalid language model profile.')
        ident=item['id']
        if not isinstance(ident,str) or not IDS.fullmatch(ident) or ident in llms: raise ValueError('Choose a unique profile ID.')
        llms.add(ident)
        if item['kind'] not in LLM_KINDS or type(item['vision']) is not bool: raise ValueError('Invalid model type.')
        for key in ('label','model'):
            if not isinstance(item[key],str) or not item[key].strip() or len(item[key])>300: raise ValueError('Enter a name and model ID.')
        item['url']=endpoint(item['url'])
    if not isinstance(out['workflowProfiles'],list) or len(out['workflowProfiles'])>30: raise ValueError('Too many workflow profiles.')
    media={'image':{'qwen-local','none'},'video':{'ltx-local','none'}}
    seen=set()
    for item in out['workflowProfiles']:
        if not isinstance(item,dict) or not isinstance(item.get('kind'),str) or item['kind'] not in media or not isinstance(item.get('id'),str) or item['id'] in seen|{'none','qwen-local','ltx-local'}:
            raise ValueError('Invalid or duplicate workflow profile.')
        # The bundled pure adapter schema is validated before profile import;
        # validate again at server startup before any job can be queued.
        _workflow_validator()(item)
        seen.add(item['id']);media[item['kind']].add(item['id'])
    selected=out['selected']
    if not isinstance(selected,dict) or set(selected)!={'llm','image','video'}: raise ValueError('Choose model profiles.')
    for key,choices in {'llm':llms,**media}.items():
        if not isinstance(selected[key],str) or selected[key] not in choices: raise ValueError('Unknown selected profile: '+key)
    if len(json.dumps(out,ensure_ascii=False).encode())>4_000_000:raise ValueError('Connection file is too large.')
    return out


def _workflow_validator():
    from contracts.comfy_workflow import validate_profile
    return validate_profile


class ConnectionStore:
    def __init__(self,path=None): self.path=Path(path or config_path()).expanduser().resolve()
    def load(self):
        if not self.path.exists(): return defaults()
        if self.path.stat().st_size>4_000_000: raise ValueError('Connection file is too large.')
        return validate(json.loads(self.path.read_text()))
    def save(self,doc):
        doc=validate(doc)
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd,tmp=tempfile.mkstemp(prefix='.connections-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'w') as handle:
                json.dump(doc,handle,ensure_ascii=False,indent=2);handle.write('\n');handle.flush();os.fsync(handle.fileno())
            os.replace(tmp,self.path)
        finally: Path(tmp).unlink(missing_ok=True)
        return doc


def selected_llm(doc):
    return 'splash-local' if doc['selected']['llm']=='local' else doc['selected']['llm']


def credential_account(doc, role):
    """Bind a stored credential to its configured endpoint as well as profile."""
    if role=='comfy':url=doc['comfyUrl']
    else:
        ident=role.removeprefix('llm:')
        profile=next((p for p in doc['llmProfiles'] if p['id']==ident),None)
        if not profile:return role+':unconfigured'
        url=profile['url']
    return role+':'+hashlib.sha256(endpoint(url).encode()).hexdigest()[:24]


def service_document(doc):
    ident=doc['selected']['llm']
    item=next((x for x in doc['llmProfiles'] if x['id']==ident),None)
    if not item:return None
    return {'services':[dict(item) | {'apiKeyEnv':'RAVEN_LLM_API_KEY','requiresKey':False}]}


def media_document(doc):
    profiles={p['id']:p for p in doc['workflowProfiles']}
    return {'version':1,'image':profiles.get(doc['selected']['image'],doc['selected']['image']),
            'video':profiles.get(doc['selected']['video'],doc['selected']['video'])}


def write_private(path,doc):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(prefix='.settings-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as handle:json.dump(doc,handle,ensure_ascii=False,indent=2);handle.write('\n');handle.flush();os.fsync(handle.fileno())
        os.replace(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)

"""Passive setup checks: files and metadata GETs only, never inference."""
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,build_opener,HTTPRedirectHandler
from connections import validate,credential_account

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def _get(url,key='',kind='openai-compatible'):
    headers={'Accept':'application/json'}
    if key:headers['x-api-key' if kind=='anthropic' else 'Authorization']=key if kind=='anthropic' else 'Bearer '+key
    if kind=='anthropic':headers['anthropic-version']='2023-06-01'
    with build_opener(NoRedirect).open(Request(url,headers=headers),timeout=5) as response:
        raw=response.read(8_000_001)
    if len(raw)>8_000_000:raise ValueError('Metadataresponsen er for stor.')
    return json.loads(raw)


def probe_connections(doc,secret_loader=lambda _:''):
    doc=validate(doc);out=[]
    load_secret=lambda role:secret_loader(credential_account(doc,role))
    def result(name,status,detail):out.append({'name':name,'status':status,'detail':detail})
    engine=Path(doc['paths']['engine']);python=Path(doc['paths']['python'])
    result('Produksjonsmotor','klar' if (engine/'server/__main__.py').is_file() else 'mangler',str(engine))
    if python.is_file():
        try:
            check=subprocess.run([str(python),'-I','-B','-c','import sys; assert sys.version_info[:2]==(3,12); import PIL,numpy,av'],capture_output=True,timeout=10)
            result('Python-runtime','klar' if check.returncode==0 else 'mangler','Python 3.12 med Pillow, NumPy og PyAV' if check.returncode==0 else 'Kjør scripts/setup-engine.sh med valgt motor og runtime.')
        except (OSError,subprocess.TimeoutExpired):result('Python-runtime','utilgjengelig','Runtime kunne ikke kontrolleres.')
    else:result('Python-runtime','mangler',str(python))
    try:
        _get(doc['comfyUrl']+'/system_stats',load_secret('comfy'))
        result('ComfyUI','klar',doc['comfyUrl'])
    except HTTPError as exc:result('ComfyUI','utilgjengelig',f'HTTP {exc.code}')
    except Exception:result('ComfyUI','utilgjengelig','Tjenesten svarte ikke med gyldige metadata.')
    selected=doc['selected']['llm']
    service=next((p for p in doc['llmProfiles'] if p['id']==selected),None)
    if selected=='local':
        try:
            records=json.loads((engine/'llm-services.json').read_text())['services']
            service=next((p for p in records if p.get('id')=='splash-local'),None)
        except (OSError,ValueError,KeyError,TypeError):pass
    if selected=='none':result('Språkmodell','ikke valgt','Tekst og redigering kan fortsatt gjøres manuelt.')
    elif service:
        base=service['url'].rstrip('/');kind=service.get('kind','openai-compatible')
        url=base+'/api/tags' if kind=='ollama' else base+('/models' if base.endswith('/v1') else '/v1/models')
        try:
            data=_get(url,load_secret('llm:'+selected),kind)
            ids={str(x.get('id') or x.get('name')) for x in data.get('data',data.get('models',[])) if isinstance(x,dict)}
            model=service.get('model','')
            result('Språkmodell','klar' if model in ids else 'ikke bekreftet',model if model in ids else 'Forbindelsen svarer; valgt modell-ID er ikke bekreftet i modellisten.')
        except HTTPError as exc:result('Språkmodell','utilgjengelig',f'HTTP {exc.code}')
        except Exception:result('Språkmodell','utilgjengelig','Modellisten kunne ikke leses. Ingen modell ble startet.')
    else:result('Språkmodell','mangler','Velg eller legg til en språkmodellprofil.')
    from contracts.comfy_workflow import WorkflowClient
    for kind in ('image','video'):
        selected=doc['selected'][kind]
        profile=next((p for p in doc['workflowProfiles'] if p['id']==selected),None)
        if profile:
            try:
                info=WorkflowClient(doc['comfyUrl'],api_key=load_secret('comfy'),timeout=5).preflight(profile)
                result(profile['label'],'klar' if info['ok'] else 'mangler','Workflow-kontroll uten generering' if info['ok'] else ', '.join(info['missingNodes']))
            except Exception:result(profile['label'],'utilgjengelig','Workflowens noder og modellvalg kunne ikke bekreftes.')
        else:result('Bildeprofil' if kind=='image' else 'Videoprofil','ikke valgt' if selected=='none' else 'lokalt oppsett','Status kontrolleres også av produksjonsmotoren.')
    return out

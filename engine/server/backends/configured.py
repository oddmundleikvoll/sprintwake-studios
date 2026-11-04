"""Explicit image/video selections without changing the native LTX default."""
from __future__ import annotations

import json
import os
from pathlib import Path
from .base import Backend
from .ltx_backend import LTXBackend
from .qwen_backend import WithQwenImage, QWEN_ID
from .comfy_workflow_backend import ComfyWorkflowBackend
from ..comfy_workflow import profile_digest


class DisabledBackend(Backend):
    id='none';label='Ingen videomodell valgt'
    def capabilities(self):return []
    def health(self):return False,'Velg en videoprofil i Modelltilkoblinger.'
    def prepare(self,*args):raise ValueError('Denne modelltypen er ikke konfigurert.')
    def run(self,*args):raise ValueError('Denne modelltypen er ikke konfigurert.')


class ConfiguredBackend(Backend):
    id='configured';label='Valgte ComfyUI-profiler'
    def __init__(self,document,workspace,comfyui_url):
        if not isinstance(document,dict) or set(document)!={'version','image','video'} or document['version']!=1:
            raise ValueError('Ukjent medieprofilformat.')
        def build(value,kind):
            if value=='none':return DisabledBackend()
            if value=='ltx-local' and kind=='video':return LTXBackend(workspace=workspace,base_url=comfyui_url)
            if value=='qwen-local' and kind=='image':return WithQwenImage(DisabledBackend(),comfyui_url)
            if not isinstance(value,dict) or value.get('kind')!=kind:raise ValueError('Ugyldig valgt '+kind+'-profil.')
            return ComfyWorkflowBackend(value,comfyui_url,workspace,api_key=os.environ.get('RAVEN_COMFY_API_KEY',''))
        self.video=build(document['video'],'video');self.image=build(document['image'],'image')
        self.native_film=document['video']=='ltx-local' and document['image']=='qwen-local'
        self.image_model_id=QWEN_ID if document['image']=='qwen-local' else (document['image'].get('id') if isinstance(document['image'],dict) else 'none')
    def __getattr__(self,name):return getattr(self.video,name)
    def capabilities(self):return self.video.capabilities()+self.image.capabilities()
    def health(self):return (self.image if self.video.id=='none' else self.video).health()
    def health_for(self,model):
        self._released()
        return (self.image if model==self.image_model_id else self.video).health()
    def _released(self):
        for owner in (self.image,self.video):
            client=getattr(owner,'client',None)
            if hasattr(client,'assert_released'):client.assert_released()
    def admission_ready(self):self._released()
    def project_defaults(self):
        if isinstance(self.video,ComfyWorkflowBackend):
            return {'model':self.video.profile['id'],'modelProvider':'comfy-workflow','modelProfileDigest':profile_digest(self.video.profile)}
        return {}
    def prepare(self,shot,project,paths):
        self._released()
        image=shot.get('model')==self.image_model_id
        owner=self.image if image else self.video
        if not image:
            defaults=project.get('defaults') or {};model=shot.get('model') or defaults.get('model')
            if model not in {c.id for c in owner.capabilities()}:raise ValueError('Velg den aktive videoprofilen eksplisitt på klippkortet før generering.')
            if isinstance(owner,ComfyWorkflowBackend) and ((shot.get('modelProvider') or defaults.get('modelProvider'))!='comfy-workflow' or (shot.get('modelProfileDigest') or defaults.get('modelProfileDigest'))!=profile_digest(owner.profile)):
                raise ValueError('Modellprofilen er endret. Bekreft valgt workflow på klippkortet før generering.')
        spec=owner.prepare(shot,project,paths)
        spec.payload['_raven_media_role']='image' if image else 'video'
        return spec
    def _owner(self,spec):return self.image if spec.payload.get('_raven_media_role')=='image' else self.video
    def run(self,spec,on_event,should_cancel):return self._owner(spec).run(spec,on_event,should_cancel)
    def validate(self,spec,result):return self._owner(spec).validate(spec,result)
    def cancel(self):
        self.video.cancel();self.image.cancel()


def from_file(path,workspace,comfyui_url):
    path=Path(path)
    if path.stat().st_size>4_000_000:raise ValueError('Medieprofilfilen er for stor.')
    return ConfiguredBackend(json.loads(path.read_text()),workspace,comfyui_url)

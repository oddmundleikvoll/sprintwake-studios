# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Resource boundaries for native Qwen, independent LTX CLI and explicit workflows."""
from pathlib import Path
import os
import uuid
from .comfy_workflow import ComfyProvider, validate_profile
from .providers import NativeComfyProvider, qwen_graph
from .store import read_json, safe_path, atomic_json

def clip_prompt(shot):
    parts=[shot.get('prompt','')]
    for key,label in [('castDescription','Stable cast'),('start','Opening'),('action','Action'),('end','Ending'),('camera','Camera'),('dialogue','Spoken dialogue'),('soundNote','Audio')]:
        value=shot.get(key)
        if value: parts.append(label+': '+str(value))
    text='\n'.join(parts).strip()
    if not text: raise ValueError('The clip needs a production prompt')
    return text

class EngineProvider(NativeComfyProvider):
    def __init__(self, *args, config_dir=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_dir=Path(config_dir) if config_dir else None
        self._memory_state=safe_path(self.state_dir,self._key+'-memory.json')
        self.ltx_process_journal=safe_path(self.state_dir,'ltx-process.json')
    def local_qwen_profile(self, **kwargs):
        # A lazy marker: graph discovery and all model work happen only on an explicit job.
        return {'native':'qwen'}
    def local_ltx_profile(self, **kwargs):
        return {'native':'ltx'}
    def _runner(self):
        from .ltx_runner import LTXRunner
        settings=read_json(self.config_dir/'server-config.json',{}) if self.config_dir else {}
        local=settings.get('sprintwakeLtx',{})
        command=os.environ.get('SPRINTWAKE_LTX_COMMAND') or local.get('command')
        model=os.environ.get('SPRINTWAKE_LTX_MODEL') or local.get('model')
        if not command or not model:
            raise ValueError('Configure sprintwakeLtx.command and sprintwakeLtx.model in private EngineSettings/server-config.json; no model is downloaded automatically')
        return LTXRunner(Path(command),Path(model),self.work_dir/'LTX',process_journal=self.ltx_process_journal)
    def release_native_memory(self, output_dir=None):
        info=self.object_info()
        if 'SprintwakeReleaseMemory' not in info:
            raise ValueError('Install the MIT Sprintwake Comfy memory-barrier plugin before using the native local workflow')
        atomic_json(self._memory_state,{'state':'unconfirmed'})
        ident,_=self.submit({'sprintwake_release':{'class_type':'SprintwakeReleaseMemory','inputs':{}}})
        result=self.wait_terminal(ident)
        atomic_json(self._memory_state,{'state':'drained'})
        return result
    def ensure_memory_drained(self):
        state=read_json(self._memory_state,{})
        if state and state.get('state')!='drained': self.release_native_memory()
    def assert_ltx_drained(self):
        state=self.ltx_process_journal
        if state.exists() or state.is_symlink(): self._runner().assert_drained()
    def resume_available(self,run_id):
        runner=self._runner(); runner.assert_drained()
        manifest=read_json(safe_path(runner.work_dir,run_id,'manifest.json'))
        return isinstance(manifest,dict) and manifest.get('runId')==run_id
    def _before_ltx(self):
        self.release_native_memory()
        queue=self._request('GET','/queue')
        if queue.get('queue_running') or queue.get('queue_pending'):
            raise ValueError('Comfy has other work; wait until it drains before loading LTX')
    def generate(self, profile, prompt, seed=None, frames=None,width=None,height=None,
                 references=None,start_image=None,end_image=None,cancel=lambda:False,on_progress=lambda _:None):
        native=profile.get('native') if isinstance(profile,dict) else 'ltx' if profile=='ltx-local' else 'qwen' if profile=='qwen-local' else None
        if native=='qwen':
            atomic_json(self._memory_state,{'state':'unconfirmed'})
            info=self.object_info()
            if 'SprintwakeReleaseMemory' not in info:
                raise ValueError('The required synchronous Sprintwake memory barrier is unavailable')
            uploaded=[self.upload_image(Path(p)) for p in references or []]
            graph=qwen_graph(info,prompt,width or 1536,height or 1024,seed or 0,uploaded)
            bound={'prompt':{'node':'4','input':'prompt'},'width':{'node':'5','input':'width'},'height':{'node':'5','input':'height'},'seed':{'node':'6','input':'noise_seed'}}
            selected={'version':1,'id':'qwen-image-2-1-turbo','label':'Native Qwen 2.1 Turbo','kind':'image',
                      'workflow':graph,'bindings':bound,'output':{'node':'13','key':'images'},'capabilities':{'sizeAlign':64,'maxReferences':10}}
            try:
                return ComfyProvider.generate(self,selected,prompt,seed,width=width,height=height,cancel=cancel,on_progress=on_progress)
            finally:
                # A synchronous queued barrier, never a /free request or a metadata flag.
                if not self.has_unresolved(): self.release_native_memory()
        if native=='ltx':
            self._before_ltx()
            result=self._runner().run([prompt],width or 384,height or 256,frames or 121,[],seed or 0,
                start_image=start_image,end_image=end_image,cancel=cancel,on_progress=on_progress)
            return {'media':result['media'],'recipe':result['recipe']}
        return ComfyProvider.generate(self,profile,prompt,seed,frames,width,height,references,
                                      start_image,end_image,cancel,on_progress)
    def generate_group(self, shots, *, start_image=None,end_image=None,anchors=None,cancel=lambda:False,on_progress=lambda _:None,resume_run_id=None,run_id=None,on_checkpoint=lambda _:None):
        if not shots or len(shots)>6: raise ValueError('A camera group needs 1–6 shots')
        self._before_ltx()
        first=shots[0]; resolution=first.get('resolution','384x256')
        width,height=[int(n) for n in resolution.split('x')]
        return self._runner().run([clip_prompt(s) for s in shots],
            width,height,first.get('frames',121),[s.get('frames',120) for s in shots[1:]],first.get('seed',0),
            anchors=anchors,start_image=start_image,end_image=end_image,resume_run_id=resume_run_id,
            cancel=cancel,on_progress=on_progress,run_id=run_id,on_checkpoint=on_checkpoint)

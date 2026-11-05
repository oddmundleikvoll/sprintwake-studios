# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Offline MIT LTX CLI, with durable clip checkpoints and owned process groups."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid
from .store import atomic_json, read_json, safe_path
from .media import file_sha256, validate_decoded, trim_carried_frames, tail_frames, join_clips

def reconcile_process_journal(path):
    path=Path(path); state=read_json(path)
    if state is None: return
    if not isinstance(state,dict) or type(state.get('pid')) is not int or state['pid']<=1:
        raise RuntimeError('LTX launch completion is uncertain; inspect its process journal before recovery')
    try: os.killpg(state['pid'],0)
    except ProcessLookupError: path.unlink(); return
    raise RuntimeError('A previous LTX process group may still own model resources')

class LTXRunner:
    def __init__(self,command,model,work_dir,process_journal=None):
        self.command,self.model,self.work_dir=Path(command).absolute(),Path(model).absolute(),Path(work_dir).absolute()
        if not self.command.is_file() or not os.access(self.command,os.X_OK): raise ValueError('LTX CLI is unavailable')
        if not self.model.is_dir(): raise ValueError('Local LTX pack is unavailable')
        safe_path(self.work_dir).mkdir(parents=True,exist_ok=True)
        self._active=Path(process_journal) if process_journal else safe_path(self.work_dir,'active-process.json')
        safe_path(self._active.parent,self._active.name,create_parent=True)
    @staticmethod
    def _group_gone(pid):
        try: os.killpg(pid,0)
        except ProcessLookupError: return True
        return False
    def assert_drained(self):
        reconcile_process_journal(self._active)
    def _drain(self,process):
        for sig,seconds in ((signal.SIGTERM,2),(signal.SIGKILL,5)):
            if self._group_gone(process.pid): break
            try: os.killpg(process.pid,sig)
            except ProcessLookupError: break
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                process.poll()
                if self._group_gone(process.pid): break
                time.sleep(.05)
        process.poll()
        if not self._group_gone(process.pid): raise RuntimeError('LTX child processes have not confirmed drain')
    def _run_process(self,argv,cancel):
        self.assert_drained()
        if cancel(): raise RuntimeError('LTX generation cancelled before launch')
        log=safe_path(self.work_dir,'ltx-'+uuid.uuid4().hex+'.log')
        # Write before spawn: a parent crash in the launch window blocks admission.
        atomic_json(self._active,{'phase':'launching','createdAt':time.time()})
        process=None
        with log.open('xb') as handle:
            try:
                process=subprocess.Popen(argv,cwd=self.work_dir,env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1'),
                    start_new_session=True,stdout=handle,stderr=subprocess.STDOUT)
                atomic_json(self._active,{'pid':process.pid,'phase':'running','createdAt':time.time()})
                while process.poll() is None:
                    if cancel():
                        self._drain(process)
                        raise RuntimeError('LTX generation cancelled')
                    time.sleep(.05)
                code=process.returncode
                self._drain(process)
                if code: raise RuntimeError('LTX CLI failed; inspect the retained private run log')
            finally:
                if process is not None:
                    self._drain(process)
                    if self._group_gone(process.pid): self._active.unlink(missing_ok=True)
    def _fingerprint(self,prompts,width,height,first,continuations,seed,anchors,start,end):
        def reference(path):
            path=Path(path)
            validate_decoded(path,kind='image')
            return {'name':path.name,'sha256':file_sha256(path)}
        model=[]
        for path in self.model.rglob('*'):
            if path.is_file():
                stat=path.stat(); model.append([str(path.relative_to(self.model)),stat.st_size,stat.st_mtime_ns])
                if len(model)>100_000: raise ValueError('Model pack manifest is too large')
        if not model: raise ValueError('Local model pack is empty')
        request={'prompts':prompts,'size':[width,height],'firstFrames':first,'continuationFrames':continuations,'seed':seed,
            'modelFiles':sorted(model),'cli':file_sha256(self.command),'start':reference(start) if start else None,
            'end':reference(end) if end else None,'anchors':[]}
        for anchor in anchors or []:
            request['anchors'].append({**{k:v for k,v in anchor.items() if k not in {'path','image'}},'image':reference(anchor.get('path') or anchor.get('image'))})
        return hashlib.sha256(json.dumps(request,sort_keys=True,separators=(',',':')).encode()).hexdigest(),request
    @staticmethod
    def _image_args(path,frame,strength,frames):
        if not (frame=='last' or type(frame) is int and 0<=frame<frames): raise ValueError('Image anchor lies outside its clip')
        if type(strength) not in {int,float} or not 0<strength<=1: raise ValueError('Invalid anchor strength')
        validate_decoded(path,kind='image')
        return ['--image',str(path),str(frame),str(strength)]
    def run(self,prompts,width,height,first_frames,continuation_frames,seed,anchors=None,start_image=None,end_image=None,
            resume_run_id=None,cancel=lambda:False,on_progress=lambda _:None,run_id=None,on_checkpoint=lambda _:None):
        if not isinstance(prompts,list) or not 1<=len(prompts)<=6 or any(not isinstance(p,str) or not p.strip() or len(p)>32_000 for p in prompts):
            raise ValueError('Choose one to six bounded clip prompts')
        if any(type(n) is not int or not 64<=n<=4096 or n%64 for n in (width,height)): raise ValueError('Invalid LTX size')
        if type(first_frames) is not int or not 25<=first_frames<=481 or first_frames%8!=1: raise ValueError('Invalid opening frame grid')
        if len(continuation_frames)!=len(prompts)-1 or any(type(n) is not int or not 8<=n<=480 or n%8 for n in continuation_frames):
            raise ValueError('Invalid continuation frame grid')
        if type(seed) is not int or not 0<=seed<2**32: raise ValueError('Invalid LTX seed')
        if resume_run_id and run_id: raise ValueError('Choose either a new run or a resume run')
        ident=resume_run_id or run_id or uuid.uuid4().hex
        if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident): raise ValueError('Invalid run ID')
        self.assert_drained()
        folder=safe_path(self.work_dir,ident); folder.mkdir(exist_ok=True)
        manifest_path=safe_path(folder,'manifest.json')
        fingerprint,request=self._fingerprint(prompts,width,height,first_frames,continuation_frames,seed,anchors,start_image,end_image)
        manifest=read_json(manifest_path)
        if resume_run_id:
            if not isinstance(manifest,dict) or manifest.get('fingerprint')!=fingerprint: raise ValueError('Resume inputs changed or checkpoint is missing')
        elif manifest is not None: raise ValueError('Run already exists; explicitly resume its verified checkpoint')
        else: manifest={'runId':ident,'fingerprint':fingerprint,'clipRecords':[]}
        records=manifest.get('clipRecords')
        if not isinstance(records,list) or len(records)>len(prompts): raise ValueError('Invalid clip checkpoint list')
        clips=[]
        for index,record in enumerate(records):
            expected=safe_path(folder,f'clip-{index:03d}'+('-trimmed' if index else '')+'.mp4')
            if record.get('path')!=str(expected) or file_sha256(expected)!=record.get('sha256'): raise ValueError('Checkpoint media changed')
            validate_decoded(expected,kind='video',width=width,height=height,frames=first_frames if index==0 else continuation_frames[index-1],fps=24,audio_hz=48000)
            clips.append(expected)
        atomic_json(safe_path(folder,'request.json'),request); atomic_json(manifest_path,manifest); on_checkpoint(copy.deepcopy(manifest))
        for index,prompt in enumerate(prompts):
            if index<len(clips): continue
            if cancel(): raise RuntimeError('LTX generation cancelled')
            frames=first_frames if index==0 else continuation_frames[index-1]
            output=safe_path(folder,f'clip-{index:03d}.mp4')
            argv=[str(self.command),'generate' if index==0 else 'extend','--distilled','--model',str(self.model),'--prompt',prompt,'--seed',str(seed),'-o',str(output)]
            if index==0: argv += ['--frame-rate','24','-f',str(frames),'-W',str(width),'-H',str(height)]
            else:
                tail=safe_path(folder,f'tail-{index:03d}-{uuid.uuid4().hex}.mp4'); tail_frames(clips[-1],tail)
                argv += ['--video',str(tail),'--extend-frames',str(frames//8)]
            chosen={}
            if index==0 and start_image: chosen[0]=(start_image,1.0)
            if index==len(prompts)-1 and end_image: chosen['last']=(end_image,.65)
            for anchor in anchors or []:
                if anchor.get('clip_index',0)==index: chosen[anchor.get('frame',0)]=(anchor.get('path') or anchor.get('image'),anchor.get('strength',1.0))
            for frame,(image,strength) in chosen.items(): argv += self._image_args(image,frame,strength,frames)
            self._run_process(argv,cancel)
            validate_decoded(output,kind='video',width=width,height=height,frames=frames if index==0 else frames+25,fps=24,audio_hz=48000)
            if index:
                selected=safe_path(folder,f'clip-{index:03d}-trimmed.mp4')
                if selected.exists(): selected.rename(safe_path(folder,f'partial-{uuid.uuid4().hex}.mp4'))
                trim_carried_frames(output,selected)
            else: selected=output
            clips.append(selected); records.append({'path':str(selected),'sha256':file_sha256(selected),'frames':frames})
            manifest['clips']=[str(p) for p in clips]; atomic_json(manifest_path,manifest)
            on_checkpoint(copy.deepcopy(manifest)); on_progress(index+1)
        recipe={'runId':ident,'model':str(self.model),'distilled':True,'fps':24,'audioHz':48000,'firstFrames':first_frames,
            'continuationFrames':continuation_frames,'seed':seed,'fingerprint':fingerprint}
        atomic_json(safe_path(folder,'recipe.json'),recipe); manifest['recipe']=recipe; atomic_json(manifest_path,manifest)
        movie=safe_path(folder,'joined.mp4')
        if movie.exists(): validate_decoded(movie,kind='video',frames=first_frames+sum(continuation_frames),fps=24,audio_hz=48000)
        else: join_clips(clips,movie)
        return {'media':clips,'movie':movie,'recipe':recipe,'manifest':manifest,'run_id':ident}

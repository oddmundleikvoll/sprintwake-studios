"""Single-clip Comfy workflow backend with verified, preserved publication."""
from __future__ import annotations
import json, os, shutil, subprocess, tempfile, time, uuid
from pathlib import Path
from ..comfy_workflow import WorkflowClient, validate_profile, profile_digest, _atomic, publish_pair
from .base import Backend,Check,FrameRule,JobSpec,ModelCapability,ProgressEvent,RunResult,ShotPaths,Validation
from .render_helpers import _ref_source, _wh

class ComfyWorkflowBackend(Backend):
    id='comfy-workflow';label='ComfyUI API workflow'
    def __init__(self,profile,comfyui_url,workspace,api_key=''):
        self.profile=validate_profile(profile);self.workspace=Path(workspace).resolve()
        self.client=WorkflowClient(comfyui_url,api_key=api_key,state_dir=Path(os.environ.get('RAVEN_COMFY_STATE_DIR',str(self.workspace/'.raven-comfy-jobs'))))
        self.image_model_id=self.profile['id']
    def capabilities(self):
        p=self.profile;c=p['capabilities'];rule=c.get('frameRule',{})
        return [ModelCapability(id=p['id'],label=p['label'],kind=p['kind'],supports_start_anchor=bool(c.get('supportsStartAnchor')),supports_end_anchor=bool(c.get('supportsEndAnchor')),supports_audio=bool(c.get('supportsAudio')),supports_style_refs=bool(c.get('maxReferences')),max_style_refs=c.get('maxReferences',0),frame_rule=FrameRule(kind=rule.get('kind','any'),step=rule.get('step',1),offset=rule.get('offset',0),minimum=rule.get('minimum',1)),resolutions=c.get('resolutions',[]),size_align=c.get('sizeAlign',1),default_steps=c.get('defaultSteps',8),engine='comfyui')]
    def health(self):
        try:
            self.client.assert_released();out=self.client.preflight(self.profile)
            return out['ok'],'Workflow klar' if out['ok'] else ', '.join(out['missingNodes'])
        except Exception as exc:return False,str(exc)
    def prepare(self,shot,project,paths:ShotPaths):
        self.client.assert_released();bindings=self.profile['bindings'];caps=self.profile['capabilities'];defaults=project.get('defaults',{})
        if shot.get('imageAnchors') or shot.get('styleRefs'):raise ValueError('Tidsfestede ankere/styleRefs krever egen adapter; bruk deklarerte referansebindinger.')
        if shot.get('dialogueSource')=='recording' and shot.get('dialogue'):raise ValueError('Opptaksbasert dubbing støttes ikke av denne workflowen.')
        values={'prompt':shot.get('prompt') or ''}
        if 'seed' in bindings:values['seed']=int(shot.get('seed') or 0)
        resolution=shot.get('resolution') or defaults.get('resolution') or (caps.get('resolutions') or ['768x512'])[0]
        width,height=_wh(resolution)
        align=caps.get('sizeAlign',1)
        if min(width,height)<64 or max(width,height)>4096 or width%align or height%align:raise ValueError('Oppløsningen bryter workflowens størrelsesregler.')
        if caps.get('resolutions') and resolution not in caps['resolutions']:raise ValueError('Velg en oppløsning fra workflowens liste.')
        for key,value in [('width',width),('height',height)]:
            if key in bindings:values[key]=value
        for field,name in [('startRef','start_image'),('endRef','end_image')]:
            if shot.get(field):
                if name not in bindings:raise ValueError(field+' selected but no '+name+' binding')
                source=_ref_source(shot[field],paths)
                if not source or not Path(source).is_file():raise ValueError('Missing selected image reference')
                values[name]=str(source)
        refs=shot.get('referenceImages') or []
        if len(refs)>caps.get('maxReferences',0):raise ValueError('Too many references for selected workflow')
        for i,ref in enumerate(refs,1):
            source=_ref_source(ref,paths)
            if not source or not Path(source).is_file():raise ValueError('Missing reference image')
            values[f'reference_{i}']=str(source)
        frames=0
        if self.profile['kind']=='video':
            frames=shot.get('frames') or defaults.get('frames') or 121;rule=self.capabilities()[0].frame_rule
            if type(frames) is not int or frames>min(caps['maxFrames'],caps['frameRule'].get('maximum',caps['maxFrames'])) or rule.check(frames):raise ValueError('Rammetallet bryter workflowens rammegitter; endre klipplengden først.')
            if 'frames' in bindings:values['frames']=frames
            if 'fps' in bindings:values['fps']=24
        paths.ensure();target=paths.abs_dir/('image.png' if self.profile['kind']=='image' else 'clip.mp4')
        return JobSpec(shot_id=shot['id'],expected_outputs=[target],expected_frames=frames,expected_seconds=0.0,payload={'profile':self.profile,'values':values,'workflowDigest':profile_digest(self.profile),'width':width,'height':height},summary=self.profile['label'])
    def _video_check(self,path,expected):
        try:
            import av
            with av.open(str(path)) as media:
                if len(media.streams.video)!=1:return Check('video contract',False,'Expected one video stream')
                stream=media.streams.video[0]
                if stream.average_rate is None or abs(float(stream.average_rate)-24)>.001 or max(stream.width,stream.height)>4096:
                    return Check('video contract',False,'Unsupported video fps/geometry')
                count=0;deadline=time.monotonic()+120
                for frame in media.decode(stream):
                    count+=1
                    if (expected and count>expected) or count>14400 or time.monotonic()>deadline:
                        return Check('video contract',False,'Video decode exceeds declared contract')
                return Check('video contract',count>0 and (not expected or count==expected),f'24 fps, {count} decoded frames')
        except Exception:return Check('video contract',False,'Actual video could not be decoded')
    def run(self,spec,on_event,should_cancel):
        result=RunResult(started_at=time.time());spec.started_at=result.started_at;target=spec.expected_outputs[0]
        try:
            with tempfile.TemporaryDirectory(prefix='.workflow-',dir=target.parent) as work:
                out=self.client.run(self.profile,spec.payload['values'],work,should_cancel=should_cancel,on_progress=lambda e:on_event(ProgressEvent(phase='comfy',detail=str(e),percent=0)))
                if out.cancelled or should_cancel():result.cancelled=True;raise InterruptedError('Cancelled after owned job drained')
                source=out.outputs[0];stage=Path(work)/('verified-'+target.name)
                if self.profile['kind']=='image':
                    from PIL import Image
                    with Image.open(source) as image:image.convert('RGB').save(stage,format='PNG')
                else:
                    check=self._video_check(source,spec.expected_frames)
                    if not check.passed:raise ValueError(check.detail)
                    shutil.copyfile(source,stage)
                recipe={'profile':self.profile,'workflowDigest':out.workflow_digest,'compiledWorkflow':out.compiled_workflow,'model':self.profile['id'],'promptId':out.prompt_id}
                recipe_stage=Path(work)/'recipe.json';_atomic(recipe_stage,recipe)
                publish_pair(stage,recipe_stage,target)
                result.exit_code=0
        except Exception as exc:result.exit_code=1;result.error=str(exc)
        finally:result.ended_at=time.time()
        return result
    def validate(self,spec,result):
        if result.cancelled or result.exit_code!=0 or result.error:return Validation([Check('run',False,result.error or 'cancelled')],'failed','Run failed')
        checks=super().validate(spec,result).checks
        target=spec.expected_outputs[0]
        if self.profile['kind']=='video':checks.append(self._video_check(target,spec.expected_frames))
        else:
            try:
                from PIL import Image
                with Image.open(target) as image:image.load()
                checks.append(Check('image decode',True,'Decoded actual image'))
            except Exception:checks.append(Check('image decode',False,'Invalid image'))
        failed=[c for c in checks if c.passed is False]
        return Validation(checks,'failed' if failed else 'done','; '.join(c.detail for c in failed))

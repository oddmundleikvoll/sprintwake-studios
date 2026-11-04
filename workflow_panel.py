"""Explicit single-take production for selected external workflow profiles."""
import re
import views as v
from contracts.comfy_workflow import profile_digest

class WorkflowPanelMixin:
    def workflow_action(self,action):
        if action not in {'video.generate','video.confirm','video.stop'}:return False
        if not self.ready or not self.raw_shot:raise ValueError('Open a project and choose a shot first.')
        if action=='video.stop':
            state=self.backend.get('/api/status')
            if state.get('slug')!=self.slug or not state.get('busy'):raise ValueError('No active take job in this project.')
            self.submit('video.stop',lambda:self.backend.post('/api/stop',{}),lambda _:self.toast('Stop requested. Waiting for the local take job to finish.'))
            return True
        selected=self.connection_settings['selected']['video']
        profile=next((p for p in self.connection_settings['workflowProfiles'] if p['id']==selected and p['kind']=='video'),None)
        if not profile:raise ValueError('This tool requires an external workflow video profile. The standard LTX profile uses Production.')
        if self._busy():raise ValueError('Wait for the active job to finish.')
        if action=='video.generate':
            caps=profile['capabilities']
            resolution=self.raw_shot.get('resolution') or self.board.get('defaults',{}).get('resolution','768x512')
            allowed=caps.get('resolutions') or []
            if allowed and resolution not in allowed:resolution=allowed[0]
            body=v.label('New take with '+profile['label'],20)+v.label('Previous takes are preserved. Choose and review the result in take history before using it.',12,True)
            body+=v.field('video.frames','Frame count · 24 fps',self.raw_shot.get('frames',121),34,multiline=False)
            body+=v.field('video.resolution','Resolution · width x height',resolution,34,multiline=False)
            body+=v.label('Frame grid: '+str(caps['frameRule'])+' · max '+str(caps['maxFrames']),11,True)
            body+=v.label('Without a binding, size or duration is controlled directly by the workflow. The result is checked against the shot duration.',11,True)
            body+=v.label('Start saves the selected model, duration, and format on the shot card. An earlier take may appear outdated after a failed attempt; it remains in history and the cut.',11,True)
            self.show_modal('Create workflow take',body,'video.confirm','Start new take')
        else:
            frames=int(self.text('video.frames'));resolution=self.text('video.resolution').strip()
            caps=profile['capabilities'];rule=caps['frameRule']
            if not rule.get('minimum',1)<=frames<=min(caps['maxFrames'],rule.get('maximum',caps['maxFrames'])) or (rule['kind']=='affine' and frames%rule.get('step',1)!=rule.get('offset',0)):
                raise ValueError('The shot duration violates the profile frame grid.')
            if not re.fullmatch(r'[0-9]{2,4}x[0-9]{2,4}',resolution):raise ValueError('Use width x height, for example 768x512.')
            size=[int(n) for n in resolution.split('x')]
            if any(not 64<=n<=4096 or n%caps.get('sizeAlign',1) for n in size):raise ValueError('The resolution violates the profile size rules.')
            if caps.get('resolutions') and resolution not in caps['resolutions']:raise ValueError('Choose a resolution from the profile list.')
            self.raw_shot.update(model=profile['id'],modelProvider='comfy-workflow',modelProfileDigest=profile_digest(profile),frames=frames,resolution=resolution)
            self.set_dirty(True)
            slug,sid=self.slug,self.raw_shot['id']
            def launch():
                self.modal_close()
                self.submit('video.generate',lambda:self.backend.post('/api/render-seeds',{'slug':slug,'shotId':sid,'count':1}),lambda _:self.toast('A new take is queued. Follow progress below and open take history when it is finished.'))
            self.save(after=launch)
        return True

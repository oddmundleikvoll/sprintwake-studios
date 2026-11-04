"""Explicit single-take production for selected external workflow profiles."""
import re
import views as v
from contracts.comfy_workflow import profile_digest

class WorkflowPanelMixin:
    def workflow_action(self,action):
        if action not in {'video.generate','video.confirm','video.stop'}:return False
        if not self.ready or not self.raw_shot:raise ValueError('Åpne et prosjekt og velg et klipp først.')
        if action=='video.stop':
            state=self.backend.get('/api/status')
            if state.get('slug')!=self.slug or not state.get('busy'):raise ValueError('Ingen aktiv opptaksjobb i dette prosjektet.')
            self.submit('video.stop',lambda:self.backend.post('/api/stop',{}),lambda _:self.toast('Avbrudd er bedt om. Venter på at egen opptaksjobb avslutter.'))
            return True
        selected=self.connection_settings['selected']['video']
        profile=next((p for p in self.connection_settings['workflowProfiles'] if p['id']==selected and p['kind']=='video'),None)
        if not profile:raise ValueError('Dette verktøyet krever en ekstern workflow-videoprofil. Standard LTX bruker Produksjon.')
        if self._busy():raise ValueError('Vent til den aktive jobben er avsluttet.')
        if action=='video.generate':
            caps=profile['capabilities']
            resolution=self.raw_shot.get('resolution') or self.board.get('defaults',{}).get('resolution','768x512')
            allowed=caps.get('resolutions') or []
            if allowed and resolution not in allowed:resolution=allowed[0]
            body=v.label('Nytt take med '+profile['label'],20)+v.label('Tidligere opptak beholdes. Velg og se resultatet i opptakshistorikken før du bruker det.',12,True)
            body+=v.field('video.frames','Antall bilder · 24 fps',self.raw_shot.get('frames',121),34,multiline=False)
            body+=v.field('video.resolution','Oppløsning · bredde x høyde',resolution,34,multiline=False)
            body+=v.label('Rammegitter: '+str(caps['frameRule'])+' · maks '+str(caps['maxFrames']),11,True)
            body+=v.label('Uten en binding styres størrelse eller lengde direkte av workflowen. Resultatet kontrolleres mot klippets deklarerte varighet.',11,True)
            body+=v.label('Start lagrer valgt modell, lengde og format på klippkortet. Et tidligere take kan derfor vises som utdatert etter et mislykket forsøk; det beholdes i historikken og klippelaget.',11,True)
            self.show_modal('Lag workflow-opptak',body,'video.confirm','Start nytt take')
        else:
            frames=int(self.text('video.frames'));resolution=self.text('video.resolution').strip()
            caps=profile['capabilities'];rule=caps['frameRule']
            if not rule.get('minimum',1)<=frames<=min(caps['maxFrames'],rule.get('maximum',caps['maxFrames'])) or (rule['kind']=='affine' and frames%rule.get('step',1)!=rule.get('offset',0)):
                raise ValueError('Klipplengden bryter profilens rammegitter.')
            if not re.fullmatch(r'[0-9]{2,4}x[0-9]{2,4}',resolution):raise ValueError('Bruk bredde x høyde, for eksempel 768x512.')
            size=[int(n) for n in resolution.split('x')]
            if any(not 64<=n<=4096 or n%caps.get('sizeAlign',1) for n in size):raise ValueError('Oppløsningen bryter profilens størrelsesregler.')
            if caps.get('resolutions') and resolution not in caps['resolutions']:raise ValueError('Velg en oppløsning fra profilens liste.')
            self.raw_shot.update(model=profile['id'],modelProvider='comfy-workflow',modelProfileDigest=profile_digest(profile),frames=frames,resolution=resolution)
            self.set_dirty(True)
            slug,sid=self.slug,self.raw_shot['id']
            def launch():
                self.modal_close()
                self.submit('video.generate',lambda:self.backend.post('/api/render-seeds',{'slug':slug,'shotId':sid,'count':1}),lambda _:self.toast('Et nytt take er i kø. Følg fremdriften under og åpne opptakshistorikken når det er ferdig.'))
            self.save(after=launch)
        return True

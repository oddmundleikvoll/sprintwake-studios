"""Wait for existing work, save, reserve backend admission, then hand off to Sparkle."""
from __future__ import annotations
import time
import views as v
from updates import NativeUpdater,local_blockers

class UpdatesMixin:
    def initialize_updates(self):
        self.updater=NativeUpdater();self.update_waiting=False;self.update_phase='idle';self.update_deadline=0;self.update_message=''
        self.update_had_lease=False;self.update_lease_token=None;self.update_owner_closed=False

    def _cancel_update_guard(self):
        guard=getattr(self,'update_guard',None)
        if guard is not None:
            try: guard.cancel_update();return True
            except Exception:
                self.update_message='Oppdateringen kunne ikke frigjøre livslåsen sikkert.'
                return False
        return True

    def _approve_update(self):
        guard=getattr(self,'update_guard',None)
        if guard is not None and not guard.writer_ready():
            self.update_message='Venter på at andre Raven-motorer avslutter.'
            self.set_label('updates.status',self.update_message)
            return False
        if self.updater.approve(self.update_request_id):
            self.update_phase='installing';self.cleaned=True
            return True
        return False
    def updates_action(self,action):
        if not action.startswith('updates.'):return False
        if action=='updates.show':
            if not self.update_waiting and self.updater.status()['stage']=='available':self.updater.check()
            else:self.show_updates()
        elif action=='updates.check':self.modal_close();self.updater.check()
        elif action=='updates.install':self.updater.request_install();self.begin_update_wait()
        elif action=='updates.cancel' and self.update_phase not in {'preparing','installing'} and not getattr(self,'update_had_lease',False):
            def release():
                status=self.backend.get('/api/desktop/update-status')
                if status.get('quiesced'):
                    reservation=self.backend.post('/api/desktop/update-quiesce',{})
                    token=reservation.get('leaseToken')
                    if token:self.backend.post('/api/desktop/update-resume',{'leaseToken':token})
            def released(_):
                if self._cancel_update_guard():
                    self.updater.defer();self.update_waiting=False;self.update_phase='idle';self.modal_close()
                else:self.set_attr('updates.cancel','Enabled',False)
            self.submit('updates.resume',release,released,scoped=False)
        return True
    def show_updates(self):
        status=self.updater.status()
        body=v.label('Appen og produksjonsmotoren oppdateres sammen. Prosjekter, medier, modeller og tilkoblingsoppsett ligger utenfor appen og beholdes.',13,True)
        body+=v.label(self.update_message or status['message'],13,True,Name='updates.status')
        if self.update_waiting:
            body+=v.button('Fortsett arbeidet – installer senere','updates.cancel',Name='updates.cancel',Enabled=str(self.update_phase not in {'preparing','installing'}).lower())
        elif status['stage']=='staged':body+=v.button('Installer når arbeidet er ferdig','updates.install',True)
        elif status['configured']:body+=v.button('Se etter oppdateringer','updates.check',True)
        self.show_modal('Oppdater Sprintwake Studios',body,allow_close=not self.update_waiting)
    def begin_update_wait(self):
        if self.update_waiting:return
        self.sync_fields();self.capture_drafts();self.update_waiting=True;self.update_phase='waiting';self.update_message='Venter på at aktive produksjoner og lagring fullføres.';self.show_updates()
    def tick_updates(self):
        if not hasattr(self,'updater') or time.monotonic()<self.update_deadline:return
        self.update_deadline=time.monotonic()+.5
        state=self.updater.status()
        self.set_attr('updates.button','Text','↓ Ny versjon' if state['stage'] in {'available','staged','waiting'} else 'Oppdateringer')
        if self.update_phase=='installing' and state['stage'] in {'error','idle'}:
            if not self._cancel_update_guard():return
            self.update_waiting=False;self.update_had_lease=False;self.update_lease_token=None;self.update_phase='idle';self.cleaned=False
            self.modal_close();self.submit('boot',self._bootstrap,self._booted,scoped=False);return
        if state['stage']=='waiting' and not self.update_waiting:self.begin_update_wait()
        if not self.update_waiting or self.update_phase=='installing':return
        if self.modal_kind!='Oppdater Sprintwake Studios':self.show_updates()
        if self.update_phase=='preparing':return
        if getattr(self,'update_had_lease',False):
            process=self.backend.process
            if process is None or process.poll() is not None:
                if state.get('stage') not in {'waiting','installing'}:
                    # Sparkle abandoned the staged update.  The child has
                    # already exited, so release the persistent marker and
                    # boot a clean backend; never keep retrying approval.
                    if self._cancel_update_guard():
                        self.update_had_lease=False;self.update_lease_token=None;self.update_phase='idle';self.update_waiting=False
                        self.submit('boot',self._bootstrap,self._booted,scoped=False)
                    return
                self.ready=False
                self._approve_update()
            return
        if state['stage'] not in {'waiting','installing'} and self.update_phase=='waiting':
            self.update_waiting=False;self.update_phase='idle'
            if self.modal_kind=='Oppdater Sprintwake Studios':self.modal_close()
            return
        blockers=local_blockers(self)
        if self.dirty and not any(key!='poll' for key in self.pending) and not self._busy():self.save()
        if blockers:
            self.update_message='Venter: '+', '.join(blockers[:4]);self.set_label('updates.status',self.update_message);return
        request_id=state['requestId'];self.update_request_id=request_id;self.update_phase='preparing'
        self.set_attr('updates.cancel','Enabled',False)
        self.set_label('updates.status','Kontrollerer motoren og venter på at den avsluttes trygt…')
        def prepare():
            status=self.backend.get('/api/desktop/update-status')
            if not isinstance(status,dict) or type(status.get('ready')) is not bool or type(status.get('quiesced')) is not bool:raise ValueError('Motorens oppdateringsstatus er ukjent.')
            if not status['ready'] and not status['quiesced']:return status
            reservation=self.backend.post('/api/desktop/update-quiesce',{})
            if reservation.get('quiesced') is not True or reservation.get('blockedReasons'):return reservation
            process=self.backend.process
            if process is None or reservation.get('pid')!=process.pid:raise ValueError('Oppdateringen fikk ingen bekreftet prosesseier.')
            token=reservation.get('leaseToken')
            if not isinstance(token,str) or not token:raise ValueError('Motoren ga ingen sikker oppdateringslease.')
            self.update_had_lease=True
            self.update_lease_token=token
            try:
                guard=getattr(self,'update_guard',None)
                if guard is not None:
                    target=int(state.get('build') or 0)
                    if target<=0:raise ValueError('Oppdateringen mangler build-identitet.')
                    guard.reserve(target)
                self.backend.close()
            except Exception:
                # A live child still owns a usable admission lease.  Resume it
                # before returning to the editor; never force-stop the process.
                owner_pipe_open=not bool(getattr(getattr(process,'stdin',None),'closed',False))
                if process.poll() is None and owner_pipe_open:
                    self.backend.post('/api/desktop/update-resume',{'leaseToken':token})
                    self.update_had_lease=False;self.update_lease_token=None;self._cancel_update_guard()
                else:self.update_owner_closed=True
                raise
            if process.poll() is None:raise ValueError('Produksjonsmotoren kjører fremdeles.')
            return {'readyToInstall':True}
        def done(result):
            if result.get('readyToInstall'):
                self.ready=False
                self.update_phase='waiting'
                self._approve_update()
            else:
                self.update_phase='waiting';self.update_message='Venter: '+', '.join(result.get('blockedReasons') or ['Motoren er opptatt'])
                self.set_attr('updates.cancel','Enabled',True)
                self.set_label('updates.status',self.update_message)
        self.submit('updates.prepare',prepare,done,scoped=False)
    def update_failed(self,error):
        self.update_phase='waiting';self.update_message='Oppdateringen venter: '+str(error)[:350]
        self.set_label('updates.status',self.update_message)
        self.set_attr('updates.cancel','Enabled',not getattr(self,'update_had_lease',False))

import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from updates import local_blockers
from update_panel import UpdatesMixin
from connections import ConnectionStore,defaults
from installation import migrate_local_settings,rebase_bundle_paths

class UpdateTests(unittest.TestCase):
    def test_download_indicator_focuses_existing_sparkle_dialog(self):
        events=[];panel=UpdatesMixin()
        panel.__dict__.update(update_waiting=False,updater=SimpleNamespace(status=lambda:{'stage':'available'},check=lambda:events.append('focus')),show_updates=lambda:events.append('modal'))
        self.assertTrue(panel.updates_action('updates.show'));self.assertEqual(events,['focus'])

    def test_every_local_work_item_and_dirty_state_blocks(self):
        studio=SimpleNamespace(ready=True,pending={'poll':object()},dirty=False)
        self.assertEqual(local_blockers(studio),[])
        for field in ('opening','director_starting','coverage_drag','cut_gesture_pending','sound_gesture_pending','dirty'):
            setattr(studio,field,True);self.assertTrue(local_blockers(studio),field);setattr(studio,field,False)
        studio.pending['sound.export']=object();self.assertTrue(local_blockers(studio))
        studio.pending={};studio.ready=False;self.assertTrue(local_blockers(studio))

    def test_install_waits_without_canceling_production_then_closes_before_approval(self):
        panel=UpdatesMixin();events=[]
        updater=SimpleNamespace(status=lambda:{'stage':'waiting','requestId':3},approve=lambda rid:events.append(('approve',rid)) or True)
        alive=[True]
        process=SimpleNamespace(pid=12,poll=lambda:None if alive[0] else 0)
        def get(_):events.append('status');return {'ready':True,'quiesced':False}
        def post(*_):events.append('lease');return {'ready':True,'quiesced':True,'pid':12,'leaseToken':'test-lease'}
        def close():events.append('close');alive[0]=False
        backend=SimpleNamespace(get=get,post=post,process=process,close=close)
        panel.__dict__.update(updater=updater,backend=backend,ready=True,dirty=False,pending={'director.author':object()},update_deadline=0,update_waiting=True,update_phase='waiting',modal_kind='Update Sprintwake Studios',update_message='')
        panel.set_attr=lambda *_:None;panel.set_label=lambda *_:None
        panel.submit=lambda _k,op,done,**_kw:done(op())
        panel.tick_updates();self.assertEqual(events,[])
        panel.pending={};panel.update_deadline=0;panel.tick_updates()
        self.assertEqual(events,['status','lease','close',('approve',3)]);self.assertTrue(panel.cleaned)

    def test_unknown_backend_never_approves_installation(self):
        panel=UpdatesMixin();approved=[]
        panel.__dict__.update(updater=SimpleNamespace(status=lambda:{'stage':'waiting','requestId':3},approve=lambda _:approved.append(1)),backend=SimpleNamespace(get=lambda _:None),ready=True,dirty=False,pending={},update_deadline=0,update_waiting=True,update_phase='waiting',modal_kind='Update Sprintwake Studios',update_message='')
        panel.set_attr=lambda *_:None;panel.set_label=lambda *_:None
        panel.submit=lambda _k,op,done,**_kw:done(op())
        with self.assertRaisesRegex(ValueError,'unknown'):panel.tick_updates()
        self.assertEqual(approved,[])

    def test_close_failure_resumes_live_backend_and_cancels_global_guard(self):
        panel=UpdatesMixin();events=[]
        class Guard:
            def reserve(self,build):events.append(('reserve',build))
            def cancel_update(self):events.append('guard-cancel')
        process=SimpleNamespace(pid=12,poll=lambda:None)
        def post(path,payload):
            events.append((path,payload))
            if path.endswith('quiesce'):return {'ready':True,'quiesced':True,'pid':12,'leaseToken':'live-lease'}
            return {'resumed':True}
        def close():events.append('close');raise OSError('owner pipe unavailable')
        panel.__dict__.update(
            updater=SimpleNamespace(status=lambda:{'stage':'waiting','requestId':3,'build':14}),
            backend=SimpleNamespace(get=lambda _: {'ready':True,'quiesced':False},post=post,process=process,close=close),
            update_guard=Guard(),ready=True,dirty=False,pending={},update_deadline=0,
            update_waiting=True,update_phase='waiting',modal_kind='Update Sprintwake Studios',update_message='')
        panel.set_attr=lambda *_:None;panel.set_label=lambda *_:None
        panel.submit=lambda _k,op,done,**_kw:done(op())
        with self.assertRaises(OSError):panel.tick_updates()
        self.assertIn(('reserve',14),events)
        self.assertIn(('/api/desktop/update-resume',{'leaseToken':'live-lease'}),events)
        self.assertIn('guard-cancel',events)
        self.assertFalse(panel.update_had_lease)

    def test_close_failure_after_owner_pipe_closed_keeps_lease_until_exit(self):
        panel=UpdatesMixin();events=[]
        class Proc:
            pid=9
            class Pipe: closed=True
            stdin=Pipe()
            alive=True
            def poll(self):return None if self.alive else 0
        process=Proc()
        class Guard:
            def reserve(self,b):events.append(('reserve',b))
            def writer_ready(self):return True
            def cancel_update(self):events.append('cancel')
        def post(path,payload):
            events.append(path)
            if path.endswith('quiesce'):return {'quiesced':True,'ready':True,'pid':9,'leaseToken':'keep'}
            raise AssertionError('must not resume a closed owner pipe')
        panel.__dict__.update(updater=SimpleNamespace(status=lambda:{'stage':'waiting','requestId':4,'build':15},approve=lambda rid:events.append(('approve',rid)) or True),backend=SimpleNamespace(get=lambda _: {'ready':True,'quiesced':False},post=post,process=process,close=lambda:(_ for _ in ()).throw(OSError('closed'))),update_guard=Guard(),ready=True,dirty=False,pending={},update_deadline=0,update_waiting=True,update_phase='waiting',modal_kind='Update Sprintwake Studios',update_message='')
        panel.set_attr=lambda *_:None;panel.set_label=lambda *_:None;panel.submit=lambda _k,op,done,**_kw:done(op())
        with self.assertRaises(OSError) as failed:panel.tick_updates()
        panel.update_failed(failed.exception)
        self.assertTrue(panel.update_had_lease);self.assertNotIn('/api/desktop/update-resume',events)
        process.alive=False;panel.update_deadline=0;panel.tick_updates();self.assertIn(('approve',4),events)

    def test_writer_gate_blocks_then_allows_approval(self):
        panel=UpdatesMixin();allowed=[False]
        panel.__dict__.update(update_request_id=8,updater=SimpleNamespace(approve=lambda rid: True),update_guard=SimpleNamespace(writer_ready=lambda:allowed[0]),set_label=lambda *_:None)
        self.assertFalse(panel._approve_update());allowed[0]=True;self.assertTrue(panel._approve_update());self.assertEqual(panel.update_phase,'installing')

    def test_peer_reader_keeps_handoff_waiting_after_own_child_exits(self):
        panel=UpdatesMixin();events=[];allowed=[False];alive=[True]
        process=SimpleNamespace(pid=12,poll=lambda:None if alive[0] else 0)
        def close():alive[0]=False;events.append('close')
        def submit(key,op,done,**kw):
            self.assertEqual(key,'updates.prepare');done(op())
        panel.__dict__.update(
            updater=SimpleNamespace(status=lambda:{'stage':'waiting','requestId':3,'build':14},approve=lambda rid:events.append('approve') or True),
            backend=SimpleNamespace(get=lambda _: {'ready':True,'quiesced':False},post=lambda *_:{'quiesced':True,'pid':12,'leaseToken':'held'},process=process,close=close),
            update_guard=SimpleNamespace(reserve=lambda _:events.append('reserve'),writer_ready=lambda:allowed[0]),
            ready=True,dirty=False,pending={},update_deadline=0,update_waiting=True,update_phase='waiting',modal_kind='Update Sprintwake Studios',update_message='',
            set_attr=lambda *_:None,set_label=lambda *_:None,submit=submit)
        panel.tick_updates()
        self.assertEqual(events,['reserve','close']);self.assertTrue(panel.update_waiting);self.assertTrue(panel.update_had_lease)
        self.assertEqual(panel.update_phase,'waiting')
        allowed[0]=True;panel.update_deadline=0;panel.tick_updates()
        self.assertEqual(events,['reserve','close','approve']);self.assertTrue(panel.cleaned)

    def test_abandoned_sdk_update_restarts_only_after_child_exit(self):
        events=[];alive=[True];panel=UpdatesMixin()
        panel.__dict__.update(update_request_id=3,update_had_lease=True,update_lease_token='held',update_waiting=True,update_phase='waiting',update_deadline=0,modal_kind='Update Sprintwake Studios',update_message='',
            updater=SimpleNamespace(status=lambda:{'stage':'error'},approve=lambda _:events.append('unsafe-approve')),
            backend=SimpleNamespace(process=SimpleNamespace(poll=lambda:None if alive[0] else 0)),
            update_guard=SimpleNamespace(cancel_update=lambda:events.append('unlock')),
            set_attr=lambda *_:None,set_label=lambda *_:None,_bootstrap=lambda:None,_booted=lambda _:None,
            submit=lambda key,*args,**kw:events.append(key))
        panel.tick_updates();self.assertEqual(events,[])
        alive[0]=False;panel.update_deadline=0;panel.tick_updates()
        self.assertEqual(events,['unlock','boot']);self.assertFalse(panel.update_waiting);self.assertIsNone(panel.update_lease_token)

    def test_migration_preserves_private_settings_and_explicit_external_runtime(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);legacy=root/'legacy';legacy.mkdir();bundle=root/'Sprintwake Studios.app'
            (legacy/'server-config.json').write_text(json.dumps({'workspace':str(root/'existing-work'),'llmKeys':{'mine':'test-private'}}))
            store=ConnectionStore(root/'prefs/connections.json');doc=defaults();doc['paths']['engine']=str(legacy);doc['paths']['python']=str(root/'custom-runtime/bin/python');doc['paths']['root']=str(root/'productions');store.save(doc)
            result=migrate_local_settings(legacy,bundle,store)
            self.assertEqual(result['paths']['python'],str(root/'custom-runtime/bin/python'));self.assertEqual(result['paths']['root'],str(root/'productions'))
            private=store.path.parent/'EngineSettings/server-config.json';self.assertEqual(json.loads(private.read_text())['llmKeys'],{'mine':'test-private'});self.assertEqual(private.stat().st_mode&0o777,0o600)
            private.write_text('{"kept":true}');migrate_local_settings(legacy,bundle,store);self.assertTrue(json.loads(private.read_text())['kept'])
            moved=rebase_bundle_paths(result,root/'Moved.app');self.assertEqual(moved['paths']['engine'],str(root/'Moved.app/Contents/Resources/Studio/engine'))

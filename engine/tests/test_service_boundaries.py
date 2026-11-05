# SPDX-License-Identifier: MIT
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from engine.server.app import Service, Handler
from engine.server.store import Conflict, default_shot
from engine.server.comfy_workflow import WorkflowClient, GenerationCancelled
from engine.comfy_nodes.pending import cancel_pending

class ServiceBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.service=Service(self.root/'Projects',self.root/'Work',media_profiles={'image':'none','video':'none'})
        self.service.resources.path=self.root/'gpu.lock'
        self.slug,self.board=self.service.store.create('Film')
    def test_director_context_has_no_media_paths_recipes_or_editorial_history(self):
        board=self.board; board['shots']=[default_shot()]; board['filmBible']={'locations':[{'id':'room','name':'A room','referenceImage':'/media/PRIVATE_LOCATION.png'}],'props':[{'id':'cup','image':{'path':'PRIVATE_PROP.png'}}]}
        board['shots'][0].update(outputs=['/media/PRIVATE_MOVIE.mp4'],recipe={'token':'PRIVATE_RECIPE'},takes=[{'password':'PRIVATE_TAKE'}])
        board['characters']=[{'id':'c1','name':'Ada','description':'Blue coat','voice':'PRIVATE_RECORDING.wav'}]
        self.service.store.put(self.slug,board)
        script=self.service.store.project_dir(self.slug)/'Script'
        (script/'structure.json').write_text(json.dumps({'revision':1,'undo':[],'redo':[],'state':{'sequences':[{'title':'Act','referenceImage':'PRIVATE_SEQUENCE','scenes':[{'title':'Scene','path':'PRIVATE_SCENE','shotIds':[]}]}]}}))
        (script/'sound.json').write_text(json.dumps({'revision':1,'undo':[],'redo':[],'state':{'tracks':[],'clips':[],'original':{'enabled':True,'path':'PRIVATE_SOUND'}}}))
        captured=[]
        language=SimpleNamespace(profile={'kind':'openai-compatible','url':'https://fixture.invalid','model':'test'},api_key='',chat=lambda messages,**kw:(captured.append(messages) or '{"message":"Ready","actions":[]}'))
        with patch.object(self.service,'language_service',return_value=language):
            self.assertEqual(self.service.director({'slug':self.slug,'message':'Inspect this script'})['actions'],[])
        serialized=json.dumps(captured)
        for value in ('PRIVATE_MOVIE','PRIVATE_RECIPE','PRIVATE_TAKE','PRIVATE_RECORDING','PRIVATE_LOCATION','PRIVATE_PROP','PRIVATE_SEQUENCE','PRIVATE_SCENE','PRIVATE_SOUND'):
            self.assertNotIn(value,serialized)
        self.assertIn('Blue coat',serialized)
    def test_assembly_does_not_overwrite_edit_while_joining(self):
        from PIL import Image
        shot=default_shot(); image=self.root/'image.png'; Image.new('RGB',(2,2)).save(image)
        ref=self.service.store.import_media(self.slug,image,'refs/picture.png')
        # Path selection is independent of codec validation, which media tests exercise.
        movie=self.service.store.project_dir(self.slug)/'shots/prior.mp4'; movie.parent.mkdir(exist_ok=True); movie.write_bytes(b'fixture')
        shot['outputs']=[self.service.store.ref(movie)['url']]; self.board['shots']=[shot]; self.service.store.put(self.slug,self.board)
        handler=object.__new__(Handler); handler.server=SimpleNamespace(service=self.service)
        def join(_clips,target):
            current=self.service.store.get(self.slug); current['sceneDescription']='Concurrent edit'
            self.service.store.put(self.slug,current); target.write_bytes(b'assembled fixture')
        with patch.object(self.service.film,'assert_assembly_ready'),patch('engine.server.media.join_clips',side_effect=join):
            with self.assertRaises(Conflict): handler.mutate_api('POST','/api/assemble',{'slug':self.slug})
        current=self.service.store.get(self.slug)
        self.assertEqual(current['sceneDescription'],'Concurrent edit'); self.assertNotIn('finalVideo',current)
        self.assertEqual(len(list(self.service.store.project_dir(self.slug).glob('Output/Video/film-*.mp4'))),1)
    def test_current_catalogue_anchor_is_saved_with_pre_edit_revision(self):
        from PIL import Image
        image=self.root/'image.png'; Image.new('RGB',(2,2)).save(image)
        ref=self.service.store.import_media(self.slug,image,'refs/picture.png'); shot=default_shot(); shot['startRef']=ref
        self.board['shots']=[shot]; self.service.store.put(self.slug,self.board)
        status=self.service.film_status(self.slug); identifier=status['imageCatalog'][0]['imageId']
        self.service.film.action({'slug':self.slug,'action':{'tool':'set_shot_anchors','shotId':shot['id'],'revision':status['anchorRevision'],
            'anchors':[{'imageId':'project:'+identifier,'frame':'last','strength':.65}]}})
        self.assertEqual(self.service.store.get(self.slug)['shots'][0]['imageAnchors'][0]['strength'],.65)

    def test_long_script_keeps_selected_context_and_all_opaque_ids_bounded(self):
        board=self.board; board['shots']=[]
        for index in range(1000):
            shot=default_shot(); shot.update(id='shot-'+str(index),prompt='A quiet cinematic action. '*30)
            board['shots'].append(shot)
        self.service.store.put(self.slug,board)
        captured=[]
        language=SimpleNamespace(profile={'kind':'openai-compatible','url':'https://fixture.invalid','model':'test'},api_key='',chat=lambda messages,**kw:(captured.append(messages) or '{"message":"Ready","actions":[]}'))
        with patch.object(self.service,'language_service',return_value=language):
            self.service.director({'slug':self.slug,'message':'Help with this shot','selectedShotId':'shot-500'})
        system=captured[0][0]['content']; context=json.loads(system.split('CONTEXT:\n',1)[1])
        self.assertEqual(context['project']['shotCount'],1000)
        self.assertTrue(context['project']['contextShotsArePartial'])
        self.assertIn('shot-500',[shot['id'] for shot in context['project']['shots']])
        self.assertEqual(len(context['shots']),1000)

    def test_status_reconciles_only_known_finished_work_without_replaying_it(self):
        from engine.server.store import atomic_json
        atomic_json(self.service.provider._journal,{'a'*32:{'state':'active','promptId':'owned'}})
        atomic_json(self.service.provider.ltx_process_journal,{'pid':2147483000,'phase':'running'})
        calls=[]
        def metadata(method,path,body=None):
            calls.append((method,path))
            if path=='/queue': return {'queue_running':[],'queue_pending':[]}
            return {'owned':{'status':{'completed':True,'status_str':'success'}}}
        with patch.object(self.service.provider,'_request',side_effect=metadata):
            self.assertFalse(self.service.status()['busy'])
        self.assertTrue(all(method=='GET' for method,path in calls))
        self.assertFalse(self.service.provider.has_unresolved())
        self.assertFalse(self.service.provider.ltx_process_journal.exists())

    def test_malformed_provider_journal_keeps_status_available_and_blocks_models(self):
        self.service.provider._journal.write_text('{"invalid":{"state":"unknown"}}')
        status=self.service.status()
        self.assertTrue(status['busy']); self.assertTrue(status['resourceRecoveryRequired'])

class PendingCancellation(unittest.TestCase):
    def test_atomic_receipt_only_removes_matching_never_started_prompt(self):
        client='a'*32; rows=[[1,'own',{}, {'client_id':client}],[2,'foreign',{}, {'client_id':'b'*32}]]
        class Queue:
            def delete_queue_item(self,predicate):
                for item in list(rows):
                    if predicate(item): rows.remove(item); return True
                return False
        queue=Queue()
        self.assertFalse(cancel_pending(queue,'foreign',client)['cancelledBeforeStart'])
        self.assertTrue(cancel_pending(queue,'own',client)['cancelledBeforeStart'])
        self.assertEqual([row[1] for row in rows],['foreign'])
        self.assertFalse(cancel_pending(queue,'own',client)['cancelledBeforeStart'])
    def test_positive_receipt_releases_journal_without_inventing_running_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            client=WorkflowClient('http://127.0.0.1:9',state_dir=directory)
            def request(method,path,body=None):
                if path=='/queue': return {'queue_running':[],'queue_pending':[]}
                if path=='/prompt': return {'prompt_id':'own'}
                if path=='/sprintwake/cancel-pending': return dict(body,cancelledBeforeStart=True)
                return {}
            with patch.object(client,'_request',side_effect=request):
                ident,_=client.submit({'1':{}})
                with self.assertRaises(GenerationCancelled): client.wait_terminal(ident,cancel=lambda:True,poll=.001,timeout=1)
            self.assertFalse(client.has_unresolved())

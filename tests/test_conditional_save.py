import copy
import tempfile
import unittest
from pathlib import Path

from backend import Backend, BackendError
from studio_state import authoring_digest


class ConditionalSaveTests(unittest.TestCase):
    def test_actual_endpoint_refuses_edit_between_read_and_put(self):
        with tempfile.TemporaryDirectory() as temp:
            backend=Backend(Path(temp))
            try:
                backend.start()
                created=backend.post('/api/boards',{'name':'Conditional writer test'})
                slug, initial=created['slug'],created['board']
                path='/api/boards/'+slug
                newer=copy.deepcopy(initial)
                newer['sceneDescription']='External author edit must survive'
                backend.put(path,newer)
                candidate=copy.deepcopy(initial)
                candidate['sceneDescription']='Stale model reply'
                with self.assertRaises(BackendError) as failure:
                    backend.put(path,candidate,expected_digest=authoring_digest(initial),expected_updated_at=initial['updatedAt'])
                self.assertEqual(failure.exception.status,409)
                current=backend.get(path)['board']
                self.assertEqual(current['sceneDescription'],newer['sceneDescription'])
                candidate=copy.deepcopy(current)
                candidate['soundscape']='Quiet forest'
                backend.put(path,candidate,expected_digest=authoring_digest(current),expected_updated_at=current['updatedAt'])
                self.assertEqual(backend.get(path)['board']['soundscape'],'Quiet forest')
            finally:
                backend.close()


    def test_resume_after_real_setup_put_and_server_normalization(self):
        from director_authoring import author_project
        from workspace import Workspace
        class Crash(BaseException): pass
        with tempfile.TemporaryDirectory() as temp:
            backend=Backend(Path(temp))
            workspace=Workspace(Path(temp))
            try:
                backend.start()
                created=backend.post('/api/boards',{'name':'Recover writer'})
                slug=created['slug']
                class DirectorFixture:
                    crash=True
                    calls=0
                    def get(self,path): return backend.get(path)
                    def put(self,path,payload,**kwargs):
                        value=backend.put(path,payload,**kwargs)
                        if self.crash:
                            self.crash=False
                            raise Crash()
                        return value
                    def chat(self,payload):
                        self.calls+=1
                        if self.calls==1:
                            return {'proposalStatus':'ready','actions':[
                                {'tool':'set_board_fields','fields':{'sceneDescription':'En maur finner hjem.','filmBible':{'acts':[{'id':'act1','title':'Hjemreisen','description':'Mauren finner hjem.'}],'locations':[{'id':'loc','title':'Skogen','description':'Mose under et tre.'}]}}},
                                {'tool':'add_character','character':{'name':'Maur','description':'Liten brun maur.'}}]}
                        board=backend.get('/api/boards/'+slug)['board']
                        return {'proposalStatus':'ready','actions':[{'tool':'add_shot','shot':{'title':'Oppvåkning','prompt':'A brown ant slowly rises on wet moss. Static wide shot, soft daylight and gentle rain, no speech.','start':'The ant lies on moss.','action':'The ant rises.','end':'The ant stands on moss.','camera':'Locked wide shot','characterIds':[board['characters'][0]['id']],'locationId':'loc','directorScene':1,'frames':121}}]}
                fixture=DirectorFixture()
                with self.assertRaises(Crash):
                    author_project(fixture,workspace,slug,'En maur finner hjem.',1)
                canonical=backend.get('/api/boards/'+slug)['board']
                self.assertEqual(len(canonical['characters']),1)
                self.assertIn('voiceText',canonical['characters'][0])
                result=author_project(fixture,workspace,slug,'En maur finner hjem.',1)
                self.assertEqual(result['status'],'done',result.get('error'))
                self.assertEqual(len(result['board']['characters']),1)
                self.assertEqual(len(result['board']['shots']),1)
                self.assertEqual(fixture.calls,2)
            finally:
                backend.close()

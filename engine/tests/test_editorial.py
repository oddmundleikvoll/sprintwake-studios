import json,tempfile,unittest
from pathlib import Path
from engine.server.raven_editorial import editorial_context
class EditorialPrivacyTests(unittest.TestCase):
 def test_nested_media_and_history_are_not_projected(self):
  with tempfile.TemporaryDirectory() as t:
   script=Path(t)/'film/Script'; script.mkdir(parents=True)
   doc={'format':'raven-cut','version':1,'fps':24,'revision':1,'clips':[{'shotId':'s1','source':{'id':'take','video':'Input/secret.mov','recipe':{'token':'PRIVATE'}} ,'inFrame':0,'outFrame':24,'sourceFrames':24,'included':True}], 'undo':[{'label':'PRIVATE HISTORY','clips':[]}], 'redo':[]}
   (script/'cut.json').write_text(json.dumps(doc)); encoded=json.dumps(editorial_context(Path(t),'film'))
   self.assertNotIn('secret',encoded); self.assertNotIn('PRIVATE',encoded); self.assertNotIn('video',encoded)
 def test_script_symlink_and_oversized_journal_fail_closed(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t); outside=Path(t).parent/'outside-editorial'; outside.mkdir(exist_ok=True)
   (root/'Projects').mkdir(); (root/'Projects/film').mkdir(); (root/'Projects/film/Script').symlink_to(outside,target_is_directory=True)
   with self.assertRaises(ValueError): editorial_context(root/'Projects','film')
  with tempfile.TemporaryDirectory() as t:
   script=Path(t)/'film/Script'; script.mkdir(parents=True); (script/'cut.json').write_bytes(b'{' + b' '*(4*1024*1024))
   self.assertEqual(editorial_context(Path(t),'film')['cut']['status'],'truncated')

 def test_wrong_runtime_goal_type_does_not_leak_nested_state(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory); script=root/'film/Script'; script.mkdir(parents=True)
   (script/'structure.json').write_text(json.dumps({'revision':1,'undo':[],'redo':[],'state':{'sequences':[],'targetFrames':{'path':'PRIVATE'}}}))
   result=editorial_context(root,'film')
   self.assertEqual(result['structure']['status'],'invalid'); self.assertNotIn('PRIVATE',json.dumps(result))

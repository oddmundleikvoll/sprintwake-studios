import json, unittest
from engine.server import raven_contract as c
class ContractTests(unittest.TestCase):
 def test_catalogue_is_structured_and_stage_bound(self):
  all_tools=c.catalogue(); self.assertIsInstance(all_tools,list); names={x['name'] for x in all_tools}; self.assertIn('create_project',names)
  self.assertEqual({x['name'] for x in c.catalogue('shots')},{'add_shot'})
  for item in all_tools: self.assertIn('schema',item); self.assertEqual(item['schema']['properties']['tool']['const'],item['name'])
 def test_model_reply_returns_response_and_diagnostics(self):
  reply,errors=c.parse_model_reply(json.dumps({'message':'ok','actions':[]})); self.assertEqual(reply['message'],'ok'); self.assertEqual(errors,[])
  reply,errors=c.parse_model_reply('{bad}'); self.assertEqual(reply['actions'],[]); self.assertTrue(errors)
 def test_validation_is_strict_and_bounded(self):
  actions,errors=c.validate_actions([{'tool':'status','extra':1}]); self.assertFalse(actions); self.assertTrue(errors)
  actions,errors=c.validate_actions([{'tool':'add_shot','title':'x'}],has_project=True); self.assertEqual(actions[0]['tool'],'add_shot'); self.assertFalse(errors)

 def test_director_cannot_insert_literal_media_paths_into_shot_actions(self):
  for field in ('referenceImages','imageAnchors'):
   clean,errors=c.validate_actions([{'tool':'add_shot',field:[{'path':'/media/other/private.png'}]}],has_project=True,slug='film')
   self.assertEqual(clean,[]); self.assertTrue(errors)

import unittest
from engine.server.comfy_workflow import ComfyProvider, validate_profile
from engine.server.providers import local_qwen_profile, local_ltx_profile
class ProviderTests(unittest.TestCase):
 def test_wrappers_require_explicit_graph_and_output_declaration(self):
  with self.assertRaises(ValueError): local_qwen_profile({})
  with self.assertRaises(ValueError): local_ltx_profile({})
  profile=local_qwen_profile({'1':{'class_type':'SaveImage','inputs':{'text':''}}},{'prompt':{'node':'1','input':'text'}},{'node':'1','key':'images'})
  self.assertEqual(validate_profile(profile)['output']['key'],'images')
 def test_declared_bindings_only_and_no_global_controls(self):
  profile={'version':1,'id':'x','label':'x','kind':'image','workflow':{'1':{'class_type':'X','inputs':{'text':''}}},'bindings':{'prompt':{'node':'1','input':'text'}},'output':{'node':'1','key':'images'},'capabilities':{}}
  self.assertEqual(set(validate_profile(profile)['bindings']),{'prompt'})
  provider=ComfyProvider('http://127.0.0.1:9',state_dir='.')
  self.assertFalse(any(name in {'interrupt','free'} for name in ('interrupt','free') if hasattr(provider,name)))

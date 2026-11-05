import unittest
from engine.server.providers import qwen_graph, NativeCapabilityError

class NativeGraphTests(unittest.TestCase):
    def test_qwen_graph_uses_only_advertised_inputs(self):
        info={name:{'input':{'required':{}}} for name in {'UNETLoader','CLIPLoader','VAELoader','TextEncodeQwenImage21','EmptyLatentImage','RandomNoise','ManualSigmas','KSamplerSelect','QwenImage21Cache','CFGGuider','SamplerCustomAdvanced','VAEDecode','SaveImage'}}
        graph=qwen_graph(info,'scene',768,512,3,['a.png']); self.assertEqual(graph['1']['class_type'],'UNETLoader'); self.assertEqual(graph['6']['inputs']['noise_seed'],3)
    def test_missing_capability_is_explicit(self):
        with self.assertRaises(NativeCapabilityError): qwen_graph({'Other':{'input':{'required':{}}}},'x',1,1,1)

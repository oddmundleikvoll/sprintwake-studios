import types, unittest
from engine.comfy_nodes.memory import _release, SprintwakeReleaseMemory

class MemoryTests(unittest.TestCase):
    def test_cleanup_order_and_mps_barrier(self):
        events=[]
        mm=types.SimpleNamespace(soft_empty_cache=lambda:events.append('cache'),unload_all_models=lambda:events.append('unload'),current_loaded_models=[])
        gcmod=types.SimpleNamespace(collect=lambda:events.append('gc'),get_objects=lambda:[])
        torch=types.SimpleNamespace(mps=types.SimpleNamespace(synchronize=lambda:events.append('sync'),empty_cache=lambda:events.append('mps')))
        execution=types.SimpleNamespace(CacheType=types.SimpleNamespace(CLASSIC='classic',NONE='none'),PromptExecutor=type('PE',(),{}))
        self.assertIn('complete',_release(mm,torch,gcmod,execution)); self.assertEqual(events,['cache','unload','gc','cache','sync','mps'])
    def test_fail_closed_when_public_api_missing(self):
        with self.assertRaises(RuntimeError): _release(types.SimpleNamespace(),None,types.SimpleNamespace(get_objects=lambda:[],collect=lambda:None),types.SimpleNamespace(CacheType=types.SimpleNamespace(CLASSIC='classic',NONE='none'),PromptExecutor=type('PE',(),{})))
    def test_node_is_output_barrier_without_inputs(self):
        self.assertEqual(SprintwakeReleaseMemory.INPUT_TYPES(),{'required':{}}); self.assertTrue(SprintwakeReleaseMemory.OUTPUT_NODE)
    def test_lru_executor_fails_closed(self):
        PE=type('PE',(),{}); ex=PE(); ex.cache_type='lru'; ex.caches=types.SimpleNamespace(all=[])
        gcmod=types.SimpleNamespace(get_objects=lambda:[ex],collect=lambda:None)
        mm=types.SimpleNamespace(soft_empty_cache=lambda:None,unload_all_models=lambda:None)
        execution=types.SimpleNamespace(CacheType=types.SimpleNamespace(CLASSIC='classic',NONE='none'),PromptExecutor=PE)
        with self.assertRaises(RuntimeError): _release(mm,None,gcmod,execution)

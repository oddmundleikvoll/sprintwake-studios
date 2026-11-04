import tempfile, unittest
from pathlib import Path
from server.backends.comfy_workflow_backend import ComfyWorkflowBackend
from server.backends.base import ShotPaths

def p():
    return {"version":1,"id":"demo","label":"Demo","kind":"image","workflow":{"1":{"class_type":"KSampler","inputs":{"text":""}}},"bindings":{"prompt":{"node":"1","input":"text"}},"output":{"node":"1","key":"images"},"capabilities":{}}

class BackendTests(unittest.TestCase):
    def test_prepare_is_portable_and_rejects_unbound_anchor(self):
        with tempfile.TemporaryDirectory() as d:
            b=ComfyWorkflowBackend(p(), "http://127.0.0.1:8188", d)
            paths=ShotPaths(Path(d)/"shot", Path(d), Path(d))
            spec=b.prepare({"id":"s1","prompt":"hello"},{},paths)
            self.assertEqual(spec.expected_outputs[0].name,"image.png")
            with self.assertRaises(ValueError): b.prepare({"id":"s1","prompt":"x","startRef":"bad"},{},paths)

if __name__ == "__main__": unittest.main()

import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from server.comfy_workflow import validate_profile, profile_digest, WorkflowClient

def profile(kind="image"):
    return {"version":1,"id":"demo.workflow","label":"Demo Norsk","kind":kind,"workflow":{"1":{"class_type":"KSampler","inputs":{"text":"","seed":0}}},"bindings":{"prompt":{"node":"1","input":"text"},"seed":{"node":"1","input":"seed"}},"output":{"node":"1","key":"images"},"capabilities":({} if kind=="image" else {"fps":24,"frameRule":{"kind":"any","step":1,"offset":0,"minimum":1,"maximum":121},"maxFrames":121,"sizeAlign":64})}

class WorkflowContractTests(unittest.TestCase):
    def test_profile_and_digest_change(self):
        p=validate_profile(profile()); self.assertEqual(p["id"], "demo.workflow")
        q=dict(p); q["label"]="Other"; self.assertNotEqual(profile_digest(p), profile_digest(q))
    def test_credentials_rejected(self):
        p=profile(); p["workflow"]["1"]["inputs"]["api_key"]="secret"
        with self.assertRaises(ValueError): validate_profile(p)
        for key in ('X-API-Key','bearerToken','access_token','Proxy-Authorization'):
            p=profile();p['workflow']['1']['inputs']['headers']={key:'secret'}
            with self.subTest(key=key),self.assertRaises(ValueError):validate_profile(p)

    def test_pending_job_recovers_by_owned_client_id_and_never_submits(self):
        with tempfile.TemporaryDirectory() as temp:
            c=WorkflowClient('http://example.test',state_dir=Path(temp));c._save_pending({'clientId':'owned','promptId':None})
            calls=[]
            def req(method,path,*args):
                calls.append((method,path))
                if path=='/queue':return {'queue_running':[],'queue_pending':[]}
                rec={'prompt':[1,'job-owned',{}, {'client_id':'owned'}], 'status':{'completed':True,'status_str':'success'}}
                return {'job-owned':rec}
            c._request=req;c.assert_released()
            self.assertFalse(c.uncertain_path.exists());self.assertTrue(all(method=='GET' for method,_ in calls))
    def test_preflight_is_get_only(self):
        c=WorkflowClient("http://example.test")
        calls=[]
        def req(method,path,data=None,headers=None):
            calls.append((method,path)); return ({"KSampler":{}} if path=="/object_info" else {"queue_running":[],"queue_pending":[]})
        c._request=req
        self.assertTrue(c.preflight(profile())["ok"]); self.assertEqual([x[0] for x in calls], ["GET","GET"])
    def test_references_need_binding(self):
        p=profile(); p["capabilities"]["supportsStartAnchor"]=True
        with self.assertRaises(ValueError): validate_profile(p)

if __name__ == "__main__": unittest.main()

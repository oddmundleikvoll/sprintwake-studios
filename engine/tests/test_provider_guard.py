import threading,unittest
from types import SimpleNamespace
from unittest.mock import patch
from server.film import GuardedLLM

class ProviderGuardTests(unittest.TestCase):
    def guard(self,inner):
        orch=SimpleNamespace(_start_lock=threading.RLock(),_llm_active=0,busy=False,stills_busy=False,_thread=None)
        return GuardedLLM(inner,orch),orch

    def test_provider_errors_redact_credentials_before_diagnostics(self):
        def fail(*_args,**_kw):raise RuntimeError('provider echoed fixture-secret')
        service,orch=self.guard(SimpleNamespace(api_key='fixture-secret',complete=fail))
        with self.assertRaisesRegex(RuntimeError,r'provider echoed \[redacted\]'):service.complete('system','user')
        self.assertEqual(orch._llm_active,0)

    def test_text_only_profile_refuses_image_before_admission(self):
        service,orch=self.guard(SimpleNamespace(vision=False))
        with self.assertRaisesRegex(RuntimeError,'bare tekst'):service.complete_with_image('system','user','image.png')
        self.assertEqual(orch._llm_active,0)

    def test_optional_sdk_disables_redirects_for_its_api_key_header(self):
        from server.llm import AnthropicLLM
        sent={}
        def transport(**kw):sent.update(kw);return object()
        fake=SimpleNamespace(Anthropic=lambda **kw:kw)
        with patch.dict('sys.modules',{'anthropic':fake,'httpx':SimpleNamespace(Client=transport)}):
            _,client=AnthropicLLM('own','Own','https://example.test','model')._client(5)
        self.assertIs(sent['follow_redirects'],False);self.assertIn('http_client',client)

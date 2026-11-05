# SPDX-License-Identifier: MIT
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import base64
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import Backend, BackendError
from studio_state import authoring_digest

class OwnedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'SPRINTWAKE_UPDATE_LOCK_ROOT': str(self.root/'Locks')})
        self.env.start(); self.addCleanup(self.env.stop)
        settings = self.root/'Settings'; settings.mkdir()
        for name in ('llm-services.json','tts-services.json','soundtrack-services.json'):
            (settings/name).write_text('{"services":[]}')
        config = {'version':1,'paths':{},'comfyUrl':'http://127.0.0.1:1',
                  'selected':{'llm':'none','image':'none','video':'none'},'llmProfiles':[],'workflowProfiles':[]}
        self.backend = Backend(self.root/'Studio', engine_root=ROOT/'engine',
            runtime_python=ROOT/'runtime/bundle-venv/bin/python', workspace=self.root/'Work', config_dir=settings,
            connection_settings=config, credential_loader=lambda _:'', llm='none', startup_timeout=10)
        self.addCleanup(self.backend.close)
        self.info = self.backend.start()
    def test_real_owned_child_create_upload_save_and_conflict(self):
        created = self.backend.post('/api/boards', {'name':'Ny åpning'})
        slug, board = created['slug'], created['board']
        self.assertEqual(self.backend.get('/api/boards/'+slug)['board'], board)
        image = self.root/'chosen.png'
        from PIL import Image
        Image.new('RGB',(4,4),'orange').save(image)
        ref = self.backend.upload(slug, image)
        self.assertTrue(ref['path'].startswith(slug+'/refs/'))
        edited = dict(board, sceneDescription='En kort beskrivelse.')
        saved = self.backend.put('/api/boards/'+slug, edited, expected_digest=authoring_digest(board))['board']
        with self.assertRaises(BackendError) as failure:
            self.backend.put('/api/boards/'+slug, board, expected_digest=authoring_digest(board))
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual(self.backend.get('/api/boards/'+slug)['board'], saved)
        self.assertFalse(self.backend.get('/api/status')['busy'])
    def test_update_control_is_authenticated_and_gates_mutations(self):
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError) as failure:
            urlopen(self.backend.base_url+'/api/desktop/update-status')
        self.assertEqual(failure.exception.code, 403)
        request = Request(self.backend.base_url+'/api/desktop/update-quiesce', data=b'{}',
                          headers={'Content-Type':'application/json','X-Sprintwake-Update':self.backend._update_token})
        with urlopen(request) as response: lease=json.load(response)
        self.assertTrue(lease['quiesced'])
        with self.assertRaises(BackendError) as blocked: self.backend.post('/api/boards', {'name':'Blocked'})
        self.assertEqual(blocked.exception.status,409)
        request=Request(self.backend.base_url+'/api/desktop/update-resume', data=json.dumps({'leaseToken':lease['leaseToken']}).encode(),
                        headers={'Content-Type':'application/json','X-Sprintwake-Update':self.backend._update_token})
        with urlopen(request) as response: self.assertFalse(json.load(response)['quiesced'])
        self.assertIn('slug',self.backend.post('/api/boards',{'name':'Resumed'}))

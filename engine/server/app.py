# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Loopback API for the independent Sprintwake production service."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

from .store import Store, Conflict, MAX_DOCUMENT, BOARD_INPUTS, SHOT_INPUTS, atomic_json, authoring_digest, read_json, safe_path
from .jobs import Scheduler
from .film import FilmController
from .comfy_workflow import ComfyProvider, profile_digest
from .engine_provider import EngineProvider
from .llm import LanguageService
from .audio import AudioServices
from .resources import Resources
from .language_lifecycle import managed_language
from .raven_projects import trash_project, restore_project


_CONTEXT_HIDDEN = frozenset('path url ref image referenceImage referenceImages styleRefs startRef endRef imageAnchors voice voiceText outputs takes recipe workflowRecipe history undo redo dialogueAudioUrl dubUrl logUrl finalVideo'.split())
def director_projection(value, depth=0):
    if depth>12: raise ValueError('Director context is nested too deeply')
    if isinstance(value,dict):
        return {key:director_projection(item,depth+1) for key,item in value.items() if key not in _CONTEXT_HIDDEN and key in set(BOARD_INPUTS+SHOT_INPUTS+['shots','id','title','description','name','theme','storyArc','acts','locations','props','constraints','appearance','role','state','notes','targetFrames'])}
    if isinstance(value,list):
        if len(value)>20_000: raise ValueError('Director context collection is too large')
        return [director_projection(item,depth+1) for item in value]
    if value is None or type(value) in {bool,int,float}: return value
    if isinstance(value,str): return value[:16_000]
    return None


class Service:
    def __init__(self, data_dir, workspace, *, media_profiles=None, config_dir=None,
                 llm_config_dir=None, llm='none', comfy_url='http://127.0.0.1:8188',
                 update_token='', provider=None):
        self.config_dir = Path(config_dir) if config_dir else Path(data_dir).parent / 'System/EngineSettings'
        self.llm_config_dir = Path(llm_config_dir) if llm_config_dir else self.config_dir
        self.llm_id = llm
        self.profiles = media_profiles or {'image': 'qwen-local', 'video': 'ltx-local'}
        defaults = {}
        selected = self.profiles.get('video')
        if isinstance(selected, dict):
            defaults.update(model=selected['id'], modelProvider='comfy-workflow', modelProfileDigest=profile_digest(selected))
        self.store = Store(data_dir, workspace, defaults)
        self.scheduler = Scheduler()
        self.admission = self.scheduler.admission_lock
        self.provider = provider or EngineProvider(comfy_url, config_dir=self.config_dir,
            work_dir=Path(workspace) / 'Jobs', state_dir=Path(os.environ.get('RAVEN_COMFY_STATE_DIR', str(Path(data_dir).parent / 'System/ProviderJobs'))),
            api_key=os.environ.get('RAVEN_COMFY_API_KEY', ''))
        self.resources = Resources(Path.home()/'Library/Application Support/Raven Studio/Locks/gpu-admission.lock', self.provider)
        self.scheduler.resource_context = self.resources.lease
        self.audio = AudioServices(self.store,self.scheduler,self.config_dir)
        self.film = FilmController(self.store, self.scheduler, self.provider, self.profiles)
        self.update_token = update_token
        self.quiesced = False
        self.closing = False
        self.active_mutations = 0
        self.language_work = 0
        self.lease = None
        self.images = {}
        self._image_documents = {}
        self.scheduler.before_start = self._admit_job

    def _admit_job(self):
        if self.quiesced or self.closing or self.language_work or self.active_mutations>1:
            raise Conflict('Model resources are unavailable until existing work has drained')
        return True

    def active_jobs(self):
        with self.admission:
            return [j for j in self.scheduler._jobs.values() if j.status in {'queued', 'running', 'cancelling'}]

    @contextmanager
    def mutation(self):
        with self.admission:
            if self.quiesced or self.closing:
                raise Conflict('The service is draining for an application update')
            self.active_mutations += 1
        try:
            yield
        finally:
            with self.admission:
                self.active_mutations -= 1

    def assert_idle(self):
        if self.active_jobs() or self.language_work or self.active_mutations>1:
            raise Conflict('Wait until the active production or language operation has drained')

    def info(self):
        models = []
        selected_ids = {}
        for kind, local in (('image', 'qwen-image-2.1-turbo'), ('video', 'ltx-2.5')):
            value = self.profiles.get(kind, 'none')
            ident = value.get('id') if isinstance(value, dict) else local if value != 'none' else None
            selected_ids[kind] = ident
            if ident:
                models.append({'id': ident, 'label': value.get('label', ident) if isinstance(value, dict) else ident,
                               'kind': kind, 'fps': 24 if kind == 'video' else None})
        return {'engine': 'Sprintwake', 'version': 1,
                'backend': {'id': 'sprintwake', 'label': 'Sprintwake production engine', 'ready': True},
                'models': models, 'llms': self.language_profiles(), 'llm': {'default': self.llm_id, 'services': [{'id':'none','label':'No language model'}]+self.language_profiles()}, 'voices': [], 'soundtracks': [],
                'connections': {'nativeFilm': self.profiles.get('image') == 'qwen-local' and self.profiles.get('video') == 'ltx-local',
                                'imageModel': selected_ids['image'], 'videoModel': selected_ids['video']}}

    def language_profiles(self):
        data = read_json(self.llm_config_dir / 'llm-services.json', {'services': []})
        values = data.get('services', []) if isinstance(data, dict) else []
        # Never expose private configuration or secret/environment key names via /api/info.
        return [{k: p.get(k) for k in ('id', 'label', 'kind', 'model', 'vision')} for p in values if isinstance(p, dict)]

    def language_service(self, selected=None):
        selected = selected or self.llm_id
        if selected == 'none':
            raise ValueError('Choose a language model before asking the director')
        data = read_json(self.llm_config_dir / 'llm-services.json', {'services': []})
        profiles = data.get('services', []) if isinstance(data, dict) else []
        profile = next((p for p in profiles if isinstance(p, dict) and p.get('id') == selected), None)
        if not profile:
            raise ValueError('The selected language profile is unavailable')
        profile=dict(profile)
        lifecycle=read_json(self.config_dir/'server-config.json',{}).get('languageLifecycle',{}).get(selected)
        if isinstance(lifecycle,dict):
            if set(lifecycle)-{'lifecycle','lifecycleTimeout'}: raise ValueError('Unknown managed language settings')
            profile.update(lifecycle)
        elif lifecycle: profile.update(lifecycle=lifecycle,lifecycleTimeout=720)
        return LanguageService(profile, api_key=os.environ.get('RAVEN_LLM_API_KEY', '') if selected==self.llm_id else '')

    def uncertain_resources(self):
        try:
            check=getattr(self.provider,'has_unresolved',None)
            if check and check(): return True
            if self.provider.ltx_process_journal.exists(): return True
            journal=read_json(self.resources.language_journal,{})
            return bool(journal and journal.get('state')!='drained')
        except (ValueError,OSError,AttributeError,RuntimeError): return True

    def status(self):
        jobs = self.active_jobs()
        if not jobs:
            # Reconcile only recorded owned work; this performs no discovery or inference.
            try:
                if self.provider.has_unresolved(): self.provider.recover()
                from .ltx_runner import reconcile_process_journal
                reconcile_process_journal(self.provider.ltx_process_journal)
            except (OSError,ValueError,RuntimeError): pass
        current = jobs[0] if jobs else next(reversed(self.scheduler._jobs.values()), None)
        result = {'busy': bool(jobs) or self.uncertain_resources(), 'resourceRecoveryRequired':self.uncertain_resources(), 'languageWork': self.language_work, 'quiesced': self.quiesced}
        if current:
            result.update(slug=current.slug, jobId=current.id, status=current.status,
                          phase=current.phase, progress=current.completed / max(1, current.total), error=current.error)
        return result

    def film_status(self, slug):
        board = self.store.get(slug)
        project = self.film.status(slug)
        digest = authoring_digest(board)
        project.setdefault('anchorRevision', digest)
        if not project.get('revision'):
            project['revision'] = digest
        project.setdefault('continuationAnchor', False)
        project.setdefault('plannedFilm', {})
        project['canProduce'] = project.get('canProduce', True) and bool(board.get('shots'))
        from .ltx_anchors import image_catalog
        images=image_catalog(board,self.store.data_dir)
        return {'project': project, 'anchorRevision': project['anchorRevision'], 'gpuBusy': bool(self.active_jobs()),
                'imageCatalog':[dict(value,imageId=key) for key,value in images.items()]}

    def image_list(self, slug):
        self.store.get(slug)
        path = safe_path(self.store.project_dir(slug), 'System', 'generated-images.json')
        legacy=read_json(safe_path(self.store.project_dir(slug),'images.json'),[])
        document = read_json(path, {'images': legacy if isinstance(legacy,list) else []})
        own = self._image_documents.get(slug, document)
        job = copy.deepcopy(self.images.get(slug))
        if job:
            current = self.scheduler.status(slug)
            if current and current.id == job['jobId']:
                job.update(status={'completed':'done','cancelled':'interrupted'}.get(current.status,current.status),
                           phase=current.phase, error=current.error, busy=current.status in {'queued','running','cancelling'},
                           progress=100*current.completed/max(1,current.total))
        return {'images': own.get('images', []), 'job': job,
                'busy': bool(job and any(j.id == job.get('jobId') for j in self.active_jobs()))}

    def generate_image(self, payload):
        slug = payload.get('slug')
        self.store.get(slug)
        prompt = payload.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 32_000:
            raise ValueError('A bounded image prompt is required')
        profile = self.profiles.get('image')
        if not isinstance(profile, dict):
            factory = getattr(self.provider, 'local_qwen_profile', None)
            if not factory or profile != 'qwen-local':
                raise ValueError('The selected image provider is not configured')
            profile = factory()
        references = [self.store.media_path(ref) for ref in payload.get('references', [])]
        width, height = payload.get('width', 1536), payload.get('height', 1024)
        if any(type(n) is not int or n < 64 or n > 4096 for n in (width, height)):
            raise ValueError('Invalid image dimensions')
        def work(job):
            ident = uuid.uuid4().hex
            document = copy.deepcopy(self.image_list(slug))
            record = {'id': ident, 'prompt': prompt, 'seed': payload.get('seed'), 'status': 'running',
                      'model': profile.get('id','qwen-image-2.1-turbo')}
            document['images'].append(record)
            self._image_documents[slug] = document
            try:
                result = self.provider.generate(profile, prompt, payload.get('seed'), width=width, height=height,
                    references=references, cancel=job.cancel_event.is_set)
                media = result.get('media', [])
                if not media:
                    raise ValueError('Image provider returned no decoded output')
                source = Path(media[0])
                ref = self.store.import_media(slug, source, f'images/{ident}/image{source.suffix.lower()}')
                record.update(status='done', ref=ref, path=ref['path'], recipe=result.get('recipe', {}), workflowRecipe=result.get('recipe', {}))
                atomic_json(safe_path(self.store.project_dir(slug), 'images', ident, 'recipe.json', create_parent=True), result.get('recipe', {}))
            except Exception as error:
                record.update(status='cancelled' if job.cancel_event.is_set() else 'failed', error=str(error))
                raise
            finally:
                atomic_json(safe_path(self.store.project_dir(slug), 'System', 'generated-images.json', create_parent=True), {'images': document['images']})
        job = self.scheduler.submit(slug, 'image', work)
        self.images[slug] = {'jobId': job.id, 'slug': slug, 'status': 'queued', 'busy': True}
        return {'job': self.images[slug]}

    def director(self, payload):
        from . import raven_contract as contract
        from .raven_editorial import editorial_context
        message = payload.get('message')
        if not isinstance(message, str) or not message.strip() or len(message) > 100_000:
            raise ValueError('A bounded director message is required')
        slug = payload.get('slug')
        board = self.store.get(slug) if slug else None
        projected=director_projection(board)
        if board and len(board.get('shots',[]))>24:
            shots=projected.get('shots',[])
            selected=next((i for i,shot in enumerate(shots) if shot.get('id')==payload.get('selectedShotId')),None)
            projected['shots']=shots[max(0,selected-8):selected+16] if selected is not None else shots[:6]+shots[-18:]
            projected.update(shotCount=len(shots),contextShotsArePartial=True)
        context = {'projects': self.store.list(), 'project': projected, 'modelConnections': self.info()['connections']}
        if board:
            context['editorial'] = editorial_context(self.store.data_dir, slug, board)
            context.update(locations=director_projection((board.get('filmBible') or {}).get('locations',[])), props=director_projection((board.get('filmBible') or {}).get('props',[])))
            from .ltx_anchors import image_catalog
            context['images']=[dict(id='project:'+key,label=Path(value['path']).name) for key,value in image_catalog(board,self.store.data_dir).items()]
            context['images'] += [dict(id='generated:'+str(record['id']),label=str(record.get('prompt','Generated image'))[:200]) for record in self.image_list(slug)['images'] if record.get('id') and record.get('status')=='done']
            context.update(shots=[s['id'] for s in board.get('shots', [])], characters=[c['id'] for c in board.get('characters', []) if c.get('id')])
        library=read_json(safe_path(self.store.data_dir.parent,'Library','index.json'),[])
        context['sharedLibrary']=[{'id':'shared:'+str(item['id']),'name':str(item.get('name','Image'))[:200]} for item in library if isinstance(item,dict) and item.get('kind')=='image' and item.get('id')] if isinstance(library,list) else []
        if len(json.dumps(context,ensure_ascii=False).encode())>512_000: raise ValueError('Director context is too large; select a smaller project or request')
        guide_path = Path(__file__).with_name('raven_director_guide.md')
        guide = guide_path.read_text(encoding='utf-8')
        stage=payload.get('authoringStage')
        if stage not in {None,'setup','shots'}: raise ValueError('Unknown manuscript stage')
        catalog = contract.catalogue(stage)
        history = payload.get('history', [])
        if not isinstance(history, list) or len(history) > 100:
            raise ValueError('Conversation history is too large')
        messages = [{'role': 'system', 'content': guide + '\nTOOLS:\n' + json.dumps(catalog) + '\nCONTEXT:\n' + json.dumps(context, ensure_ascii=False)}]
        for turn in history[-24:]:
            if not isinstance(turn, dict) or turn.get('role') not in {'user', 'assistant'} or not isinstance(turn.get('content'), str):
                continue
            messages.append({'role': turn['role'], 'content': turn['content'][:100_000]})
        messages.append({'role': 'user', 'content': message})
        with self.admission:
            self.assert_idle()
            if self.quiesced or self.closing:
                raise Conflict('Language work is suspended for an update')
            self.language_work += 1
        try:
            with self.resources.lease():
                with managed_language(self.language_service(payload.get('service')),self.config_dir,self.resources.language_journal) as language:
                    raw = language.chat(messages, json_mode=True, think=payload.get('thinking'))
        finally:
            with self.admission:
                self.language_work -= 1
        try:
            result = json.loads(raw.strip().removeprefix('```json').removesuffix('```').strip())
        except (TypeError, ValueError):
            return {'message': 'The director response was incomplete. No action was executed.', 'actions': [], 'proposalStatus': 'incomplete'}
        if not isinstance(result, dict) or not isinstance(result.get('message', ''), str):
            raise ValueError('Director reply has an invalid structure')
        actions, errors = contract.validate_actions(result.get('actions', []), context,
            slug=slug, known_slugs={p['slug'] for p in self.store.list()}, has_project=bool(board), stage=stage)
        return {'message': result.get('message', ''), 'actions': actions,
                'proposalStatus': 'rejected' if errors else 'pending' if actions else 'no_changes',
                'errors': errors, 'catalogVersion': contract.CATALOG_VERSION}

    def rewrite(self,payload):
        text=payload.get('text')
        if not isinstance(text,str) or len(text)>100_000: raise ValueError('A bounded source text is required')
        slug=payload.get('slug'); board=self.store.get(slug)
        shot=next((shot for shot in board.get('shots',[]) if shot['id']==payload.get('shotId')),None)
        if not shot: raise ValueError('Choose an existing shot')
        with self.admission:
            self.assert_idle()
            if self.quiesced or self.closing: raise Conflict('Language work is suspended')
            self.language_work+=1
        try:
            with self.resources.lease():
                language=self.language_service(payload.get('service'))
                with managed_language(language,self.config_dir,self.resources.language_journal):
                    raw=language.chat([
                    {'role':'system','content':'Revise the supplied video prompt using the script context. Preserve identities, dialogue, action and camera continuity. Return JSON {"text": "revised English video prompt"}. Do not run tools.'},
                    {'role':'user','content':json.dumps({'text':text,'shot':director_projection(shot),'brief':board.get('sceneDescription',''),'bible':director_projection(board.get('filmBible',{})),'requirement':payload.get('requirement','')},ensure_ascii=False)}])
            result=json.loads(raw)
            if not isinstance(result,dict) or not isinstance(result.get('text'),str) or not result['text'].strip() or len(result['text'])>32_000:
                raise ValueError('Language provider returned an invalid prompt suggestion')
            return {'text':result['text']}
        finally:
            with self.admission: self.language_work-=1

    def refine(self,payload):
        # Native confirmation is a prompt edit; generation creates a separate take.
        slug=payload.get('slug'); before=self.store.get(slug)
        shot=next((shot for shot in before.get('shots',[]) if shot['id']==payload.get('shotId')),None)
        if not shot: raise ValueError('Choose an existing shot')
        suggestion=self.rewrite(dict(payload,text=shot.get('prompt','')))
        with self.admission,self.store.transaction():
            self.assert_idle()
            current=self.store.get(slug)
            if authoring_digest(current)!=authoring_digest(before): raise Conflict('The project changed during prompt refinement')
            next(s for s in current['shots'] if s['id']==shot['id'])['prompt']=suggestion['text']
            self.store.put(slug,current,expected_digest=authoring_digest(before))
            return self.film.render_seeds({'slug':slug,'shotId':shot['id'],'count':1})

    def update(self, path, payload, token):
        if not self.update_token or not hmac.compare_digest(str(token or ''), self.update_token):
            raise PermissionError('Update control requires the owning application token')
        with self.admission:
            state = {'ready': not self.active_jobs() and not self.uncertain_resources() and self.language_work == 0 and self.active_mutations == 0,
                     'quiesced': self.quiesced, 'pid': os.getpid(), 'leaseToken': self.lease}
            if path.endswith('update-quiesce'):
                if not state['ready']:
                    return dict(state, ready=False, reason='Owned work is still draining')
                self.quiesced = True
                self.scheduler.quiesce()
                self.lease = secrets.token_urlsafe(32)
            elif path.endswith('update-resume'):
                if not self.lease or not hmac.compare_digest(str(payload.get('leaseToken', '')), self.lease):
                    raise Conflict('Update lease does not match')
                self.quiesced = False; self.lease = None
                self.scheduler.resume()
            return dict(state, ready=True if self.quiesced else state['ready'], quiesced=self.quiesced, leaseToken=self.lease)

    def close(self):
        with self.admission:
            self.closing = True
            jobs = self.active_jobs()
            for job in jobs:
                self.scheduler.stop(job.slug, job.id)
        for job in jobs:
            self.scheduler.wait(job.slug)
        while self.language_work or self.active_mutations:
            time.sleep(.05)


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    def server_bind(self):
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name, self.server_port = self.server_address[:2]


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'Sprintwake/1'
    def log_message(self, *_):
        pass
    @property
    def service(self):
        return self.server.service
    def send_json(self, status, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw))); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(raw)
    def body(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length < 0 or length > MAX_DOCUMENT or self.headers.get('Transfer-Encoding'):
            raise ValueError('Request body is too large or unsupported')
        return self.rfile.read(length)
    def dispatch(self, method):
        try:
            host = self.headers.get('Host', '')
            expected = f'127.0.0.1:{self.server.server_port}'
            if host != expected or (self.headers.get('Origin') and self.headers['Origin'] != 'http://' + expected):
                raise PermissionError('Only the owning local origin is accepted')
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if '\\' in path or '\x00' in path or any(p in {'.', '..'} for p in path.split('/')):
                raise ValueError('Unsafe API path')
            query = parse_qs(parsed.query)
            if method == 'GET' and path.startswith('/media/'):
                return self.media(path)
            if method == 'GET':
                return self.send_json(200, self.get_api(path, query))
            raw = self.body()
            if path.startswith('/api/boards/') and path.endswith('/refs') and method == 'POST':
                slug = path.split('/')[3]
                with self.service.mutation():
                    value = self.service.store.upload(slug, self.headers.get('X-Filename', 'image.png'), raw)
                return self.send_json(200, value)
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('Mutations require application/json')
            payload = json.loads(raw or b'{}')
            if not isinstance(payload, dict):
                raise ValueError('Expected an object request')
            if path.startswith('/api/desktop/update-'):
                return self.send_json(200, self.service.update(path, payload, self.headers.get('X-Sprintwake-Update')))
            if path in {'/api/raven/chat', '/api/chat'}:
                with self.service.mutation():
                    result = self.service.director(payload)
                if payload.get('stream'):
                    data = (json.dumps({'type': 'result', 'result': result}, ensure_ascii=False) + '\n').encode()
                    self.send_response(200); self.send_header('Content-Type', 'application/x-ndjson'); self.send_header('Content-Length', str(len(data)))
                    self.end_headers(); self.wfile.write(data); return
                return self.send_json(200, result)
            with self.service.mutation():
                return self.send_json(200, self.mutate_api(method, path, payload))
        except Conflict as error: self.send_json(409, {'error': str(error)})
        except PermissionError as error: self.send_json(403, {'error': str(error)})
        except FileNotFoundError as error: self.send_json(404, {'error': str(error)})
        except (ValueError, KeyError, TypeError) as error: self.send_json(400, {'error': str(error)})
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception:
            self.send_json(500, {'error': 'The production operation failed; retained project files are preserved'})
    def do_GET(self): self.dispatch('GET')
    def do_POST(self): self.dispatch('POST')
    def do_PUT(self): self.dispatch('PUT')
    def do_DELETE(self): self.dispatch('DELETE')
    def get_api(self, path, query):
        service = self.service; store = service.store
        slug = query.get('slug', [''])[0]
        if path == '/api/desktop': return {'pid': os.getpid(), 'desktopOwned': bool(getattr(self.server, 'desktop_owned', False))}
        if path == '/api/info': return service.info()
        if path == '/api/status': return service.status()
        if path == '/api/boards': return {'boards': store.list()}
        if path == '/api/images': return service.image_list(slug) if slug else {'images': [], 'busy': False}
        if path == '/api/film/status': return service.film_status(slug)
        if path.startswith('/api/desktop/update-'):
            return service.update(path, {}, self.headers.get('X-Sprintwake-Update'))
        match = re.fullmatch(r'/api/boards/([^/]+)/shots/([^/]+)/seed-takes', path)
        if match: return {'takes': service.film.takes(*match.groups())}
        match = re.fullmatch(r'/api/boards/([^/]+)', path)
        if match: return {'board': store.get(match[1])}
        raise FileNotFoundError('Unknown API endpoint')
    def mutate_api(self, method, path, payload):
        service = self.service; store = service.store
        if path == '/api/boards' and method == 'POST':
            slug, board = store.create(payload.get('name')); return {'slug': slug, 'board': board}
        match = re.fullmatch(r'/api/boards/([^/]+)', path)
        if match and method == 'PUT':
            return {'board': store.put(match[1], payload,
                expected_digest=self.headers.get('X-Raven-Authoring-Digest'),
                expected_updated_at=self.headers.get('X-Raven-Expected-Updated-At'))}
        match = re.fullmatch(r'/api/boards/([^/]+)/rename', path)
        if match:
            with service.admission:
                service.assert_idle(); slug, board = store.rename(match[1], payload.get('name'))
                return {'slug': slug, 'board': board}
        match = re.fullmatch(r'/api/raven/boards/([^/]+)/trash', path)
        if match:
            with service.admission, store.transaction():
                service.assert_idle()
                if store.get(match[1])['name'] != payload.get('confirmName'):
                    raise Conflict('The project name changed; confirm again')
                result = trash_project(store.data_dir, match[1]); result['entry'] = str(result['entry']); return result
        match = re.fullmatch(r'/api/raven/trash/([^/]+)/restore', path)
        if match:
            with service.admission, store.transaction():
                service.assert_idle(); return {'slug': restore_project(store.data_dir, match[1])}
        if path == '/api/images': return service.generate_image(payload)
        if path in {'/api/images/stop', '/api/film/stop', '/api/stop'}:
            return {'stopped': service.scheduler.stop(str(payload.get('slug', '')), payload.get('jobId'))}
        if path == '/api/rewrite': return service.rewrite(payload)
        if path == '/api/refine': return service.refine(payload)
        if path == '/api/prepare-dialogue': return service.audio.prepare_dialogue(payload)
        match = re.fullmatch(r'/api/boards/([^/]+)/shots/([^/]+)/dub', path)
        if match: return service.audio.dub(*match.groups(),payload)
        match = re.fullmatch(r'/api/boards/([^/]+)/soundtrack',path)
        if match: return service.audio.soundtrack(match[1],payload)
        if path == '/api/render-seeds': return service.film.render_seeds(payload)
        match = re.fullmatch(r'/api/boards/([^/]+)/shots/([^/]+)/seed-takes/(-?\d+)/adopt', path)
        if match: return {'board': service.film.adopt_take(match[1], match[2], int(match[3]))}
        if path in {'/api/film', '/api/film/action'}:
            service.film.action(payload); return service.film_status(payload.get('slug'))
        if path == '/api/assemble':
            from .media import join_clips
            slug = payload.get('slug'); board = store.get(slug)
            service.film.assert_assembly_ready(slug)
            clips = [store.media_path(next((p for p in s.get('outputs', []) if str(p).lower().endswith('.mp4')), '')) for s in board.get('shots', [])]
            if not clips: raise ValueError('No reviewed clips are available')
            target = safe_path(store.project_dir(slug), 'Output/Video', 'film-' + uuid.uuid4().hex + '.mp4', create_parent=True)
            join_clips(clips, target)
            with store.transaction():
                current=store.get(slug)
                if authoring_digest(current)!=authoring_digest(board) or [shot.get('outputs') for shot in current.get('shots',[])]!=[shot.get('outputs') for shot in board.get('shots',[])]:
                    raise Conflict('The project changed while assembling; the rendered movie was retained without changing selected media')
                current['finalVideo']=store.ref(target)['url']
                saved=store.put(slug,current,expected_digest=authoring_digest(board),expected_updated_at=board.get('updatedAt'))
            return {'url':saved['finalVideo'],'board':saved}
        raise FileNotFoundError('Unknown production endpoint')
    def media(self, path):
        source = self.service.store.media_path(path)
        size = source.stat().st_size
        begin, end = 0, size - 1
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', requested)
            if not match: raise ValueError('Unsupported media range')
            begin = int(match[1]); end = min(size - 1, int(match[2])) if match[2] else size - 1
            if not 0 <= begin <= end < size: raise ValueError('Media range is outside the file')
        self.send_response(206 if requested else 200)
        self.send_header('Content-Type', mimetypes.guess_type(source.name)[0] or 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes'); self.send_header('Content-Length', str(end - begin + 1))
        if requested: self.send_header('Content-Range', f'bytes {begin}-{end}/{size}')
        self.end_headers()
        with source.open('rb') as stream:
            stream.seek(begin); remaining = end - begin + 1
            while remaining:
                chunk = stream.read(min(remaining, 1024 * 1024))
                if not chunk: break
                self.wfile.write(chunk); remaining -= len(chunk)

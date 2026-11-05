"""Independent opt-in audio providers and immutable output handling (MIT)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations
import hashlib, io, json, os, re, shutil, subprocess, tempfile, urllib.error, urllib.request, wave
from urllib.parse import urlsplit
from .store import authoring_digest, safe_path
from pathlib import Path

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)

class AudioServices:
    MAX_RESPONSE = 32 * 1024 * 1024
    def __init__(self, store, scheduler, config_dir=None, credential_loader=None):
        self.store, self.scheduler = store, scheduler
        self.config_dir = Path(config_dir) if config_dir else None
        self.credential_loader = credential_loader
    def _config(self, name):
        if not self.config_dir: return {}
        p = self.config_dir / name
        if p.is_symlink() or not p.is_file(): return {}
        try: value = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc: raise ValueError(f"invalid {name}") from exc
        return value if isinstance(value, dict) else {}
    def _service(self, name, payload):
        services = self._config(name).get("services", [])
        wanted = payload.get("service") or payload.get("profile")
        service = next((s for s in services if isinstance(s, dict) and (not wanted or s.get("id") == wanted)), None)
        if not service: raise ValueError(f"No configured {name.removesuffix('-services.json')} service")
        return dict(service)
    def _guard(self):
        guard = getattr(self.scheduler, "before_start", None)
        if callable(guard) and guard() is False: raise RuntimeError("model release/admission guard refused audio work")
        if getattr(self.scheduler, "quiesced", False): raise RuntimeError("production admission is quiesced")
    def _request(self, service, body):
        if service.get("kind") not in {"openai-compatible", "http-audio"}: raise ValueError(f"Unsupported audio provider kind: {service.get('kind')}")
        url = service.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")): raise ValueError("audio provider URL must be HTTP(S)")
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment: raise ValueError("audio provider URL cannot contain credentials/query/fragment")
        endpoint = url.rstrip("/")
        if endpoint.endswith("/v1"): endpoint += "/audio/speech"
        elif not endpoint.endswith("/v1/audio/speech"): endpoint += "/v1/audio/speech"
        headers = {"Content-Type":"application/json", "Accept":"audio/wav"}
        key = self.credential_loader(service) if callable(self.credential_loader) else None
        variable=service.get('apiKeyEnv')
        if not key and isinstance(variable,str) and re.fullmatch('[A-Z][A-Z0-9_]{0,99}',variable): key=os.environ.get(variable)
        if key: headers["Authorization"] = "Bearer " + str(key)
        req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.build_opener(_NoRedirect).open(req, timeout=120) as response: raw = response.read(self.MAX_RESPONSE + 1)
        except urllib.error.HTTPError as exc: raise RuntimeError(f"audio provider HTTP {exc.code}") from exc
        except urllib.error.URLError as exc: raise RuntimeError("audio provider transport failed") from exc
        if len(raw) > self.MAX_RESPONSE: raise RuntimeError("audio provider response exceeds limit")
        return raw
    def _project(self, slug):
        project = self.store.project_dir(slug)
        if project.is_symlink() or not project.is_dir(): raise ValueError("unknown project")
        return project
    def _write_audio(self, slug, raw, prefix):
        project, digest = self._project(slug), hashlib.sha256(raw).hexdigest()
        output = safe_path(project,"Output","Audio"); output.mkdir(parents=True, exist_ok=True)
        target = output / f"{prefix}-{digest[:16]}.wav"
        if not target.exists():
            fd, temporary = tempfile.mkstemp(prefix=".audio-", dir=output); os.close(fd)
            try: Path(temporary).write_bytes(raw); os.link(temporary,target)
            finally: Path(temporary).unlink(missing_ok=True)
        return {"path":str(target.relative_to(project)), "sha256":digest, "sampleRate":48000, "channels":2}
    @staticmethod
    def _validate_wav(raw):
        try:
            with wave.open(io.BytesIO(raw), "rb") as stream:
                if stream.getnframes() <= 0: raise ValueError("audio is empty")
                if stream.getframerate() != 48000 or stream.getnchannels() != 2: raise ValueError("audio must be decoded 48 kHz stereo WAV")
                if stream.getsampwidth() not in (2,3,4): raise ValueError("unsupported audio sample width")
        except (wave.Error, EOFError) as exc: raise ValueError("audio provider did not return a valid WAV") from exc
    def _submit(self, slug, kind, worker):
        self._guard(); return self.scheduler.submit(slug, kind, worker)
    def _attach_shot_audio(self, slug, shot_id, audio_path, expected):
        board = self.store.get(slug)
        if authoring_digest(board) != expected: raise RuntimeError("project changed while audio was being generated")
        updated = json.loads(json.dumps(board)); found = False
        for shot in updated.get("shots", []):
            if isinstance(shot, dict) and shot.get("id") == shot_id:
                shot["dialogueAudioUrl"] = self.store.ref(audio_path)["url"]; found = True; break
        if not found: raise ValueError("unknown shot")
        self.store.put(slug, updated, expected_digest=expected)
    def _attach_soundtrack(self, slug, audio_path, expected):
        board = self.store.get(slug)
        if authoring_digest(board) != expected: raise RuntimeError("project changed while soundtrack was being generated")
        updated = json.loads(json.dumps(board)); updated["soundtrackUrl"] = self.store.ref(audio_path)["url"]
        self.store.put(slug, updated, expected_digest=expected)
    def _dialogue_text(self, slug, payload):
        text = payload.get("text") or payload.get("dialogue")
        if isinstance(text, str) and text.strip(): return text.strip()
        board = self.store.get(slug); shots = board.get("shots", []) if isinstance(board, dict) else []
        if payload.get("shotId"): shots = [s for s in shots if isinstance(s, dict) and s.get("id") == payload["shotId"]]
        lines = [str(s.get("dialogue", "")).strip() for s in shots if isinstance(s, dict) and str(s.get("dialogue", "")).strip()]
        if not lines: raise ValueError("dialogue text is required")
        return "\n".join(lines)
    def prepare_dialogue(self, payload):
        if not isinstance(payload, dict) or not payload.get("slug"): raise ValueError("slug is required")
        slug, service = str(payload["slug"]), self._service("tts-services.json", payload); captured = authoring_digest(self.store.get(slug)); text = self._dialogue_text(slug, payload)
        if payload.get("clone") or payload.get("voiceClone"):
            if not isinstance(payload.get("voiceRecording") or payload.get("voiceRef"), str): raise ValueError("voice cloning requires a verified character recording")
        def worker(job):
            shot_id = payload.get("shotId")
            targets = [(shot_id, text)] if shot_id else [(s.get("id"), str(s.get("dialogue", "")).strip()) for s in self.store.get(slug).get("shots", []) if s.get("dialogue")]
            if not targets and text: targets = [(None, text)]
            results=[]; expected=captured
            for current_id, current_text in targets:
                raw = self._request(service, {"model":payload.get("model") or service.get("model","tts-1"), "input":current_text, "voice":payload.get("voice") or "alloy", "response_format":"wav"})
                self._validate_wav(raw); result = self._write_audio(slug, raw, "dialogue"); results.append(result)
                if current_id:
                    self._attach_shot_audio(slug, current_id, self._project(slug) / result["path"], expected)
                    expected = authoring_digest(self.store.get(slug))
            return results if len(results) != 1 else results[0]
        job = self._submit(slug, "dialogue", worker); return {"jobId":job.id, "status":job.status, "operationStatus":"queued"}
    def _shot_video(self, slug, shot):
        project = self._project(slug); candidates=[]
        for item in shot.get("outputs", []) if isinstance(shot,dict) else []:
            candidates.extend([item.get(k) for k in ("path","video","clip")] if isinstance(item,dict) else [item])
        candidates.extend([shot.get("videoPath"),shot.get("clipPath"),shot.get("clip")])
        for rel in candidates:
            if not isinstance(rel,str): continue
            try:
                path = self.store.media_path(rel) if hasattr(self.store, "media_path") else (project / rel).resolve()
            except (OSError, ValueError):
                path = (project / rel).resolve()
            if path.is_file() and not path.is_symlink() and project.resolve() in path.resolve().parents: return path.resolve()
        return None
    def _mux_audio(self, video, audio, output):
        ffmpeg = os.environ.get("RAVEN_FFMPEG") or shutil.which("ffmpeg")
        if not ffmpeg: raise RuntimeError("ffmpeg is required to dub a picture take")
        from .media import validate_decoded
        metadata=validate_decoded(video,kind='video',fps=24)
        frames=next(row['nb_frames'] for row in metadata['streams'] if row['codec_type']=='video')
        samples=frames*2000
        cmd=[ffmpeg,"-hide_banner","-loglevel","error","-y","-i",str(video),"-i",str(audio),"-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-af",f"apad=whole_len={samples},atrim=end_sample={samples}",str(output)]
        subprocess.run(cmd, check=True, timeout=300, capture_output=True)
    def dub(self, slug, shotid, payload):
        board=self.store.get(slug); shot=next((s for s in board.get("shots",[]) if isinstance(s,dict) and s.get("id")==shotid),None)
        if not shot: raise ValueError("unknown shot")
        request=dict(payload or {}); request.update({"slug":slug,"shotId":shotid}); service=self._service("tts-services.json",request); text=self._dialogue_text(slug,request); video=self._shot_video(slug,shot)
        if video is None: raise ValueError("shot has no verified picture take to dub")
        captured=authoring_digest(board); source_hash=hashlib.sha256(video.read_bytes()).hexdigest()
        def worker(job):
            raw=self._request(service,{"model":request.get("model") or service.get("model","tts-1"),"input":text,"voice":request.get("voice") or "alloy","response_format":"wav"}); self._validate_wav(raw)
            if hashlib.sha256(video.read_bytes()).hexdigest() != source_hash: raise RuntimeError("picture take changed while audio was generated")
            audio=self._write_audio(slug,raw,"dialogue"); project=self._project(slug); outdir=safe_path(project,"Output","Dubbed"); outdir.mkdir(parents=True,exist_ok=True); digest=hashlib.sha256(video.read_bytes()+raw).hexdigest(); target=outdir/f"{shotid}-{digest[:16]}.mp4"
            if not target.exists():
                fd,tmp=tempfile.mkstemp(prefix=".dub-",suffix=".mp4",dir=outdir); os.close(fd)
                try: self._mux_audio(video,project/audio["path"],Path(tmp)); os.replace(tmp,target)
                finally: Path(tmp).unlink(missing_ok=True)
            from .media import validate_decoded
            validate_decoded(target,kind='video',fps=24,audio_hz=48000)
            with self.store.transaction():
                current=self.store.get(slug)
                if authoring_digest(current)!=captured or hashlib.sha256(video.read_bytes()).hexdigest()!=source_hash:
                    raise RuntimeError('The script or selected picture changed while dubbing; output retained separately')
                changed=json.loads(json.dumps(current))
                for item in changed['shots']:
                    if item.get('id')==shotid:
                        item['dialogueAudioUrl']=self.store.ref(project/audio['path'])['url']
                        item['dubUrl']=self.store.ref(target)['url']
                self.store.put(slug,changed,expected_digest=captured,expected_updated_at=current.get('updatedAt'))
            return {"video":str(target.relative_to(project)),"audio":audio,"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"shotId":shotid}
        job=self._submit(slug,"dub",worker); return {"jobId":job.id,"status":job.status,"operationStatus":"queued","shotId":shotid}
    def soundtrack(self, slug, payload):
        payload=dict(payload or {}); service=self._service("soundtrack-services.json",payload)
        if service.get("kind") not in {"openai-compatible","http-audio"}: raise ValueError(f"Unsupported soundtrack provider kind: {service.get('kind')}")
        prompt=payload.get("prompt") or self.store.get(slug).get("soundtrackPrompt","")
        if not isinstance(prompt,str) or not prompt.strip(): raise ValueError("soundtrack prompt is required")
        captured=authoring_digest(self.store.get(slug))
        def worker(job):
            raw=self._request(service,{"model":payload.get("model") or service.get("model","music"),"prompt":prompt,"response_format":"wav"}); self._validate_wav(raw); result=self._write_audio(slug,raw,"soundtrack"); self._attach_soundtrack(slug,self._project(slug)/result["path"],captured); return result
        job=self._submit(slug,"soundtrack",worker); return {"jobId":job.id,"status":job.status,"operationStatus":"queued"}

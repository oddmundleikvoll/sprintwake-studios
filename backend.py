"""Small, owned-process bridge to the existing Storyboard HTTP server.

Raven owns the process lifetime, while Storyboard remains the production API
and filesystem implementation.  This module deliberately contains no model
or rendering code; starting the server is cheap and rendering remains an
explicit API operation.
"""

from __future__ import annotations

import http.client
import json
import mimetypes
import math
import os
from pathlib import Path
import shutil
import socket
import secrets
import subprocess
import tempfile
import time
from typing import Any, Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
DEFAULT_ROOT = Path.home() / "Movies/Raven Studio"
COMFYUI_URL = "http://127.0.0.1:8188"


class BackendError(RuntimeError):
    """A readable transport or Storyboard API failure."""

    def __init__(self, message: str, *, status: int | None = None, path: str | None = None):
        super().__init__(message)
        self.status = status
        self.path = path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Backend:
    def __init__(
        self,
        root: Path = DEFAULT_ROOT,
        engine_root: Path | None = None,
        *,
        runtime_python: Path | None = None,
        workspace: Path | None = None,
        comfyui_url: str = COMFYUI_URL,
        llm: str = "splash-local",
        startup_timeout: float = 45.0,
        request_timeout: float = 20.0,
        chat_timeout: float = 1200.0,
        connection_settings: dict | None = None,
        credential_loader=None,
        job_state_dir: Path | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self.connection_settings = connection_settings
        self.credential_loader = credential_loader
        self.root = Path(root).expanduser().resolve()
        selected_engine = engine_root or ((ROOT / "engine") if (ROOT / "engine").is_dir() else ROOT.parent / "storyboard")
        self.engine_root = Path(selected_engine).expanduser().resolve()
        self.job_state_dir = Path(job_state_dir) if job_state_dir else self.root/"System/provider-jobs"
        self.config_dir = Path(config_dir).resolve() if config_dir else None
        self.data_dir = self.root / "Projects"
        self.system_dir = self.root / "System"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.system_dir.mkdir(parents=True, exist_ok=True)
        selected_python = runtime_python or (ROOT / "runtime/bundle-venv/bin/python" if (ROOT / "runtime/bundle-venv/bin/python").is_file() else ROOT.parent / "ComfyUI-Film/.venv/bin/python")
        self.runtime_python = Path(selected_python)
        self.workspace = Path(workspace or self._configured_workspace()).expanduser().resolve()
        self.comfyui_url = comfyui_url
        self.llm = llm
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.chat_timeout = chat_timeout
        self.base_url: str | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.info: dict[str, Any] | None = None
        self._log_file: Any = None
        self._update_token = None

    def _configured_workspace(self) -> Path:
        config = self.engine_root / "server-config.json"
        try:
            value = json.loads(config.read_text()).get("workspace")
            if value:
                return Path(value)
        except (OSError, ValueError, TypeError):
            pass
        return self.root / "System/EngineWork"

    def _is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _child_environment(self) -> dict[str, str]:
        """Build the isolated Storyboard environment, including project tools."""
        env = os.environ.copy()
        env.pop('PYTHONHOME',None)
        env.pop('VIRTUAL_ENV',None)
        local_bin = self.engine_root / "local" / "bin"
        owned_tools = ROOT / "runtime/tools"
        env["PATH"] = os.pathsep.join((str(local_bin), str(owned_tools), env.get("PATH", ""),"/opt/homebrew/bin","/usr/local/bin"))
        env["PYTHONPATH"] = str(self.engine_root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["RAVEN_COMFY_OUTPUT_DIR"] = str(self.workspace)
        env["RAVEN_COMFY_STATE_DIR"] = str(self.job_state_dir)
        if self._update_token:env['SPRINTWAKE_UPDATE_TOKEN']=self._update_token
        if self.config_dir:
            self.config_dir.mkdir(parents=True,exist_ok=True)
            env['RAVEN_GPU_LOCK']=str(self.config_dir.parent/'Locks/gpu-admission.lock')
            try:
                settings=json.loads((self.config_dir/'server-config.json').read_text())
                install=settings.get('modelInstallRoot')
                if isinstance(install,str) and Path(install).is_absolute():env['RAVEN_MODEL_INSTALL_ROOT']=install
            except (OSError,ValueError):pass
        if self.connection_settings and self.credential_loader:
            from connections import credential_account
            # Native UI credentials are endpoint-scoped. Environment keys are
            # reserved for the explicit headless host, which has no loader.
            env.pop("RAVEN_LLM_API_KEY",None)
            env.pop("RAVEN_COMFY_API_KEY",None)
            env.pop("ANTHROPIC_API_KEY",None)
            env.pop("ANTHROPIC_AUTH_TOKEN",None)
            ident=self.connection_settings["selected"]["llm"]
            for variable,account in (("RAVEN_LLM_API_KEY","llm:"+ident),("RAVEN_COMFY_API_KEY","comfy")):
                value=self.credential_loader(credential_account(self.connection_settings,account))
                if value:env[variable]=value
        return env

    def start(self) -> dict[str, Any]:
        if self._is_running() and self.info is not None:
            return self.info
        if self.process is not None:
            self.close()
        if not self.runtime_python.is_file():
            raise BackendError(f"Storyboard runtime not found: {self.runtime_python}")
        if not (self.engine_root / "server").is_dir():
            raise BackendError(f"Storyboard engine not found: {self.engine_root}")
        port = _free_port()
        self.base_url = f"http://127.0.0.1:{port}"
        log_path = self.system_dir / "raven-backend.log"
        self._log_file = log_path.open("ab")
        self._update_token=secrets.token_urlsafe(32)
        env = self._child_environment()
        provider_args=['--config-dir',str(self.config_dir)] if self.config_dir else []
        backend_kind="ltx"
        if self.connection_settings:
            from connections import service_document, media_document, write_private
            services=service_document(self.connection_settings)
            if services is not None:
                if not env.get("RAVEN_LLM_API_KEY"):services["services"][0].pop("apiKeyEnv",None)
                config_dir=self.system_dir/"provider-settings"
                write_private(config_dir/"llm-services.json",services)
                provider_args += ["--llm-config-dir",str(config_dir)]
            media=media_document(self.connection_settings)
            if media["image"]!="qwen-local" or media["video"]!="ltx-local":
                path=self.system_dir/"provider-settings/media.json"
                write_private(path,media)
                provider_args += ["--media-profiles",str(path)]
                backend_kind="configured"
        command = [
            str(self.runtime_python), "-u", "-m", "server",
            "--bind", "127.0.0.1", "--port", str(port), "--desktop",
            "--workspace", str(self.workspace), "--data-dir", str(self.data_dir),
            "--comfyui-url", self.comfyui_url, "--backend", backend_kind, "--llm", self.llm,
            *provider_args,
        ]
        try:
            self.process = subprocess.Popen(
                command, cwd=self.engine_root, env=env, stdin=subprocess.PIPE,
                stdout=self._log_file, stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            self._close_log()
            raise BackendError(f"Could not start Storyboard: {exc}") from exc

        deadline = time.monotonic() + self.startup_timeout
        last_error = "server did not become ready"
        while time.monotonic() < deadline:
            if not self._is_running():
                last_error = self._log_tail()
                self.close()
                raise BackendError(f"Storyboard exited during startup: {last_error}")
            try:
                desktop = self.get("/api/desktop", timeout=2.0)
                pid = desktop.get("pid") if isinstance(desktop, dict) else None
                if pid != self.process.pid or not desktop.get("desktopOwned"):
                    raise BackendError("Storyboard desktop ownership was not confirmed")
                info = self.get("/api/info", timeout=2.0)
                if isinstance(info, dict):
                    self.info = info
                    return info
            except (BackendError, OSError, HTTPError, URLError) as exc:
                last_error = str(exc)
            time.sleep(0.15)
        self.close()
        raise BackendError(f"Storyboard startup timed out: {last_error}")

    def _require_base_url(self) -> str:
        if not self.base_url:
            raise BackendError("Backend has not been started")
        return self.base_url

    @staticmethod
    def _path(path: str, *, media: bool = False) -> str:
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise BackendError("API path must be an absolute local path")
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or "\\" in path:
            raise BackendError("External URLs are not allowed")
        clean = parsed.path
        if any(part in ("", ".", "..") for part in clean.split("/")[1:]):
            raise BackendError("Unsafe API path")
        if not (clean.startswith("/api/") or (media and clean.startswith("/media/"))):
            raise BackendError("Only Storyboard API/media paths are allowed")
        return clean + (("?" + parsed.query) if parsed.query else "")

    def _request(self, method: str, path: str, payload: Any = None, *, raw: bytes | None = None,
                 headers: dict[str, str] | None = None, timeout: float | None = None) -> Any:
        path = self._path(path)
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
        request_headers = {"Accept": "application/json"}
        if payload is not None and raw is None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        if path.split('?',1)[0] in {'/api/desktop/update-status','/api/desktop/update-quiesce','/api/desktop/update-resume'} and self._update_token:
            request_headers['X-Sprintwake-Update']=self._update_token
        request = Request(self._require_base_url() + path, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=timeout or self.request_timeout) as response:
                data = response.read()
                return self._decode(data, response.headers.get("Content-Type", ""))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise BackendError(f"Storyboard {method} {path} failed ({exc.code}): {detail[:500]}", status=exc.code, path=path) from exc
        except (OSError, URLError) as exc:
            raise BackendError(f"Storyboard {method} {path} unavailable: {exc}", path=path) from exc

    @staticmethod
    def _decode(data: bytes, content_type: str) -> Any:
        if not data:
            return None
        if "json" in content_type or data[:1] in (b"{", b"["):
            try:
                return json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                pass
        return data

    def get(self, path: str, *, timeout: float | None = None) -> Any:
        return self._request("GET", path, timeout=timeout)

    def post(self, path: str, payload: Any = None, *, timeout: float | None = None) -> Any:
        return self._request("POST", path, payload, timeout=timeout)

    def put(self, path: str, payload: Any = None, *, timeout: float | None = None,
            expected_updated_at: float | str | None = None,
            expected_digest: str | None = None) -> Any:
        headers = {}
        if expected_updated_at is not None:
            try:
                valid = type(expected_updated_at) in (str, int, float) and math.isfinite(float(expected_updated_at))
            except (ValueError, TypeError):
                valid = False
            if not valid:
                raise BackendError("Invalid conditional-save timestamp")
            headers["X-Raven-Expected-Updated-At"] = str(expected_updated_at)
        if expected_digest is not None:
            if not isinstance(expected_digest, str) or len(expected_digest) != 64 or any(c not in "0123456789abcdef" for c in expected_digest):
                raise BackendError("Invalid authoring digest")
            headers["X-Raven-Authoring-Digest"] = expected_digest
        return self._request("PUT", path, payload, timeout=timeout, headers=headers)

    def upload(self, slug: str, source: Path) -> Any:
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise BackendError(f"Upload is not a file: {source}")
        if source.stat().st_size > 32 * 1024 * 1024:
            raise BackendError("Upload exceeds Storyboard's 32 MB limit")
        path = f"/api/boards/{slug}/refs"
        # Slugs are generated by Storyboard; reject path traversal before URL construction.
        if "/" in slug or "\\" in slug or slug in ("", ".", ".."):
            raise BackendError("Invalid project slug")
        data = source.read_bytes()
        return self._request("POST", path, raw=data, headers={
            "Content-Type": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            "Content-Length": str(len(data)), "X-Filename": source.name,
        })

    def download(self, path: str, target: Path) -> Path:
        path = self._path(path, media=True)
        target = Path(target).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        request = Request(self._require_base_url() + path, headers={"Accept": "*/*"})
        try:
            with urlopen(request, timeout=self.request_timeout) as response:
                fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
                try:
                    with os.fdopen(fd, "wb") as out:
                        shutil.copyfileobj(response, out)
                    os.replace(temp_name, target)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
        except (OSError, URLError, HTTPError) as exc:
            raise BackendError(f"Download {path} failed: {exc}", path=path) from exc
        return target

    def chat(self, payload: dict[str, Any], on_progress: Callable[[dict[str, Any]], None] | None = None, *, api_path="/api/chat") -> Any:
        path = self._path(api_path)
        body = json.dumps(payload | {"stream": True}).encode()
        parsed = urlsplit(self._require_base_url())
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=self.chat_timeout)
        try:
            conn.request("POST", path, body=body, headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"})
            response = conn.getresponse()
            if response.status >= 400:
                detail = response.read().decode("utf-8", "replace")
                raise BackendError(f"Storyboard chat failed ({response.status}): {detail[:500]}", status=response.status, path=path)
            final: Any = None
            while True:
                line = response.readline()
                if not line:
                    break
                try:
                    event = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if event.get("type") == "error":
                    message = event.get("error") or event.get("message") or "unknown Storyboard chat error"
                    raise BackendError(f"Storyboard chat failed: {message}", path=path)
                if on_progress:
                    on_progress(event)
                if event.get("type") in ("result", "final", "done"):
                    final = event.get("result", event.get("data", event))
            if final is None:
                raise BackendError("Storyboard chat ended without a result event", path=path)
            return final
        except (OSError, http.client.HTTPException) as exc:
            raise BackendError(f"Storyboard chat unavailable: {exc}", path=path) from exc
        finally:
            conn.close()

    def raven_chat(self, payload, on_progress=None):
        return self.chat(dict(payload,service=self.llm), on_progress, api_path="/api/raven/chat")

    def authoring_chat(self, payload):
        """Use Raven's expert context with the bounded manuscript writer."""
        payload = dict(payload)
        payload["message"] = payload["message"].replace("set_board_fields", "edit_project")
        response = self.raven_chat(payload)
        actions = []
        for action in response.get("actions", []):
            tool = action.get("tool")
            if tool == "edit_project":
                actions.append({"tool":"set_board_fields", "fields":action.get("fields", {})})
            elif tool == "edit_bible":
                actions.append({"tool":"set_board_fields", "fields":{"filmBible":action["fields"]}})
            elif tool == "add_character":
                actions.append({"tool":tool, "character":{k:v for k,v in action.items() if k != "tool"}})
            elif tool == "add_shot":
                actions.append({"tool":tool, "shot":{k:v for k,v in action.items() if k != "tool"}})
            else:
                raise BackendError("Director proposed an action outside the script assignment")
        return dict(response, actions=actions)

    def _log_tail(self) -> str:
        try:
            return self.system_dir.joinpath("raven-backend.log").read_text(errors="replace")[-1000:]
        except OSError:
            return "no backend log available"

    def _close_log(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def close(self, timeout: float = 1200.0) -> None:
        """Release this server without interrupting owned model cleanup.

        Storyboard's desktop shutdown drains render/inference workers.  It can
        legitimately take many minutes, so this method never sends terminate
        or kill: a timeout leaves the owned process available for a later
        retry and raises a readable error.
        """
        if timeout < 1000:
            raise ValueError("backend close timeout must be at least 1000 seconds")
        process = self.process
        if process is not None and process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write(b"\n")
                    process.stdin.close()
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise BackendError(
                    f"Storyboard is still draining after {timeout:.0f}s; "
                    "the owned process was left running safely"
                ) from exc
            except OSError as exc:
                raise BackendError(f"Could not close Storyboard owner pipe: {exc}") from exc
        self.process = None
        self._close_log()
        self.base_url = None
        self.info = None

    def __enter__(self) -> "Backend":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

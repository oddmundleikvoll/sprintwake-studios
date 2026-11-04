"""Small, dependency-free client for the installed Comfy film workflows.

The client deliberately owns only the prompt ids it creates.  It uses the
native Qwen-Image-2.1-Turbo and LTX-2.5 Q8 graphs; no model
management or download endpoint is called here.
"""

from __future__ import annotations
import os

import json
import hashlib
import io
import mimetypes
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler

from .ltx_media import atomic_json, digest, file_hash, runtime_identity, validate_video, validate_wave
from .film_production import archive_take


def _canonical_image_content(path: Path) -> bytes | None:
    """Normalize uploaded anchors before Comfy's potentially different image decoders."""
    import numpy as np
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            array = np.asarray(image).astype(np.float32) / 255.0
            content = io.BytesIO()
            Image.fromarray(np.clip(array * 255, 0, 255).astype(np.uint8)).save(content, format="PNG")
    except Exception:
        return None  # Comfy retains its ordinary malformed-image rejection.
    return content.getvalue()


def _canonical_image_hash(path: Path) -> str:
    """Hash the exact normalized RGB PNG bytes uploaded and persisted by Comfy."""
    content = _canonical_image_content(path)
    return hashlib.sha256(content).hexdigest() if content is not None else file_hash(path)


INSTALL_ROOT = Path(os.environ.get(
    "RAVEN_MODEL_INSTALL_ROOT",
    str(Path(__file__).resolve().parents[2]),
)).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get("RAVEN_LTX_RUNTIME_DIR",str(INSTALL_ROOT / "LTX-2.5-MLX")))
MODEL_DIR = Path(os.environ.get("RAVEN_LTX_MODEL_DIR",str(Path.home()/"ComfyUI-Shared/models/ltx/ltx-2.5-mlx-q8")))
BRIDGE_DIR = Path(os.environ.get("RAVEN_LTX_BRIDGE_DIR",str(INSTALL_ROOT / "ComfyUI-Film/ComfyUI/custom_nodes/ComfyUI-LTX25-MLX")))
IMAGE_MODEL = "qwen-image-2.1-turbo"
QWEN_MODELS = {"unet": "qwen_image_2.1_turbo_bf16.safetensors", "clip": "qwen3vl_8b_bf16.safetensors", "vae": "qwen_image_2.1_vae_bf16.safetensors"}
QWEN_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"

Progress = Callable[[str, str, float], None]
Cancel = Callable[[], bool]


class ComfyFilmError(RuntimeError):
    pass


class _NoCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ComfyFilmExecutionError(ComfyFilmError):
    def __init__(self, message: str, entry: dict[str, Any]):
        super().__init__(message)
        self.entry = entry


class ComfyFilmClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188", output_dir: Path | None = None):
        self.base_url = base_url.rstrip("/")
        self.output_dir = Path(output_dir or os.environ.get("RAVEN_COMFY_OUTPUT_DIR",str(Path.home()/"ComfyUI-Shared/output")))
        self._prompt_id: str | None = None
        self.api_key = os.environ.get("RAVEN_COMFY_API_KEY", "")

    def _open(self, request, timeout):
        if self.api_key:
            request.add_header("Authorization", "Bearer " + self.api_key)
            return build_opener(_NoCredentialRedirect).open(request, timeout=timeout)
        return urlopen(request, timeout=timeout)

    def _request(self, method: str, path: str, payload: Any = None, *, raw: bool = False) -> Any:
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        try:
            with self._open(Request(self.base_url + path, data=body, headers=headers, method=method), timeout=30) as response:
                data = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if self.api_key:detail = detail.replace(self.api_key, "[redacted]")
            raise ComfyFilmError(f"ComfyUI HTTP {exc.code} on {method} {path}: {detail}") from exc
        except (URLError, OSError) as exc:
            raise ComfyFilmError(
                f"ComfyUI på {self.base_url} svarer ikke. Åpne Comfy Desktop og start "
                "«LTX-2.5 Q8», vent til den er klar, og prøv igjen.") from exc
        if raw:
            return data
        return json.loads(data.decode("utf-8")) if data else {}

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _post(self, path: str, payload: Any) -> Any:
        return self._request("POST", path, payload)

    @staticmethod
    def _names(info: dict[str, Any], class_name: str, input_name: str) -> list[str]:
        node = info.get(class_name) or {}
        required = node.get("input", {}).get("required", {})
        value = required.get(input_name, [None])[0] if input_name in required else None
        return list(value) if isinstance(value, (list, tuple)) else []

    def preflight(self, require_images: bool = False) -> dict[str, Any]:
        info = self._get("/object_info")
        queue = self._get("/queue")
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise ComfyFilmError("ComfyUI-køen er ikke tom. Vent til køen er tom; andre jobber stoppes ikke.")
        required = ["LoadImage", "LTX25MLXScene", "LTX25MLXReleaseMemory"]
        if require_images:
            required += ["UNETLoader", "CLIPLoader", "VAELoader", "TextEncodeQwenImage21", "QwenImage21Cache",
                         "EmptyLatentImage", "ManualSigmas", "RandomNoise", "KSamplerSelect", "CFGGuider",
                         "SamplerCustomAdvanced", "VAEDecode", "SaveImage", "LTX25MLXImageAnchor"]
        missing = [name for name in required if name not in info]
        if missing:
            raise ComfyFilmError("Start Comfy Desktop på nytt for Qwen/LTX-nodene: " + ", ".join(missing))
        contracts = {"LTX25MLXScene": ("shot_prompts", "width", "height", "first_frames", "continuation_frames", "seed", "low_ram")}
        if require_images:
            contracts.update({"TextEncodeQwenImage21": ("clip", "prompt", "negative_prompt", "vae", "resolution"),
                "UNETLoader": ("unet_name", "weight_dtype"), "CLIPLoader": ("clip_name", "type", "device"),
                "VAELoader": ("vae_name",), "ManualSigmas": ("sigmas",),
                "QwenImage21Cache": ("model", "device", "dtype")})
        for name, fields in contracts.items():
            schema = info[name].get("input", {})
            available = set(schema.get("required", {})) | set(schema.get("optional", {}))
            if set(fields) - available:
                raise ComfyFilmError(f"Uventet {name}-kontrakt; mangler: {', '.join(sorted(set(fields)-available))}")
        models = {}
        if require_images:
            for kind, node, field in (("unet", "UNETLoader", "unet_name"), ("clip", "CLIPLoader", "clip_name"), ("vae", "VAELoader", "vae_name")):
                models[kind] = self._choose_exact(self._names(info, node, field), QWEN_MODELS[kind], "Qwen " + kind)
        return {"base_url": self.base_url, "queue_empty": True, "nodes": list(info), "qwen_models": models}

    @staticmethod
    def _choose(names: list[str], needles: tuple[str, ...], label: str) -> str:
        for name in names:
            if any(needle.lower() in str(name).lower() for needle in needles):
                return str(name)
        raise ComfyFilmError(f"Fant ikke installert {label} i ComfyUI: {names}")

    @staticmethod
    def _choose_exact(names: list[str], expected: str, label: str) -> str:
        for name in names:
            if Path(str(name)).name.casefold() == expected.casefold():
                return str(name)
        raise ComfyFilmError(f"Fant ikke installert {label} ({expected}) i ComfyUI: {names}")

    @staticmethod
    def _graph(nodes: list[dict[str, Any]], links: list[list[Any]]) -> dict[str, dict[str, Any]]:
        sources = {int(link[0]): (str(link[1]), int(link[2])) for link in links}
        graph: dict[str, dict[str, Any]] = {}
        for node in nodes:
            inputs: dict[str, Any] = {}
            widget_index = 0
            values = node.get("widgets_values") or []
            for item in node.get("inputs", []):
                name = item.get("name")
                if not name:
                    continue
                link = item.get("link")
                if link is not None and int(link) in sources:
                    inputs[name] = list(sources[int(link)])
                elif item.get("widget") is not None and widget_index < len(values):
                    inputs[name] = values[widget_index]
                    widget_index += 1
            graph[str(node["id"])] = {"class_type": node["type"], "inputs": inputs}
        return graph

    def _upload(self, path: Path, *, canonical: bool = False) -> str:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        boundary = "----codex" + uuid.uuid4().hex
        content = _canonical_image_content(path) if canonical else None
        canonical_png = content is not None
        if content is None:
            content = path.read_bytes()
        content_type = "image/png" if canonical_png else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        # Do not put a user supplied name in a multipart header.  Comfy only
        # needs a stable input filename; the original basename is irrelevant.
        suffix = ".png" if canonical_png else path.suffix.lower()
        filename = "storyboard-ref-" + uuid.uuid4().hex + (suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".png")
        value = filename + " [input]"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n".encode() + content + b"\r\n",
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n".encode(),
            f"--{boundary}--\r\n".encode(),
        ]
        request = Request(self.base_url + "/upload/image", data=b"".join(parts), method="POST")
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with self._open(request, timeout=60) as response:
            result = json.loads(response.read().decode())
        if not result.get("name") or result.get("type") != "input":
            raise ComfyFilmError("ComfyUI returnerte ikke et opplastet filnavn")
        subfolder = str(result.get("subfolder") or "").strip("/")
        returned = str(result["name"])
        if Path(returned).name != returned or "\n" in returned or "\r" in returned:
            raise ComfyFilmError("ComfyUI returnerte et ugyldig opplastet filnavn")
        return (subfolder + "/" if subfolder else "") + returned + " [input]"

    def _wait(self, prompt_id: str, phase: str, cancel: Cancel | None, on_progress: Progress | None) -> dict[str, Any]:
        deadline = time.monotonic() + getattr(self, "wait_timeout", 900)
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("ComfyUI overskred tidsgrensen; ferdige klipp kan gjenopptas")
            if cancel and cancel():
                self._cancel(prompt_id)
                raise ComfyFilmError("Kjøringen ble kansellert")
            history = self._get("/history/" + prompt_id)
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") != "success" or status.get("completed") is not True:
                    raise ComfyFilmExecutionError("ComfyUI-kjøringen feilet: " + json.dumps(status, ensure_ascii=False), entry)
                if on_progress:
                    on_progress(phase, "ferdig", 100.0)
                return entry
            if on_progress:
                on_progress(phase, "venter på ComfyUI", 0.0)
            time.sleep(0.2)

    def _submit(self, graph: dict[str, Any], cancel: Cancel | None, on_progress: Progress | None, phase: str) -> dict[str, Any]:
        prompt_id = str(uuid.uuid4())
        self._prompt_id = prompt_id
        try:
            result = self._post("/prompt", {"prompt": graph, "prompt_id": prompt_id, "client_id": "storyboard"})
            if result.get("error") or result.get("node_errors"):
                raise ComfyFilmError(json.dumps(result, ensure_ascii=False))
            return self._wait(prompt_id, phase, cancel, on_progress)
        except Exception:
            # Covers timeout/disconnect even when POST acceptance was ambiguous.
            self._cancel(prompt_id)
            raise
        finally:
            self._prompt_id = None

    def _cancel(self, prompt_id: str | None = None) -> None:
        prompt_id = prompt_id or self._prompt_id
        if not prompt_id:
            return
        self._post("/interrupt", {"prompt_id": prompt_id})
        self._post("/queue", {"delete": [prompt_id]})
        # The queue entry disappears only after the worker process group has
        # been reaped. Never advertise a stopped job while Metal is still busy.
        deadline = time.monotonic() + 45
        while True:
            state = self._get("/queue")
            if not any(item[1] == prompt_id for item in state.get("queue_running", []) + state.get("queue_pending", [])):
                break
            if time.monotonic() > deadline:
                raise ComfyFilmError("LTX stopper fortsatt; vent til ComfyUI-køen er tom")
            time.sleep(.2)

    def cancel(self) -> None:
        self._cancel()

    def release_memory(self, cancel: Cancel | None = None) -> dict[str, Any]:
        """Run the bridge's synchronous unload node as a queue barrier."""
        self.preflight()
        entry = self._submit({"release": {"class_type": "LTX25MLXReleaseMemory", "inputs": {}}}, cancel, None, "Memory release")
        if "LTX worker stopped; Comfy memory released" not in json.dumps(entry, ensure_ascii=False):
            raise ComfyFilmError("LTX memory release returned no acknowledgement")
        return entry

    def _download_result(self, item: dict[str, Any], destination: Path) -> Path:
        query = urlencode({"filename": item.get("filename", ""), "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")})
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._request("GET", "/view?" + query, raw=True))
        return destination

    @staticmethod
    def _items(entry: dict[str, Any], kind: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for output in entry.get("outputs", {}).values():
            for item in output.get(kind, []):
                if isinstance(item, dict) and item.get("filename"):
                    found.append(item)
        return found

    @staticmethod
    def _video_items(entry: dict[str, Any]) -> list[dict[str, Any]]:
        return ComfyFilmClient._items(entry, "videos") or ComfyFilmClient._items(entry, "gifs")

    def qwen_image(self, prompt: str, dest: Path, references: list[tuple[Path, str]] | None = None, *,
                   width: int = 768, height: int = 512, seed: int = 0,
                   cancel: Cancel | None = None, on_progress: Progress | None = None) -> Path:
        """Native Turbo text/image generation; every supplied reference is encoded."""
        references = references or []
        if len(references) > 10:
            raise ValueError("Qwen-bildeflyten støtter inntil ti referansebilder")
        if min(width, height) < 64 or width % 32 or height % 32:
            raise ValueError("Qwen-bildestørrelsen må være minst 64 og delelig med 32")
        if cancel and cancel():
            raise ComfyFilmError("Kjøringen ble kansellert")
        models = self.preflight(require_images=True)["qwen_models"]
        listing = "; ".join(f"<image{i+1}>: {label}" for i, (_, label) in enumerate(references))
        text = (f"Reference images: {listing}.\n" if references else "") + prompt
        graph = {
            "model": {"class_type": "UNETLoader", "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
            "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": models["clip"], "type": "qwen_image", "device": "default"}},
            "vae": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
            "text": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["clip", 0], "vae": ["vae", 0],
                "prompt": text, "negative_prompt": "", "resolution": 768}},
            "latent": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "sigmas": {"class_type": "ManualSigmas", "inputs": {"sigmas": QWEN_SIGMAS}},
            "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "cache": {"class_type": "QwenImage21Cache", "inputs": {"model": ["model", 0], "device": "cpu", "dtype": "default"}},
            "guider": {"class_type": "CFGGuider", "inputs": {"model": ["cache", 0], "positive": ["text", 0], "negative": ["text", 1], "cfg": 1.0}},
            "sample": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["noise", 0], "guider": ["guider", 0],
                "sampler": ["sampler", 0], "sigmas": ["sigmas", 0], "latent_image": ["latent", 0]}},
            "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
            "save": {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": "storyboard/qwen-image-2.1-turbo"}},
        }
        for i, (path, _) in enumerate(references):
            if cancel and cancel():
                raise ComfyFilmError("Kjøringen ble kansellert")
            key = f"ref{i+1}"
            graph[key] = {"class_type": "LoadImage", "inputs": {"image": self._upload(path)}}
            graph["text"]["inputs"][f"images.image_{i+1}"] = [key, 0]
        if cancel and cancel():
            raise ComfyFilmError("Kjøringen ble kansellert")
        entry = self._submit(graph, cancel, on_progress, "Qwen-Image-2.1-Turbo")
        items = self._items(entry, "images")
        if not items:
            raise ComfyFilmError("Qwen fullførte uten et bilde")
        return self._download_result(items[-1], Path(dest))

    @staticmethod
    def qwen_model_signature() -> dict[str, Any]:
        root = Path(os.environ.get("RAVEN_MODEL_ROOT", str(Path.home() / "ComfyUI-Shared/models"))).expanduser()
        directories = {"unet": "diffusion_models", "clip": "text_encoders", "vae": "vae"}
        result = {}
        for kind, filename in QWEN_MODELS.items():
            stat = (root / directories[kind] / filename).stat()
            result[filename] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        return result

    def director_scene(self, prompts: list[str], references: list[Path], dest_dir: Path, *,
                       width: int = 384, height: int = 256, first_frames: int = 73,
                       continuation_frames: int = 96, steps: int = 8, seed: int = 0,
                       end_image: Path | None = None, low_ram: bool = False,
                       continuation_image: Path | None = None, continuation_frame: int = 0,
                       continuation_strength: float = .35, provenance: Any = None,
                       image_anchors: list[list[dict]] | None = None,
                       take_archive_root: Path | None = None,
                       cancel: Cancel | None = None, on_progress: Progress | None = None,
                       on_checkpoint: Callable[[int], None] | None = None) -> dict[str, Any]:
        if not prompts or any(not prompt.strip() for prompt in prompts):
            raise ValueError("LTX krever minst ett klipp")
        if len(references) > 1:
            raise ValueError("LTX bruker ett åpningsbilde. Separate portretter er ikke bildeankere.")
        if steps != 8:
            raise ValueError("LTX distilled bruker fast 8 + 3 steg")
        from .ltx_anchors import clean_anchors, frame_index
        if image_anchors is None:
            image_anchors = [[] for _ in prompts]
        if not isinstance(image_anchors, list) or len(image_anchors) != len(prompts):
            raise ValueError("Bildeankrene må oppgis per klipp i scenen")
        image_anchors = [clean_anchors(items, first_frames if i == 0 else continuation_frames, image_key="path")
                         for i, items in enumerate(image_anchors)]
        self.preflight()
        node_info = self._get("/object_info")
        info = node_info["LTX25MLXScene"]["input"]
        available = set(info.get("required", {})) | set(info.get("optional", {}))
        if not {"resume_run_id", "request_fingerprint", "continuation_image"} <= available:
            raise ComfyFilmError("Start ComfyUI Desktop på nytt for oppdaterte LTX-noder før filmen fortsettes")
        if any(image_anchors) and ("image_anchors" not in available or "LTX25MLXImageAnchor" not in node_info):
            raise ComfyFilmError("Start ComfyUI Desktop på nytt for støtte for flere bildeankre")
        installed = runtime_identity(RUNTIME_DIR, MODEL_DIR, BRIDGE_DIR)
        expected = {"engine": "ltx-2.5", "prompts": prompts, "width": width, "height": height,
                    "first_frames": first_frames, "continuation_frames": continuation_frames, "seed": seed,
                    "low_ram": low_ram, "continuation_frame": continuation_frame,
                    "continuation_strength": continuation_strength}
        anchors = [("100", "start_image", references[0] if references else None),
                   ("101", "end_image", end_image), ("102", "continuation_image", continuation_image)]
        normalized_provenance = self._scene_provenance(provenance)
        canonical_anchors = [[{"hash": _canonical_image_hash(a["path"]),
                              "frame": frame_index(a["frame"], first_frames if i == 0 else continuation_frames),
                              "strength": a["strength"]} for a in items]
                             for i, items in enumerate(image_anchors)]
        signature = digest({"config": expected, "installed": installed, "provenance": normalized_provenance,
                            **({"image_anchors": canonical_anchors} if any(canonical_anchors) else {}),
                            "references": {key: file_hash(path) if path else None for _, key, path in anchors}})
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        state_path = dest_dir / "scene-state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        previous_run_id = state.get("run_id")
        previous_signature = state.get("signature")
        if (previous_signature != signature and
                (dest_dir / "recipe.json").is_file() and (dest_dir / "manifest.json").is_file() and
                any(path.is_file() and path.suffix.lower() in {".mp4", ".wav"} for path in dest_dir.rglob("*"))):
            archive_take(dest_dir, take_archive_root or dest_dir.parent / "takes" / dest_dir.name / "native",
                         {"previousRunId": previous_run_id,
                          "previousSignature": previous_signature})
        run_id = previous_run_id if previous_signature == signature else None
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            run_id = uuid.uuid4().hex
        # Commit our ID before HTTP submission, so a crash cannot lose the run.
        atomic_json(state_path, {"signature": signature, "run_id": run_id})
        root = self.output_dir / "ltx25_mlx" / "scene_runs"
        source_dir = root / run_id
        self._safe_child(root, source_dir)
        source_dir.mkdir(parents=True, exist_ok=True)
        complete = False
        manifest_path = source_dir / "manifest.json"
        # A small server-owned record lets a changed scene import only a
        # validated unchanged prefix.  It is deliberately separate from the
        # worker recipe, which remains the authoritative runtime checkpoint.
        input_record = {"format": 1, "signature": signature, "installed": installed,
                        "config": expected, "provenance": normalized_provenance,
                        "references": {key: file_hash(path) if path else None for _, key, path in anchors},
                        "image_anchors": canonical_anchors}
        input_record["provenance_hash"] = digest(input_record["provenance"])
        input_record_path = source_dir / "storyboard-inputs.json"
        if manifest_path.is_file():
            try:
                self._scene_records(source_dir, len(prompts), expected)
                recipe = json.loads((source_dir / "recipe.json").read_text())
                complete = self._recipe_matches(recipe, expected, signature, installed)
            except (OSError, ValueError, KeyError, ComfyFilmError):
                complete = False
        if not complete:
            if (run_id != previous_run_id and isinstance(previous_run_id, str)
                    and re.fullmatch(r"[0-9a-f]{32}", previous_run_id)):
                self._seed_prefix(root / previous_run_id, source_dir, input_record, cancel=cancel)
                input_record_path = source_dir / "storyboard-inputs.json"
            atomic_json(input_record_path, input_record)
            graph: dict[str, Any] = {"200": {"class_type": "LTX25MLXScene", "inputs": {
                "shot_prompts": "\n---\n".join(prompts), "width": int(width), "height": int(height),
                "first_frames": int(first_frames), "continuation_frames": int(continuation_frames),
                "seed": int(seed), "low_ram": bool(low_ram), "resume_run_id": run_id,
                "request_fingerprint": signature, "continuation_frame": continuation_frame,
                "continuation_strength": continuation_strength}}}
            for node_id, key, path in anchors:
                if path:
                    graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": self._upload(Path(path))}}
                    graph["200"]["inputs"][key] = [node_id, 0]
            previous, serial = None, 0
            for clip_index, items in enumerate(image_anchors):
                for anchor in items:
                    image_id, anchor_id = str(1000 + serial * 2), str(1001 + serial * 2)
                    serial += 1
                    graph[image_id] = {"class_type": "LoadImage", "inputs": {"image": self._upload(Path(anchor["path"]), canonical=True)}}
                    inputs = {"image": [image_id, 0], "clip_index": clip_index,
                              "frame": frame_index(anchor["frame"], first_frames if clip_index == 0 else continuation_frames),
                              "strength": anchor["strength"]}
                    if previous:
                        inputs["previous"] = previous
                    graph[anchor_id] = {"class_type": "LTX25MLXImageAnchor", "inputs": inputs}
                    previous = [anchor_id, 0]
            if previous:
                graph["200"]["inputs"]["image_anchors"] = previous
            reported = -1
            def progress(phase, detail, percent):
                nonlocal reported
                if manifest_path.is_file() and on_checkpoint:
                    data = json.loads(manifest_path.read_text())
                    # Only the worker commits validated clips to this manifest.
                    count = len(data.get("segments", []))
                    if count != reported:
                        on_checkpoint(count)
                        reported = count
                if on_progress:
                    on_progress(phase, detail, percent)
            self.wait_timeout = 7300
            entry = self._submit(graph, cancel, progress, "LTX-2.5 Q8")
            run_ids = entry.get("outputs", {}).get("200", {}).get("ltx_run", [])
            if run_ids != [run_id]:
                raise ComfyFilmError("LTX returnerte ikke kjøringens avtalte run-ID")
        recipe_data = json.loads((source_dir / "recipe.json").read_text())
        if not self._recipe_matches(recipe_data, expected, signature, installed):
            raise ComfyFilmError("LTX-oppskriften avviker fra jobben som ble sendt")
        records = self._scene_records(source_dir, len(prompts), expected)
        if cancel and cancel():
            raise InterruptedError("Filmjobben ble stoppet")
        def publish(source):
            self._safe_child(source_dir, source)
            target = dest_dir / source.name
            if target.is_file() and file_hash(target) == file_hash(source):
                return
            pending = target.with_name('.' + target.name + '.pending')
            shutil.copy2(source, pending)
            pending.replace(target)
        for name in ("movie.mp4", "manifest.json", "recipe.json"):
            publish(source_dir / name)
        shots = []
        for record in records:
            for key in ("file", "audio"):
                publish(source_dir / record[key])
            shots.append({"video": dest_dir / record["file"], "audio": dest_dir / record["audio"], "frames": record["frames"]})
        if on_checkpoint:
            on_checkpoint(len(records))
        return {"shots": shots, "movie": dest_dir / "movie.mp4", "manifest": dest_dir / "manifest.json",
                "recipe": dest_dir / "recipe.json", "run_id": run_id, "reused": complete}

    @staticmethod
    def _scene_provenance(value: Any) -> Any:
        """Normalize provenance to scene-local, deterministic data."""
        if not isinstance(value, dict):
            return value
        result = dict(value)
        # Shot prompts/seeds are compared positionally below; keeping them in
        # the scene fingerprint would unnecessarily invalidate every prefix.
        result.pop("shots", None)
        scene = result.get("scene")
        if isinstance(scene, dict):
            scene = dict(scene)
            scene.pop("shots", None)
            result["scene"] = scene
        cast = result.get("cast")
        keys = result.get("scene", {}).get("characterKeys") if isinstance(result.get("scene"), dict) else None
        if isinstance(cast, list) and isinstance(keys, list):
            wanted = set(keys)
            result["cast"] = [item for item in cast if isinstance(item, dict) and item.get("key") in wanted]
        return result

    @staticmethod
    def _recipe_matches(recipe: dict[str, Any], expected: dict[str, Any], signature: str,
                        installed: dict[str, Any]) -> bool:
        inputs = recipe.get("inputs") if isinstance(recipe.get("inputs"), dict) else recipe
        if not isinstance(inputs, dict):
            return False
        engine = inputs.get("engine", recipe.get("engine"))
        if engine != expected.get("engine") or recipe.get("engine", engine) != engine:
            return False
        return (all(inputs.get(key) == value for key, value in expected.items() if key != "engine")
                and (inputs.get("request_fingerprint") == signature
                     or recipe.get("request_fingerprint") == signature)
                and recipe.get("installed") == installed)

    @staticmethod
    def _sidecar_signature(record: dict[str, Any]) -> str:
        anchors = record.get("image_anchors") or []
        payload = {"config": record.get("config"), "installed": record.get("installed"),
                   "provenance": ComfyFilmClient._scene_provenance(record.get("provenance")),
                   "references": record.get("references", {})}
        if any(anchors):
            payload["image_anchors"] = anchors
        return digest(payload)

    def _seed_prefix(self, old_dir: Path, new_dir: Path, current: dict[str, Any],
                     cancel: Cancel | None = None) -> int:
        """Copy a strictly validated contiguous prefix into a fresh worker run."""
        if cancel and cancel():
            raise InterruptedError("Filmjobben ble stoppet")
        if not old_dir.is_dir() or old_dir.is_symlink():
            return 0
        try:
            old_record = json.loads((old_dir / "storyboard-inputs.json").read_text())
            old_recipe = json.loads((old_dir / "recipe.json").read_text())
            old_config = old_record.get("config") if isinstance(old_record, dict) else None
            if not isinstance(old_config, dict):
                old_config = old_recipe.get("inputs") if isinstance(old_recipe.get("inputs"), dict) else old_recipe
            if not isinstance(old_config, dict) or old_recipe.get("installed") != current["installed"]:
                return 0
            # Bind the sidecar to the canonical worker recipe.  An edited
            # storyboard-inputs file must never redefine what the old worker
            # actually rendered.
            recipe_inputs = old_recipe.get("inputs") if isinstance(old_recipe.get("inputs"), dict) else None
            if recipe_inputs is None or old_recipe.get("signature") != digest({
                    "inputs": recipe_inputs, "installed": old_recipe.get("installed"),
                    "format": old_recipe.get("format"), "frame_rate": old_recipe.get("frame_rate"),
                    "carry_frames": old_recipe.get("carry_frames"),
                    "stage1_steps": old_recipe.get("stage1_steps"),
                    "stage2_steps": old_recipe.get("stage2_steps")}):
                return 0
            for key in ("prompts", "width", "height", "first_frames", "continuation_frames", "seed", "low_ram",
                        "continuation_frame", "continuation_strength"):
                if old_record.get("config", {}).get(key) != recipe_inputs.get(key):
                    return 0
            old_request = recipe_inputs.get("request_fingerprint")
            if old_record.get("signature") != old_request:
                return 0
            if old_record.get("provenance_hash") != digest(old_record.get("provenance")):
                return 0
            if old_record.get("signature") != self._sidecar_signature(old_record):
                return 0
            old_prompts = old_config.get("prompts")
            if not isinstance(old_prompts, list):
                old_prompts = old_recipe.get("prompts")
            if not isinstance(old_prompts, list):
                return 0
            # Runtime, geometry, seed, and anchor settings are scene-global.
            for key in ("width", "height", "first_frames", "continuation_frames", "seed", "low_ram",
                        "continuation_frame", "continuation_strength"):
                if old_config.get(key) != current["config"].get(key):
                    return 0
            old_refs = old_record.get("references", {})
            if old_refs != current.get("references", {}):
                return 0
            old_anchors = old_record.get("image_anchors", [])
            new_anchors = current.get("image_anchors", [])
            if len(old_anchors) != len(new_anchors):
                return 0
            prefix = 0
            for index, prompt in enumerate(current["config"].get("prompts", [])):
                if index >= len(old_prompts) or old_prompts[index] != prompt:
                    break
                if index >= len(old_anchors) or old_anchors[index] != new_anchors[index]:
                    break
                prefix += 1
            if prefix <= 0:
                return 0
            # A changed scene-level provenance invalidates the whole scene;
            # per-shot prompt/anchor changes still retain their earlier prefix.
            if self._scene_provenance(old_record.get("provenance")) != current.get("provenance"):
                return 0
            old_expected = dict(old_config)
            old_expected["prompts"] = old_prompts
            records = self._validated_prefix_records(old_dir, old_expected)
            if cancel and cancel():
                raise InterruptedError("Filmjobben ble stoppet")
            new_dir.mkdir(parents=True, exist_ok=True)
            copied = []
            for record in records[:prefix]:
                if cancel and cancel():
                    raise InterruptedError("Filmjobben ble stoppet")
                entry = dict(record)
                for key in ("file", "audio", "window"):
                    source = old_dir / record[key]
                    target = new_dir / source.name
                    self._safe_child(old_dir, source)
                    self._safe_child(new_dir, target)
                    before = file_hash(source)
                    shutil.copy2(source, target)
                    if before != file_hash(source) or before != file_hash(target):
                        raise ComfyFilmError("LTX-kildemediet endret seg under kopiering")
                copied.append(entry)
            # Compute the worker recipe signature from its original recipe
            # inputs. Persisting this exact signature is what allows the worker
            # to consume the seeded manifest without bypassing its checks.
            new_inputs = dict(old_recipe.get("inputs", old_recipe))
            new_inputs["prompts"] = current["config"]["prompts"]
            new_inputs["request_fingerprint"] = current["signature"]
            canonical_anchors = current.get("image_anchors") or []
            if any(canonical_anchors):
                new_inputs["image_anchors"] = [[{"path": item["hash"], "frame": item["frame"],
                                                  "strength": item["strength"]} for item in items]
                                                 for items in canonical_anchors]
            else:
                new_inputs.pop("image_anchors", None)
            identity = {"inputs": new_inputs, "installed": current["installed"], "format": 2,
                        "frame_rate": 24, "carry_frames": 25, "stage1_steps": 8, "stage2_steps": 3}
            worker_signature = digest(identity)
            atomic_json(new_dir / "recipe.json", {**identity, "signature": worker_signature})
            atomic_json(new_dir / "manifest.json", {"state": "running", "signature": worker_signature,
                                                       "segments": copied, "reused": len(copied)})
            return len(copied)
        except InterruptedError:
            raise
        except (OSError, ValueError, KeyError, TypeError, ComfyFilmError):
            return 0

    def _validated_prefix_records(self, root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
        """Validate only the committed contiguous records of an interrupted run."""
        data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        entries = data.get("segments")
        if not isinstance(entries, list):
            raise ComfyFilmError("LTX-manifesten mangler klipp")
        valid: list[dict[str, Any]] = []
        for index, entry in enumerate(entries):
            try:
                if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) for k in ("file", "audio", "window")):
                    raise ValueError("malformed segment")
                expected = config["first_frames"] if index == 0 else config["continuation_frames"]
                if entry.get("frames") != expected or entry.get("prompt") != config["prompts"][index]:
                    raise ValueError("recipe differs")
                for key in ("file", "audio", "window"):
                    source = root / entry[key]
                    self._safe_child(root, source)
                    if file_hash(source) != entry["hashes"][key]:
                        raise ValueError("media changed")
                validate_video(root / entry["file"], expected, config["width"], config["height"])
                validate_video(root / entry["window"], expected + (25 if index else 0), config["width"], config["height"])
                validate_wave(root / entry["audio"], expected)
            except (OSError, KeyError, TypeError, ValueError, ComfyFilmError):
                break
            valid.append(entry)
        return valid

    def legacy_scene(self, directory: Path, run_id: str, prompts: list[str], reference: Path,
                     *, seed: int, cancel: Cancel | None = None) -> dict | None:
        """Retain completed pre-checkpoint scenes only with verifiable old provenance."""
        try:
            if not re.fullmatch(r"[0-9a-f]{32}", run_id):
                return None
            runtime, model = RUNTIME_DIR, MODEL_DIR
            source = self.output_dir / "ltx25_mlx/scene_runs" / run_id
            self._safe_child(source.parent, source)
            for root in (source, directory):
                for name in ("recipe.json", "manifest.json", "movie.mp4"):
                    self._safe_child(root, root / name)
            recipe = json.loads((source / "recipe.json").read_text())
            if recipe != json.loads((directory / "recipe.json").read_text()):
                return None
            expected = {"engine": "ltx-2.5", "model": str(model), "prompts": prompts, "seed": seed,
                        "width": 384, "height": 256, "first_frames": 73, "continuation_frames": 96,
                        "low_ram": False, "frame_rate": 24, "carry_frames": 25, "stage1_steps": 8, "stage2_steps": 3}
            if any(recipe.get(k) != v for k, v in expected.items()) or recipe.get("end_image"):
                return None
            marker = json.loads((model / ".ltx-installed.json").read_text())
            commit = subprocess.check_output(["git", "-C", str(runtime), "rev-parse", "HEAD"], text=True, timeout=10).strip()
            if recipe.get("runtime_commit") != commit or marker.get("runtime_commit") != commit:
                return None
            if subprocess.run(["git", "-C", str(runtime), "diff", "--quiet", "HEAD", "--", "packages", "uv.lock"], timeout=10).returncode:
                return None
            manifest = json.loads((source / "manifest.json").read_text())
            started = manifest["started_at"]
            if marker["installed_at"] > started:
                return None
            for name, size in marker["files"].items():
                path = model / name
                if not path.resolve().is_relative_to(model.resolve()):
                    return None
                stat = path.stat()
                if stat.st_size != size or max(stat.st_mtime, stat.st_ctime) > started:
                    return None
            anchor = Path(recipe["start_image"])
            self._safe_child(source, anchor)
            from PIL import Image
            with Image.open(anchor) as old, Image.open(reference) as current:
                if old.size != current.size or old.convert("RGB").tobytes() != current.convert("RGB").tobytes():
                    return None
            records = self._scene_records(directory, len(prompts))
            if manifest.get("state") != "complete" or records != manifest.get("segments"):
                return None
            shots = []
            for index, record in enumerate(records):
                frames = 73 if index == 0 else 96
                if record["frames"] != frames or record.get("prompt") != prompts[index]:
                    return None
                for key in ("file", "audio"):
                    for root in (source, directory):
                        self._safe_child(root, root / record[key])
                    if file_hash(source / record[key]) != file_hash(directory / record[key]):
                        return None
                validate_video(directory / record["file"], frames, 384, 256, cancel)
                validate_wave(directory / record["audio"], frames)
                shots.append({"video": directory / record["file"], "audio": directory / record["audio"], "frames": frames})
            movie = directory / "movie.mp4"
            if file_hash(source / "movie.mp4") != file_hash(movie):
                return None
            validate_video(movie, sum(s["frames"] for s in records), 384, 256, cancel)
            return {"shots": shots, "movie": movie, "run_id": run_id, "reused": True, "legacy": True}
        except InterruptedError:
            raise
        except Exception:
            return None  # No trustworthy old checkpoint; preserve it and use a new run.

    @staticmethod
    def _safe_child(root: Path, path: Path) -> None:
        if path.is_symlink() or path.resolve().parent != root.resolve():
            raise ComfyFilmError("LTX-manifesten peker utenfor sin egen run-mappe")

    def _scene_records(self, root: Path, count: int, config: dict | None = None) -> list[dict[str, Any]]:
        data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        entries = data.get("segments")
        if data.get("state") != "complete" or not isinstance(entries, list) or len(entries) != count:
            raise ComfyFilmError("LTX-manifesten er ikke komplett")
        for entry in entries:
            if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) for k in ("file", "audio")):
                raise ComfyFilmError("LTX-manifesten mangler klippfiler")
            if any(Path(entry[k]).name != entry[k] for k in ("file", "audio")):
                raise ComfyFilmError("LTX-manifesten inneholder usikre filbaner")
            if type(entry.get("frames")) is not int or entry["frames"] <= 0:
                raise ComfyFilmError("LTX-manifesten mangler frameantall")
        if config is not None:
            for index, entry in enumerate(entries):
                frames = config["first_frames"] if index == 0 else config["continuation_frames"]
                if entry["frames"] != frames or entry.get("prompt") != config["prompts"][index]:
                    raise ComfyFilmError("LTX-klippet avviker fra oppskriften")
                for key in ("file", "audio", "window"):
                    source = root / entry[key]
                    self._safe_child(root, source)
                    if file_hash(source) != entry["hashes"][key]:
                        raise ComfyFilmError("LTX-klippets innhold er endret")
                validate_video(root / entry["file"], frames, config["width"], config["height"])
                validate_wave(root / entry["audio"], frames)
            movie = root / "movie.mp4"
            self._safe_child(root, movie)
            if file_hash(movie) != data.get("movie_hash"):
                raise ComfyFilmError("LTX-scenefilmen er endret")
            validate_video(movie, sum(e["frames"] for e in entries), config["width"], config["height"])
        return entries

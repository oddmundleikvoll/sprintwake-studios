"""Local LTX video backend with the installed still-image engines."""
from pathlib import Path
from .base import Backend
from .ltx_backend import LTXBackend
from .qwen_backend import WithQwenImage

BACKEND_IDS = ("ltx", "configured")


def build_backend(kind: str, *, vpipe_binary: Path, workspace: Path,
                  comfyui_url: str = "http://127.0.0.1:8188",
                  project_root: Path | None = None, media_profiles: Path | None = None) -> Backend:
    if kind == "configured":
        if media_profiles is None: raise ValueError("configured backend requires --media-profiles")
        from .configured import from_file
        return from_file(media_profiles,workspace,comfyui_url)
    if kind != "ltx":
        raise ValueError("Denne installasjonen bruker LTX-2.5 Q8 (backend='ltx').")
    return WithQwenImage(LTXBackend(workspace=workspace, base_url=comfyui_url), comfyui_url)

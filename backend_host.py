"""Optional process host for integrating Backend with a desktop shell."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import time

from backend import Backend
from connections import ConnectionStore, selected_llm, validate


def write_ready(path: Path, payload: dict) -> None:
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--engine-root", type=Path, default=None)
    parser.add_argument("--runtime-python", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--comfyui-url")
    parser.add_argument("--ready-file", type=Path, default=None)
    args = parser.parse_args(argv)
    store=ConnectionStore(args.config);doc=store.load()
    for key,value in (("root",args.root),("engine",args.engine_root),("python",args.runtime_python),("workspace",args.workspace)):
        if value:doc["paths"][key]=str(value.expanduser().resolve())
    if args.comfyui_url:doc["comfyUrl"]=args.comfyui_url
    doc=validate(doc);paths=doc["paths"]
    # Shell users can provide RAVEN_LLM_API_KEY/RAVEN_COMFY_API_KEY in their
    # environment; this headless helper never reads or writes the Keychain.
    backend=Backend(Path(paths["root"]),engine_root=Path(paths["engine"]),runtime_python=Path(paths["python"]),workspace=Path(paths["workspace"]),comfyui_url=doc["comfyUrl"],llm=selected_llm(doc),connection_settings=doc,job_state_dir=store.path.parent/"Jobs")
    stop = False
    def request_stop(*_: object) -> None:
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        info = backend.start()
        ready = args.ready_file or backend.system_dir / "backend-ready.json"
        write_ready(ready, {"pid": backend.process.pid if backend.process else None, "baseUrl": backend.base_url, "dataDir": str(backend.data_dir), "info": info})
        while not stop and backend.process is not None and backend.process.poll() is None:
            time.sleep(0.25)
        return 0
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())

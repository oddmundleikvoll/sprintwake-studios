#!/usr/bin/env python3
"""Switchboard director gateway; oMLX owns the MLX/Splash model."""
from pathlib import Path
import sys
import signal
import subprocess
import threading
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.film import _local_json, prepare_language_memory
from server.director_omlx import DirectorOMLX, validate_checkpoint
from server.fable import OMLX_CLI
from server.fable_gateway import FableGateway
from server.gpu_handoff import gpu_admission


def start():
    try:
        status = _local_json("http://127.0.0.1:9877/api/status")
    except URLError:
        status = {}
    film = status.get("film") or {}
    planning = status.get("operation") == "film" and film.get("phase") in ("plan", "memory", "review")
    refining = (status.get("operation") == "refine" and status.get("languageActive", 0) > 0
                and (status.get("refine") or {}).get("state") != "rendering")
    if (status.get("stills") or {}).get("busy") or (status.get("busy") and not (planning or refining)):
        raise RuntimeError("Storyboard lager bilde/video. Vent til jobben er ferdig.")
    prepare_language_memory()
    for port, endpoint in ((12345, "health"),):
        try:
            _local_json(f"http://127.0.0.1:{port}/{endpoint}")
        except URLError:
            continue
        raise RuntimeError(f"En annen språkmodell kjører på {port}. Stopp den i Model Switchboard først.")
    validate_checkpoint()
    subprocess.run([str(OMLX_CLI), "start", "--timeout", "720"], check=True, timeout=750)
    client = DirectorOMLX()
    gateway = FableGateway(("127.0.0.1", 8002), 8000, omlx=client)
    signal.signal(signal.SIGTERM, lambda *_: gateway.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: gateway.stop_event.set())
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    return gateway, client, thread


def main():
    gateway = None
    try:
        with gpu_admission():
            gateway, client, thread = start()
            if not gateway.stop_event.is_set():
                client.load()
                gateway.started = True
        gateway.stop_event.wait()
    finally:
        if gateway is not None:
            with gateway.gate:
                gateway.draining = True
            while True:
                try:
                    client.release()
                    break
                except (OSError, ValueError, KeyError, StopIteration) as exc:
                    print(f"Director release not acknowledged: {type(exc).__name__}", file=sys.stderr, flush=True)
                    threading.Event().wait(1)
            gateway.shutdown()
            gateway.server_close()
            thread.join()


if __name__ == "__main__":
    main()

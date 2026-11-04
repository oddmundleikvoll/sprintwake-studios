#!/usr/bin/env python3
"""Profile-only idle shutdown; never terminate an accepted request."""
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


def stop_starting_launcher():
    pid_path = Path.home() / "Library/Application Support/ModelSwitchboard/Controller/run/qwen38-fable.pid"
    try:
        pid = int(pid_path.read_text().strip())
        command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return
    expected = str(Path(__file__).resolve().with_name("start_fable.py"))
    if expected not in shlex.split(command):
        raise RuntimeError("Ukjent oppstartsprosess for Fable; stopp ble ikke bekreftet")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise TimeoutError("Fables oppstartsprosess avsluttet ikke innen 900s")


def main():
    try:
        with urlopen(Request("http://127.0.0.1:8002/shutdown", data=b"{}",
                             headers={"Content-Type": "application/json"}), timeout=5) as response:
            if not json.load(response).get("draining"):
                raise RuntimeError("Fable bekreftet ikke trygg stopp")
    except HTTPError as exc:
        raise RuntimeError("Fable arbeider fortsatt; modellen ble ikke stoppet") from exc
    except URLError:
        return stop_starting_launcher()
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        try:
            with urlopen("http://127.0.0.1:8002/health", timeout=2):
                pass
        except (HTTPError, TimeoutError):
            pass
        except (URLError, ConnectionResetError):
            return
        time.sleep(0.1)
    raise TimeoutError("Fables modellprosess avsluttet ikke innen 900s")


if __name__ == "__main__":
    main()

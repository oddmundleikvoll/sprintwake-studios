"""Temporary hosted-runner diagnosis; no model services, keys or production data."""
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import Backend
from connections import defaults, write_private

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    settings = root / "Settings"
    for name in ("llm-services.json", "tts-services.json", "soundtrack-services.json"):
        write_private(settings / name, {"services": []})
    doc = defaults()
    doc["selected"] = {"llm": "none", "image": "none", "video": "none"}
    wrapper = root / "trace-python"
    wrapper.write_text(
        "#!" + str(ROOT / "runtime/bundle-venv/bin/python") + "\n"
        "import faulthandler,runpy,sys\n"
        "faulthandler.enable()\n"
        "faulthandler.dump_traceback_later(5,repeat=True)\n"
        "sys.argv=sys.argv[3:]\n"
        "runpy.run_module('server',run_name='__main__')\n"
    )
    wrapper.chmod(0o755)
    backend = Backend(root / "Studio", runtime_python=wrapper,
                      workspace=root / "EngineWork", config_dir=settings,
                      connection_settings=doc, credential_loader=lambda _: "",
                      startup_timeout=30)
    start = time.monotonic()
    try:
        backend.start()
        print("Startup completed in", time.monotonic() - start, "seconds", flush=True)
    except Exception as error:
        print("Startup failed after", time.monotonic() - start, "seconds:", error, flush=True)
    finally:
        backend.close()
        log = root / "Studio/System/raven-backend.log"
        if log.is_file():
            print(log.read_text(errors="replace"), flush=True)

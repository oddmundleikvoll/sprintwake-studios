"""Entry point: ``python3 -m server`` (see serve.sh)."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
from pathlib import Path
from typing import Any

from .app import SERVER_CONFIG_NAME, Context, build_server, llm_services_json
from .backends import BACKEND_IDS, build_backend
from .orchestrator import Orchestrator
from .render_timings import FILE_NAME as TIMINGS_FILE, RenderTimings
from .store import Store
from .render_record import ensure_render_record
from .llm import CONFIG_NAME as LLM_CONFIG_NAME
from .llm import load_services as load_llm_services
from .local_config import protect_local_configs, ensure_local_configs
from .tts import CONFIG_NAME, load_engines
from .desktop import DesktopLifetime, stop_owned_work
from .app_lifetime import AppLifetime

UI_ROOT = Path(__file__).resolve().parent.parent

# Defaults match the layout ./setup.sh creates: everything beside the
# storyboard folder, on the same drive. vpipe resolves models/ and its LMDB
# registry relative to the directory it is launched from, so the workspace
# must be the directory the models were prepared in.
DEFAULT_WORKSPACE = UI_ROOT.parent / "vpipe-workspace"
# Storyboards and uploads are the user's documents, not vpipe's runtime state,
# so they live in their own folder outside the workspace. Project folders live
# directly beneath this directory.
DEFAULT_DATA_DIR = UI_ROOT.parent / "storyboard-projects"
# The CLI inside the Vpipe Manager app, which ./setup.sh installs.
VPIPE_APP_CLI = "Vpipe Manager.app/Contents/Helpers/vpipe"


def _default_vpipe() -> Path:
    for apps in (Path("/Applications"), Path.home() / "Applications"):
        if (apps / VPIPE_APP_CLI).is_file():
            return apps / VPIPE_APP_CLI
    return Path("/Applications") / VPIPE_APP_CLI


def _configured(ui_root: Path, key: str) -> Path | None:
    """A path saved in server-config.json — by ./setup.sh or the Settings
    dialog — if any.

    Below the command-line flag and the SBV_* environment variable, above the
    built-in default, so a launch script that already pins one keeps working
    exactly as before no matter what was saved here.
    """
    path = ui_root / SERVER_CONFIG_NAME
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    raw = doc.get(key)
    return Path(raw).expanduser() if raw else None


def _configured_value(ui_root: Path, key: str) -> Any:
    """A raw (non-path) value from server-config.json, or None."""
    path = ui_root / SERVER_CONFIG_NAME
    try:
        return json.loads(path.read_text()).get(key)
    except (OSError, json.JSONDecodeError, AttributeError):
        return None



BANNER = r"""
  ______ _____  ____   ______  __ ______  ____  ___    ____  ____
 / __/ //_  __// __ \ / __ \ \/ // __ / / __ \/ _ |  / __ \/ __ \
_\ \ / /  / /  / /_/ // /_/ /\  // /_/ / / /_/ / __ | / /_/ / /_/ /
/___//_/  /_/   \____/ \____/ /_/ \____/  \____/_/ |_|/_____/_____/
                                              storyboard -> video
"""


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="storyboard")
    p.add_argument("--port", type=int, default=int(os.environ.get("SBV_PORT", 9877)))
    p.add_argument("--bind", default=os.environ.get("SBV_BIND", "127.0.0.1"))
    p.add_argument("--lan", action="store_true", help="bind all interfaces")
    p.add_argument("--desktop", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--backend",
        choices=BACKEND_IDS,
        default=None,
        help="render backend; falls back to SBV_BACKEND, then \"backend\" in "
             "server-config.json, then LTX-2.5",
    )
    p.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="local rendering workspace; falls "
             "back to SBV_WORKSPACE, then server-config.json, then "
             f"{DEFAULT_WORKSPACE}",
    )
    p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="where storyboards and uploads live — highest priority; falls "
             "back to SBV_DATA_DIR, then whatever Settings last saved, then "
             f"{DEFAULT_DATA_DIR}",
    )
    p.add_argument(
        "--vpipe",
        type=Path,
        default=None,
        help="path to the vpipe CLI binary; falls back to SBV_VPIPE, then "
             "server-config.json, then the Vpipe Manager app",
    )
    p.add_argument(
        "--comfyui-url", default=os.environ.get("SBV_COMFYUI", "http://127.0.0.1:8188")
    )
    p.add_argument(
        "--tts",
        default=os.environ.get("SBV_TTS", "none"),
        help=f"default speech engine id for new boards (see {CONFIG_NAME})",
    )
    p.add_argument(
        "--llm",
        default=os.environ.get("SBV_LLM", "ollama-local"),
        help=f"default prompt-rewriting model id (see {LLM_CONFIG_NAME})",
    )
    p.add_argument("--llm-config-dir",type=Path,default=None,help="private language-service configuration directory")
    p.add_argument("--config-dir", type=Path, default=None,
                   help="private per-user engine configuration directory")
    p.add_argument("--media-profiles",type=Path,default=None,help="explicit image/video workflow profile document")
    return p.parse_args(argv)


def lan_ip() -> str:
    """Best-effort local address for the printed URL."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"


def _main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    bind = "0.0.0.0" if args.lan else args.bind
    config_root = (args.config_dir or UI_ROOT).expanduser().resolve()
    config_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.config_dir:
        try:
            config_root.chmod(0o700)
        except OSError:
            pass
    # Before anything reads server-config.json: bring back a settings file a
    # git checkout removed.
    protect_local_configs(config_root, UI_ROOT)

    workspace = (args.workspace
                 or (Path(os.environ["SBV_WORKSPACE"]) if os.environ.get("SBV_WORKSPACE") else None)
                 or _configured(config_root, "workspace")
                 or DEFAULT_WORKSPACE).expanduser()
    args.vpipe = (args.vpipe
                  or (Path(os.environ["SBV_VPIPE"]) if os.environ.get("SBV_VPIPE") else None)
                  or _configured(config_root, "vpipe")
                  or _default_vpipe())
    # Two roots on purpose. The workspace is vpipe's — it resolves models/ and
    # its model registry relative to it, so it is not ours to choose. The data
    # directory is where the user's storyboards and uploads live. Priority,
    # highest first: an explicit --data-dir, SBV_DATA_DIR, whatever the
    # Settings dialog last saved to server-config.json, then DEFAULT_DATA_DIR.
    if args.data_dir:
        data_dir = args.data_dir.expanduser()
        data_dir_source = "cli"
    elif os.environ.get("SBV_DATA_DIR"):
        data_dir = Path(os.environ["SBV_DATA_DIR"]).expanduser()
        data_dir_source = "env"
    else:
        configured = _configured(config_root, "dataDir")
        data_dir = configured or DEFAULT_DATA_DIR
        data_dir_source = "config" if configured else "default"
    data_dir.mkdir(parents=True, exist_ok=True)
    # Which engine renders video: a one-time choice, normally made with
    # "backend" in server-config.json. The flag and env var still win so a
    # launch script can pin one.
    args.backend = (args.backend
                    or os.environ.get("SBV_BACKEND")
                    or _configured_value(config_root, "backend")
                    or "ltx")
    if args.backend not in BACKEND_IDS:
        print(f"error: unknown backend {args.backend!r} — expected one of "
              f"{', '.join(BACKEND_IDS)}", file=sys.stderr)
        return 2
    backend = build_backend(
        args.backend,
        vpipe_binary=args.vpipe.expanduser(),
        workspace=workspace,
        comfyui_url=args.comfyui_url,
        project_root=UI_ROOT,
        media_profiles=args.media_profiles,
    )
    # Engines come from tts-services.json: a service is a name and a URL, so
    # adding another instance is an edit rather than a code change. None loads
    # anything until asked, so a board can switch between them at runtime.
    ensure_local_configs(config_root, UI_ROOT)
    tts_engines = load_engines(
        config_root, vpipe_binary=args.vpipe.expanduser(), workspace=workspace
    )

    # Prompt rewriting, from llm-services.json, on the same linked-not-installed
    # footing as the speech engines.
    llm_services = load_llm_services(args.llm_config_dir or config_root)

    store = Store(workspace=workspace, data_dir=data_dir)
    # Anything left mid-run by a previous process is not running now.
    stranded = store.reconcile_startup()
    # Locked shots from before render records existed: give each one what
    # its clip was rendered with, once (render_record.py). Nothing renders
    # at startup, so this cannot race a render's own save.
    for entry in store.list_boards():
        try:
            board = store.load(entry["slug"])
            if sum(ensure_render_record(s, data_dir, config_root / TIMINGS_FILE)
                   for s in board.get("shots") or [] if s.get("locked")):
                store.save(entry["slug"], board)
        except Exception as exc:  # noqa: BLE001 - never block startup on this
            print(f"render record backfill skipped for {entry.get('slug')}: {exc}",
                  file=sys.stderr)
    orch = Orchestrator(backend=backend, store=store, workspace=workspace,
                        data_dir=data_dir,
                        timings=RenderTimings(config_root / TIMINGS_FILE,
                            namespace="ltx"))
    ctx = Context(UI_ROOT, workspace, store, backend, orch,
                  data_dir=data_dir, data_dir_source=data_dir_source,
                  vpipe_binary=args.vpipe.expanduser(), default_tts=args.tts,
                  default_llm=args.llm, llm_config_dir=args.llm_config_dir,
                  config_root=config_root)

    try:
        httpd = build_server(bind, args.port, ctx)
    except OSError as exc:
        print(f"error: cannot bind {bind}:{args.port} — {exc}", file=sys.stderr)
        print(f"       try: --port {args.port + 1}", file=sys.stderr)
        return 1

    ctx.desktop_owned = args.desktop
    lifetime = DesktopLifetime(ctx, httpd) if args.desktop else None
    if lifetime:
        lifetime.watch(sys.stdin.buffer)
        signal.signal(signal.SIGTERM, lambda *_: lifetime.request_stop())
        signal.signal(signal.SIGINT, lambda *_: lifetime.request_stop())

    shown = (
        f"http://{lan_ip()}:{args.port}/"
        if bind == "0.0.0.0"
        else f"http://localhost:{args.port}/"
    )
    healthy, message = backend.health()
    available = [c for c in backend.capabilities() if c.available]

    print(BANNER)
    print(f"  backend    {backend.id} — {backend.label}")
    print(f"  workspace  {workspace}   (local rendering workspace)")
    print(f"  projects   {data_dir}"
          + ("   (inside the workspace)" if data_dir == workspace else ""))
    print(f"  serving    {shown}")
    if bind == "0.0.0.0":
        print("             (all interfaces — reachable on your LAN)")
    else:
        print("             (this machine only — use --lan to expose it)")
    print("")
    if healthy:
        print(f"  models     {len(available)} available:")
        for c in backend.capabilities():
            mark = "ok  " if c.available else "--  "
            note = "" if c.available else f"  ({c.unavailable_reason})"
            print(f"             [{mark}] {c.id}{note}")
    else:
        print(f"  WARNING    backend not usable: {message}")
    print("")
    print(f"  speech     from {CONFIG_NAME}, default '{args.tts}'")
    for kind, eng in tts_engines.items():
        if kind == "none":
            continue
        tok, tmsg = eng.health()
        print(f"             [{'ok  ' if tok else '--  '}] {kind}"
              + ("" if tok else f"  ({tmsg.splitlines()[0][:86]})"))
    print("")
    print(f"  rewriting  from {LLM_CONFIG_NAME}, default '{args.llm}'")
    # Checked in parallel, and this fills the cache the first page load reads.
    for svc in llm_services_json(ctx):
        if svc["id"] == "none":
            continue
        lok, lmsg = svc["healthy"], svc["message"]
        print(f"             [{'ok  ' if lok else '--  '}] {svc['id']}"
              + (f"  ({svc['model']})" if lok else f"  ({lmsg.splitlines()[0][:86]})"))
    print("")
    if data_dir != workspace:
        old = workspace / "projects"
        if old.is_dir():
            left = [d.name for d in sorted(old.iterdir())
                    if (d / "storyboard.json").is_file()]
            if left:
                print(f"  NOTE       {len(left)} storyboard(s) are still in the old "
                      "location and will not be listed:")
                for name in left[:8]:
                    print(f"             {old / name}")
                if len(left) > 8:
                    print(f"             ... and {len(left) - 8} more")
                print(f"             Move them when ready:  mv {old}/* "
                      f"{data_dir}/")
                print("             (nothing is moved automatically — they are "
                      "your files)")
                print("")
    if stranded:
        print(f"  recovered   {len(stranded)} shot(s) left mid-run by a previous "
              "process, now marked interrupted:")
        for name in stranded[:5]:
            print(f"              {name}")
        print("")
    print("  Ctrl-C to stop.")
    print("")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopping...")

    # /api/server-settings/restart stops serve_forever() the same way Ctrl-C
    # does (from another thread, since shutdown() deadlocks called from the
    # loop's own thread) and sets this first, so it is the one thing that
    # tells the two apart once the loop has already exited either way.
    if ctx.restart_requested and not (lifetime and lifetime.stopping.is_set()):
        print("\n  restarting onto the updated projects folder...")
        httpd.server_close()
        os.execv(sys.executable, [sys.executable, "-u", "-m", "server"] + sys.argv[1:])
    httpd.server_close()
    stop_owned_work(ctx)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run under a process-lifetime code reader lock."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    bundle = os.environ.get("SPRINTWAKE_APP_BUNDLE") or str(UI_ROOT.parent)
    build = os.environ.get("SPRINTWAKE_APP_BUILD", "0")
    lifetime = AppLifetime(bundle=bundle, build=build)
    try:
        # ``--desktop`` means the server is owned by the native launcher; it
        # is not the native app singleton.  Studio.main owns that lock.
        lifetime.enter_engine()
        return _main(argv)
    finally:
        lifetime.close()


if __name__ == "__main__":
    raise SystemExit(main())

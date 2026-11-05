# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Owned desktop child process: no model discovery or inference at startup."""
import argparse
import os
from pathlib import Path
import sys
import threading

from .app import Handler, LocalHTTPServer, Service
from .app_lifetime import AppLifetime
from .store import read_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bind', default='127.0.0.1'); parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--desktop', action='store_true'); parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True); parser.add_argument('--config-dir', type=Path)
    parser.add_argument('--llm-config-dir', type=Path); parser.add_argument('--llm', default='none')
    parser.add_argument('--comfyui-url', default='http://127.0.0.1:8188'); parser.add_argument('--backend', default='sprintwake')
    parser.add_argument('--media-profiles', type=Path)
    args = parser.parse_args()
    if args.bind != '127.0.0.1': raise ValueError('The desktop service only binds numeric loopback')
    lifetime = AppLifetime().enter_engine()
    service = Service(args.data_dir, args.workspace, media_profiles=read_json(args.media_profiles) if args.media_profiles else None,
        config_dir=args.config_dir, llm_config_dir=args.llm_config_dir, llm=args.llm, comfy_url=args.comfyui_url,
        update_token=os.environ.get('SPRINTWAKE_UPDATE_TOKEN', ''))
    server = LocalHTTPServer((args.bind, args.port), Handler)
    server.service, server.desktop_owned = service, args.desktop
    def owner_exit():
        sys.stdin.buffer.read(1)
        service.close()
        server.shutdown()
    if args.desktop: threading.Thread(target=owner_exit, name='desktop-owner', daemon=True).start()
    try: server.serve_forever(poll_interval=.1)
    finally:
        service.close(); server.server_close(); lifetime.close()

if __name__ == '__main__': main()

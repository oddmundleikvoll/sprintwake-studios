#!/usr/bin/env python3
"""Install only the independently authored Sprintwake Comfy nodes.

SPDX-License-Identifier: MIT
Copyright (c) 2026 Oddmund Leikvoll
"""
from __future__ import annotations
import argparse
from pathlib import Path
import os
import shutil
import tempfile

FILES = ("__init__.py", "memory.py", "pending.py")

def _regular(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing symlink path: {path}")

def install(comfy_root: Path, *, dry_run: bool = False) -> list[Path]:
    root = comfy_root.expanduser().absolute()
    _regular(root)
    source = Path(__file__).resolve().parents[1] / "engine" / "comfy_nodes"
    destination = root / "custom_nodes" / "SprintwakeStudio"
    _regular(root / "custom_nodes") if (root / "custom_nodes").exists() else None
    if not source.is_dir():
        raise ValueError("replacement Comfy node source is missing")
    if destination.exists():
        _regular(destination)
        if not destination.is_dir():
            raise ValueError("Comfy node destination is not a directory")
    planned = []
    for name in FILES:
        src = source / name
        dst = destination / name
        if not src.is_file() or src.is_symlink():
            raise ValueError(f"Missing regular plugin source: {src}")
        if dst.exists() or dst.is_symlink():
            _regular(dst)
            if dst.read_bytes() != src.read_bytes():
                raise ValueError(f"Refusing to overwrite a different file: {dst}")
        planned.append(dst)
    if dry_run:
        return planned
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        src, dst = source / name, destination / name
        if dst.exists():
            continue
        fd, temporary = tempfile.mkstemp(prefix=".sprintwake-", dir=destination)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(src.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, dst)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return planned

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comfy_root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        for path in install(args.comfy_root, dry_run=args.dry_run):
            print(("would install " if args.dry_run else "installed ") + str(path))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

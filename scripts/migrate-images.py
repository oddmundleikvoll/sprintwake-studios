#!/usr/bin/env python3
"""Copy previously generated images into Raven's durable Library."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from library import MediaLibrary  # noqa: E402
from workspace import Workspace  # noqa: E402


def _source(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if not separator or not label.strip() or not path.strip():
        raise argparse.ArgumentTypeError("Kilde må ha format label=/sti")
    return label.strip(), Path(path).expanduser()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importer bilder til Raven Library")
    parser.add_argument("--root", default=str(Path.home() / "Movies" / "Raven Studio"), help="Raven produksjonsmappe")
    parser.add_argument("--source", action="append", required=True, type=_source, metavar="LABEL=PATH", help="Kilde; kan gjentas")
    args = parser.parse_args(argv)
    sources = dict(args.source)
    workspace = Workspace(Path(args.root).expanduser())
    result = MediaLibrary(workspace).import_images(sources)
    report_dir = workspace.root / "System" / "migrations"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = {"createdAt": datetime.now().isoformat(timespec="seconds"), "root": str(workspace.root), "sources": {label: str(path) for label, path in sources.items()}, "result": result}
    report_path = report_dir / f"images-{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Importerte {result['imported']} bilder ({result['deduplicated']} duplikater, {result['skipped']} hoppet over).")
    print(f"Rapport: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

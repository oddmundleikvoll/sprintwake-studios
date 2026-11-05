"""Stable, path-contained image anchor helpers."""
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
from pathlib import Path
import hashlib
from urllib.parse import urlsplit, unquote
def _safe(root,value):
    root=Path(root).absolute(); path=Path(value)
    if root.is_symlink(): return None
    if path.is_absolute():
        relative=None
        for base in (root,root.resolve()):
            try: relative=path.relative_to(base); break
            except ValueError: pass
        if relative is None: return None
    else: relative=path
    if '..' in relative.parts: return None
    current=root
    for part in relative.parts:
        current/=part
        if current.is_symlink(): return None
    try: current.resolve().relative_to(root.resolve())
    except ValueError: return None
    return current.resolve() if current.is_file() else None
def image_catalog(board, projects_dir=None):
    board=board if isinstance(board,dict) else {}; result={}
    def add(ref, prefix):
        if isinstance(ref,str): ref={"path":ref}
        if not isinstance(ref,dict): return
        path=ref.get("path") or ref.get("url") or ref.get("ref")
        if not isinstance(path,str): return
        if isinstance(path,str) and path.startswith('/media/'): path=unquote(urlsplit(path).path[7:])
        p=_safe(Path(projects_dir),path) if projects_dir else Path(path) if Path(path).is_file() else None
        if not p: return
        ident=str(ref.get("id") or hashlib.sha256(str(p).encode()).hexdigest()[:16])
        relative=p.relative_to(Path(projects_dir).resolve()).as_posix() if projects_dir else str(p)
        result[ident]={"path":relative,"url":"/media/"+relative,"ref":{"path":relative,"url":"/media/"+relative},"kind":"image"}
    for shot in board.get("shots",[]) or []:
        if isinstance(shot,dict):
            for key in ("startRef","endRef","thumb"): add(shot.get(key),"shot")
            for ref in shot.get("referenceImages",[]) or []: add(ref,"shot")
    for group in ("characters","locations","props"):
        items=list(board.get(group,[]) or [])
        bible=board.get('filmBible') if isinstance(board.get('filmBible'),dict) else {}
        items.extend(bible.get(group,[]) or [])
        for item in items:
            if isinstance(item,dict): add(item.get("image") or item.get("referenceImage"),group)
    return result
def stable_id(path, *, prefix="project"):
    return f"{prefix}:{hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:24]}"
def containment(root, path): return _safe(Path(root),path)

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Recoverable project moves, with the native Trash/<id>/Project format."""
from pathlib import Path
import json
import os
import re
import tempfile
import time
import uuid

def _slug(value):
    if not isinstance(value,str) or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}',value): raise ValueError('Invalid project ID')
    return value

def _checked(root,*parts):
    root=Path(root)
    if root.is_symlink(): raise ValueError('Unsafe project directory')
    current=root
    for part in parts:
        if not isinstance(part,str) or not part or part in {'.','..'} or '/' in part or '\\' in part: raise ValueError('Unsafe project path')
        current/=part
        if current.is_symlink(): raise ValueError('Unsafe project path')
    return current

def _sync(directory):
    fd=os.open(directory,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)

def trash_project(projects_dir,slug):
    projects=Path(projects_dir); slug=_slug(slug)
    project=_checked(projects,slug)
    if not projects.is_dir() or not project.is_dir(): raise FileNotFoundError('Project does not exist')
    if any(path.is_symlink() for path in project.rglob('*')): raise ValueError('Project contains a symlink')
    trash=_checked(projects.parent,'Trash'); trash.mkdir(exist_ok=True)
    ident=uuid.uuid4().hex; entry=_checked(trash,ident); entry.mkdir()
    source=_checked(project,'storyboard.json')
    if source.stat().st_size>32*1024*1024: raise ValueError('Oversized project document')
    board=json.loads(source.read_text())
    name=str(board.get('name') or slug)
    data={'id':ident,'slug':slug,'name':name,'deletedAt':time.time()}
    with (entry/'entry.json').open('x') as stream:
        json.dump(data,stream,ensure_ascii=False); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    _sync(entry)
    project.rename(entry/'Project'); _sync(projects); _sync(entry)
    return {'entry':entry,'id':ident,'slug':slug,'name':name}

def restore_project(projects_dir,entry_id):
    projects=Path(projects_dir)
    if not projects.is_dir() or projects.is_symlink(): raise ValueError('Unsafe projects directory')
    if not isinstance(entry_id,str) or not re.fullmatch('[0-9a-f]{32}',entry_id): raise ValueError('Invalid trash ID')
    trash=_checked(projects.parent,'Trash'); entry=_checked(trash,entry_id)
    metadata=_checked(entry,'entry.json'); source=_checked(entry,'Project')
    if not entry.is_dir() or not source.is_dir() or not metadata.is_file() or metadata.stat().st_size>64*1024: raise ValueError('Invalid trash entry')
    if {path.name for path in entry.iterdir()}!={'entry.json','Project'}: raise ValueError('Trash entry contains unexpected files; inspect it before restoring')
    meta=json.loads(metadata.read_text()); slug=_slug(meta.get('slug'))
    if meta.get('id',entry_id)!=entry_id: raise ValueError('Trash identity changed')
    target=_checked(projects,slug)
    if target.exists(): raise FileExistsError('A project already uses this ID')
    if any(path.is_symlink() for path in source.rglob('*')): raise ValueError('Trash project contains a symlink')
    source.rename(target); _sync(projects)
    metadata.unlink(); entry.rmdir(); _sync(trash)
    return slug

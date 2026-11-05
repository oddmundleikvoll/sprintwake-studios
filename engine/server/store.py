# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""JSON project repository with explicit transactions and opaque media references.

The schema is an interoperability format. This implementation owns persistence,
not rendering: failed generation never edits an existing selected take.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import uuid
from urllib.parse import unquote

MAX_DOCUMENT = 32 * 1024 * 1024
MEDIA_SUFFIXES = frozenset('.png .jpg .jpeg .webp .gif .bmp .tif .tiff .mp4 .mov .m4v .webm .mkv .wav .mp3 .m4a .flac .ogg .aac'.split())
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,79}\Z')
BOARD_INPUTS = 'name sceneDescription renderStyle soundscape soundtrackPrompt defaults filmBible characters styleRefs ravenSettings'.split()
SHOT_INPUTS = 'id title prompt start action end camera locationId stateBefore stateAfter motionIntent propIds assetStates soundNote characterIds dialogue dialogueSource dialogueStyle dialogueVoice speakerId dubMode referenceImages imageAnchors resolution frames steps seed startRef endRef directorScene ravenNotes'.split()


class Conflict(ValueError):
    """A conditional mutation observed a different saved revision."""


def authoring_digest(board):
    values = {name: board.get(name) for name in BOARD_INPUTS}
    values['shots'] = [{name: ((shot.get(name) or None) if name == 'ravenNotes' else shot.get(name))
                        for name in SHOT_INPUTS} for shot in board.get('shots', [])]
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def safe_path(root: Path, *parts, create_parent=False):
    """Reject lexical escape and symlinks, including broken path components."""
    root = Path(root).absolute()
    candidate = root.joinpath(*parts)
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError('Path escapes its repository') from error
    if any(part in {'.', '..'} or '\x00' in part or '\\' in part for part in relative.parts):
        raise ValueError('Unsafe repository path')
    current = root
    if current.is_symlink():
        raise ValueError('Repository root cannot be a symlink')
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Repository paths cannot contain symlinks')
    if create_parent:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def atomic_json(path, value):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Unsafe document path')
    raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    if len(raw) > MAX_DOCUMENT:
        raise ValueError('Document exceeds the supported size')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_DOCUMENT:
        raise ValueError('Unsafe or oversized document')
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON value')))


def default_shot(defaults=None):
    source = defaults or {}
    shot = dict(id='s' + uuid.uuid4().hex[:12], title='New shot', prompt='', start='', action='', end='', camera='',
                locationId='', stateBefore='', stateAfter='', motionIntent='action', propIds=[], assetStates={},
                promptVersions=[], soundNote='', characterIds=[], dialogue='', dialogueSource='recording',
                dialogueStyle='', dialogueVoice='', speakerId='', dialogueAudioUrl=None, dubUrl=None,
                dubMode='mix', startRef=None, endRef=None, referenceImages=[], imageAnchors=[],
                resolution='384x256', frames=121, steps=8, seed=0, status='draft', reason='', progress=0,
                runtimeSeconds=None, outputs=[], validation=None, thumb=None, logUrl=None, renderedAs=None,
                renderFingerprint=None, dialogueSpokenText='', dialogueSpokenStyle='', locked=False, directorScene=1)
    for key in ('resolution', 'frames', 'steps', 'seed', 'model', 'modelProvider', 'modelProfileDigest'):
        if key in source:
            shot[key] = copy.deepcopy(source[key])
    return shot


def default_board(name, defaults=None):
    return {'schema': 1, 'name': name, 'sceneDescription': '', 'renderStyle': '', 'soundscape': '',
            'soundtrackPrompt': '', 'characters': [], 'styleRefs': [], 'shots': [], 'filmBible': {},
            'defaults': dict(model='ltx-2.5', resolution='384x256', frames=121, steps=8, seed=0)
                        | copy.deepcopy(defaults or {})}


class Store:
    def __init__(self, data_dir, workspace=None, defaults=None):
        self.data_dir = Path(data_dir).absolute()
        self.workspace = Path(workspace or self.data_dir.parent / 'System/EngineWork').absolute()
        self.defaults = copy.deepcopy(defaults or {})
        self.lock = threading.RLock()
        self._depth = threading.local()
        safe_path(self.data_dir).mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._lock_path = safe_path(self.data_dir.parent, 'System', 'repository.lock', create_parent=True)

    @contextmanager
    def transaction(self):
        with self.lock:
            depth = getattr(self._depth, 'value', 0)
            self._depth.value = depth + 1
            stream = None
            try:
                if depth == 0:
                    fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                    stream = os.fdopen(fd, 'r+b')
                    fcntl.flock(stream, fcntl.LOCK_EX)
                yield
            finally:
                self._depth.value = depth
                if stream:
                    fcntl.flock(stream, fcntl.LOCK_UN)
                    stream.close()

    def project_dir(self, slug):
        if not isinstance(slug, str) or not SLUG.fullmatch(slug):
            raise ValueError('Invalid project ID')
        return safe_path(self.data_dir, slug)

    def get(self, slug):
        with self.transaction():
            path = safe_path(self.project_dir(slug), 'storyboard.json')
            if not path.is_file():
                raise FileNotFoundError(slug)
            board = read_json(path)
            self.validate(board)
            return board

    def validate(self, board):
        if not isinstance(board, dict) or board.get('schema', 1) != 1:
            raise ValueError('Expected a schema-v1 project')
        if not isinstance(board.get('name'), str) or not board['name'].strip() or len(board['name']) > 500:
            raise ValueError('Project name is required')
        shots = board.get('shots', [])
        if not isinstance(shots, list) or len(shots) > 20_000:
            raise ValueError('Invalid shot list')
        ids = set()
        for shot in shots:
            if not isinstance(shot, dict) or not isinstance(shot.get('id'), str) or not shot['id'] or shot['id'] in ids:
                raise ValueError('Shots need unique stable IDs')
            ids.add(shot['id'])
            if not isinstance(shot.get('outputs', []), list):
                raise ValueError('Shot outputs must be a list')
        if not isinstance(board.get('characters', []), list) or not isinstance(board.get('defaults', {}), dict):
            raise ValueError('Invalid project setup')
        json.dumps(board, allow_nan=False)

    def put(self, slug, board, expected_digest=None, expected_updated_at=None):
        with self.transaction():
            current = self.get(slug)
            if expected_digest is not None and authoring_digest(current) != expected_digest:
                raise Conflict('Project changed since the proposal was read')
            if expected_updated_at is not None and str(current.get('updatedAt')) != str(expected_updated_at):
                raise Conflict('Project changed in another request')
            self.validate(board)
            saved = copy.deepcopy(board)
            saved.setdefault('schema', 1)
            saved['updatedAt'] = time.time()
            for person in saved.get('characters',[]):
                if isinstance(person,dict):
                    for key,value in {'id':'c'+uuid.uuid4().hex[:12],'name':'Character','description':'','image':None,'voice':None,'voiceText':''}.items(): person.setdefault(key,value)
            saved.setdefault('createdAt', current.get('createdAt', saved['updatedAt']))
            project = self.project_dir(slug)
            if authoring_digest(current) != authoring_digest(saved):
                history = safe_path(project, 'System', 'EngineSnapshots', uuid.uuid4().hex + '.json', create_parent=True)
                atomic_json(history, current)
            atomic_json(safe_path(project, 'storyboard.json'), saved)
            return copy.deepcopy(saved)

    def create(self, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Project name is required')
        with self.transaction():
            normalized = name.translate(str.maketrans({'ø':'o', 'Ø':'O', 'æ':'ae', 'Æ':'AE'}))
            normalized = unicodedata.normalize('NFKD', normalized).encode('ascii', 'ignore').decode().lower()
            stem = re.sub(r'[^a-z0-9]+', '-', normalized).strip('-')[:60] or 'film'
            slug, number = stem, 1
            while self.project_dir(slug).exists():
                number += 1
                slug = f'{stem}-{number}'
            project = self.project_dir(slug)
            project.mkdir()
            for directory in ('refs', 'shots', 'Script', 'Input', 'Output/Images', 'Output/Video', 'Output/Audio', 'System'):
                safe_path(project, directory).mkdir(parents=True, exist_ok=True)
            board = default_board(name.strip(), self.defaults)
            stamp = time.time()
            board.update(createdAt=stamp, updatedAt=stamp)
            atomic_json(project / 'storyboard.json', board)
            return slug, board

    def list(self):
        with self.transaction():
            result = []
            for project in sorted(self.data_dir.iterdir()):
                if not SLUG.fullmatch(project.name) or project.is_symlink() or not project.is_dir():
                    continue
                try:
                    board = self.get(project.name)
                except (OSError, ValueError):
                    continue
                shots = board.get('shots', [])
                result.append({'slug': project.name, 'name': board['name'], 'shotCount': len(shots),
                               'renderedCount': sum(bool(s.get('outputs')) for s in shots),
                               'updatedAt': board.get('updatedAt'), 'frames': sum(s.get('frames', 121) for s in shots)})
            return result

    def media_path(self, reference):
        if isinstance(reference, dict):
            reference = reference.get('path') or reference.get('url') or reference.get('ref')
        if not isinstance(reference, str) or not reference or '\x00' in reference or '\\' in reference:
            raise ValueError('Invalid media reference')
        value = unquote(reference)
        if value.startswith('/media/'):
            value = value[len('/media/'):]
        elif value.startswith('/'):
            try:
                value = Path(value).relative_to(self.data_dir).as_posix()
            except ValueError as error:
                raise ValueError('Media lies outside productions') from error
        if any(part in {'', '.', '..'} for part in value.split('/')):
            raise ValueError('Unsafe media path')
        path = safe_path(self.data_dir, value)
        if path.suffix.lower() not in MEDIA_SUFFIXES:
            raise ValueError('This document is not public media')
        if not path.is_file():
            raise FileNotFoundError(value)
        return path

    def ref(self, path):
        relative = Path(path).relative_to(self.data_dir).as_posix()
        return {'path': relative, 'url': '/media/' + relative, 'label': Path(path).name}

    def import_media(self, slug, source, relative):
        source = Path(source)
        if source.is_symlink() or not source.is_file() or source.suffix.lower() not in MEDIA_SUFFIXES:
            raise ValueError('Invalid media source')
        target = safe_path(self.project_dir(slug), relative, create_parent=True)
        if target.suffix.lower() not in MEDIA_SUFFIXES:
            raise ValueError('Invalid media destination')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest():
                return self.ref(target)
            raise Conflict('Immutable media already exists')
        fd, temp = tempfile.mkstemp(prefix='.media-', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as out, source.open('rb') as inp:
                shutil.copyfileobj(inp, out, 1024 * 1024)
                out.flush(); os.fsync(out.fileno())
            try: os.link(temp,target)
            except FileExistsError: raise Conflict('Immutable media was created by another operation')
        finally:
            Path(temp).unlink(missing_ok=True)
        return self.ref(target)

    def upload(self, slug, filename, raw):
        from PIL import Image
        import io
        self.get(slug)
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_DOCUMENT:
            raise ValueError('Upload is empty or larger than 32 MB')
        suffix = Path(str(filename)).suffix.lower()
        if suffix not in MEDIA_SUFFIXES:
            raise ValueError('Unsupported media type')
        if suffix in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}:
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 80_000_000:
                    raise ValueError('Image is too large')
                image.load()
        stem = re.sub(r'[^A-Za-z0-9_-]', '-', Path(str(filename)).stem)[:60] or 'reference'
        target = safe_path(self.project_dir(slug), 'refs', stem + '-' + hashlib.sha256(raw).hexdigest()[:16] + suffix, create_parent=True)
        with self.transaction():
            if not target.exists():
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                with os.fdopen(fd, 'wb') as out:
                    out.write(raw); out.flush(); os.fsync(out.fileno())
        return self.ref(target)

    def rename(self, slug, name):
        with self.transaction():
            board = self.get(slug)
            # Display-name changes preserve the stable project ID and every media URL.
            board['name'] = str(name).strip()
            return slug, self.put(slug, board)

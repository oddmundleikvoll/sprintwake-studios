"""Image anchors shared by the editor, chat and isolated LTX scene worker.

Frames are zero based *published* clip frames. In an extension LTX adds its
25 carried frames internally; callers must never add that offset themselves.
"""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path, PurePosixPath

MAX_ANCHORS = 32  # Editor guard, not a limit advertised by the LTX model.
MAX_SCENE_ANCHORS = MAX_ANCHORS + 2  # Explicit list plus legacy Start/End.


def readable_anchors(shot):
    """Tolerate corrupt saved metadata for inspection, never for rendering."""
    value = shot.get('imageAnchors')
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def anchor_error(shot):
    try:
        clean_anchors(shot.get('imageAnchors', []), int(shot.get('frames') or 0))
        return ''
    except (ValueError, TypeError):
        return 'Ugyldige bildeankre — rett eller tøm ankerlisten før lagring/rendering'


def frame_index(value, frames):
    if value == 'last' or (type(value) is int and value == -1):
        return frames - 1
    if type(value) is not int or not 0 <= value < frames:
        raise ValueError(f'Ankerets bildeindeks må være 0–{frames - 1} eller last')
    return value


def clean_anchors(value, frames, *, image_key='ref'):
    """Validate the whole list; never silently discard an invalid anchor."""
    limit = MAX_ANCHORS if image_key == 'ref' else MAX_SCENE_ANCHORS
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f'Oppgi en liste med høyst {limit} bildeankre per klipp')
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or image_key not in item:
            raise ValueError('Hvert bildeanker må ha et bilde, en bildeindeks og en styrke')
        image = item[image_key]
        if image_key == 'ref':
            if (not isinstance(image, dict) or image.get('kind') == 'chain'
                    or not isinstance(image.get('path') or image.get('url'), str)):
                raise ValueError('Velg et lagret bilde for hvert anker')
        elif not isinstance(image, (str, Path)) or not str(image):
            raise ValueError('Ugyldig ankerbilde')
        frame = frame_index(item.get('frame'), frames)
        strength = item.get('strength')
        if (type(strength) not in (int, float) or not math.isfinite(strength)
                or not 0 < strength <= 1):
            raise ValueError('Ankerstyrke må være større enn 0 og høyst 1')
        if frame in seen:
            raise ValueError(f'Flere bildeankre peker på samme videoramme ({frame})')
        seen.add(frame)
        result.append({image_key: copy.deepcopy(item[image_key]),
                       'frame': 'last' if item['frame'] in ('last', -1) else frame,
                       'strength': float(strength)})
    return result


def image_path(ref, data_dir, *, verify=False):
    """Only local images under the projects tree; no URLs, chains or traversal."""
    if not isinstance(ref, dict) or ref.get('kind') == 'chain':
        raise ValueError('Velg et lagret bilde for ankeret; kjeding styres med Start-bildet')
    rel = ref.get('path') or ref.get('url') or ''
    if not isinstance(rel, str):
        raise ValueError('Ugyldig ankerbilde')
    if rel.startswith('/media/'):
        rel = rel[7:]
    parts = PurePosixPath(rel)
    if not rel or parts.is_absolute() or '..' in parts.parts or ':' in rel or '\\' in rel:
        raise ValueError('Ankerbildet må ligge i prosjektets mediebibliotek')
    root = Path(data_dir).resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Ankerbildet mangler eller ligger utenfor mediebiblioteket')
    if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
        raise ValueError('Velg et PNG-, JPEG-, WebP- eller BMP-bilde')
    if verify:
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
    return path


def shot_anchors(shot, frames, data_dir):
    return [{'path': str(image_path(a['ref'], data_dir, verify=True)),
             'frame': a['frame'], 'strength': a['strength']}
            for a in clean_anchors(shot.get('imageAnchors', []), frames)]


def image_catalog(board, data_dir):
    """Opaque choices for Qwen, derived only from media already on this board."""
    refs = [(r, 'library') for r in board.get('styleRefs') or []]
    refs += [(c.get('image'), c.get('name') or 'character') for c in board.get('characters') or []]
    for i, shot in enumerate(board.get('shots') or [], 1):
        refs += [(shot.get(k), f'clip {i} {k}') for k in ('startRef', 'endRef')]
        refs += [(r, f'clip {i} reference') for r in shot.get('referenceImages') or []]
        refs += [(a.get('ref'), f'clip {i} anchor') for a in readable_anchors(shot)]
    bible = board.get('filmBible') or {}
    for group in ('locations', 'props'):
        for entry in bible.get(group) or []:
            if isinstance(entry, dict):
                refs.append((entry.get('referenceImage'), f'bible {group[:-1]}'))
    choices = {}
    for ref, source in refs:
        try:
            path = image_path(ref, data_dir)
        except ValueError:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        key = hashlib.sha256(f'{path}:{stat.st_mtime_ns}:{stat.st_size}'.encode()).hexdigest()[:20]
        rel = path.relative_to(Path(data_dir).resolve()).as_posix()
        local_ref = {k: ref[k] for k in ('label', 'tag', 'role', 'contentHash') if isinstance(ref.get(k), str)}
        local_ref.update(path=rel, url='/media/' + rel)
        choices.setdefault('img_' + key, {'ref': local_ref,
                           'label': ref.get('tag') or ref.get('label') or path.name, 'source': source})
    return choices


def public_catalog(catalog):
    return [{'imageId': key, 'label': item['label'], 'source': item['source']}
            for key, item in catalog.items()]


def public_anchors(shot, catalog):
    result = []
    for anchor in readable_anchors(shot):
        ref = anchor.get('ref') if isinstance(anchor.get('ref'), dict) else {}
        value = ref.get('path') or ref.get('url') or ''
        rel = value.removeprefix('/media/') if isinstance(value, str) else ''
        image_id = next((key for key, item in catalog.items()
                         if item['ref'].get('path') == rel), None)
        result.append({'imageId': image_id, 'frame': anchor.get('frame'), 'strength': anchor.get('strength')})
    return result

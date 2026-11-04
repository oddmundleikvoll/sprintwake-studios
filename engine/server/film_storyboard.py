"""Compile saved shot cards into LTX scenes without language inference."""
from __future__ import annotations

import re

from .ltx_anchors import clean_anchors


def _text(value):
    text = value.strip() if isinstance(value, str) else ""
    if re.search(r'(?m)^\s*---\s*$', text):
        raise ValueError('Planen inneholder den reserverte klippskilleren ---')
    return text


def _location_setting(board, location_id):
    """Resolve only an explicit bible id; never guess from scene prose."""
    bible = board.get('filmBible') or {}
    for item in bible.get('locations') or []:
        if isinstance(item, dict) and item.get('id') == location_id:
            return _text(item.get('description') or item.get('title'))
    return ""


def storyboard_plan(board, *, strict=False):
    shots = board.get('shots') or []
    if not shots:
        raise ValueError('Storyboardet har ingen planlagte klipp')
    characters = [{'id': c['id'], 'name': c.get('name', ''), 'description': c.get('description', ''), 'key': f'cast_{i}',
                   'imagePrompt': f"Single full-body portrait. {c.get('name', '')}. {c.get('description', '')}."}
                  for i, c in enumerate(board.get('characters') or [])]
    cast = {c['id']: c['key'] for c in characters}
    scenes, adjustments, seed_adjustments, assignments, previous_marker = [], [], [], [], None
    warnings = []
    if not _text(board.get('sceneDescription')):
        warnings.append({'code': 'missing_global_setting', 'message': 'Filmen mangler en felles setting.'})
    if not _text(board.get('renderStyle')):
        warnings.append({'code': 'missing_render_style', 'message': 'Filmen mangler en visuell stil.'})
    for character in characters:
        if len(_text(character.get('description'))) < 20:
            warnings.append({'code': 'weak_character_description', 'characterId': character['id'],
                             'message': f"Karakteren {character.get('name') or character['id']} trenger en stabil identitets- og kostymebeskrivelse."})
    # Old authoring cards sometimes name cast in prose without linking IDs.
    # Only unique names can be linked deterministically; preview every addition.
    aliases = {}
    for c in characters:
        for name in {c['name'].strip(), c['name'].split(',')[0].strip()} - {''}:
            aliases.setdefault(name.casefold(), set()).add(c['id'])
    for index, shot in enumerate(shots):
        prompt = str(shot.get('prompt') or '').strip()
        if not prompt and all(_text(shot.get(key)) for key in ('start', 'action', 'end')):
            prompt = ' '.join(_text(shot[key]) for key in ('start', 'action', 'end'))
        if not prompt or re.search(r'(?m)^\s*---\s*$', prompt):
            raise ValueError(f"Klipp {index+1} trenger en prompt uten klippskilleren ---")
        if shot.get('locked'):
            raise ValueError(f"Klipp {index+1} er låst; lås det opp før filmjobben startes")
        original = shot.get('frames', 97)
        if type(original) is not int or not 25 <= original <= 481:
            raise ValueError(f"Klipp {index+1} må ha 25–481 bilder")
        requested_seed = shot.get('seed', 0)
        if type(requested_seed) is not int or not 0 <= requested_seed < 2**32:
            raise ValueError(f"Klipp {index+1} trenger en seed mellom 0 og 4294967295")
        match = re.match(r'^\s*scene\s*(\d+)\b', str(shot.get('title') or ''), re.I)
        marker = shot.get('directorScene')
        if marker is not None and (type(marker) is not int or marker < 1):
            raise ValueError(f"Klipp {index+1} trenger et positivt scenenummer")
        marker = marker or (int(match[1]) if match else previous_marker or 1)
        for key in ('start', 'action', 'end'):
            if not _text(shot.get(key)):
                warnings.append({'code': f'missing_{key}', 'shotId': shot.get('id'),
                                 'message': f"Klipp {index + 1} mangler eksplisitt {key}-beat."})
            if strict and not _text(shot.get(key)):
                raise ValueError(f"Klipp {index + 1} mangler eksplisitt {key}-beat")
        if strict and not _text(shot.get('camera')):
            raise ValueError(f"Klipp {index + 1} mangler eksplisitt kamera")
        if not _text(shot.get('locationId')):
            warnings.append({'code': 'missing_location', 'shotId': shot.get('id'),
                             'message': f"Klipp {index + 1} mangler eksplisitt locationId."})
        motion = shot.get('motionIntent', 'action')
        if motion not in ('action', 'hold'):
            raise ValueError(f"Klipp {index + 1} har ugyldig motionIntent")
        prop_ids = shot.get('propIds') or []
        if not isinstance(prop_ids, list) or any(not isinstance(value, str) for value in prop_ids):
            raise ValueError(f"Klipp {index + 1} har ugyldige propIds")
        asset_states = shot.get('assetStates') or {}
        if not isinstance(asset_states, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                                     for key, value in asset_states.items()):
            raise ValueError(f"Klipp {index + 1} har ugyldige assetStates")
        location_id = _text(shot.get('locationId'))
        known_locations = {item.get('id') for item in (board.get('filmBible') or {}).get('locations', [])
                          if isinstance(item, dict)}
        if location_id and known_locations and location_id not in known_locations:
            raise ValueError(f"Klipp {index + 1} viser til ukjent locationId")
        if strict and not location_id:
            raise ValueError(f"Klipp {index + 1} mangler eksplisitt locationId")
        location_description = _location_setting(board, location_id)
        if strict and not location_description:
            raise ValueError(f"Klipp {index + 1} viser til en location uten beskrivelse")
        known_props = {item.get('id') for item in (board.get('filmBible') or {}).get('props', [])
                       if isinstance(item, dict)}
        if (strict or known_props) and any(value not in known_props for value in prop_ids):
            raise ValueError(f"Klipp {index + 1} viser til ukjent propId")
        scene_context = (location_id, tuple(sorted(set(prop_ids))),
                         tuple(sorted(asset_states.items())), tuple(sorted(shot.get('characterIds') or [])),
                         _text(shot.get('camera')))
        start = shot.get('startRef') or {}
        chain = bool(index and start.get('kind') == 'chain' and start.get('from') == shots[index-1]['id'])
        continuing = bool(scenes and (marker == previous_marker or chain))
        if continuing and scenes[-1].get('_context') != scene_context:
            if chain:
                raise ValueError(f"Klipp {index + 1} bryter eksplisitt continuity-kjede med ny setting eller cast")
            continuing = False
        frames = max(8, min(480, round(original / 8) * 8)) if continuing else max(25, min(481, round((original-1) / 8)*8+1))
        if continuing and len(scenes[-1]['shots']) > 1 and frames != scenes[-1]['continuationFrames']:
            if chain:
                raise ValueError(f"Klipp {index+1} har en annen videreføringslengde. Bruk samme lengde i kjeden, eller velg nytt utsnitt med eget startbilde.")
            continuing = False
            frames = max(25, min(481, round((original-1) / 8)*8+1))
        if not continuing:
            location_setting = location_description
            setting_parts = [part for part in (board.get('sceneDescription') or '', location_setting) if _text(part)]
            scenes.append({'title': f"Scene {len(scenes)+1}", 'setting': ' — '.join(setting_parts),
                           'locationId': location_id, 'propIds': list(dict.fromkeys(prop_ids)),
                           'assetStates': dict(asset_states), 'stateBefore': _text(shot.get('stateBefore')),
                           'stateAfter': _text(shot.get('stateAfter')), 'characterKeys': [], 'shots': [],
                           'seed': requested_seed, 'firstFrames': frames, 'continuationFrames': 96,
                           '_context': scene_context})
        else:
            scenes[-1]['continuationFrames'] = frames
        anchors = clean_anchors(shot.get('imageAnchors') or [], original)
        for anchor in anchors:
            if anchor['frame'] == original-1 and frames != original:
                anchor['frame'] = 'last'
        anchors = clean_anchors(anchors, frames)
        if frames != original:
            adjustments.append({'shotId': shot['id'], 'title': shot.get('title'), 'before': original, 'after': frames})
        seed = scenes[-1]['seed'] + len(scenes[-1]['shots'])
        if seed >= 2**32:
            raise ValueError('Scenens seed er for høy for alle videreføringsklippene; velg en lavere seed')
        if seed != requested_seed:
            seed_adjustments.append({'shotId': shot['id'], 'title': shot.get('title'), 'before': requested_seed, 'after': seed})
        ids = list(shot.get('characterIds') or [])
        if not ids:
            ids = [c['id'] for c in characters if any(
                len(found) == 1 and c['id'] in found and re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)', prompt, re.I)
                for name, found in aliases.items())]
            if ids:
                assignments.append({'shotId': shot['id'], 'title': shot.get('title'),
                                    'characters': [c['name'] for c in characters if c['id'] in ids]})
        state_before = _text(shot.get('stateBefore')) or (_text(shot.get('start')) if strict else '')
        state_after = _text(shot.get('stateAfter')) or (_text(shot.get('end')) if strict else '')
        card = {'id': shot['id'], 'title': shot.get('title') or f'Klipp {index+1}',
                'start': _text(shot.get('start')) or prompt,
                'action': _text(shot.get('action')) or prompt,
                'end': _text(shot.get('end')) or prompt,
                'camera': _text(shot.get('camera')), 'locationId': location_id,
                'stateBefore': state_before, 'stateAfter': state_after,
                'motionIntent': motion, 'propIds': list(dict.fromkeys(prop_ids)),
                'assetStates': dict(asset_states), 'frames': frames, 'seed': seed,
                'imageAnchors': anchors, 'characterIds': ids}
        scenes[-1]['shots'].append(card)
        scenes[-1]['stateAfter'] = _text(shot.get('stateAfter'))
        for cid in ids:
            if cid not in cast:
                raise ValueError(f"Klipp {index+1} viser til en ukjent karakter")
            if cast[cid] not in scenes[-1]['characterKeys']:
                scenes[-1]['characterKeys'].append(cast[cid])
        previous_marker = marker
    for scene in scenes:
        if strict and len(scene['shots']) > 6:
            raise ValueError(f"{scene['title']} har mer enn seks klipp; lag et nytt kamerakutt")
        scene.pop('_context', None)
        scene['propDescriptions'] = {entry['id']: entry.get('description') or entry.get('title') or ''
                                     for entry in (board.get('filmBible') or {}).get('props', [])
                                     if entry.get('id') in scene['propIds']}
    return {'kind': 'storyboard', 'title': board.get('name') or 'Planlagt film',
            'style': board.get('renderStyle') or '', 'characters': characters, 'scenes': scenes,
            'frameAdjustments': adjustments, 'seedAdjustments': seed_adjustments, 'characterAssignments': assignments,
            'filmBible': board.get('filmBible') or {}, 'planWarnings': warnings,
            'frames': sum(s['frames'] for g in scenes for s in g['shots'])}

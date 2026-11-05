# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Translate saved shot direction into model prose without inventing observed facts."""
from __future__ import annotations

import copy
import json


DIRECTION_VERSION = 1


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _sentence(value):
    value = _text(value)
    return value + ('' if value.endswith(('.', '!', '?', '"', '”')) else '.') if value else ''


def _name(entity):
    return _text(entity.get('name')) or _text(entity.get('title')) or 'Unnamed subject'


def _describe(entity):
    detail = _text(entity.get('description'))
    return _name(entity) + (': ' + detail if detail else '')


def _dialogue_source(shot):
    source = shot.get('dialogueSource') or 'recording'
    if not isinstance(source, str) or source not in {'native', 'recording'}:
        raise ValueError('Choose native or recording as the dialogue source')
    return source


def directed_shot(board, shot, group=None):
    """Keep the full camera-group cast stable, including temporarily offscreen cast.

    These are authored intentions, never a claim that a video performed them.
    No board, source recording, media reference or model setting is modified.
    """
    source = _dialogue_source(shot)
    result = copy.deepcopy(shot)
    cast = {ident for item in (group or [shot]) for ident in item.get('characterIds', [])}
    people = [c for c in board.get('characters', []) if c.get('id') in cast]
    result['castDescription'] = '; '.join(_describe(c) for c in people)
    visible = set(shot.get('characterIds') or [])
    result['visibleCast'] = ', '.join(_name(c) for c in people if c.get('id') in visible)
    result['offscreenCast'] = ', '.join(_name(c) for c in people if c.get('id') not in visible)
    bible = board.get('filmBible') or {}
    location = next((p for p in bible.get('locations', []) if p.get('id') == shot.get('locationId')), {})
    result['locationDescription'] = '; '.join(filter(None, [
        _describe(location) if location else '', *[_text(location.get(k)) for k in ('timeOfDay', 'season', 'lighting')]]))
    states = shot.get('assetStates') or {}
    state_ids = set(states) if isinstance(states, dict) else set()
    prop_ids = set(shot.get('propIds') or []) | state_ids
    props = [p for p in bible.get('props', []) if p.get('id') in prop_ids]
    result['propDescription'] = '; '.join(_describe(p) for p in props)
    names = {p['id']: _name(p) for p in people + props if p.get('id')}
    result['assetStateDescription'] = '; '.join(
        names[key] + ': ' + _text(value) for key, value in states.items()
        if key in names and _text(value)) if isinstance(states, dict) else ''
    speaker_id = shot.get('speakerId')
    if not speaker_id and len(visible) == 1:
        speaker_id = next(iter(visible))
    speaker = next((c for c in board.get('characters', []) if c.get('id') == speaker_id), {})
    if _text(shot.get('dialogue')) and source == 'native' and not speaker:
        raise ValueError('Native dialogue needs a known speakerId or one visible character')
    result['speakerName'] = _name(speaker) if speaker else ''
    result['renderStyle'] = _text(board.get('renderStyle'))
    result['soundNote'] = ' '.join(filter(None, [_text(board.get('soundscape')), _text(shot.get('soundNote'))]))
    result['separateScore'] = bool(_text(board.get('soundtrackPrompt')))
    return result


def clip_prompt(shot):
    """Natural chronological prose for a single LTX shot, not a multi-shot script."""
    parts = [_text(shot.get('prompt')), _text(shot.get('renderStyle'))]
    if _text(shot.get('visibleCast')):
        parts.append('The visible characters in this shot are ' + shot['visibleCast'])
    if _text(shot.get('offscreenCast')):
        parts.append('The following continuity references remain offscreen in this shot: ' + shot['offscreenCast'])
    for key, prefix in (
        ('castDescription', 'Keep these character identities and costumes consistent, including while offscreen: '),
        ('locationDescription', 'The setting is '),
        ('propDescription', 'Keep these props consistent: '),
        ('assetStateDescription', 'The current physical details are '),
        ('stateBefore', 'At the beginning, '),
        ('start', ''), ('action', ''),
    ):
        value = _text(shot.get(key))
        if value:
            parts.append(prefix + value)
    line = _text(shot.get('dialogue'))
    source = _dialogue_source(shot)
    if line and source == 'native':
        speaker = _text(shot.get('speakerName'))
        if not speaker:
            raise ValueError('Native dialogue needs a known speaker')
        delivery = _text(shot.get('dialogueStyle'))
        parts.append(speaker + ' says' + (' ' + delivery if delivery else '') + ': ' + json.dumps(line, ensure_ascii=False))
    elif line:
        parts.append('Spoken dialogue is added separately; capture the visible performance without generating speech')
    for key, prefix in (('end', 'The shot ends with '), ('stateAfter', 'By the end, '), ('camera', '')):
        if _text(shot.get(key)):
            parts.append(prefix + _text(shot[key]))
    if _text(shot.get('soundNote')):
        parts.append('The scene audio is ' + _text(shot['soundNote']))
    if shot.get('separateScore'):
        parts.append('Generate the scene sounds only; the musical score is added separately')
    if not any(_text(shot.get(k)) for k in ('prompt', 'start', 'action')):
        raise ValueError('The clip needs a production prompt')
    return ' '.join(_sentence(part) for part in parts if _text(part))


def image_prompt(board, shot, roles=(), *, closing=False):
    """Describe one complete scene frame; references supply identity, not staging."""
    directed = directed_shot(board, shot)
    pose = _text(shot.get('end' if closing else 'start'))
    pose = pose or _text(shot.get('stateAfter' if closing else 'stateBefore')) or _text(shot.get('prompt'))
    if not pose:
        raise ValueError('The scene needs an opening or ending description')
    parts = ['Create one complete scene frame', directed['renderStyle']]
    parts += ['Reference image ' + str(index) + ' supplies ' + role for index, role in enumerate(roles, 1)]
    if roles:
        parts.append('Use the references for their stated roles; compose one scene with the pose, expression and framing described here')
    for key, prefix in (('castDescription', 'The visible cast is '), ('locationDescription', 'The setting is '),
                        ('propDescription', 'The scene props are '), ('assetStateDescription', 'Current physical details: ')):
        if closing and key == 'assetStateDescription' and _text(shot.get('stateAfter')):
            continue  # Ending state supersedes initial prop ownership/condition.
        if directed[key]:
            parts.append(prefix + directed[key])
    parts += [pose, _text(shot.get('stateAfter' if closing else 'stateBefore')), _text(shot.get('camera'))]
    return ' '.join(_sentence(part) for part in parts if _text(part))

"""Pure scene navigation for canonical and editorial views, keyed by shot ID."""
from __future__ import annotations

EDITOR_MODES = frozenset({'film', 'scenes', 'structure', 'cut', 'sound'})
CUT_MODES = frozenset({'structure', 'cut', 'sound'})


def outline(project, board, state=None):
    shots = {shot.id: shot for shot in project.shots}
    order = {shot.id: i for i, shot in enumerate(project.shots)}
    def scene(ident, title, ids, narrative):
        ids = sorted((sid for sid in ids if sid in shots), key=order.__getitem__)
        return {'id': ident, 'title': title, 'shotIds': ids, 'narrative': narrative,
                'frames': sum(round(shots[sid].duration * 24) for sid in ids)}
    sequences, assigned = [], set()
    for seq in (state or {}).get('sequences', []):
        scenes = []
        for item in seq['scenes']:
            scenes.append(scene(item['id'], item['title'], item['shotIds'], True))
            assigned.update(scenes[-1]['shotIds'])
        sequences.append({'id': seq['id'], 'title': seq['title'], 'scenes': scenes, 'narrative': True})
    remaining = [shot for shot in project.shots if shot.id not in assigned]
    if sequences:
        if remaining:
            sequences.append({'id': 'unassigned', 'title': 'Unassigned shots', 'narrative': False,
                              'scenes': [scene('unassigned', 'No scene', [s.id for s in remaining], False)]})
    else:
        raw = {s['id']: s for s in board.get('shots', [])}
        groups, last = [], None
        for shot in remaining:
            row = raw.get(shot.id, {})
            key = (row.get('directorScene', 1), row.get('locationId', ''))
            if key != last:
                groups.append(scene('camera-' + str(len(groups)), 'Camera setup ' + str(key[0]), [], False))
                last = key
            groups[-1]['shotIds'].append(shot.id)
            groups[-1]['frames'] += round(shot.duration * 24)
        sequences = [{'id': 'camera-groups', 'title': 'Camera setups', 'scenes': groups, 'narrative': False}]
    return sequences


def panel_sizes(width, height, sound=False):
    """Reserve useful player space at both supported window sizes."""
    compact = width < 1300 or height < 840
    return (224 if compact else 260, 276 if compact else 300,
            280 if sound else 158 if compact else 180)

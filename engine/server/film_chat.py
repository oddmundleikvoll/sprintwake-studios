"""Film controls and compact status shared by Qwen proposals and local chat commands."""
from __future__ import annotations

import json
from pathlib import Path
import re
import uuid

from .film import clip_count, _text
from .film_comfy import IMAGE_MODEL
from .film_quality import clean_policy
from .ltx_media import digest
from .ltx_anchors import clean_anchors, image_catalog, public_catalog, public_anchors, image_path, anchor_error
from .film_storyboard import storyboard_plan

FILM_TOOLS = {'start_film', 'render_storyboard', 'resume_film', 'stop_film', 'set_film_anchor', 'set_shot_anchors'}


def _read_json(path):
    try:
        if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def context(controller, slug):
    """Recorded progress only: reuse validation remains the scene worker's job."""
    store = controller.ctx.store
    board = store.load(slug)
    root = store.project_dir(slug)
    settings = board.get('filmAutomation') or {}
    settings = settings if isinstance(settings, dict) else {}
    live = controller.status()
    job = live if live.get('slug') == slug else _read_json(root / 'film-job.json')
    job = dict(job)
    # A disk-only busy marker belongs to an interrupted server, not a live worker.
    if job.get('busy') and live.get('slug') != slug:
        job.update(busy=False, phase='interrupted')
    is_film = settings.get('engine') == 'ltx-2.5'
    shots = board.get('shots') or []
    catalog = image_catalog(board, store.data_dir)
    checkpoints = {}
    scene_numbers = sorted({s.get('directorScene') for s in shots if type(s.get('directorScene')) is int and s['directorScene'] > 0})
    for number in scene_numbers:
        state_path = root / 'director' / f'scene-{number:03d}' / 'scene-state.json'
        if not state_path.resolve().is_relative_to(root.resolve()):
            continue
        state = _read_json(state_path)
        run_id = state.get('run_id')
        if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
            continue
        runs = Path(getattr(controller.comfy, 'output_dir', Path.home() / 'ComfyUI-Shared/output')) / 'ltx25_mlx/scene_runs'
        run = runs / run_id
        if run.is_symlink() or run.resolve().parent != runs.resolve():
            continue
        recipe = _read_json(run / 'recipe.json')
        if recipe.get('request_fingerprint') != state.get('signature'):
            continue
        manifest = _read_json(run / 'manifest.json')
        entries = manifest.get('segments')
        records = entries if isinstance(entries, list) else []
        count = 0
        for record in records:
            if not isinstance(record, dict) or type(record.get('frames')) is not int or record['frames'] <= 0:
                break
            count += 1
        checkpoints[number] = {'recordedClips': count, 'state': manifest.get('state'),
                               'activeClip': manifest.get('active_clip'), 'attempt': manifest.get('attempt'),
                               'error': str(manifest.get('error') or '')[:1200]}
    clips, positions = [], {}
    for index, shot in enumerate(shots, 1):
        scene = shot.get('directorScene')
        scene = scene if type(scene) is int and scene > 0 else None
        positions[scene] = positions.get(scene, 0) + 1
        position = positions[scene]
        checkpoint = checkpoints.get(scene, {})
        status = ('published' if shot.get('status') == 'done' and shot.get('outputs') else
                  'checkpoint' if position <= checkpoint.get('recordedClips', 0) else
                  'running' if job.get('busy') and position == checkpoint.get('activeClip') else
                  shot.get('status', 'pending'))
        clips.append({'number': index, 'shotId': shot.get('id'), 'scene': scene,
                      'clipInScene': position, 'title': str(shot.get('title') or '')[:160], 'status': status,
                      'frames': shot.get('frames'), 'locked': bool(shot.get('locked')),
                      'anchorError': anchor_error(shot),
                      'imageAnchors': public_anchors(shot, catalog)})
    scenes = []
    for number in scene_numbers:
        group = [clip for clip in clips if clip['scene'] == number]
        published = sum(clip['status'] == 'published' for clip in group)
        scenes.append({'number': number, **checkpoints.get(number, {}), 'totalClips': len(group),
                       'publishedClips': published, 'recordedClips': max(published, checkpoints.get(number, {}).get('recordedClips', 0))})
    recorded = sum(clip['status'] in ('published', 'checkpoint') for clip in clips)
    busy = bool(live.get('busy'))
    gpu_busy = bool(controller.ctx.orch.busy or controller.ctx.orch.stills_busy)
    saved_planning = any((root / name).is_file() for name in ('film-plan.json', 'film-planning.json'))
    retry_planning = bool(is_film and not saved_planning and not shots and settings.get('description'))
    project = {'slug': slug, 'name': board.get('name'), 'isFilm': is_film,
               'revision': settings.get('revision'), 'jobId': job.get('jobId'),
               'busy': bool(job.get('busy')), 'phase': job.get('phase', 'unplanned'),
               'detail': str(job.get('detail') or '')[:1600], 'error': str(job.get('error') or '')[:1600],
               'completed': max(job.get('completed', 0), recorded), 'total': settings.get('plannedClips', len(shots)),
               'checkpointScene': job.get('checkpointScene'), 'checkpointClips': job.get('checkpointClips', 0),
               'continuationAnchor': bool(settings.get('continuationAnchor', False)),
               'characterReferences': bool(settings.get('characterReferences', False)),
               'resumeImageEngine': IMAGE_MODEL, 'resumeCharacterReferences': True,
               'imageEngineMigration': bool(is_film and settings.get('imageEngine') != IMAGE_MODEL),
               'canResume': bool(is_film and (saved_planning or retry_planning) and not busy and not gpu_busy),
               'canRetryPlanning': bool(retry_planning and not busy and not gpu_busy),
               'productionPolicy': settings.get('productionPolicy', {}), 'animaticUrl': settings.get('animaticUrl'),
               'imagePlan': settings.get('imagePlan'), 'imagePlanStale': bool(settings.get('imagePlanStale')),
               'imageApprovalRequired': bool(settings.get('imagePlan') and not settings.get('imageApproval')),
               'reviewRequired': bool(settings.get('reviewRequired')), 'reviewPreview': settings.get('reviewPreview'),
               'quality': settings.get('qualitySummary'),
               'scenes': scenes, 'clips': clips}
    active = ({k: live.get(k) for k in ('slug', 'jobId', 'phase', 'detail', 'completed', 'total')}
              if busy else None)
    project['canProduce'] = False
    if not is_film:
        try:
            plan = storyboard_plan(board)
            project['plannedFilm'] = {'plannedClips': len(shots), 'plannedScenes': len(plan['scenes']),
                                      'duration': plan['frames']/24, 'frameAdjustments': plan['frameAdjustments'],
                                      'seedAdjustments': plan['seedAdjustments'],
                                      'characterAssignments': plan['characterAssignments']}
            project['plannedFilm']['planWarnings'] = plan.get('planWarnings', [])
            try:
                storyboard_plan(board, strict=True)
                project['canProduce'] = not busy and not gpu_busy
            except (ValueError, TypeError, KeyError) as error:
                project['productionError'] = str(error)
        except (ValueError, TypeError, KeyError) as error:
            project['productionError'] = str(error)
    return {'project': project, 'active': active, 'gpuBusy': gpu_busy,
            'anchorRevision': digest(board), 'imageCatalog': public_catalog(catalog),
            'anchorsAvailable': not ((board.get('defaults') or {}).get('draft') and (board.get('defaults') or {}).get('sketch')),
            'canStart': not busy and not gpu_busy,
            'checkpointMeaning': 'Recorded clips; files and inputs are revalidated on resume, not by this status read.',
            'limits': {'clipTimeoutSeconds': 900, 'attempts': 2, 'sceneTimeoutSeconds': 7200,
                       'anchorNewFrame': 0, 'anchorStrength': .35, 'longFilmVerified': False, 'maxRequestedSeconds': 7200}}


def _policy_fields(raw):
    names = ("reviewMode", "automaticEndAnchors", "endAnchorStrength", "speechMode", "prepareOnly")
    if not any(k in raw for k in names):
        return {}
    cleaned = clean_policy(raw)
    return {k: cleaned[k] for k in names if k in raw}


def clean_action(raw, film_state):
    if (not isinstance(raw, dict) or not isinstance(raw.get('tool'), str)
            or raw['tool'] not in FILM_TOOLS or not film_state):
        raise ValueError('Ukjent filmhandling eller manglende filmstatus')
    tool = raw['tool']
    project = film_state['project']
    if tool == 'render_storyboard':
        if not project.get('canProduce'):
            raise ValueError(project.get('productionError') or 'Bruk Fortsett film eller vent til aktivt arbeid er avsluttet')
        if raw.get('slug') != project['slug'] or raw.get('revision') != film_state.get('anchorRevision'):
            raise ValueError('Storyboardet er endret; be om et nytt startkort')
        engine, anchor = raw.get('imageEngine', IMAGE_MODEL), raw.get('continuationAnchor', False)
        if engine != IMAGE_MODEL or type(anchor) is not bool:
            raise ValueError('Ugyldig bildemodell eller videreføringsanker')
        return {'tool': tool, 'slug': project['slug'], 'revision': raw['revision'],
                'imageEngine': engine, 'continuationAnchor': anchor, **project['plannedFilm'], **_policy_fields(raw)}
    if tool == 'set_shot_anchors':
        if raw.get('anchors') and not film_state.get('anchorsAvailable', True):
            raise ValueError('Slå av Sketch-utkast før bildeankre redigeres; ankrene brukes i normal rendering')
        if (raw.get('slug') != project['slug'] or not raw.get('revision')
                or raw['revision'] != film_state.get('anchorRevision')):
            raise ValueError('Prosjektet er endret; les prosjektet og bildeankrene igjen')
        if project.get('busy') or not film_state.get('canStart'):
            raise ValueError('Vent til aktivt arbeid er avsluttet før bildeankrene endres')
        shot = next((s for s in project['clips'] if s['shotId'] == raw.get('shotId')), None)
        if not shot or shot.get('locked'):
            raise ValueError('Klippet mangler eller er låst')
        anchors = clean_anchors(raw.get('anchors'), shot['frames'], image_key='imageId')
        choices = {item['imageId'] for item in film_state.get('imageCatalog', [])}
        if any(not isinstance(a['imageId'], str) or a['imageId'] not in choices for a in anchors):
            raise ValueError('Velg bilde-ID fra prosjektets imageCatalog; last opp manglende bilder i editoren')
        labels = {item['imageId']: item['label'] for item in film_state.get('imageCatalog', [])}
        for anchor in anchors:
            anchor['imageLabel'] = labels[anchor['imageId']]
        return {'tool': tool, 'slug': project['slug'], 'revision': raw['revision'],
                'shotId': shot['shotId'], 'anchors': anchors}
    if tool == 'start_film':
        if project.get('clips') and raw.get('newProject') is not True:
            raise ValueError('Dette er et gammelt kort for en ny film. Bruk «Start planlagt film» for klippene i dette storyboardet.')
        if not film_state.get('canStart'):
            raise ValueError('Storyboard arbeider allerede; vent før en ny film startes')
        seconds = raw.get('duration')
        try:
            count = clip_count(seconds)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('Varighet må være et tall mellom 6 og 7200 sekunder') from None
        engine = raw.get('imageEngine', IMAGE_MODEL)
        anchor = raw.get('continuationAnchor', False)
        if engine != IMAGE_MODEL or type(anchor) is not bool:
            raise ValueError('Ugyldig bildemodell eller videreføringsanker')
        return {'tool': tool, 'newProject': bool(raw.get('newProject')), 'description': _text(raw, 'description', 12000),
                'title': _text(raw, 'title', 160, True), 'duration': float(seconds),
                'imageEngine': engine, 'continuationAnchor': anchor, 'plannedClips': count, **_policy_fields(raw)}
    if tool == 'stop_film':
        active = film_state.get('active') or {}
        if not active.get('jobId') or raw.get('slug') != active.get('slug') or raw.get('jobId') != active['jobId']:
            raise ValueError('Filmjobben er ikke lenger aktiv eller er byttet; les filmstatus igjen')
        return {'tool': tool, 'slug': active['slug'], 'jobId': active['jobId']}
    if (not project.get('isFilm') or raw.get('slug') != project['slug']
            or not project.get('revision') or raw.get('revision') != project['revision']):
        raise ValueError('Filmprosjektet er endret; last inn prosjektet og les filmstatus igjen')
    if project.get('busy'):
        raise ValueError('Stopp filmjobben og vent til den er avsluttet før innstillingene endres')
    action = {'tool': tool, 'slug': project['slug'], 'revision': project['revision']}
    if tool == 'resume_film':
        action.update(_policy_fields(raw))
        if raw.get('acceptReview') is True:
            action['acceptReview'] = True
        if 'acceptImagePlan' in raw:
            manifest = project.get('imagePlan') or {}
            if (project.get('imagePlanStale') or not isinstance(raw['acceptImagePlan'], str)
                    or raw['acceptImagePlan'] != manifest.get('digest')):
                raise ValueError('Bildeplanen er endret; lag og gjennomgå en ny forhåndsvisning')
            action['acceptImagePlan'] = raw['acceptImagePlan']
        if not project.get('canResume'):
            raise ValueError('Filmen har ingen lagret plan, eller Storyboard arbeider fortsatt')
        if 'continuationAnchor' in raw:
            if type(raw['continuationAnchor']) is not bool:
                raise ValueError('Videreføringsanker må være på eller av')
            action['continuationAnchor'] = raw['continuationAnchor']
    else:
        if type(raw.get('enabled')) is not bool:
            raise ValueError('Videreføringsanker må være på eller av')
        action['enabled'] = raw['enabled']
    return action


def describe(film_state):
    project = film_state['project']
    if not project['isFilm']:
        active = film_state.get('active')
        return ('Dette prosjektet er ikke en LTX-filmjobb.' +
                (f" Aktiv film: {active['slug']} — {active['phase']}." if active else ''))
    lines = [f"{project['name']}: {project['phase']} · {project['completed']}/{project['total']} klipp lagret."]
    if project['detail']:
        lines.append(project['detail'])
    if project['error']:
        lines.append('Feil: ' + project['error'])
    if project.get('canRetryPlanning'):
        lines.append('Planleggingen ble ikke fullført. Fortsett film prøver planleggingen igjen i dette prosjektet fra den lagrede ideen.')
    lines.append('Videreføringsanker: ' + ('på' if project['continuationAnchor'] else 'av') + ' (nytt bilde 0, styrke 0,35).')
    lines.extend(f"Klipp {s['number']} (scene {s['scene']}): {s['status']} — {s['title']}"
                 + (f" · {len(s.get('imageAnchors', []))} bildeankre" if s.get('imageAnchors') else '')
                 for s in project['clips'])
    lines.append('Checkpoints er registrert arbeid; Fortsett kontrollerer medier og innganger før gjenbruk.')
    return '\n'.join(lines)


def local_reply(message, film_state):
    """No inference, including while LTX owns memory. Never execute a proposal here."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError('message is required')
    text = message.strip().casefold().rstrip('?.!')
    text = re.sub(r'^(?:kan du|vennligst|please|could you)\s+', '', text)
    command = None
    if re.fullmatch(r'(?:/film\s+status|filmstatus|vis filmstatus|klippstatus|vis klippstatus|(?:hva er )?status på filmen|hvor langt har filmen kommet|hvilke klipp er ferdige)', text):
        command = 'status'
    elif re.fullmatch(r'(?:/film\s+stop|stopp(?:e)? filmen|stopp(?:e)? filmjobben|avbryt filmjobben|stop the film)', text):
        command = 'stop'
    elif re.fullmatch(r'(?:/film\s+resume|fortsett filmen|fortsett filmjobben|gjenoppta filmen|gjenoppta filmjobben|resume the film)', text):
        command = 'resume'
    elif re.fullmatch(r'(?:/film\s+produce|start filmen|lag filmen|start planlagt film|start den planlagte filmen)', text):
        command = 'resume' if film_state['project'].get('isFilm') else 'produce'
    else:
        match = re.fullmatch(r'/film\s+anchor\s+(on|off|på|av)', text)
        if match:
            command = 'anchor'
        else:
            match = re.fullmatch(r'slå (på|av) videreføringsankeret', text)
            if match:
                command = 'anchor'
    if command is None and not (film_state.get('active') or film_state.get('gpuBusy')):
        return None
    actions = []
    reply = describe(film_state)
    project = film_state['project']
    raw = None
    if command == 'produce':
        raw = {'tool': 'render_storyboard', 'slug': project['slug'], 'revision': film_state.get('anchorRevision')}
        reply = 'Bruker klippene, rekkefølgen, karakterene og bildeankrene i dette storyboardet. Manglende åpningsbilder lages før video. Ingen ny manusplanlegging.'
    elif command == 'stop':
        active = film_state.get('active') or {}
        raw = {'tool': 'stop_film', 'slug': active.get('slug'), 'jobId': active.get('jobId')}
    elif command in ('resume', 'anchor'):
        raw = {'tool': 'resume_film' if command == 'resume' else 'set_film_anchor',
               'slug': project['slug'], 'revision': project.get('revision')}
        if command == 'resume' and project.get('reviewRequired'):
            raw['acceptReview'] = True
            reply += '\nFortsett godkjenner den viste kvalitetsrapporten; tidligere opptak beholdes.'
        if command == 'anchor':
            raw['enabled'] = match[1] in ('on', 'på')
    if raw:
        try:
            actions = [clean_action(raw, film_state)]
            reply += '\nHandlingen er foreslått under. Den utføres først når du bruker handlingsknappen.'
        except (ValueError, TypeError) as error:
            reply += '\n' + str(error)
    elif command is None:
        reply += '\nQwen venter mens bilde/video kjører. Du kan skrive «filmstatus», «stopp filmen» eller «fortsett filmen» her uten å laste modellen.'
    return {'message': reply, 'actions': actions, 'service': 'Storyboard filmkontroll',
            'model': None, 'localControl': True, 'filmState': film_state}


def execute(controller, slug, raw):
    action = clean_action(raw, context(controller, slug))
    tool = action.pop('tool')
    if tool == 'render_storyboard':
        return {'operation': 'planned', **controller.start({'storyboardSlug': action['slug'],
            'storyboardRevision': action['revision'], 'imageEngine': action['imageEngine'],
            'continuationAnchor': action['continuationAnchor'], **_policy_fields(action)})}
    if tool == 'set_shot_anchors':
        # Same admission lock as render/start. Recheck live board, image choices
        # and ownership after acquiring it; a proposal cannot race a new job.
        with controller.ctx.orch._start_lock:
            action = clean_action(raw, context(controller, slug))
            store = controller.ctx.store
            board = store.load(slug)
            catalog = image_catalog(board, store.data_dir)
            shot = next(s for s in board['shots'] if s['id'] == action['shotId'])
            anchors = []
            for item in action['anchors']:
                ref = catalog[item['imageId']]['ref']
                image_path(ref, store.data_dir, verify=True)
                anchors.append({'ref': ref, 'frame': item['frame'], 'strength': item['strength']})
            shot['imageAnchors'] = anchors
            board['anchorsRevision'] = uuid.uuid4().hex
            if board.get('filmAutomation'):
                board['filmAutomation']['revision'] = uuid.uuid4().hex
            store.save(slug, board)
        return {'operation': 'shot_anchors', 'filmState': context(controller, slug)}
    if tool == 'start_film':
        action.pop('plannedClips')
        return {'operation': 'start', **controller.start(action)}
    if tool == 'resume_film':
        # Chat proposals navigate to the review panel; they cannot acknowledge
        # footage or images. Only the dedicated revision-bound review UI does.
        project = context(controller, slug)['project']
        if project.get('reviewRequired') or action.get('acceptReview') or action.get('acceptImagePlan'):
            raise ValueError('Åpne Filmproduksjon, se bildene/videoen og bekreft gjennomgangen der før filmen fortsetter')
        payload = {'resumeSlug': action['slug'], 'expectedRevision': action['revision']}
        if 'continuationAnchor' in action:
            payload['continuationAnchor'] = action['continuationAnchor']
        payload.update(_policy_fields(action))
        return {'operation': 'resume', **controller.start(payload)}
    if tool == 'stop_film':
        status = controller.stop(slug=action['slug'], job_id=action['jobId'])
        return {'operation': 'stop', 'status': status}
    controller.set_anchor(action['slug'], action['enabled'], action['revision'])
    return {'operation': 'anchor', 'filmState': context(controller, slug)}

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Declarative, independently authored allow-list for director proposals.

These public tool names and field shapes are the desktop interoperability API.
They grant no filesystem, approval or arbitrary HTTP capability.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import re

CATALOG_VERSION = 'raven-director-1'
PANEL_NAMES = frozenset('home projects overview brief bible images film scenes coverage cut structure sound production library director'.split())
BOARD_FIELDS = frozenset('name sceneDescription renderStyle soundscape soundtrackPrompt'.split())
BIBLE_FIELDS = frozenset('theme storyArc acts locations props'.split())
SHOT_FIELDS = frozenset('title prompt start action end camera locationId stateBefore stateAfter motionIntent propIds assetStates soundNote characterIds dialogue dialogueSource dialogueStyle dialogueVoice speakerId dubMode resolution frames steps seed directorScene'.split())
READ_ONLY_TOOLS = frozenset('open_project list_library list_seed_takes review_production review_clip reveal status open_panel inspect_cut inspect_coverage inspect_structure inspect_sound'.split())
# tool: (classification, project required, required fields, optional fields)
_SPEC = {
 'create_project': ('project',False,'name','brief style newProject'),
 'open_project': ('read',False,'slug',''),
 'rename_project': ('project',True,'name',''),
 'archive_project': ('project',True,'',''),
 'edit_project': ('authoring',True,'fields',''),
 'edit_bible': ('authoring',True,'fields',''),
 'add_character': ('authoring',True,'name','description imageId voiceId'),
 'update_character': ('authoring',True,'characterId','name description imageId voiceId'),
 'add_shot': ('authoring',True,'',' '.join(SHOT_FIELDS)),
 'edit_shot': ('authoring',True,'shotId',' '.join(SHOT_FIELDS)),
 'write_manuscript': ('operation',True,'brief','count'),
 'list_library': ('read',False,'','query'),
 'adopt_library_image': ('resource',True,'imageId',''),
 'publish_image': ('resource',True,'imageId',''),
 'generate_image': ('generation',True,'prompt','referenceIds aspect seed'),
 'stop_image': ('operation',True,'jobId',''),
 'assign_image': ('authoring',True,'imageId target','entityId shotId'),
 'list_seed_takes': ('read',True,'shotId',''),
 'adopt_seed_take': ('operation',True,'shotId seed',''),
 'render_seed_takes': ('generation',True,'shotId','count'),
 'refine_shot': ('generation',True,'shotId requirement',''),
 'prepare_dialogue': ('operation',True,'',''),
 'dub_dialogue': ('generation',True,'shotId',''),
 'generate_soundtrack': ('generation',True,'','prompt'),
 'prepare_film': ('operation',True,'revision',''),
 'resume_film': ('operation',True,'revision',''),
 'stop_film': ('operation',True,'jobId',''),
 'set_film_anchor': ('operation',True,'revision enabled',''),
 'set_shot_anchors': ('authoring',True,'shotId anchorRevision anchors',''),
 'assemble': ('operation',True,'',''),
 'export_project': ('operation',True,'',''),
 'review_clip': ('read',True,'shotId',''),
 'rewrite_text': ('authoring',True,'scope text','shotId'),
 'reveal': ('read',False,'','target'),
 'open_panel': ('read',True,'panel',''),
 'status': ('read',False,'',''),
 **{name: ('read',True,'','') for name in ('review_production','inspect_cut','inspect_coverage','inspect_structure','inspect_sound')},
}

def _schema(name, spec):
    kind, project, required, optional = spec
    fields = set(required.split()+optional.split())
    properties = {'tool': {'const':name}}
    for field in fields:
        typ = 'integer' if field in {'count','frames','steps','seed','directorScene'} else 'boolean' if field in {'enabled','newProject'} else 'object' if field in {'fields','assetStates'} else 'array' if field in {'anchors','characterIds','propIds','referenceIds','referenceImages','imageAnchors'} else 'string'
        properties[field] = {'type':typ}
    return {'type':'object','properties':properties,'required':['tool']+required.split(),'additionalProperties':False}

TOOL_SCHEMAS = {name:_schema(name,spec) for name,spec in _SPEC.items()}
TOOLS = tuple({'name':name,'kind':spec[0],'requiresProject':spec[1],
               'requiresApproval':name not in READ_ONLY_TOOLS,
               'description':f'{name.replace("_"," ").capitalize()}; return a proposal for the native studio.',
               'schema':TOOL_SCHEMAS[name]} for name,spec in _SPEC.items())

def catalogue(stage=None):
    allowed = {'edit_project','edit_bible','add_character','update_character'} if stage=='setup' else {'add_shot'} if stage=='shots' else set(_SPEC)
    return [copy.deepcopy(t) for t in TOOLS if t['name'] in allowed]

def _ids(context, kind):
    result=set()
    for item in (context or {}).get(kind,[]) or []:
        value=item.get('id') if isinstance(item,dict) else item
        if isinstance(value,str): result.add(value)
    return result

def validate_actions(actions, context=None, *, slug=None, known_slugs=None, has_project=False, stage=None):
    if not isinstance(actions,list): return [],['actions must be a list']
    if len(actions)>64: return [],['proposal has too many actions']
    context=context or {}; errors=[]; accepted=[]
    chain=len(actions)==2 and all(isinstance(a,dict) for a in actions) and [a.get('tool') for a in actions]==['create_project','write_manuscript']
    allowed={t['name'] for t in catalogue(stage)}
    for index,raw in enumerate(actions):
        label=f'action {index+1}'
        if not isinstance(raw,dict) or raw.get('tool') not in allowed:
            errors.append(label+': unknown or unavailable tool'); continue
        action=copy.deepcopy(raw); name=action['tool']; spec=_SPEC[name]; schema=TOOL_SCHEMAS[name]
        if spec[1] and not (has_project or slug or chain): errors.append(label+': select a project first')
        if set(action)-set(schema['properties']): errors.append(label+': unknown fields are not permitted')
        for key in schema['required']:
            if key not in action: errors.append(label+': missing '+key)
        for key,value in action.items():
            if key=='tool' or key not in schema['properties']: continue
            expected=schema['properties'][key].get('type')
            valid = type(value) is int if expected=='integer' else type(value) is bool if expected=='boolean' else isinstance(value,dict) if expected=='object' else isinstance(value,list) if expected=='array' else isinstance(value,str)
            if not valid: errors.append(label+': invalid '+key); continue
            if isinstance(value,str) and (not value.strip() or len(value)>100_000): errors.append(label+': empty or oversized '+key)
            if isinstance(value,list) and len(value)>64: errors.append(label+': oversized '+key)
        if name=='create_project' and (has_project or slug) and action.get('newProject') is not True: errors.append(label+': newProject must be true on a bound project')
        if name=='open_project' and known_slugs is not None and action.get('slug') not in known_slugs: errors.append(label+': unknown project')
        if name=='open_panel' and action.get('panel') not in PANEL_NAMES: errors.append(label+': unknown panel')
        if 'shotId' in action and context and action['shotId'] not in _ids(context,'shots'): errors.append(label+': unknown shotId')
        if 'characterId' in action and context and action['characterId'] not in _ids(context,'characters'): errors.append(label+': unknown characterId')
        if 'characterIds' in action and isinstance(action['characterIds'],list) and context and set(action['characterIds'])-_ids(context,'characters'): errors.append(label+': unknown characterIds')
        if 'locationId' in action and action['locationId'] and context and action['locationId'] not in _ids(context,'locations'): errors.append(label+': unknown locationId')
        images=_ids(context,'images')|_ids(context,'sharedLibrary')
        for key in ('imageId','voiceId'):
            if key in action and context and action[key] not in images: errors.append(label+': unknown '+key)
        if name=='assign_image':
            target=action.get('target')
            if target not in {'character','location','prop','opening','closing'}: errors.append(label+': unknown image target')
            if target in {'opening','closing'} and not action.get('shotId'): errors.append(label+': shotId required')
            kinds={'character':'characters','location':'locations','prop':'props'}
            if target in kinds and action.get('entityId') not in _ids(context,kinds[target]): errors.append(label+': unknown entityId')
        if name=='set_shot_anchors':
            anchors=action.get('anchors')
            if not isinstance(anchors,list) or len(anchors)>32: errors.append(label+': at most 32 anchors')
            else:
                for anchor in anchors:
                    if not isinstance(anchor,dict) or set(anchor)-{'imageId','frame','strength'} or set(anchor)!={'imageId','frame','strength'}: errors.append(label+': invalid anchor'); continue
                    if not str(anchor['imageId']).startswith('project:') or (context and anchor['imageId'] not in images): errors.append(label+': anchor must use a known project image')
                    if not (anchor['frame']=='last' or type(anchor['frame']) is int and anchor['frame']>=0): errors.append(label+': invalid anchor frame')
                    if type(anchor['strength']) not in {int,float} or not 0<anchor['strength']<=1: errors.append(label+': invalid anchor strength')
        if name in {'edit_project','edit_bible'} and isinstance(action.get('fields'),dict):
            if set(action['fields'])-(BOARD_FIELDS if name=='edit_project' else BIBLE_FIELDS): errors.append(label+': unsupported edit field')
        if name=='reveal' and action.get('target','project') not in {'root','project','library','script','input','output'}: errors.append(label+': unknown reveal target')
        if name=='rewrite_text' and action.get('scope') not in {'board','shot'}: errors.append(label+': unsupported text scope')
        if 'count' in action and (type(action['count']) is not int or not 1<=action['count']<=(48 if name=='write_manuscript' else 6)): errors.append(label+': invalid count')
        if 'frames' in action and (type(action['frames']) is not int or not 1<=action['frames']<=24*3600): errors.append(label+': invalid frame count')
        accepted.append(action)
    names=[a['tool'] for a in accepted]
    if names.count('add_shot')>6: errors.append('At most six new shots per proposal')
    if chain:
        if actions[0].get('newProject') is False: errors.append('Combined creation must create a new project')
    elif len(names)>1 and not all(name in READ_ONLY_TOOLS for name in names) and not all(_SPEC[name][0]=='authoring' for name in names):
        errors.append('Project and production operations require separate proposals')
    return ([],errors) if errors else (accepted,[])

def parse_model_reply(raw):
    if not isinstance(raw,str): return {'message':'The director returned an incomplete response.','actions':[],'proposalStatus':'incomplete'},['Expected text']
    text=raw.strip()
    if text.startswith('```'): text=re.sub(r'^```(?:json)?\s*|\s*```$', '',text,flags=re.I)
    try: result=json.loads(text)
    except ValueError: return {'message':'The director proposal was incomplete; no changes were executed.','actions':[],'proposalStatus':'incomplete'},['Invalid proposal JSON']
    if not isinstance(result,dict) or not isinstance(result.get('message',''),str) or not isinstance(result.get('actions',[]),list):
        return {'message':'The director response has an invalid format.','actions':[],'proposalStatus':'incomplete'},['Invalid proposal structure']
    return {'message':result.get('message',''),'actions':result.get('actions',[])},[]

def expert_system_prompt(stage=None):
    return Path(__file__).with_name('raven_director_guide.md').read_text()+'\nReturn JSON {message, actions}.\n'+json.dumps(catalogue(stage),ensure_ascii=False)

def validate_request(payload, known_slugs=None):
    errors=[]
    if not isinstance(payload,dict): return {},['Expected a request object']
    if not isinstance(payload.get('message'),str) or not payload['message'].strip(): errors.append('A message is required')
    slug=payload.get('slug')
    if slug is not None and known_slugs is not None and slug not in known_slugs: errors.append('Unknown project')
    return copy.deepcopy(payload),errors

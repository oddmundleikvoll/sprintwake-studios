# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oddmund Leikvoll
"""Revision- and pixel-bound production journal; originals are never a scratch area."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import uuid

from .store import Conflict, atomic_json, authoring_digest, read_json, safe_path
from .comfy_workflow import profile_digest, GenerationCancelled
from .media import immutable_output, validate_decoded


def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def file_digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): value.update(block)
    return value.hexdigest()


class FilmController:
    def __init__(self,store,scheduler,provider,profiles=None):
        self.store,self.scheduler,self.provider=store,scheduler,provider
        self.profiles=profiles or {'image':'qwen-local','video':'ltx-local'}
    def _path(self,slug): return safe_path(self.store.project_dir(slug),'System','production.json')
    def _read(self,slug):
        state=read_json(self._path(slug),{})
        if not isinstance(state,dict): raise ValueError('Production journal is invalid')
        return state
    def _save(self,slug,state): atomic_json(self._path(slug),state)
    def _resolve(self,ref): return self.store.media_path(ref)
    def _source(self,board):
        refs=[]
        def add(ref):
            if ref:
                path=self._resolve(ref); refs.append([str(path.relative_to(self.store.data_dir)),file_digest(path)])
        for ref in board.get('styleRefs',[]) or []: add(ref)
        for person in board.get('characters',[]): add(person.get('image'))
        for kind in ('locations','props'):
            for entity in (board.get('filmBible') or {}).get(kind,[]) or []: add(entity.get('referenceImage') or entity.get('image'))
        for shot in board.get('shots',[]):
            for key in ('startRef','endRef'): add(shot.get(key))
            for ref in shot.get('referenceImages',[]) or []: add(ref)
            for anchor in shot.get('imageAnchors',[]) or []: add(anchor.get('ref'))
        return digest({'authoring':authoring_digest(board),'references':refs,'profiles':self.profiles})
    def _groups(self,board):
        groups=[]; seen=set()
        for shot in board.get('shots',[]):
            group=shot.get('directorScene',1)
            if type(group) is not int or group<1: raise ValueError('Every shot needs a positive camera group')
            if not groups or groups[-1][0]!=group:
                if group in seen: raise ValueError('A camera group must be contiguous')
                groups.append((group,[])); seen.add(group)
            groups[-1][1].append(shot)
        for _,shots in groups:
            if len(shots)>6: raise ValueError('A continuous camera group cannot exceed six shots')
            camera=shots[0].get('camera','')
            location=shots[0].get('locationId','')
            for index,shot in enumerate(shots):
                frames=shot.get('frames',121 if index==0 else 120)
                if type(frames) is not int or frames not in ({97,121,145} if index==0 else {96,120,144}): raise ValueError('Camera-group frame counts violate the opening/continuation grid')
                if shot.get('camera','')!=camera or shot.get('locationId','')!=location or shot.get('resolution','384x256')!=shots[0].get('resolution','384x256'): raise ValueError('A continuous camera group cannot change camera or location')
        return groups
    def _plan_digest(self,state):
        values=[]
        for card in (state.get('imagePlan') or {}).get('cards',[]):
            path=self._resolve(card['ref']); values.append([card['shotId'],file_digest(path)])
            if card.get('endRef'): values.append([card['shotId']+'-end',file_digest(self._resolve(card['endRef']))])
        return digest(values) if values else None
    def _revision(self,board,state):
        source=self._source(board)
        return digest({'source':source,'images':self._plan_digest(state)}) if state.get('imagePlan') else source
    def status(self,slug):
        board=self.store.get(slug); state=self._read(slug); error=None
        try:
            groups=self._groups(board); source=self._source(board); revision=self._revision(board,state)
            stale=bool(state.get('source') and state['source']!=source)
            if state.get('imagePlan') and state['imagePlan'].get('digest')!=self._plan_digest(state): stale=True
        except (ValueError,OSError,KeyError) as failure:
            groups=[]; source=authoring_digest(board); revision=source; stale=True; error=str(failure)
        job=self.scheduler.status(slug)
        active=bool(job and job.status in {'queued','running','cancelling'})
        phase=state.get('phase','unplanned')
        uncertain=phase in {'planning','rendering'} and not active
        pending=bool(state.get('imagePlan') and (state.get('acceptedImages')!=state['imagePlan']['digest'] or stale))
        plan=copy.deepcopy(state.get('imagePlan'))
        recoverable=False
        if uncertain and phase=='rendering':
            group_id=next((str(ident) for ident,_ in groups if str(ident) not in state.get('reviewedGroups',[])),None)
            run=(state.get('groupRuns') or {}).get(group_id,{})
            check=getattr(self.provider,'resume_available',None)
            if run.get('runId') and check:
                try: recoverable=check(run['runId'])
                except (ValueError,RuntimeError,OSError): pass
        return {'slug':slug,'isFilm':bool(plan),'anchorRevision':source,'revision':revision,
                'phase':'recovery_required' if uncertain else phase,'busy':active,'jobId':job.id if active else None,
                'completed':len(state.get('completed',{})),'total':len(board.get('shots',[])),
                'canProduce':bool(groups) and not active and (not error or bool(state.get('imagePlan'))),
                'canResume':bool(plan) and not stale and (not uncertain or recoverable),'imagePlan':plan,
                'imagePlanStale':stale,'imageApprovalRequired':pending,'reviewRequired':bool(state.get('reviewPreview')),
                'reviewPreview':copy.deepcopy(state.get('reviewPreview')),'continuationAnchor':bool(state.get('continuationAnchor',False)),
                'error':error or state.get('error') or (job.error if job else None),
                'recoveryRequired':uncertain,'plannedFilm':{}}
    def _assert_revision(self,slug,board,state,payload):
        expected=payload.get('revision')
        try: actual=self._source(board) if payload.get('tool')=='set_shot_anchors' else self._revision(board,state)
        except (ValueError,OSError,KeyError):
            if not payload.get('prepareOnly'): raise
            actual=authoring_digest(board)
        if not isinstance(expected,str) or expected!=actual: raise Conflict('The film revision changed; refresh before continuing')
    def _references(self,board,shot):
        result=list(shot.get('referenceImages') or [])+list(board.get('styleRefs') or [])
        ids=set(shot.get('characterIds') or [])
        result += [c.get('image') for c in board.get('characters',[]) if c.get('id') in ids and c.get('image')]
        bible=board.get('filmBible') or {}
        result += [p.get('referenceImage') for p in bible.get('locations',[]) if p.get('id')==shot.get('locationId') and p.get('referenceImage')]
        result += [p.get('referenceImage') for p in bible.get('props',[]) if p.get('id') in shot.get('propIds',[]) and p.get('referenceImage')]
        paths=[]
        for ref in result:
            path=self._resolve(ref)
            if path not in paths: paths.append(path)
        if len(paths)>10: raise ValueError('A scene image accepts at most ten selected references')
        return paths
    def _prepare(self,slug,source,job):
        board=self.store.get(slug); cards=[]
        try:
            for shot in board['shots']:
                if job.cancel_event.is_set(): raise GenerationCancelled('Image-plan preparation cancelled')
                ref=shot.get('startRef')
                if ref:
                    path=self._resolve(ref); validate_decoded(path,kind='image')
                else:
                    prompt=' '.join(str(v) for v in [board.get('renderStyle',''),shot.get('start',''),shot.get('camera',''),board.get('sceneDescription','')] if v)
                    if not prompt: raise ValueError('The scene needs an opening description')
                    result=self.provider.generate({'native':'qwen'},prompt,shot.get('seed',0),width=768,height=512,
                        references=self._references(board,shot),cancel=job.cancel_event.is_set)
                    if not result.get('media'): raise ValueError('Image preparation produced no pixels')
                    path=Path(result['media'][0]); validate_decoded(path,kind='image')
                    ref=self.store.import_media(slug,path,'images/film-'+uuid.uuid4().hex+path.suffix.lower())
                card={'shotId':shot['id'],'ref':copy.deepcopy(ref),'opening':copy.deepcopy(ref),'images':[copy.deepcopy(ref)]}
                if self._read(slug).get('continuationAnchor') and not shot.get('endRef'):
                    result=self.provider.generate({'native':'qwen'},' '.join([board.get('renderStyle',''),shot.get('end',''),shot.get('camera','')]),shot.get('seed',0),width=768,height=512,references=self._references(board,shot),cancel=job.cancel_event.is_set)
                    closing=self.store.import_media(slug,Path(result['media'][0]),'images/film-end-'+uuid.uuid4().hex+'.png')
                    card['endRef']=closing; card['images'].append(closing)
                cards.append(card)
                job.completed=len(cards)
                with self.store.transaction():
                    current=self.store.get(slug)
                    if self._source(current)!=source: raise Conflict('The project changed during image preparation')
            state={'continuationAnchor':bool(self._read(slug).get('continuationAnchor',False)),'source':source,'phase':'images','imagePlan':{'cards':cards,'revision':source},'completed':{},'reviewedGroups':[]}
            state['imagePlan']['images']=[{'ref':card['endRef']} for card in cards if card.get('endRef')]
            state['imagePlan']['digest']=self._plan_digest(state)
            with self.store.transaction():
                if self._source(self.store.get(slug))!=source: raise Conflict('The project changed during image preparation')
                self._save(slug,state)
        except Exception as error:
            state=self._read(slug); state.update(phase='stopped' if job.cancel_event.is_set() else 'failed',error=str(error))
            self._save(slug,state); raise
    def action(self,payload):
        inner=payload.get('action')
        action=dict(inner) if isinstance(inner,dict) else dict(payload)
        slug=payload.get('slug') or action.get('slug')
        if not isinstance(slug,str): raise ValueError('A project is required')
        with self.scheduler.admission_lock,self.store.transaction():
            board=self.store.get(slug); state=self._read(slug); before=authoring_digest(board)
            self._assert_revision(slug,board,state,action)
            if self.scheduler.active(): raise Conflict('Wait until owned production work drains')
            if action.get('tool')=='set_shot_anchors':
                from .ltx_anchors import image_catalog
                images={key:item['ref'] for key,item in image_catalog(board,self.store.data_dir).items()}
                shot=next((s for s in board.get('shots',[]) if s['id']==action.get('shotId')),None)
                if not shot: raise ValueError('Choose an existing shot')
                anchors=action.get('anchors')
                if not isinstance(anchors,list) or len(anchors)>32: raise ValueError('Invalid image anchor list')
                clean=[]
                for anchor in anchors:
                    if not isinstance(anchor,dict) or set(anchor)!={'imageId','frame','strength'}: raise ValueError('Invalid anchor')
                    ref=images.get(str(anchor['imageId']).removeprefix('project:'))
                    frame=anchor['frame']; strength=anchor['strength']
                    if not ref or not (frame=='last' or type(frame) is int and 0<=frame<shot.get('frames',121)) or type(strength) not in {int,float} or not 0<strength<=1:
                        raise ValueError('Anchor reference, frame or strength is invalid')
                    clean.append({'ref':ref,'frame':frame,'strength':strength})
                shot['imageAnchors']=clean; self.store.put(slug,board,expected_digest=before); return self.status(slug)
            if action.get('tool')=='set_film_anchor':
                if type(action.get('enabled')) is not bool: raise ValueError('An explicit anchor choice is required')
                state['continuationAnchor']=action['enabled']; self._save(slug,state); return self.status(slug)
            if action.get('acceptImagePlan'):
                plan=state.get('imagePlan') or {}
                if state.get('source')!=self._source(board) or action['acceptImagePlan']!=plan.get('digest') or plan.get('digest')!=self._plan_digest(state): raise Conflict('The image plan or its pixels changed')
                state['acceptedImages']=plan['digest']; self._save(slug,state); return self.status(slug)
            if action.get('acceptReview') is True:
                preview=state.get('reviewPreview')
                if not preview or state.get('source')!=self._source(board): raise Conflict('No current human review is pending')
                if self._plan_digest(state)!=(state.get('imagePlan') or {}).get('digest'): raise Conflict('Review references changed')
                by_id={shot['id']:shot for shot in board['shots']}
                for media in preview.get('clips',[]):
                    if by_id.get(media['shotId'],{}).get('outputs')!=[media['video']]: raise Conflict('Selected clips changed during review')
                    if file_digest(self._resolve(media['video']))!=media['sha256']: raise Conflict('Reviewed media changed')
                state.setdefault('reviewedGroups',[]).append(preview['groupId']); state.pop('reviewPreview',None)
                state['phase']='done' if len(state['reviewedGroups'])==len(self._groups(board)) else 'ready'
                self._save(slug,state); return self.status(slug)
            if action.get('prepareOnly') is True:
                if self.profiles.get('image')!='qwen-local' or self.profiles.get('video')!='ltx-local': raise ValueError('Whole-film preparation requires the native local providers')
                self._groups(board)
                if not board.get('shots'): raise ValueError('Create scene cards before preparing a film')
                source=self._source(board)
                old=copy.deepcopy(state); old.update(phase='planning',error=None)
                job=self.scheduler.submit(slug,'prepare',lambda job:self._prepare(slug,source,job))
                old['jobId']=job.id; self._save(slug,old); return self.status(slug)
            if not state.get('imagePlan') or state.get('acceptedImages')!=state['imagePlan']['digest'] or state.get('source')!=self._source(board) or self._plan_digest(state)!=state['imagePlan']['digest']:
                raise ValueError('Prepare and explicitly review the current image plan before video generation')
            if state.get('reviewPreview'): raise ValueError('Review the completed camera group before resuming')
            if state.get('phase') in {'planning','rendering'} and not self.status(slug).get('canResume'):
                raise Conflict('Interrupted work has no verified checkpoint; prepare a fresh image plan explicitly')
            groups=self._groups(board); reviewed=set(state.get('reviewedGroups',[]))
            group=next(((ident,shots) for ident,shots in groups if str(ident) not in reviewed),None)
            if group is None: return self.status(slug)
            state.update(phase='rendering',error=None)
            source=state['source']; self._save(slug,state)
            self.scheduler.submit(slug,'film',lambda job:self._render_group(slug,group,source,job))
            return self.status(slug)
    def _render_group(self,slug,group,source,job):
        ident,shots=group; state=self._read(slug)
        run=state.setdefault('groupRuns',{}).get(str(ident))
        selected=run.get('selected') if run else {s['id']:copy.deepcopy(s.get('outputs',[])) for s in shots}
        resume=bool(run and run.get('checkpoint'))
        if run and getattr(self.provider,'resume_available',None):
            resume=self.provider.resume_available(run['runId'])
        if not run:
            run={'runId':uuid.uuid4().hex,'selected':selected}; state['groupRuns'][str(ident)]=run
            self._save(slug,state)
        def checkpoint(manifest):
            run['checkpoint']=copy.deepcopy(manifest); self._save(slug,state)
        try:
            cards={c['shotId']:c for c in state['imagePlan']['cards']}
            anchors=[]
            for index,shot in enumerate(shots):
                for anchor in shot.get('imageAnchors',[]):
                    anchors.append({'clip_index':index,'path':str(self._resolve(anchor['ref'])),'frame':anchor['frame'],'strength':anchor['strength']})
            rendering=copy.deepcopy(shots)
            board=self.store.get(slug)
            for shot in rendering:
                ids=set(shot.get('characterIds',[]))
                shot['castDescription']='; '.join(str(c.get('name',''))+': '+str(c.get('description','')) for c in board.get('characters',[]) if c.get('id') in ids)
                shot['soundNote']=' '.join([board.get('soundscape',''),shot.get('soundNote','')]).strip()
            result=self.provider.generate_group(rendering,start_image=self._resolve(cards[shots[0]['id']]['ref']),
                end_image=self._resolve(shots[-1]['endRef'] or cards[shots[-1]['id']].get('endRef')) if shots[-1].get('endRef') or cards[shots[-1]['id']].get('endRef') else None,anchors=anchors,
                cancel=job.cancel_event.is_set,on_progress=lambda n:setattr(job,'completed',n),
                resume_run_id=run['runId'] if resume else None,run_id=None if resume else run['runId'],on_checkpoint=checkpoint)
            files=result.get('media',[])
            if len(files)!=len(shots): raise ValueError('The camera group returned an unexpected number of clips')
            clips=[]
            for shot,path in zip(shots,files):
                validate_decoded(path,kind='video',fps=24,frames=shot['frames'])
                folder='shots/'+shot['id']+'/productions/'+uuid.uuid4().hex
                ref=self.store.import_media(slug,path,folder+'/clip.mp4')
                recipe=copy.deepcopy(result.get('recipe',{})); recipe['sourceRevision']=source
                atomic_json(safe_path(self.store.project_dir(slug),folder,'recipe.json',create_parent=True),recipe)
                clips.append({'shotId':shot['id'],'video':ref['url'],'sha256':file_digest(path),'recipe':recipe})
            with self.store.transaction():
                board=self.store.get(slug)
                if self._source(board)!=source or any(s.get('outputs',[])!=selected.get(s['id'],s.get('outputs',[])) for s in board['shots']): raise Conflict('The script or selected takes changed while rendering; generated media was retained without replacing selected takes')
                by_id={s['id']:s for s in board['shots']}
                for clip in clips:
                    by_id[clip['shotId']].update(outputs=[clip['video']],status='review',renderFingerprint=source)
                    state.setdefault('completed',{})[clip['shotId']]=clip
                self.store.put(slug,board,expected_digest=authoring_digest(board))
                next_group=next((ss for gid,ss in self._groups(board) if str(gid)!=str(ident) and str(gid) not in state.get('reviewedGroups',[])),None)
                from .media import last_frame
                tail_path=safe_path(self.store.project_dir(slug),'images','review-tail-'+uuid.uuid4().hex+'.png',create_parent=True)
                last_frame(self._resolve(clips[-1]['video']),tail_path)
                preview={'key':uuid.uuid4().hex,'tail':self.store.ref(tail_path),'groupId':str(ident),'shotIds':[s['id'] for s in shots],'clips':clips,
                         'nextOpening':cards[next_group[0]['id']]['ref'] if next_group else None}
                state.update(reviewPreview=preview,phase='review'); self._save(slug,state)
        except Exception as error:
            state.update(phase='stopped' if job.cancel_event.is_set() else 'failed',error=str(error)); self._save(slug,state); raise
    def _take_path(self,slug,shotid):
        if not isinstance(shotid,str) or not shotid or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in shotid): raise ValueError('Invalid shot ID')
        return safe_path(self.store.project_dir(slug),'shots',shotid,'seed-takes','index.json')
    def _take_rows(self,slug,shotid):
        rows=read_json(self._take_path(slug,shotid),[])
        if not isinstance(rows,list) or len(rows)>10_000: raise ValueError('Invalid take history')
        return rows
    def takes(self,slug,shotid):
        board=self.store.get(slug); rows=copy.deepcopy(self._take_rows(slug,shotid)); seen={row.get('seed') for row in rows}
        legacy=safe_path(self.store.project_dir(slug),'seed-takes',shotid)
        if legacy.is_dir():
            for folder in sorted(legacy.iterdir()):
                if folder.is_symlink() or not folder.is_dir() or not folder.name.isdigit() or int(folder.name) in seen: continue
                record=read_json(safe_path(folder,'take.json'),{})
                if not isinstance(record,dict) or record.get('status') not in {'done','review','failed','cancelled'}: continue
                output=safe_path(folder,'clip.mp4')
                row=copy.deepcopy(record); row.update(seed=int(folder.name),legacy=True)
                if output.is_file():
                    row.update(video=self.store.ref(output)['url'],recipe=str(safe_path(folder,'recipe.json').relative_to(self.store.project_dir(slug))),frames=record.get('frames'))
                rows.append(row)
                if len(rows)>10_000: raise ValueError('Take history is too large')
        for row in rows:
            value=row.get('video')
            if value:
                try: row['clipUrl']=self.store.ref(self._resolve(value))['url']
                except (ValueError,OSError): row['clipUrl']=None
            row['current']=bool(row.get('sourceRevision') and row['sourceRevision']==authoring_digest(board))
        return rows
    def render_seeds(self,payload):
        slug,shotid=payload.get('slug'),payload.get('shotId'); count=payload.get('count',1)
        if type(count) is not int or not 1<=count<=6: raise ValueError('Choose 1–6 takes')
        board=self.store.get(slug); shot=next((s for s in board['shots'] if s['id']==shotid),None)
        if not shot: raise ValueError('Choose an existing shot')
        profile=self.profiles.get('video')
        if isinstance(profile,dict):
            defaults=board.get('defaults',{}); provider=shot.get('modelProvider',defaults.get('modelProvider'))
            saved=shot.get('modelProfileDigest',defaults.get('modelProfileDigest'))
            if provider!='comfy-workflow' or saved!=profile_digest(profile) or shot.get('model',defaults.get('model'))!=profile['id']: raise Conflict('Confirm the selected workflow profile before creating a take')
        elif profile!='ltx-local': raise ValueError('No video provider is selected')
        snapshot=copy.deepcopy(shot); rows=self.takes(slug,shotid); used={r.get('seed') for r in rows}
        seeds=[]; seed=shot.get('seed',0)
        while len(seeds)<count:
            if seed not in used: seeds.append(seed); used.add(seed)
            seed=(seed+1)%2**32
        def work(job):
            for seed in seeds:
                if job.cancel_event.is_set(): raise GenerationCancelled('Take comparison was cancelled')
                entry={'seed':seed,'status':'running','shotId':shotid,'sourceRevision':authoring_digest(board)}; rows=self._take_rows(slug,shotid); rows.append(entry)
                atomic_json(self._take_path(slug,shotid),rows)
                folder='shots/'+shotid+'/seed-takes/'+str(seed)+'-'+uuid.uuid4().hex
                try:
                    width,height=[int(n) for n in snapshot.get('resolution',board.get('defaults',{}).get('resolution','384x256')).split('x')]
                    result=self.provider.generate(profile,snapshot.get('prompt') or snapshot.get('action',''),seed,snapshot.get('frames',121),width,height,
                        references=[self._resolve(ref) for ref in snapshot.get('referenceImages',[])],
                        start_image=self._resolve(snapshot['startRef']) if snapshot.get('startRef') else None,
                        end_image=self._resolve(snapshot['endRef']) if snapshot.get('endRef') else None,cancel=job.cancel_event.is_set)
                    media=result.get('media',[])
                    if len(media)!=1: raise ValueError('A take must contain exactly one video')
                    validate_decoded(media[0],kind='video',fps=24,frames=snapshot.get('frames',121))
                    ref=self.store.import_media(slug,media[0],folder+'/clip.mp4')
                    recipe=copy.deepcopy(result.get('recipe',{})); recipe['sha256']=file_digest(media[0])
                    atomic_json(safe_path(self.store.project_dir(slug),folder,'recipe.json',create_parent=True),recipe)
                    entry.update(status='review',video=ref['url'],outputs=[ref['url']],recipe=folder+'/recipe.json',recipeDigest=digest(recipe),sha256=recipe['sha256'],frames=snapshot.get('frames',121),duration=snapshot.get('frames',121)/24)
                except Exception as error:
                    entry.update(status='cancelled' if job.cancel_event.is_set() else 'failed',error=str(error)); raise
                finally: atomic_json(self._take_path(slug,shotid),rows)
                job.completed+=1
        job=self.scheduler.submit(slug,'takes',work); job.total=count
        return {'jobId':job.id,'slug':slug,'accepted':True}
    def adopt_take(self,slug,shotid,seed):
        with self.scheduler.admission_lock,self.store.transaction():
            if self.scheduler.active(): raise Conflict('Wait until production drains before selecting a take')
            board=self.store.get(slug); shot=next((s for s in board['shots'] if s['id']==shotid),None)
            take=next((r for r in self.takes(slug,shotid) if r.get('seed')==seed and r.get('status') in {'done','review'}),None)
            if not shot or not take: raise ValueError('Choose a completed take')
            source=self._resolve(take['video']); recipe_path=safe_path(self.store.project_dir(slug),take['recipe'])
            recipe=read_json(recipe_path)
            if not isinstance(recipe,dict) or not recipe: raise ValueError('The retained take recipe is missing')
            actual=file_digest(source)
            if take.get('legacy'):
                if recipe.get('sha256') is not None and recipe['sha256']!=actual: raise ValueError('The retained legacy take changed')
                recipe=dict(recipe,sha256=actual,importedLegacyTake=True)
            elif recipe.get('sha256')!=actual or recipe.get('sha256')!=take.get('sha256') or digest(recipe)!=take.get('recipeDigest'):
                raise ValueError('The retained take or its recipe changed')
            validate_decoded(source,kind='video',fps=24,frames=shot.get('frames',take.get('frames')))
            # A new immutable selection path prevents a failed CAS from overwriting prior bytes.
            folder='shots/'+shotid+'-'+uuid.uuid4().hex
            ref=self.store.import_media(slug,source,folder+'/clip.mp4')
            atomic_json(safe_path(self.store.project_dir(slug),folder,'recipe.json',create_parent=True),recipe)
            shot.update(outputs=[ref['url']],status='review')
            saved=self.store.put(slug,board,expected_digest=authoring_digest(board))
            state=self._read(slug)
            if state.get('source')==self._source(saved) and shotid in state.get('completed',{}):
                group_id,group=next((ident,shots) for ident,shots in self._groups(saved) if any(s['id']==shotid for s in shots))
                state['completed'][shotid]={'shotId':shotid,'video':ref['url'],'sha256':actual,'recipe':recipe}
                clips=[state['completed'][item['id']] for item in group]
                from .media import last_frame
                tail=safe_path(self.store.project_dir(slug),'images','review-tail-'+uuid.uuid4().hex+'.png',create_parent=True)
                last_frame(self._resolve(clips[-1]['video']),tail)
                cards={card['shotId']:card for card in state['imagePlan']['cards']}
                groups=self._groups(saved); position=next(i for i,(ident,_) in enumerate(groups) if ident==group_id)
                next_shots=groups[position+1][1] if position+1<len(groups) else None
                state['reviewedGroups']=[ident for ident in state.get('reviewedGroups',[]) if ident!=str(group_id)]
                state.update(phase='review',reviewPreview={'key':uuid.uuid4().hex,'groupId':str(group_id),'shotIds':[s['id'] for s in group],
                    'clips':clips,'tail':self.store.ref(tail),'nextOpening':cards[next_shots[0]['id']]['ref'] if next_shots else None})
                self._save(slug,state)
            return saved
    def assert_assembly_ready(self,slug):
        board=self.store.get(slug); state=self._read(slug)
        if self.scheduler.active(): raise Conflict('Wait until owned work drains before assembly')
        groups={str(group) for group,_ in self._groups(board)}
        if not groups or state.get('source')!=self._source(board) or state.get('phase')!='done' or set(state.get('reviewedGroups',[]))!=groups:
            raise Conflict('Every camera group requires the current image plan and explicit final video/audio review before assembly')
        if state.get('acceptedImages')!=(state.get('imagePlan') or {}).get('digest') or self._plan_digest(state)!=state['imagePlan']['digest']:
            raise Conflict('Approved image pixels changed')
        for shot in board['shots']:
            receipt=state.get('completed',{}).get(shot['id'])
            if not receipt or shot.get('outputs')!=[receipt['video']] or file_digest(self._resolve(receipt['video']))!=receipt['sha256']:
                raise Conflict('A reviewed selected clip changed')
        return state

    def stop(self,payload): return {'stopped':self.scheduler.stop(payload.get('slug'),payload.get('jobId'))}

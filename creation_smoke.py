"""Real native home/quick-room checks, without media inference."""
import json
import os
from pathlib import Path

import raven as r
from app import ROOT


def run(studio,pump,wait,check):
    qa=Path(os.environ.get('SPRINTWAKE_QA_DIR',str(ROOT/'qa')))
    def enter(kind):
        studio.on_action(None,'home.'+kind,None)
        wait(lambda:studio.mode=='create' and studio.creation_mode==kind and 'save' not in studio.pending,f'{kind} entrance ready after previous edits save')
        pump(.1)

    def click_open():
        x,y,w,h=r.Rwidget_get_rect(studio.find('creation.open'))
        r.Rwindow_inject_mouse_move(studio.window,x+w/2,y+h/2)
        r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,True)
        r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,False)
        pump(.1)
    studio.modal_close()
    studio.save()
    wait(lambda:not studio.dirty and 'save' not in studio.pending,'Saved before testing Start')
    original=studio.workspace.projects_dir/studio.slug/'storyboard.json'
    original_bytes=original.read_bytes()
    studio.on_action(None,'page.home',None);pump(.2)
    for width,height in ((1440,920),(1180,760)):
        r.Rwindow_set_size(studio.window,width,height);pump(.2)
        boxes=[r.Rwidget_get_rect(studio.find('home.'+kind)) for kind in ('image','clip','studio')]
        check(all(w>280 and h>380 and x>=0 and x+w<=width and y+h<=height for x,y,w,h in boxes),f'All three home cards fit at {width} × {height}')
        studio.capture(qa/f'studio-start-{width}.png')
    r.Rwindow_set_size(studio.window,1440,920);pump(.1)
    x,y,w,h=r.Rwidget_get_rect(studio.find('home.image'))
    r.Rwindow_inject_mouse_move(studio.window,x+w/2,y+70)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,True)
    r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,False)
    pump(.15)
    check(studio.mode=='create' and studio.creation_mode=='image','Image card opens its own simple entrance')
    studio.on_action(None,'page.home',None)
    check(original.read_bytes()==original_bytes,'Opening and leaving creation preserves existing production bytes')
    for kind,destination in (('image','images'),('clip','clip')):
        enter(kind)
        check(not studio.text('creation.title') and not studio.text('creation.prompt'),f'{kind} entrance begins with optional empty fields')
        click_open()
        wait(lambda:studio.mode==destination and not studio.opening and 'creation' not in studio.pending,f'Empty {kind} studio opens through actual native button')
        check(studio.board['name']==('Nytt bilde' if kind=='image' else 'Nytt klipp') and not studio.board['sceneDescription'],f'{kind} studio opens without a name or description')
        check(not studio._busy() and 'image.generate' not in studio.pending and 'clip.generate' not in studio.pending,f'Opening empty {kind} studio never generates media')
        studio.on_action(None,'page.home',None);pump(.1)
    enter('image')
    studio.set_text('creation.title','Bilde fra startsiden')
    studio.set_text('creation.prompt','En stille skog i morgenlys.')
    pump(.1);click_open()
    wait(lambda:studio.mode=='images' and not studio.opening and 'creation' not in studio.pending,'Image studio opened')
    image_slug=studio.slug
    check(studio.text('image.prompt')=='En stille skog i morgenlys.' and not studio.board['shots'],'Image entrance restores prompt and needs no film manuscript or shot')
    check(not studio._busy() and 'image.generate' not in studio.pending,'Opening image studio starts no inference')
    studio.capture(qa/'studio-start-image.png')
    studio.on_action(None,'page.home',None)
    enter('clip')
    studio.set_text('creation.title','Klipp fra startsiden')
    studio.set_text('creation.prompt','A small bird lands beside a nest. Static wide shot, quiet birdsong.')
    pump(.1);click_open()
    wait(lambda:studio.mode=='clip' and not studio.opening and 'creation' not in studio.pending,'Clip studio opened')
    clip_slug=studio.slug
    check(len(studio.board['shots'])==1 and studio.text('quickclip.prompt')==studio.board['shots'][0]['prompt'],'Clip entrance creates exactly one real editable shot')
    studio.capture(qa/'studio-start-clip.png')
    studio.set_text('quickclip.prompt','The bird looks toward the quiet nest. Locked camera.')
    r.Rcombo_box_set_selected(studio.find('quickclip.duration'),2)
    studio.on_action(None,'page.home',None)
    studio.save()
    wait(lambda:not studio.dirty and 'save' not in studio.pending,'Quick clip edits saved on navigation')
    board=studio.backend.get('/api/boards/'+clip_slug)['board']
    check(board['shots'][0]['prompt']=='The bird looks toward the quiet nest. Locked camera.' and board['shots'][0]['frames']==145,'Clip prompt and duration persist through ordinary saves')
    studio.open_project(image_slug)
    wait(lambda:studio.slug==image_slug and not studio.opening,'Image work reopened')
    check(studio.mode=='images','Image work reopens in image studio')
    studio.open_project(clip_slug)
    wait(lambda:studio.slug==clip_slug and not studio.opening,'Clip work reopened')
    check(studio.mode=='clip' and studio.text('quickclip.prompt')==board['shots'][0]['prompt'],'Clip work reopens in clip studio with saved description')
    actual_post=studio.backend.post
    renders=[]
    def inert_render(path,payload,*args,**kwargs):
        if path=='/api/render-seeds':
            renders.append(payload)
            return {'busy':False}
        return actual_post(path,payload,*args,**kwargs)
    studio.backend.post=inert_render
    try:
        studio.on_action(None,'clip.generate',None)
        wait(lambda:bool(renders) and 'clip.generate' not in studio.pending,'One-clip button completed without inference')
    finally:
        studio.backend.post=actual_post
    check(renders==[{'slug':clip_slug,'shotId':board['shots'][0]['id'],'count':1}],'Native clip action requests exactly one take for the saved shot')
    session=studio.current_conversation['id']
    studio._free_project();studio.mode_set('home')
    studio._booted((studio.info,studio.backend.get('/api/boards')))
    pump(.2)
    check(studio.mode=='home' and studio.board is None and studio.current_conversation['projectSlug']==clip_slug,'Startup with a project-bound saved conversation stays at Start')
    studio.on_action(None,'home.studio',None)
    check(studio.mode=='projects' and studio.board is None,'Filmstudio card opens productions without silently selecting a project')
    studio.on_action(None,'home.director',None)
    wait(lambda:studio.mode=='director' and not studio.opening,'Director conversation explicitly restored')
    check(studio.current_conversation['id']==session and studio.slug==clip_slug,'Explicit director choice restores the same conversation and project')
    check(original.read_bytes()==original_bytes,'All quick-room operations preserve the original film')
    check(not studio._busy(),'Quick-room QA never starts a model')
    studio.mode_set('home');pump(.1)

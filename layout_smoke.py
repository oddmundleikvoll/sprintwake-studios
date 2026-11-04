"""Geometry, ID routing and inline playback in the real native window."""
import raven as r
from app import ROOT
from pathlib import Path
import os
QA_ROOT=Path(os.environ.get("SPRINTWAKE_QA_DIR",str(ROOT/"qa")))


def geometry(studio,check):
    rect=lambda name:r.Rwidget_get_rect(studio.find(name))
    left,viewer,right,bottom=map(rect,('workspace.sidebar','viewer.frame','workspace.details','timeline.panel'))
    x,y,w,h=viewer
    check(left[0]+left[2] <= x and x+w <= right[0] and y+h <= bottom[1]+1,
          'Scene outline, player and inspector occupy separate columns above the timeline')
    check(w>=500 and h>=100 and left[3]>100 and right[3]>100,
          'Native player and both sidebars retain usable space')
    width,height=r.Rwindow_get_size(studio.window)
    check(bottom[0]<25 and bottom[2]>width-50 and bottom[1]+bottom[3]<=height-25,
          'Timeline spans the bottom of the editing workspace inside the window')


def run(studio,pump,wait,check):
    def enter(mode):
        studio.mode_set(mode)
        wait(lambda:studio.mode==mode and 'cut.load' not in studio.pending and not studio.editorial_io_busy(),'Shared workspace '+mode)
        pump(.2)
    enter('cut')
    studio.select_shot(next((i for i,s in enumerate(studio.project.shots) if s.take and (s.take.video or s.take.image)),0))
    chosen=studio.shot.id
    project=studio.workspace.projects_dir/studio.slug
    paths=[project/'storyboard.json']+[project/'Script'/f'{name}.json' for name in ('cut','structure','sound','coverage')]
    before={p:p.read_bytes() for p in paths}
    enter('structure')
    sequence=studio.sequence_doc['state']['sequences'][0]
    studio.on_action(None,'outline.select.sequence.'+sequence['id'],None)
    enter('cut');enter('structure')
    check(studio.layout_group==('sequence',sequence['id']),'Selected narrative group survives switching between cut and structure')
    studio.select_shot(next(i for i,s in enumerate(studio.project.shots) if s.id==chosen))
    for size in ((1440,920),(1180,760)):
        r.Rwindow_set_size(studio.window,*size);pump(.3)
        for mode,inspector in [('film','inspector'),('scenes','scene.inspector'),('structure','structure.inspector'),('cut','cut.inspector'),('sound','sound.details')]:
            enter(mode)
            check(studio.shot.id==chosen,'Shot ID survives transition to '+mode+' at '+str(size))
            check(r.Rwidget_get_visibility(studio.find(inspector))==r.R_VISIBLE,'Context inspector is visible for '+mode)
            geometry(studio,check)
            if mode in {'cut','sound'}:
                studio.capture(QA_ROOT/f'layout-{mode}-{size[0]}.png')
        # Real tab click, with mixer controls fitted to the right column.
        tab=studio.find('sound.tab.mixer');x,y,w,h=r.Rwidget_get_rect(tab)
        r.Rwindow_inject_mouse_move(studio.window,x+w/2,y+h/2)
        r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,True)
        r.Rwindow_inject_mouse_button(studio.window,r.R_MOUSE_LEFT,False);pump(.2)
        check(studio.sound_detail=='mixer' and r.Rwidget_get_visibility(studio.find('sound.mix'))==r.R_VISIBLE,'Native mixer tab opens within right inspector')
        studio.capture(QA_ROOT/f'layout-mixer-{size[0]}.png')
        studio.on_action(None,'workspace.sound.clip',None)
    check(all(p.read_bytes()==saved for p,saved in before.items()),'Layout mode changes preserve every canonical and editorial journal byte')
    # Collapse/expand is presentation state, never a project edit.
    scene=next(s for seq in studio.layout_sequences for s in seq['scenes'] if chosen in s['shotIds'])
    studio.on_action(None,'outline.toggle.scene.'+scene['id'],None)
    check(studio.find('shot.'+str(studio.index))==r.R_INVALID_ID,'Scene collapse compacts the native outline')
    if studio.index+1<len(studio.project.shots):
        studio.on_action(None,'transport.next',None)
        studio.on_action(None,'transport.prev',None)
        check(studio.shot.id==chosen and not studio.errors,'Transport preserves selection while scene rows are collapsed')
    studio.on_action(None,'outline.toggle.scene.'+scene['id'],None)
    check(studio.find('shot.'+str(studio.index))!=r.R_INVALID_ID,'Scene expansion restores shot selection by ID')
    absolute=round(studio.project.duration*24)+96
    studio.on_sound_scrub(None,absolute/24,None)
    check(studio.sound_cursor==absolute and studio.clock<=studio.project.duration and all(p.read_bytes()==saved for p,saved in before.items()),'Sound scrub beyond picture end preserves absolute placement and every saved journal')
    studio.sound_cursor=0
    studio.select_shot(next(i for i,s in enumerate(studio.project.shots) if s.id==chosen))
    # Inline mixed film uses the central viewer and never creates review receipts.
    receipts=set(getattr(studio,'review_played',set()))
    studio.on_action(None,'sound.film',None)
    wait(lambda:'sound.film' not in studio.pending and bool(studio.preview_media),'Inline mix prepared',timeout=90)
    wait(lambda:studio.player.time()>.1 and studio.frame_count>0,'Inline mix playing')
    check(studio.preview_media.get('inline') and not studio.modal_kind and studio.player.has_audio(),'Mixed film plays in the central native viewer with no modal')
    check(set(getattr(studio,'review_played',set()))==receipts,'Editorial playback creates no production review receipts')
    studio.on_action(None,'transport.play',None)
    check(not studio.preview_media['playing'],'Central transport pauses the existing mix')
    studio.seek(.5);pump(.15)
    check(abs(studio.player.time()-.5)<.15,'Timeline seek addresses the mixed film')
    studio.capture(QA_ROOT/'layout-inline-playback.png')
    enter('film')
    check(studio.preview_media is None and not studio.playing,'Changing editing modes releases inline media ownership')
    r.Rwindow_set_size(studio.window,1440,920);pump(.2)

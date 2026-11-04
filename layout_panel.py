"""Shared native scene outline and contextual inspectors around one player."""
from __future__ import annotations

import raven as r
from layout_model import EDITOR_MODES, outline, panel_sizes
from layout_playback import LayoutPlaybackMixin
from editorial_timing import time_text
import views as v


class LayoutMixin(LayoutPlaybackMixin):
    def initialize_layout(self):
        self.layout_group = ('film', 'film')
        self.layout_collapsed = set()
        self.sound_detail = 'clip'
        self.layout_size = None
        self.layout_sequences = []

    def _free_project(self):
        self.initialize_layout()
        self.replace_host('scene.details','')
        return super()._free_project()

    def sidebar_xml(self):
        if not getattr(self, 'live', False) or not self.board:
            return super().sidebar_xml()
        state = self.sequence_doc['state'] if self.sequence_doc else None
        self.layout_sequences = outline(self.project, self.board, state)
        indices = {shot.id: i for i, shot in enumerate(self.project.shots)}
        body = ''
        for seq in self.layout_sequences:
            if seq['narrative']:
                body += self.outline_heading('sequence', seq['id'], seq['title'], sum(s['frames'] for s in seq['scenes']))
            if ('sequence', seq['id']) in self.layout_collapsed:
                continue
            for scene in seq['scenes']:
                body += self.outline_heading('scene', scene['id'], scene['title'], scene['frames'])
                if ('scene', scene['id']) in self.layout_collapsed:
                    continue
                for sid in scene['shotIds']:
                    i = indices[sid]; shot = self.project.shots[i]; take = shot.take
                    status = 'VIDEO' if take and take.video else 'IMAGE' if take and take.image else 'MISSING IMAGE'
                    image = v.image(take.image, width=44, height=34) if take and take.image else '<Border Background="#242C2F" Width="44" Height="34" CornerRadius="5"><Label Text="—" Class="muted" Slot.HAlign="Center" Slot.VAlign="Center"/></Border>'
                    body += f'<Button Name="shot.{i}" Class="shot {"shot-active" if i==self.index else ""}" Height="62" OnClicked="shot.{i}" Metric.PaddingX="6" Metric.PaddingY="5"><HBox Spacing="8" HitTestable="false">{image}<VBox Slot.Fill="1" Spacing="3"><Label Text="{v.esc(f"{i+1:02}  {shot.title}")}" TextSize="12" Wrap="true" MaxHeight="30"/>{v.label(status+f" · {shot.duration:.1f} s",9,True)}</VBox></HBox></Button>'
        return '<VBox Spacing="5">' + (body or v.label('Add a shot to begin.',12,True)) + '</VBox>'

    def outline_heading(self, kind, ident, title, count):
        key = (kind, ident)
        arrow = '›' if key in self.layout_collapsed else '⌄'
        active = 'nav nav-active' if key == self.layout_group else 'nav'
        return f'<HBox Spacing="2"><Button Text="{arrow}" Width="24" Height="30" Style="Flat" Metric.PaddingX="0" Metric.PaddingY="0" OnClicked="outline.toggle.{kind}.{v.esc(ident)}"/><Button Class="{active}" Slot.Fill="1" Height="34" Metric.PaddingY="3" OnClicked="outline.select.{kind}.{v.esc(ident)}"><VBox HitTestable="false" Spacing="1">{v.label(title,11)}{v.label(time_text(count),9,True)}</VBox></Button></HBox>'

    def render_outline(self):
        if self.board:
            self.replace('shots', self.sidebar_xml())

    def layout_editor(self):
        if not self.live:
            return
        for name in EDITOR_MODES:
            self.set_attr('workspace.nav.'+name,'Class','nav nav-active' if self.mode==name else 'nav')
        for name, visible in {'inspector':self.mode=='film', 'cut.inspector':self.mode=='cut',
                              'scene.inspector':self.mode=='scenes', 'structure.inspector':self.mode=='structure',
                              'sound.details':self.mode=='sound', 'sound.panel':self.mode=='sound',
                              'picture.timeline':self.mode!='sound', 'shot.authoring':self.mode in {'film','scenes'}}.items():
            self.set_attr(name,'Visibility','Visible' if visible else 'Collapsed')
        self.set_attr('nav.film','Class','nav nav-active' if self.mode in EDITOR_MODES else 'nav')
        self.set_attr('sound.cue','Visibility','Visible' if self.sound_detail=='clip' else 'Collapsed')
        self.set_attr('sound.mix','Visibility','Visible' if self.sound_detail=='mixer' else 'Collapsed')
        for name in ('clip','mixer'):
            self.set_attr('sound.tab.'+name,'Class','nav nav-active' if name==self.sound_detail else 'nav')
        width,height=r.Rwindow_get_size(self.window)
        sizes=panel_sizes(width,height,self.mode=='sound')
        if (self.mode,sizes)!=self.layout_size:
            self.layout_size=(self.mode,sizes)
            for name,size in zip(('workspace.sidebar','workspace.details','timeline.panel'),sizes):
                self.set_attr(name,'Height' if name=='timeline.panel' else 'Width',size)
            r.Rlabel_set_text_size(self.w('project.title'),20)

    def render_scene_details(self):
        if not self.board: return
        scene = next((s for seq in self.layout_sequences for s in seq['scenes'] if s['id']==self.layout_group[1]),None)
        if scene is None and self.shot:
            scene = next((s for seq in self.layout_sequences for s in seq['scenes'] if self.shot.id in s['shotIds']),None)
        body=v.label('SCENE OVERVIEW',10,True)
        if scene:
            body += v.label(scene['title'],22,**{'Font':'/System/Library/Fonts/NewYork.ttf'})
            body += v.label(f"{len(scene['shotIds'])} shots · {time_text(scene['frames'])}",12,True)
        if self.shot:
            take=self.shot.take
            body += '<Separator/>'+v.label(self.shot.title,18)
            body += v.label('Take ready' if take and take.video else 'Image plan' if take and take.image else 'Waiting for image',12,True)
            body += v.label(self.shot.action or 'Describe the action in Direction.',13)
            body += v.label(self.shot.camera or 'Camera not described.',12,True)
            body += v.button('Open direction','page.film',True)+v.button('Edit cut','page.cut')
        body += '<Separator/>'+v.button('Sequences and runtime','page.structure')+v.button('Room map and coverage','page.coverage')+v.button('Production and review','page.production')
        self.replace_host('scene.details',body)

    def layout_action(self, action):
        if action.startswith('workspace.sound.'):
            self.sound_detail=action.rsplit('.',1)[1];self.layout_editor();return True
        if action=='outline.root':
            self.layout_group=('film','film')
            if self.mode=='structure':self.render_structure()
            else:self.render_scene_details()
            return True
        if not action.startswith('outline.'):
            return False
        _,verb,kind,ident=action.split('.',3)
        key=(kind,ident)
        if verb=='toggle':
            self.layout_collapsed.symmetric_difference_update({key});self.render_outline()
        elif verb=='select':
            self.layout_group=key
            scenes=[s for seq in self.layout_sequences for s in seq['scenes'] if (seq['id'] if kind=='sequence' else s['id'])==ident]
            ids=[sid for scene in scenes for sid in scene['shotIds']]
            if ids:
                self.select_shot(next(i for i,s in enumerate(self.project.shots) if s.id==ids[0]))
            self.layout_group=key
            self.render_outline()
            if self.mode=='structure':self.render_structure()
            elif self.mode=='scenes':self.render_scene_details()
        return True

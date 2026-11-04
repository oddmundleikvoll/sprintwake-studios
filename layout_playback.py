"""Inline editorial previews using the existing single native AVPlayer."""
import raven as r


class LayoutPlaybackMixin:
    def stop_workspace_preview(self, restore=False):
        if not self.preview_media or not self.preview_media.get('inline'):
            return False
        self.preview_media=None
        self.player.close();self.player.mute(self.muted)
        self.active_video='';self.playing=False
        if restore and self.board:
            self.select_shot(self.index,seek=False)
        return True

    def start_workspace_preview(self,path,title,video):
        # The caller has checked the path and closed the former player source.
        self.preview_media={'path':str(path),'inline':True,'time_media':True,'video':video,'playing':False,'muted':False}
        if not self.player.open(str(path).encode()):
            self.preview_media=None
            raise ValueError('Kunne ikke åpne forhåndsvisningen.')
        self.set_label('viewer.kind','FILM MED LYDMIKS' if video else 'LYDMIKS')
        self.set_label('viewer.caption',title)
        self.set_label('viewer.camera','Hele klippen · lagret miks')
        self.set_label('viewer.source','Forhåndsvisning av lagret klipp og miks')
        self.set_attr('viewer.empty','Visibility','Collapsed')
        self.set_attr('audio','Enabled',True)
        r.Rbutton_set_text(self.w('audio'),'Lyd på')

    def tick_workspace_preview(self):
        pixels,width,height=self.player.read_frame()
        if pixels:
            if self.video_texture==r.R_INVALID_ID:self.video_texture=r.Rtexture_create_from_pixels(pixels,width,height)
            else:r.Rtexture_update(self.video_texture,pixels,width,height)
            r.Rimage_set_texture(self.w('viewer'),self.video_texture);self.frame_count+=1
        current,duration=self.player.time(),self.player.duration()
        self.clock=current
        self.sound_cursor=max(0,round(current*24))
        self.update_clock()
        r.Rtimeline_set_time(self.find('sound.timeline'),current)
        self.set_label('viewer.source',f'Lagret miks · {current:.2f} / {duration:.2f} s')
        if self.player.status()<0:
            self.preview_media['playing']=False
            self.toast(self.player.error().decode(),True)
        if duration and current>=duration-1/24:
            self.player.play(False);self.preview_media['playing']=False
        r.Rbutton_set_text(self.w('play'),'Ⅱ' if self.preview_media['playing'] else '▶')

    def seek(self,seconds):
        if self.preview_media and self.preview_media.get('inline'):
            duration=self.player.duration() or self.project.duration
            self.player.seek(max(0,min(duration,seconds)))
            self.clock=max(0,min(duration,seconds));self.sound_cursor=round(self.clock*24)
            self.update_clock()
            r.Rtimeline_set_time(self.find('sound.timeline'),self.clock)
            return
        return super().seek(seconds)

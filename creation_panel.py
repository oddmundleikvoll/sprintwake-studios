"""Small, explicit image/clip entrances over the existing production APIs."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import quote

import raven as r
import views as v
from workspace import _atomic_text, new_shot


CLIP_FRAMES = (97, 121, 145)
CLIP_SIZES = ('384x256', '512x320', '640x384', '768x512')


def creation_kind(workspace, slug):
    """Only a Raven-owned sidecar selects the default room on explicit reopen."""
    try:
        path = workspace.projects_dir / slug / 'Script/creation.json'
        workspace._assert_managed_path(path)
        document = json.loads(path.read_text())
        if document.get('version') == 1 and document.get('kind') in {'image', 'clip'}:
            return document['kind']
    except (OSError, ValueError, AttributeError):
        pass
    return 'studio'


def prepare_creation(board, kind, prompt, model):
    """Keep the backend's canonical defaults and add just one independent shot."""
    if kind not in {'image', 'clip'}:
        raise ValueError('Choose an image or clip.')
    result = copy.deepcopy(board)
    result.update(sceneDescription=prompt, renderStyle='', soundscape='')
    if kind == 'clip':
        shot = new_shot('Shot 01')
        shot.update(prompt=prompt, dialogueSource='native')
        result['shots'] = [shot]
        result.setdefault('defaults', {})['model'] = model
    return result


class CreationMixin:
    def _free_project(self):
        self.replace_host('clip.content','')
        self.creation_kind = 'studio'
        return super()._free_project()

    def layout_creation(self):
        if self.mode != 'home':
            return
        size = tuple(r.Rwindow_get_size(self.window))
        if size == getattr(self,'creation_layout_size',None):
            return
        self.creation_layout_size = size
        compact = size[1] < 840
        for kind in ('image','clip','studio'):
            self.set_attr('home.art.'+kind,'Height',112 if compact else 160)
        r.Rlabel_set_text_size(self.find('home.heading'),34 if compact else 40)

    def creation_action(self, action):
        if action in {'home.image', 'home.clip'}:
            self.show_creation(action.split('.')[1])
        elif action == 'home.studio':
            self.mode_set('projects')
        elif action == 'home.director':
            self.activate_conversation(self.current_conversation['id'])
        elif action == 'creation.open':
            self.create_work()
        elif action == 'clip.save':
            self.save()
        elif action == 'clip.generate':
            self.generate_clip()
        elif action == 'clip.stop':
            self.workflow_action('video.stop')
        elif action == 'clip.images':
            self.mode_set('images')
        elif action == 'clip.preview':
            if self.shot and self.shot.take and self.shot.take.video:
                self.preview(Path(self.shot.take.video), self.shot.title)
        elif action == 'clip.studio':
            self.mode_set('film')
        else:
            return False
        return True

    def show_creation(self, kind):
        # Capture and save the previous production before opening a new work.
        self.sync_fields()
        if self.dirty:
            self.save(after=lambda: self.show_creation(kind))
            return
        self.creation_mode = kind
        heading = 'Give an image your signature.' if kind == 'image' else 'A moment is a good beginning.'
        detail = 'Open the image studio to find subject, references, format, and style.' if kind == 'image' else 'Open the clip studio to choose action, length, and format.'
        body = v.title('Create an image' if kind == 'image' else 'Create a clip', heading, detail)
        fields = v.field('creation.title', 'Name · optional', height=38, multiline=False, placeholder='New image' if kind == 'image' else 'New clip')
        fields += v.field('creation.prompt', 'Describe the image · optional' if kind == 'image' else 'Describe the action · optional', height=185, placeholder='Subject, composition, colors, and light…' if kind == 'image' else 'Who or what do we see? What moves, and how does the camera follow?')
        fields += v.button('Open image studio' if kind == 'image' else 'Open clip studio', 'creation.open', True, Name='creation.open')
        fields += v.label('You can fill in the description inside the studio.',12,True,Name='creation.status')
        features = [('References that set direction', 'Use up to ten images to shape subject, characters, and style.'), ('Find the right framing', 'Choose landscape, film, portrait, or square.'), ('Keep the look', 'Compare results and save your favorite to Output.')] if kind == 'image' else [('Start with text or an image', 'Write a video description, or create and choose an opening image.'), ('One take at a time', 'Choose length and format. Earlier takes are kept.'), ('Continue whenever you like', 'Watch the take with audio or continue it in Film Studio.')]
        hints = ''.join('<VBox Spacing="9">'+v.label(title,20,Font='/System/Library/Fonts/NewYork.ttf')+v.label(text,13,True)+'</VBox>' for title,text in features)
        body += f'<HBox Spacing="30"><Border Slot.Fill="1" Background="#1D2426" Padding="24" CornerRadius="12"><VBox Spacing="20">{fields}</VBox></Border><Border Width="330" Padding="24" Background="#252523" CornerRadius="12"><VBox Spacing="26">{hints}<Separator/>{v.label("Work is saved separately and can be reopened from Projects.",12,True)}</VBox></Border></HBox>'
        body += v.button('← Back to start', 'page.home')
        self.replace_host('create.content', body)
        self.mode_set('create')
        r.Rwidget_focus(self.find('creation.title'))

    def create_work(self):
        if not self.ready:
            raise ValueError('The studio is still starting. Try again in a moment.')
        if self.opening or 'creation' in self.pending:
            return
        name, prompt = self.text('creation.title').strip(), self.text('creation.prompt').strip()
        kind = self.creation_mode
        name = name or (prompt.splitlines()[0][:60] if prompt else 'New image' if kind == 'image' else 'New clip')
        model = self.connection_settings['selected']['video']
        def operation():
            created = self.backend.post('/api/boards', {'name': name})
            slug = created['slug']
            board = prepare_creation(created['board'], kind, prompt, model)
            saved = self.backend.put('/api/boards/'+quote(slug), board).get('board', board)
            project = self.workspace.ensure_project(slug)
            self.workspace.sync_script(slug, saved)
            _atomic_text(project/'Script/creation.json', json.dumps({'version':1, 'kind':kind})+'\n')
            return slug, self.backend.get('/api/boards')
        def done(result):
            slug, listing = result
            self.summaries = listing['boards']
            if kind == 'image':
                self.drafts[slug] = {'imagePrompt': prompt, 'imageAspect': 0, 'references': []}
            self.project_destination = 'images' if kind == 'image' else 'clip'
            self.open_project(slug)
        self.submit('creation', operation, done, scoped=False)
        self.set_attr('creation.open','Enabled',False)
        self.toast('Opening image studio…' if kind == 'image' else 'Opening clip studio…')

    def render_clip(self):
        shot = self.raw_shot
        if not shot:
            return
        fields = v.field('quickclip.prompt', 'Video description', shot.get('prompt',''), height=170, placeholder='Describe subject, movement, camera, and sound…')
        external = self.clip_profile()
        if external:
            fields += v.label(external['label'],16)+v.label('Length and format are chosen before each new workflow take.',12,True)
        else:
            fields += '<Label Text="LENGTH · 24 FPS" Class="eyebrow"/><ComboBox Name="quickclip.duration" Items="4 seconds|5 seconds|6 seconds"/>'
            fields += '<Label Text="FORMAT" Class="eyebrow"/><ComboBox Name="quickclip.size" Items="384 × 256|512 × 320|640 × 384|768 × 512"/>'
        fields += v.button('Create new take', 'clip.generate', True, Name='quickclip.generate')
        fields += v.button('Stop my take', 'clip.stop', Name='quickclip.stop', Enabled='false')
        fields += v.button('Save description', 'clip.save')
        fields += v.label('New takes are kept in take history. Choose which one to use.',12,True)
        take = self.shot.take if self.shot else None
        poster = take.image if take else ''
        preview = f'<Border Background="#0C1113" CornerRadius="10" Height="320"><Overlay>{v.image(poster,height=320,fit=True) if poster else v.label("A moment is waiting to come alive.",24,Font="/System/Library/Fonts/NewYork.ttf",**{"Slot.HAlign":"Center","Slot.VAlign":"Center","MaxWidth":"330"})}</Overlay></Border>'
        actions = v.button('View selected take', 'clip.preview', Name='quickclip.preview', Enabled='true' if take and take.video else 'false')+v.button('Take history', 'take.history')
        actions += v.button('Create / choose opening image', 'clip.images')
        body = v.title('Clip Studio / '+self.board.get('name',self.slug), 'Bring the moment to life.', 'Create one self-contained take, review it, and try a new take whenever you like.')
        body += f'<HBox Spacing="22"><Border Width="330" Background="#1D2426" Padding="22" CornerRadius="11"><VBox Spacing="15">{fields}</VBox></Border><VBox Slot.Fill="1" Spacing="16">{preview}<WrapBox Spacing="8">{actions}</WrapBox><Label Name="quickclip.status" Text="Ready for a new take." Class="muted" Wrap="true"/>{v.button("Continue in Film Studio →","clip.studio")}</VBox></HBox>'
        self.replace_host('clip.content',body)
        if not external:
            frames, size = shot.get('frames',121),shot.get('resolution','384x256')
            # Custom parameters remain untouched until explicitly changed elsewhere.
            frames_items = list(CLIP_FRAMES) if frames in CLIP_FRAMES else [frames,*CLIP_FRAMES]
            sizes = list(CLIP_SIZES) if size in CLIP_SIZES else [size,*CLIP_SIZES]
            self.clip_frames, self.clip_sizes = frames_items, sizes
            r.Rcombo_box_clear_items(self.find('quickclip.duration'))
            for value in frames_items:
                r.Rcombo_box_add_item(self.find('quickclip.duration'),f'{value/24:.2f} seconds')
            r.Rcombo_box_clear_items(self.find('quickclip.size'))
            for value in sizes: r.Rcombo_box_add_item(self.find('quickclip.size'),value.replace('x',' × '))
            r.Rcombo_box_set_selected(self.find('quickclip.duration'),frames_items.index(frames))
            r.Rcombo_box_set_selected(self.find('quickclip.size'),sizes.index(size))

    def clip_profile(self):
        selected = self.connection_settings['selected']['video']
        return next((p for p in self.connection_settings['workflowProfiles'] if p['kind']=='video' and p['id']==selected),None)

    def sync_clip(self):
        if not self.raw_shot or self.find('quickclip.prompt') == r.R_INVALID_ID:
            return
        shot = self.raw_shot
        values = {'prompt':self.text('quickclip.prompt')}
        if not self.clip_profile():
            values.update(frames=self.clip_frames[max(0,self.selected('quickclip.duration'))],resolution=self.clip_sizes[max(0,self.selected('quickclip.size'))])
        if any(shot.get(key) != value for key,value in values.items()):
            shot.update(values)
            self.refresh_view(populate=False)
            self.set_dirty(True)

    def generate_clip(self):
        self.sync_clip()
        if not self.ready or not self.raw_shot or not self.raw_shot.get('prompt','').strip():
            raise ValueError('Describe the clip first, and wait until the studio is ready.')
        if self._busy() or any(k in self.pending for k in ('clip.generate','video.generate')):
            raise ValueError('Wait until the active job is finished.')
        if self.clip_profile():
            self.workflow_action('video.generate')
            return
        if self.raw_shot.get('modelProvider') == 'comfy-workflow':
            raise ValueError('The clip uses a workflow model. Choose the same video profile under Models before a new take.')
        slug, sid = self.slug,self.raw_shot['id']
        def launch():
            self.submit('clip.generate',lambda:self.backend.post('/api/render-seeds',{'slug':slug,'shotId':sid,'count':1}),lambda _:self.toast('A new take is queued. Open take history when it is finished.'))
            self.poll_deadline=0
        self.save(after=launch)

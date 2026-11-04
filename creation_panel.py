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
        raise ValueError('Velg bilde eller klipp.')
    result = copy.deepcopy(board)
    result.update(sceneDescription=prompt, renderStyle='', soundscape='')
    if kind == 'clip':
        shot = new_shot('Klipp 01')
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
        heading = 'Gi et bilde din signatur.' if kind == 'image' else 'Et øyeblikk er en god begynnelse.'
        detail = 'Åpne bildestudioet og finn motiv, referanser, format og uttrykk.' if kind == 'image' else 'Åpne klippstudioet og velg handling, lengde og format.'
        body = v.title('Generer et bilde' if kind == 'image' else 'Lag et klipp', heading, detail)
        fields = v.field('creation.title', 'Navn · valgfritt', height=38, multiline=False, placeholder='Nytt bilde' if kind == 'image' else 'Nytt klipp')
        fields += v.field('creation.prompt', 'Beskriv bildet · valgfritt' if kind == 'image' else 'Beskriv handlingen · valgfritt', height=185, placeholder='Motiv, komposisjon, farger og lys…' if kind == 'image' else 'Hvem eller hva ser vi? Hva beveger seg, og hvordan følger kameraet med?')
        fields += v.button('Åpne bildestudioet' if kind == 'image' else 'Åpne klippstudioet', 'creation.open', True, Name='creation.open')
        fields += v.label('Du kan fylle ut beskrivelsen inne i studioet.',12,True,Name='creation.status')
        features = [('Referanser som gir retning', 'Bruk opptil ti bilder for å forme motiv, figurer og stil.'), ('Finn riktig utsnitt', 'Velg landskap, filmformat, portrett eller kvadrat.'), ('Ta vare på uttrykket', 'Sammenlign resultater og lagre favoritten til Output.')] if kind == 'image' else [('Begynn med tekst eller bilde', 'Skriv en videobeskrivelse, eller lag og velg et åpningsbilde.'), ('Ett opptak om gangen', 'Velg lengde og format. Tidligere takes blir beholdt.'), ('Bygg videre når du vil', 'Se opptaket med lyd eller ta det videre til Filmstudio.')]
        hints = ''.join('<VBox Spacing="9">'+v.label(title,20,Font='/System/Library/Fonts/NewYork.ttf')+v.label(text,13,True)+'</VBox>' for title,text in features)
        body += f'<HBox Spacing="30"><Border Slot.Fill="1" Background="#1D2426" Padding="24" CornerRadius="12"><VBox Spacing="20">{fields}</VBox></Border><Border Width="330" Padding="24" Background="#252523" CornerRadius="12"><VBox Spacing="26">{hints}<Separator/>{v.label("Arbeidet lagres for seg og kan åpnes igjen fra Prosjekter.",12,True)}</VBox></Border></HBox>'
        body += v.button('← Tilbake til start', 'page.home')
        self.replace_host('create.content', body)
        self.mode_set('create')
        r.Rwidget_focus(self.find('creation.title'))

    def create_work(self):
        if not self.ready:
            raise ValueError('Studioet starter fortsatt. Prøv igjen om et øyeblikk.')
        if self.opening or 'creation' in self.pending:
            return
        name, prompt = self.text('creation.title').strip(), self.text('creation.prompt').strip()
        kind = self.creation_mode
        name = name or (prompt.splitlines()[0][:60] if prompt else 'Nytt bilde' if kind == 'image' else 'Nytt klipp')
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
        self.toast('Åpner bildestudioet…' if kind == 'image' else 'Åpner klippstudioet…')

    def render_clip(self):
        shot = self.raw_shot
        if not shot:
            return
        fields = v.field('quickclip.prompt', 'Videobeskrivelse', shot.get('prompt',''), height=170, placeholder='Beskriv motiv, bevegelse, kamera og lyd…')
        external = self.clip_profile()
        if external:
            fields += v.label(external['label'],16)+v.label('Lengde og format velges før hvert nye workflow-opptak.',12,True)
        else:
            fields += '<Label Text="LENGDE · 24 FPS" Class="eyebrow"/><ComboBox Name="quickclip.duration" Items="4 sekunder|5 sekunder|6 sekunder"/>'
            fields += '<Label Text="FORMAT" Class="eyebrow"/><ComboBox Name="quickclip.size" Items="384 × 256|512 × 320|640 × 384|768 × 512"/>'
        fields += v.button('Lag nytt take', 'clip.generate', True, Name='quickclip.generate')
        fields += v.button('Stopp eget opptak', 'clip.stop', Name='quickclip.stop', Enabled='false')
        fields += v.button('Lagre beskrivelsen', 'clip.save')
        fields += v.label('Nye takes beholdes i opptakshistorikken. Velg selv hvilket du vil bruke.',12,True)
        take = self.shot.take if self.shot else None
        poster = take.image if take else ''
        preview = f'<Border Background="#0C1113" CornerRadius="10" Height="320"><Overlay>{v.image(poster,height=320,fit=True) if poster else v.label("Et øyeblikk venter på å bli levende.",24,Font="/System/Library/Fonts/NewYork.ttf",**{"Slot.HAlign":"Center","Slot.VAlign":"Center","MaxWidth":"330"})}</Overlay></Border>'
        actions = v.button('Se valgt opptak', 'clip.preview', Name='quickclip.preview', Enabled='true' if take and take.video else 'false')+v.button('Opptakshistorikk', 'take.history')
        actions += v.button('Lag / velg åpningsbilde', 'clip.images')
        body = v.title('Klippstudio / '+self.board.get('name',self.slug), 'Gi øyeblikket liv.', 'Lag ett selvstendig opptak, vurder resultatet og prøv et nytt take når du vil.')
        body += f'<HBox Spacing="22"><Border Width="330" Background="#1D2426" Padding="22" CornerRadius="11"><VBox Spacing="15">{fields}</VBox></Border><VBox Slot.Fill="1" Spacing="16">{preview}<WrapBox Spacing="8">{actions}</WrapBox><Label Name="quickclip.status" Text="Klar til et nytt opptak." Class="muted" Wrap="true"/>{v.button("Fortsett i Filmstudio →","clip.studio")}</VBox></HBox>'
        self.replace_host('clip.content',body)
        if not external:
            frames, size = shot.get('frames',121),shot.get('resolution','384x256')
            # Custom parameters remain untouched until explicitly changed elsewhere.
            frames_items = list(CLIP_FRAMES) if frames in CLIP_FRAMES else [frames,*CLIP_FRAMES]
            sizes = list(CLIP_SIZES) if size in CLIP_SIZES else [size,*CLIP_SIZES]
            self.clip_frames, self.clip_sizes = frames_items, sizes
            r.Rcombo_box_clear_items(self.find('quickclip.duration'))
            for value in frames_items:
                r.Rcombo_box_add_item(self.find('quickclip.duration'),f'{value/24:.2f} sekunder')
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
            raise ValueError('Beskriv klippet først, og vent til studioet er klart.')
        if self._busy() or any(k in self.pending for k in ('clip.generate','video.generate')):
            raise ValueError('Vent til den aktive jobben er ferdig.')
        if self.clip_profile():
            self.workflow_action('video.generate')
            return
        if self.raw_shot.get('modelProvider') == 'comfy-workflow':
            raise ValueError('Klippet bruker en workflowmodell. Velg den samme videoprofilen under Modeller før et nytt take.')
        slug, sid = self.slug,self.raw_shot['id']
        def launch():
            self.submit('clip.generate',lambda:self.backend.post('/api/render-seeds',{'slug':slug,'shotId':sid,'count':1}),lambda _:self.toast('Ett nytt take er i kø. Åpne opptakshistorikken når det er ferdig.'))
            self.poll_deadline=0
        self.save(after=launch)

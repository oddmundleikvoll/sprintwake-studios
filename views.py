"""Native Raven presentation fragments. No browser or generated screenshot UI."""
from html import escape


def esc(value):
    return escape(str(value), quote=True)


def label(text, size=13, muted=False, **attrs):
    attrs = " ".join(f'{k}="{esc(v)}"' for k, v in attrs.items())
    return f'<Label Text="{esc(text)}" TextSize="{size}" Wrap="true" {"Class=\"muted\"" if muted else ""} {attrs}/>'


def button(text, action, primary=False, **attrs):
    attrs = " ".join(f'{k}="{esc(v)}"' for k, v in attrs.items())
    return f'<Button Text="{esc(text)}" OnClicked="{esc(action)}" Style="{"Primary" if primary else "Default"}" {attrs}/>'


def title(eyebrow, heading, detail=""):
    return f'<VBox Spacing="9"><Label Text="{esc(eyebrow.upper())}" Class="eyebrow"/><Label Text="{esc(heading)}" TextSize="34" Font="/System/Library/Fonts/NewYork.ttf" Wrap="true"/>{label(detail, 13, True) if detail else ""}</VBox>'


def field(name, heading, text="", height=80, placeholder="", multiline=True):
    tag = "TextEditor" if multiline else "TextInput"
    extra = 'Wrap="true" LineNumbers="false" Font="/System/Library/Fonts/SFNS.ttf"' if multiline else ""
    return f'<VBox Spacing="8"><Label Text="{esc(heading.upper())}" Class="eyebrow"/><{tag} Name="{esc(name)}" Text="{esc(text)}" Height="{height}" Placeholder="{esc(placeholder)}" {extra}/></VBox>'


def image(path, width=None, height=160, fit=False):
    width_attr = f'Width="{width}"' if width else ""
    if path:
        return f'<Image Source="{esc(path)}" Stretch="{"Fit" if fit else "Cover"}" Height="{height}" CornerRadius="7" {width_attr}/>'
    return f'<Border Background="#242C2F" CornerRadius="7" Height="{height}" {width_attr}><Label Text="A new moment" Class="muted" Slot.HAlign="Center" Slot.VAlign="Center"/></Border>'


def page(name, content):
    return f'<ScrollBox Name="page.{name}" Visibility="Collapsed"><VBox Name="{name}.host" Spacing="22" Slot.Padding="36,28">{content}</VBox></ScrollBox>'


def creation_card(kind, number, heading, detail, features, color, artwork):
    return f'''<Button Name="home.{kind}" OnClicked="home.{kind}" Class="creation-card" Slot.Fill="1" Metric.PaddingX="20" Metric.PaddingY="20">
      <VBox Spacing="18" HitTestable="false">
        <Border Name="home.art.{kind}" Height="160" Background="{color}" CornerRadius="9" Clip="true">{artwork}</Border>
        <HBox><Label Text="{number}" Class="eyebrow" Slot.Fill="1"/><Label Text="↗" Color="#EBA477" TextSize="20"/></HBox>
        <Label Text="{heading}" Font="/System/Library/Fonts/NewYork.ttf" TextSize="28" Wrap="true"/>
        {label(detail,13,True,Height="48")}
        <Separator/>
        <VBox Spacing="10">{''.join(label('·  '+feature,12) for feature in features)}</VBox>
        <Label Text="{'Open film studio' if kind == 'studio' else 'Get started'}  →" Color="#EBA477" TextSize="13" Slot.Padding="0,4,0,0"/>
      </VBox></Button>'''


def start_page():
    # These are native widget illustrations, so the cards remain crisp at Retina sizes.
    picture = '''<Overlay><Border Background="#A57553" Width="72" Height="72" CornerRadius="36" Slot.HAlign="Right" Slot.VAlign="Top" Slot.Padding="24,18"/>
      <Border Background="#B28C6D" Width="206" Height="108" CornerRadius="8" Slot.HAlign="Center" Slot.VAlign="Center" Padding="7"><Border Background="#303C3C" CornerRadius="4"><Overlay><Border Background="#D5B28B" Width="30" Height="30" CornerRadius="15" Slot.HAlign="Right" Slot.Padding="18"/><Border Background="#52635C" Height="42" CornerRadius="20" Slot.VAlign="Bottom" Slot.Padding="10"/><Border Background="#819083" Width="100" Height="30" CornerRadius="15" Slot.HAlign="Left" Slot.VAlign="Bottom" Slot.Padding="18,0,0,18"/></Overlay></Border></Border></Overlay>'''
    clip = '''<Overlay><Border Background="#485454" Width="205" Height="112" CornerRadius="9" Slot.HAlign="Center" Slot.VAlign="Center" Padding="8"><Border Background="#192426" CornerRadius="5"><Overlay><Border Background="#74877B" Width="54" Height="54" CornerRadius="27" Slot.HAlign="Center" Slot.VAlign="Center"><Label Text="▶" TextSize="22" Color="#EAE2D3" Slot.HAlign="Center" Slot.VAlign="Center"/></Border><HBox Spacing="5" Slot.VAlign="Bottom" Slot.Padding="10"><Border Height="3" Background="#D7B794" Slot.Fill="2"/><Border Height="3" Background="#4E6060" Slot.Fill="1"/></HBox></Overlay></Border></Border></Overlay>'''
    studio = '''<Border Width="218" Height="128" Background="#3C3441" CornerRadius="8" Padding="9" Slot.HAlign="Center" Slot.VAlign="Center"><VBox Spacing="7"><HBox Spacing="7" Slot.Fill="1"><Border Background="#73616C" Width="32" CornerRadius="3"/><Border Background="#201D27" Slot.Fill="1" CornerRadius="4"><Label Text="S" Font="/System/Library/Fonts/NewYork.ttf" Color="#CCA683" TextSize="42" Slot.HAlign="Center" Slot.VAlign="Center"/></Border></HBox><HBox Spacing="4" Height="13"><Border Background="#B48A70" Slot.Fill="2" CornerRadius="2"/><Border Background="#8D9C88" Slot.Fill="3" CornerRadius="2"/><Border Background="#927386" Slot.Fill="2" CornerRadius="2"/></HBox><Border Background="#67776E" Height="5" CornerRadius="2"/></VBox></Border>'''
    cards = creation_card('image','01 / IMAGE','Generate an image','From a description to an image worth keeping.',
        ['Visual references','Four image formats','Gallery, comparison, and export'],'#302B27',picture)
    cards += creation_card('clip','02 / MOTION','Create a clip','Bring a moment to life. One take at a time.',
        ['Text or an opening image','Choose length and format','New takes with audio playback'],'#263235',clip)
    cards += creation_card('studio','03 / FILM','Film studio','Build a complete story, from first idea to final cut.',
        ['Director, script, and film bible','Scenes, room maps, and production','Editing, soundtracks, and export'],'#302934',studio)
    return page('home',f'''<HBox><VBox Spacing="10"><Label Text="SPRINTWAKE STUDIOS" Class="eyebrow"/>
      <Label Name="home.heading" Text="What do you want to create today?" TextSize="40" Font="/System/Library/Fonts/NewYork.ttf" Wrap="true"/>
      {label('An image. A moment in motion. Or a whole film.',14,True)}</VBox><Spacer Slot.Fill="1"/>
      {button('Your library','mode.library',**{'Slot.VAlign':'Center'})}</HBox>
      <HBox Name="home.choices" Spacing="18">{cards}</HBox>
      <HBox><Label Text="CONTINUE WHERE YOU LEFT OFF" Class="eyebrow" Slot.Fill="1"/>{button('All projects →','page.projects')}</HBox>
      <VBox Name="home.recent" Spacing="8"/>
      <HBox><Label Name="home.connection" Text="Connecting to studio…" Class="muted" Slot.Fill="1"/>{button('Models and connections','connections.show')}</HBox>''')


def pages():
    return [
        ("projects", page("projects", f'''<HBox Spacing="20">{title("Film studio / Projects", "Stories begin here.", "Open a project or start a new story.")}<Spacer Slot.Fill="1"/>{button("+ New project", "project.new", True, **{"Slot.VAlign":"Center"})}</HBox>
          <Border Material="TintedGlass" Background="#232A29" CornerRadius="12" Padding="26" BorderColor="#4B5145" BorderWidth="1"><HBox Spacing="28"><VBox Slot.Fill="1" Spacing="12"><Label Text="CREATE WITH CARE" Class="eyebrow"/><Label Text="A clear idea. A living image.\nA story that holds together." TextSize="29" Font="/System/Library/Fonts/NewYork.ttf" Wrap="true"/>{label("Give characters an identity, find the scene's image, and take one shot at a time.", 13, True)}<HBox Spacing="10">{button("Start with the director", "home.director", True)}{button("Import a project", "project.import")}</HBox></VBox><Border Background="#C59168" CornerRadius="9" Width="154" Height="140"><VBox Spacing="12" Slot.VAlign="Center" Slot.HAlign="Center"><Label Text="S" TextSize="65" Font="/System/Library/Fonts/NewYork.ttf" Color="#27241C"/><Label Text="SPRINTWAKE STUDIOS" TextSize="10" Color="#27241C"/></VBox></Border></HBox></Border>
          <HBox><Label Text="YOUR PROJECTS" Class="eyebrow" Slot.Fill="1"/>{button("Trash", "project.trash")}{button("Open production folder", "folder.root")}</HBox><VBox Name="home.cards" Spacing="16"/>
          <HBox>{button("Back to start", "page.home")}{button("Settings", "settings.show")}</HBox>''')),
        ("brief", page("brief", f'''{title("01 / Idea and script", "Give the story direction.", "Describe what we see, what happens, and how the film should feel.")}
          <HBox Spacing="10">{button("Film idea and style", "brief.idea")}{button("Script and clips", "brief.script", True)}<Label Name="brief.script.summary" Text="" Class="muted" Slot.VAlign="Center"/></HBox>
          <HBox Name="brief.form" Spacing="26"><VBox Slot.Fill="1" Spacing="18">{field("brief.description", "Film idea and world", height=200, placeholder="What is the film about? Where are we, and what changes?")}{field("brief.style", "Visual style", height=90, placeholder="Materials, light, colors, and camera feel")}{field("brief.sound", "Sound and mood", height=75, placeholder="Ambient sound, music, and dialogue language")}<HBox Spacing="10">{button("Save script", "file.save", True)}{button("Create script and clips", "director.author.show")}{button("Discuss the film idea", "director.plan")}{button("Add a clip yourself", "shot.add")}</HBox></VBox>
          <Border Width="300" Background="#1C2425" CornerRadius="10" Padding="22"><VBox Spacing="18">{label("A GOOD STARTING POINT", 10, True)}{label("What do you want us to experience?", 23, **{"Font":"/System/Library/Fonts/NewYork.ttf"})}{label("Give the story a concrete action. Then let references and scene images make the idea visible.",13,True)}<Separator/>{label("The script follows the project",16)}{label("A readable script is saved in the Script folder with the project. Export the full production whenever you like.",12,True)}{button("Open script folder", "folder.script")}</VBox></Border></HBox><VBox Name="brief.manuscript" Visibility="Collapsed" Spacing="18"><VBox Name="brief.script.cards" Spacing="16"/></VBox>''')),
        ("images", page("images", f'''{title("03 / Images", "Find the scene’s look.", "Create images from a description, or build on selected references.")}
          <HBox Spacing="22"><Border Width="300" Padding="20" Background="#1D2426" CornerRadius="11"><VBox Spacing="16">{field("image.prompt", "Describe the image", height=170, placeholder="Subject, setting, action, framing, and light…")}<Label Text="REFERENCES" Class="eyebrow"/><VBox Name="image.references" Spacing="7"/><HBox Spacing="7">{button("Choose references", "image.references")}{button("Import", "media.import")}</HBox><Label Text="FORMAT" Class="eyebrow"/><ComboBox Name="image.aspect" Items="Landscape · 3:2|Film · 16:9|Portrait · 2:3|Square · 1:1"/><Label Text="SEED (OPTIONAL)" Class="eyebrow"/><TextInput Name="image.seed" Placeholder="Random"/><Button Name="image.generate" Text="Create image" Style="Primary" OnClicked="image.generate"/><Button Name="image.stop" Text="Stop this image job" Style="Flat" OnClicked="image.stop"/><Label Name="image.status" Text="Ready to create something new." Class="muted" Wrap="true"/></VBox></Border>
          <VBox Slot.Fill="1" Spacing="14"><Border Background="#0C1113" CornerRadius="11" BorderColor="#3A4346" BorderWidth="1" Height="390"><Overlay><Image Name="image.preview" Stretch="Fit"/><VBox Name="image.empty" Spacing="14" Slot.HAlign="Center" Slot.VAlign="Center"><Label Text="A new moment is waiting." TextSize="28" Font="/System/Library/Fonts/NewYork.ttf"/><Label Text="Describe the scene. Choose references. Find the look." Class="muted"/><Label Name="image.engine" Text="SELECTED IMAGE MODEL" Class="eyebrow"/></VBox></Overlay></Border><HBox Spacing="9">{button("Use as clip opening", "image.assign", True, Name="image.assign")}{button("Save to Output", "image.publish", Name="image.publish")}{button("Show file", "image.reveal", Name="image.reveal")}{button("Compare", "image.compare")}</HBox><Label Name="image.description" Text="Choose a result to see the image and recipe." Class="muted" Wrap="true"/><WrapBox Name="image.gallery" Spacing="12"/></VBox></HBox>''')),
        ("production", page("production", f'''{title("05 / Production and review", "One finished take at a time.", "Review the image plan before video. Review the actual take before the story continues.")}
          <Border Material="TintedGlass" Background="#202B28" CornerRadius="11" Padding="23"><VBox Spacing="12"><HBox><Label Name="production.phase" Text="Ready to prepare" TextSize="23" Font="/System/Library/Fonts/NewYork.ttf" Slot.Fill="1"/>{button("Refresh", "production.refresh")}</HBox><Label Name="production.detail" Text="Complete the script and clips before creating the image plan." Wrap="true" Class="muted"/><Label Name="production.scope" Text="" Class="muted" Wrap="true"/><ProgressBar Name="production.progress" Value="0"/><HBox Spacing="9">{button("Create / update image plan", "production.prepare", True, Name="production.prepare")}{button("Continue production", "production.resume", Name="production.resume")}{button("Stop my film job", "production.stop", Name="production.stop")}</HBox></VBox></Border>
          <VBox Name="production.gate" Spacing="16"/><VBox Name="production.clips" Spacing="10"/>
          <Border Background="#1B2225" Padding="22" CornerRadius="11"><VBox Spacing="14"><Label Text="DELIVERY" TextSize="23" Font="/System/Library/Fonts/NewYork.ttf"/><Label Text="Once the takes are reviewed, assemble the film and export the project." Class="muted" Wrap="true"/><HBox Spacing="9">{button("Assemble film", "production.assemble", True, Name="production.assemble")}{button("Play finished film", "production.play_final", Name="production.play_final")}{button("Export project", "project.export")}{button("Show Output", "folder.output")}</HBox></VBox></Border>''')),
        ("director", f'''<HBox Name="page.director" Visibility="Collapsed" Spacing="0">
          <Border Width="248" Background="#1C2427" Padding="18"><VBox Spacing="16">
            <Label Text="DIRECTOR’S ROOM" Class="eyebrow"/>
            <Label Text="Conversations" Font="/System/Library/Fonts/NewYork.ttf" TextSize="27"/>
            <Button Text="＋ New conversation" Style="Primary" OnClicked="conversation.new"/>
            <Button Text="Start with a free idea" Style="Flat" OnClicked="conversation.general"/>
            <Label Text="CONVERSATION PROJECT" Class="eyebrow"/>
            <ComboBox Name="conversation.project" OnChanged="conversation.project"/>
            <Separator/>
            <ScrollBox Slot.Fill="1"><VBox Name="conversation.list" Spacing="8"/></ScrollBox>
          </VBox></Border>
          <Border Slot.Fill="1" Padding="26" Background="#171D20"><VBox Spacing="18">
            <HBox Spacing="12"><VBox Slot.Fill="1" Spacing="7"><Label Name="director.title" Text="New conversation" Font="/System/Library/Fonts/NewYork.ttf" TextSize="28"/><Label Name="director.context" Text="One idea is enough." Class="muted" Wrap="true"/></VBox>{button("Rename", "conversation.rename")}{button("Archive", "conversation.archive")}</HBox>
            <HBox Spacing="10">{button("Create script and clips", "director.author.show", Name="director.author.button")}{button("Read the script", "director.open_script", Name="director.read.button")}</HBox>
            <ScrollBox Name="director.messages" Slot.Fill="1"><VBox Name="director.history" Spacing="15"/></ScrollBox>
            <Border Background="#20282B" CornerRadius="12" Padding="18"><VBox Spacing="12">{field("director.message", "Your message", height=96, placeholder="Tell us what you want to create. The director helps with the next step…")}<HBox Spacing="12"><Button Name="director.send" Text="Send to director" Style="Primary" OnClicked="director.send"/><ComboBox Name="director.thinking" Items="Quick review|Think a little more|Thorough" Width="165"/><Label Name="director.status" Text="Ready when you are." Class="muted" Wrap="true" Slot.Fill="1" Slot.VAlign="Center"/></HBox></VBox></Border>
          </VBox></Border></HBox>'''),
        ("overview", page("overview", f'''{title("Your project", "From idea to finished film.")}<VBox Name="overview.content" Spacing="22"/>''')),
    ]


def modal(heading, body, action=None, action_label="Save", detail="", allow_close=True):
    footer = button("Close", "modal.close") if allow_close else ""
    if action:
        footer += button(action_label, action, True)
    return f'<VBox Spacing="17">{title("Sprintwake Studios", heading, detail)}<ScrollBox MaxHeight="540"><VBox Spacing="15">{body}</VBox></ScrollBox><Separator/><Label Name="modal.error" Text="" Color="#EFA1A1" Wrap="true"/><HBox Spacing="10"><Spacer Slot.Fill="1"/>{footer}</HBox></VBox>'

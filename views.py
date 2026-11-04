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
    return f'<Border Background="#242C2F" CornerRadius="7" Height="{height}" {width_attr}><Label Text="Et nytt øyeblikk" Class="muted" Slot.HAlign="Center" Slot.VAlign="Center"/></Border>'


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
        <Label Text="{'Åpne filmstudioet' if kind == 'studio' else 'Kom i gang'}  →" Color="#EBA477" TextSize="13" Slot.Padding="0,4,0,0"/>
      </VBox></Button>'''


def start_page():
    # These are native widget illustrations, so the cards remain crisp at Retina sizes.
    picture = '''<Overlay><Border Background="#A57553" Width="72" Height="72" CornerRadius="36" Slot.HAlign="Right" Slot.VAlign="Top" Slot.Padding="24,18"/>
      <Border Background="#B28C6D" Width="206" Height="108" CornerRadius="8" Slot.HAlign="Center" Slot.VAlign="Center" Padding="7"><Border Background="#303C3C" CornerRadius="4"><Overlay><Border Background="#D5B28B" Width="30" Height="30" CornerRadius="15" Slot.HAlign="Right" Slot.Padding="18"/><Border Background="#52635C" Height="42" CornerRadius="20" Slot.VAlign="Bottom" Slot.Padding="10"/><Border Background="#819083" Width="100" Height="30" CornerRadius="15" Slot.HAlign="Left" Slot.VAlign="Bottom" Slot.Padding="18,0,0,18"/></Overlay></Border></Border></Overlay>'''
    clip = '''<Overlay><Border Background="#485454" Width="205" Height="112" CornerRadius="9" Slot.HAlign="Center" Slot.VAlign="Center" Padding="8"><Border Background="#192426" CornerRadius="5"><Overlay><Border Background="#74877B" Width="54" Height="54" CornerRadius="27" Slot.HAlign="Center" Slot.VAlign="Center"><Label Text="▶" TextSize="22" Color="#EAE2D3" Slot.HAlign="Center" Slot.VAlign="Center"/></Border><HBox Spacing="5" Slot.VAlign="Bottom" Slot.Padding="10"><Border Height="3" Background="#D7B794" Slot.Fill="2"/><Border Height="3" Background="#4E6060" Slot.Fill="1"/></HBox></Overlay></Border></Border></Overlay>'''
    studio = '''<Border Width="218" Height="128" Background="#3C3441" CornerRadius="8" Padding="9" Slot.HAlign="Center" Slot.VAlign="Center"><VBox Spacing="7"><HBox Spacing="7" Slot.Fill="1"><Border Background="#73616C" Width="32" CornerRadius="3"/><Border Background="#201D27" Slot.Fill="1" CornerRadius="4"><Label Text="S" Font="/System/Library/Fonts/NewYork.ttf" Color="#CCA683" TextSize="42" Slot.HAlign="Center" Slot.VAlign="Center"/></Border></HBox><HBox Spacing="4" Height="13"><Border Background="#B48A70" Slot.Fill="2" CornerRadius="2"/><Border Background="#8D9C88" Slot.Fill="3" CornerRadius="2"/><Border Background="#927386" Slot.Fill="2" CornerRadius="2"/></HBox><Border Background="#67776E" Height="5" CornerRadius="2"/></VBox></Border>'''
    cards = creation_card('image','01 / BILDE','Generer et bilde','Fra en beskrivelse til et uttrykk du vil ta vare på.',
        ['Visuelle referanser','Fire bildeformater','Galleri, sammenligning og eksport'],'#302B27',picture)
    cards += creation_card('clip','02 / BEVEGELSE','Lag et klipp','Gi et øyeblikk liv. Ett opptak om gangen.',
        ['Tekst eller et åpningsbilde','Velg lengde og format','Nye takes og avspilling med lyd'],'#263235',clip)
    cards += creation_card('studio','03 / FILM','Filmstudio','Bygg en hel historie, fra første idé til siste klipp.',
        ['Regissør, manus og filmbibel','Scener, romkart og produksjon','Klipping, lydspor og eksport'],'#302934',studio)
    return page('home',f'''<HBox><VBox Spacing="10"><Label Text="SPRINTWAKE STUDIOS" Class="eyebrow"/>
      <Label Name="home.heading" Text="Hva vil du skape i dag?" TextSize="40" Font="/System/Library/Fonts/NewYork.ttf" Wrap="true"/>
      {label('Et bilde. Et øyeblikk i bevegelse. Eller en hel film.',14,True)}</VBox><Spacer Slot.Fill="1"/>
      {button('Biblioteket ditt','mode.library',**{'Slot.VAlign':'Center'})}</HBox>
      <HBox Name="home.choices" Spacing="18">{cards}</HBox>
      <HBox><Label Text="FORTSETT DER DU SLAPP" Class="eyebrow" Slot.Fill="1"/>{button('Alle prosjekter →','page.projects')}</HBox>
      <VBox Name="home.recent" Spacing="8"/>
      <HBox><Label Name="home.connection" Text="Kobler til studioet…" Class="muted" Slot.Fill="1"/>{button('Modeller og tilkoblinger','connections.show')}</HBox>''')


def pages():
    return [
        ("projects", page("projects", f'''<HBox Spacing="20">{title("Filmstudio / Prosjekter", "Historiene begynner her.", "Åpne et prosjekt eller begynn på en ny historie.")}<Spacer Slot.Fill="1"/>{button("+ Nytt prosjekt", "project.new", True, **{"Slot.VAlign":"Center"})}</HBox>
          <Border Material="TintedGlass" Background="#232A29" CornerRadius="12" Padding="26" BorderColor="#4B5145" BorderWidth="1"><HBox Spacing="28"><VBox Slot.Fill="1" Spacing="12"><Label Text="SKAP MED OMTANKE" Class="eyebrow"/><Label Text="En tydelig idé. Et levende bilde.\nEn historie som henger sammen." TextSize="29" Font="/System/Library/Fonts/NewYork.ttf" Wrap="true"/>{label("Gi karakterene et uttrykk, finn scenens bilde og ta ett opptak om gangen.", 13, True)}<HBox Spacing="10">{button("Start med regissøren", "home.director", True)}{button("Importer et prosjekt", "project.import")}</HBox></VBox><Border Background="#C59168" CornerRadius="9" Width="154" Height="140"><VBox Spacing="12" Slot.VAlign="Center" Slot.HAlign="Center"><Label Text="S" TextSize="65" Font="/System/Library/Fonts/NewYork.ttf" Color="#27241C"/><Label Text="SPRINTWAKE STUDIOS" TextSize="10" Color="#27241C"/></VBox></Border></HBox></Border>
          <HBox><Label Text="PROSJEKTENE DINE" Class="eyebrow" Slot.Fill="1"/>{button("Papirkurv", "project.trash")}{button("Vis produksjonsmappen", "folder.root")}</HBox><VBox Name="home.cards" Spacing="16"/>
          <HBox>{button("Tilbake til start", "page.home")}{button("Innstillinger", "settings.show")}</HBox>''')),
        ("brief", page("brief", f'''{title("01 / Idé og manus", "Gi historien retning.", "Beskriv hva vi ser, hva som skjer og hvordan filmen skal føles.")}
          <HBox Spacing="10">{button("Filmidé og uttrykk", "brief.idea")}{button("Manus og klipp", "brief.script", True)}<Label Name="brief.script.summary" Text="" Class="muted" Slot.VAlign="Center"/></HBox>
          <HBox Name="brief.form" Spacing="26"><VBox Slot.Fill="1" Spacing="18">{field("brief.description", "Filmideen og verdenen", height=200, placeholder="Hva handler filmen om? Hvor er vi, og hva forandrer seg?")}{field("brief.style", "Visuell stil", height=90, placeholder="Materialer, lys, farger og kamerafølelse")}{field("brief.sound", "Lyd og stemning", height=75, placeholder="Miljølyd, musikk og dialogspråk")}<HBox Spacing="10">{button("Lagre manus", "file.save", True)}{button("Lag manus og klipp", "director.author.show")}{button("Diskuter filmideen", "director.plan")}{button("Legg til et klipp selv", "shot.add")}</HBox></VBox>
          <Border Width="300" Background="#1C2425" CornerRadius="10" Padding="22"><VBox Spacing="18">{label("ET GODT UTGANGSPUNKT", 10, True)}{label("Hva vil du at vi skal oppleve?", 23, **{"Font":"/System/Library/Fonts/NewYork.ttf"})}{label("Gi historien en konkret handling. La deretter referanser og scenebilder gjøre ideen synlig.",13,True)}<Separator/>{label("Manuset følger prosjektet",16)}{label("Et lesbart manus lagres i Script-mappen sammen med prosjektet. Du kan eksportere hele produksjonen når du vil.",12,True)}{button("Vis manusmappen", "folder.script")}</VBox></Border></HBox><VBox Name="brief.manuscript" Visibility="Collapsed" Spacing="18"><VBox Name="brief.script.cards" Spacing="16"/></VBox>''')),
        ("images", page("images", f'''{title("03 / Bilder", "Finn scenens uttrykk.", "Lag bilder fra en beskrivelse, eller bygg videre på valgte referanser.")}
          <HBox Spacing="22"><Border Width="300" Padding="20" Background="#1D2426" CornerRadius="11"><VBox Spacing="16">{field("image.prompt", "Beskriv bildet", height=170, placeholder="Motiv, plassering, handling, utsnitt og lys…")}<Label Text="REFERANSER" Class="eyebrow"/><VBox Name="image.references" Spacing="7"/><HBox Spacing="7">{button("Velg referanser", "image.references")}{button("Importer", "media.import")}</HBox><Label Text="FORMAT" Class="eyebrow"/><ComboBox Name="image.aspect" Items="Landskap · 3:2|Film · 16:9|Portrett · 2:3|Kvadrat · 1:1"/><Label Text="SEED (VALGFRITT)" Class="eyebrow"/><TextInput Name="image.seed" Placeholder="Tilfeldig"/><Button Name="image.generate" Text="Lag bilde" Style="Primary" OnClicked="image.generate"/><Button Name="image.stop" Text="Stopp denne bildejobben" Style="Flat" OnClicked="image.stop"/><Label Name="image.status" Text="Klar til å lage noe nytt." Class="muted" Wrap="true"/></VBox></Border>
          <VBox Slot.Fill="1" Spacing="14"><Border Background="#0C1113" CornerRadius="11" BorderColor="#3A4346" BorderWidth="1" Height="390"><Overlay><Image Name="image.preview" Stretch="Fit"/><VBox Name="image.empty" Spacing="14" Slot.HAlign="Center" Slot.VAlign="Center"><Label Text="Et nytt øyeblikk venter." TextSize="28" Font="/System/Library/Fonts/NewYork.ttf"/><Label Text="Beskriv scenen. Velg referanser. Finn uttrykket." Class="muted"/><Label Name="image.engine" Text="VALGT BILDEMODELL" Class="eyebrow"/></VBox></Overlay></Border><HBox Spacing="9">{button("Bruk som klippets åpning", "image.assign", True, Name="image.assign")}{button("Lagre til Output", "image.publish", Name="image.publish")}{button("Vis fil", "image.reveal", Name="image.reveal")}{button("Sammenlign", "image.compare")}</HBox><Label Name="image.description" Text="Velg et resultat for å se bildet og oppskriften." Class="muted" Wrap="true"/><WrapBox Name="image.gallery" Spacing="12"/></VBox></HBox>''')),
        ("production", page("production", f'''{title("05 / Produksjon og gjennomgang", "Ett gjennomarbeidet opptak om gangen.", "Se bildeplanen før video. Vurder det faktiske opptaket før historien fortsetter.")}
          <Border Material="TintedGlass" Background="#202B28" CornerRadius="11" Padding="23"><VBox Spacing="12"><HBox><Label Name="production.phase" Text="Klar til forberedelse" TextSize="23" Font="/System/Library/Fonts/NewYork.ttf" Slot.Fill="1"/>{button("Oppdater", "production.refresh")}</HBox><Label Name="production.detail" Text="Fullfør manus og klipp før du lager bildeplanen." Wrap="true" Class="muted"/><Label Name="production.scope" Text="" Class="muted" Wrap="true"/><ProgressBar Name="production.progress" Value="0"/><HBox Spacing="9">{button("Lag / oppdater bildeplan", "production.prepare", True, Name="production.prepare")}{button("Fortsett produksjonen", "production.resume", Name="production.resume")}{button("Stopp egen filmjobb", "production.stop", Name="production.stop")}</HBox></VBox></Border>
          <VBox Name="production.gate" Spacing="16"/><VBox Name="production.clips" Spacing="10"/>
          <Border Background="#1B2225" Padding="22" CornerRadius="11"><VBox Spacing="14"><Label Text="LEVERANSEN" TextSize="23" Font="/System/Library/Fonts/NewYork.ttf"/><Label Text="Når opptakene er gjennomgått, kan du sette sammen filmen og eksportere prosjektet." Class="muted" Wrap="true"/><HBox Spacing="9">{button("Sett sammen film", "production.assemble", True, Name="production.assemble")}{button("Spill av ferdig film", "production.play_final", Name="production.play_final")}{button("Eksporter prosjekt", "project.export")}{button("Vis Output", "folder.output")}</HBox></VBox></Border>''')),
        ("director", f'''<HBox Name="page.director" Visibility="Collapsed" Spacing="0">
          <Border Width="248" Background="#1C2427" Padding="18"><VBox Spacing="16">
            <Label Text="REGIROMMET" Class="eyebrow"/>
            <Label Text="Samtaler" Font="/System/Library/Fonts/NewYork.ttf" TextSize="27"/>
            <Button Text="＋ Ny samtale" Style="Primary" OnClicked="conversation.new"/>
            <Button Text="Begynn med en fri idé" Style="Flat" OnClicked="conversation.general"/>
            <Label Text="PROSJEKT FOR SAMTALEN" Class="eyebrow"/>
            <ComboBox Name="conversation.project" OnChanged="conversation.project"/>
            <Separator/>
            <ScrollBox Slot.Fill="1"><VBox Name="conversation.list" Spacing="8"/></ScrollBox>
          </VBox></Border>
          <Border Slot.Fill="1" Padding="26" Background="#171D20"><VBox Spacing="18">
            <HBox Spacing="12"><VBox Slot.Fill="1" Spacing="7"><Label Name="director.title" Text="Ny samtale" Font="/System/Library/Fonts/NewYork.ttf" TextSize="28"/><Label Name="director.context" Text="En idé er nok." Class="muted" Wrap="true"/></VBox>{button("Gi navn", "conversation.rename")}{button("Arkiver", "conversation.archive")}</HBox>
            <HBox Spacing="10">{button("Lag manus og klipp", "director.author.show", Name="director.author.button")}{button("Les manuset", "director.open_script", Name="director.read.button")}</HBox>
            <ScrollBox Name="director.messages" Slot.Fill="1"><VBox Name="director.history" Spacing="15"/></ScrollBox>
            <Border Background="#20282B" CornerRadius="12" Padding="18"><VBox Spacing="12">{field("director.message", "Din melding", height=96, placeholder="Fortell hva du vil lage. Regissøren hjelper med neste steg…")}<HBox Spacing="12"><Button Name="director.send" Text="Send til regissøren" Style="Primary" OnClicked="director.send"/><ComboBox Name="director.thinking" Items="Rask vurdering|Tenk litt mer|Grundig" Width="165"/><Label Name="director.status" Text="Klar når du er." Class="muted" Wrap="true" Slot.Fill="1" Slot.VAlign="Center"/></HBox></VBox></Border>
          </VBox></Border></HBox>'''),
        ("overview", page("overview", f'''{title("Prosjektet ditt", "Fra idé til ferdig film.")}<VBox Name="overview.content" Spacing="22"/>''')),
    ]


def modal(heading, body, action=None, action_label="Lagre", detail="", allow_close=True):
    footer = button("Lukk", "modal.close") if allow_close else ""
    if action:
        footer += button(action_label, action, True)
    return f'<VBox Spacing="17">{title("Sprintwake Studios", heading, detail)}<ScrollBox MaxHeight="540"><VBox Spacing="15">{body}</VBox></ScrollBox><Separator/><Label Name="modal.error" Text="" Color="#EFA1A1" Wrap="true"/><HBox Spacing="10"><Spacer Slot.Fill="1"/>{footer}</HBox></VBox>'

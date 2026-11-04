"""Native shared library, available independently of an open project."""
from pathlib import Path

import views as v


class LibraryPanelMixin:
    def render_library(self):
        self.library_query = ""
        controls = '<HBox Spacing="10">' + v.button("Hele biblioteket", "library.filter.all")
        if self.board:
            controls += v.button("Dette prosjektet", "library.filter.project")
        controls += '<Spacer Slot.Fill="1"/>' + v.button("Oppdater", "library.refresh") + v.button("Vis bibliotekmappen", "folder.library") + '</HBox>'
        self.replace_host("library.host", v.title("Bibliotek", "Bildene dine, samlet.", "Tidligere bilder kan brukes i nye prosjekter. Originaler og oppskrifter bevares.") + controls + '<TextInput Name="library.search" Placeholder="Finn bilder etter navn eller opprinnelse…" OnChanged="library.search"/><Label Name="library.summary" Text="Henter biblioteket…" Class="muted"/><VBox Name="library.results" Spacing="14"/>')
        self.render_library_results()
        self.refresh_library()

    def refresh_library(self):
        slug = self.slug
        def operation():
            shared = [dict(item, scope="shared") for item in self.library.catalogue()]
            project = [dict(item, scope="project", name=Path(item["path"]).name) for item in self.workspace.catalogue(slug)] if slug else []
            return shared + project
        def done(records):
            self.library_records = records
            if self.mode == "library": self.render_library_results()
        self.submit("library.catalogue", operation, done)

    def render_library_results(self):
        query = self.text("library.search").strip().lower()
        self.library_query = query
        records = getattr(self, "library_records", [])
        if getattr(self, "library_scope", "all") == "project":
            records = [item for item in records if item.get("scope") == "project"]
        self.library_items = [item for item in records if query in (item.get("name", "") + " " + " ".join(str(o.get("label", "")) + " " + str(o.get("name", "")) for o in item.get("origins", []))).lower()]
        cards = []
        for i, item in enumerate(self.library_items[:120]):
            path = item["path"]
            title = (item.get("origins") or [{}])[0].get("name") or item.get("name") or Path(path).name
            detail = "FELLES BIBLIOTEK" if item.get("scope") == "shared" else "DETTE PROSJEKTET"
            use = v.button("Bruk som referanse", f"library.adopt.{i}", Enabled=str(bool(self.board) and item["kind"] == "image").lower())
            cover = v.image(path if item["kind"] == "image" else "", height=135, fit=True)
            preview = f'<Button OnClicked="library.open.{i}" Metric.PaddingX="0" Metric.PaddingY="0"><VBox HitTestable="false">{cover}</VBox></Button>'
            cards.append(f'<Border Background="#1B2427" Padding="12" CornerRadius="10" Width="220"><VBox Spacing="10">{preview}{v.label(title,12)}{v.label(detail,10,True)}{use}</VBox></Border>')
        self.set_label("library.summary", f"{len(self.library_items)} mediefiler" + (" · velg et prosjekt for å bruke et bilde som referanse" if not self.board else ""))
        empty = "Ingen bilder samsvarer med søket." if query else "Biblioteket er tomt. Importerte og tidligere genererte bilder vises her."
        self.replace_host("library.results", '<WrapBox Spacing="16">' + ''.join(cards) + '</WrapBox>' if cards else v.label(empty,14,True))

    def open_library(self, index):
        if 0 <= index < len(self.library_items):
            item = self.library_items[index]
            self.preview(Path(item["path"]), (item.get("origins") or [{}])[0].get("name") or item.get("name", "Bilde"))

    def adopt_library(self, index):
        if not self.board or not self.slug:
            raise ValueError("Åpne et prosjekt før du bruker bildet som referanse.")
        if not 0 <= index < len(self.library_items): return
        if len(self.image_refs) >= 10:
            raise ValueError("Du har allerede ti referanser. Fjern en før du legger til flere.")
        item, slug = dict(self.library_items[index]), self.slug
        if item.get("kind") != "image": return
        def operation():
            if item.get("scope") == "shared":
                return self.library.adopt(slug, item["id"], self.backend)
            return self.ref_for(item["path"])
        def done(ref):
            if not any(value.get("path") == ref.get("path") for value in self.image_refs):
                self.image_refs.append(ref)
            self.capture_drafts()
            self.mode_set("images")
            self.toast("Bildet er kopiert til prosjektet og valgt som referanse. Ingen generering er startet.")
            self.poll_deadline = 0
        self.save(after=lambda: self.submit("library.adopt", operation, done))

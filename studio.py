"""Raven's native production application over the existing verified film core."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import os
import copy
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import tempfile
import time
import uuid
from urllib.parse import quote

import raven as r

from app import Studio as Editor, ROOT
from backend import Backend, BackendError
from connections import ConnectionStore, defaults as connection_defaults, selected_llm
from connections_panel import ConnectionsMixin
from workflow_panel import WorkflowPanelMixin
from update_panel import UpdatesMixin
from credentials import CredentialStore
from project import Project
from production_model import apply_proposals, apply_view, film_action, next_step, proposal_title, view_project
from workspace import Workspace, new_shot
import views as v
from panels import PanelMixin
from studio_state import authoring_digest, PlaybackReceipt
from takes import TakePanelMixin
from library import MediaLibrary
from library_panel import LibraryPanelMixin
from director_state import normalize_turn, response_turn, proposal_summary
from director_panel import DirectorAuthoringMixin
from director_workspace import DirectorWorkspaceMixin
from project_removal import ProjectRemovalMixin
from editing_panel import EditingMixin
from coverage_panel import CoverageMixin, coverage_page
from editorial_workspace import StoryWorkspaceMixin
from layout_model import EDITOR_MODES, CUT_MODES
from layout_panel import LayoutMixin
from creation_panel import CreationMixin, creation_kind


PAGES = {"film": 0, "library": 1, "bible": 2, "projects": 3, "brief": 4,
         "images": 5, "production": 6, "director": 7, "overview": 8, "coverage": 9,
         "home": 10, "create": 11, "clip": 12,
         "scenes": 0, "structure": 0, "sound": 0, "cut": 0}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ProductionStudio(CreationMixin, UpdatesMixin, WorkflowPanelMixin, ConnectionsMixin, LayoutMixin, StoryWorkspaceMixin, CoverageMixin, EditingMixin, ProjectRemovalMixin, DirectorWorkspaceMixin, DirectorAuthoringMixin, LibraryPanelMixin, TakePanelMixin, PanelMixin, Editor):
    def __init__(self, root=None, *, backend=None, start_backend=True, connection_store=None, connection_settings=None):
        self.live = False
        self.connections = connection_store or ConnectionStore()
        self.connection_error = ""
        try:
            from connections import validate
            self.connection_settings = validate(connection_settings or self.connections.load())
        except (OSError,ValueError) as error:
            self.connection_settings=connection_defaults();self.connection_error=str(error)
        if os.environ.get("SPRINTWAKE_APP_BUNDLE"):
            from installation import rebase_bundle_paths
            self.connection_settings=rebase_bundle_paths(self.connection_settings,os.environ["SPRINTWAKE_APP_BUNDLE"])
        if root:self.connection_settings["paths"]["root"]=str(Path(root).expanduser().resolve())
        self.workspace = Workspace(self.connection_settings["paths"]["root"])
        self.workspace.ensure()
        self.library = MediaLibrary(self.workspace)
        self.library_records = []
        self.library_items = []
        self.library_scope = "all"
        self.library_query = ""
        self.brief_view = "idea"
        paths=self.connection_settings["paths"]
        os.environ["RAVEN_ENGINE_ROOT"]=paths["engine"]
        self.backend = backend or Backend(self.workspace.root,engine_root=Path(paths["engine"]),runtime_python=Path(paths["python"]),workspace=Path(paths["workspace"]),comfyui_url=self.connection_settings["comfyUrl"],llm=selected_llm(self.connection_settings),connection_settings=self.connection_settings,job_state_dir=self.connections.path.parent/"Jobs",config_dir=self.connections.path.parent/"EngineSettings")
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="raven")
        self.completions = queue.Queue()
        self.pending = {}
        self.epoch = 0
        self.slug = ""
        self.board = None
        self.catalogue = []
        self.covers = {}
        self.cover_signature = None
        self.summaries = self.workspace.project_summaries()
        self.film_context = {}
        self.image_state = {}
        self.engine_state = {}
        self.images = []
        self.selected_image = None
        self.image_refs = []
        self.history = []
        self.proposals = []
        self.ready = False
        self.closing = False
        self.opening = False
        self.cleaned = False
        self.form_loading = False
        self.edit_version = 0
        self.save_waiters = []
        self.save_deadline = 0
        self.poll_deadline = 0
        self.modal_kind = ""
        self.entity_target = None
        self.initialize_layout()
        self.initialize_editing()
        self.initialize_coverage()
        self.initialize_story_workspace()
        self.image_target = None
        self.gate_key = None
        self.preview_media = None
        self.drafts = {}
        self.drafts_path = self.workspace.system_dir / "drafts.json"
        if self.drafts_path.is_file():
            try:
                self.drafts = json.loads(self.drafts_path.read_text())
            except (OSError, ValueError):
                pass
        empty = Project("empty", "Sprintwake Studios", "Choose or create a project", [])
        super().__init__([empty])
        self.credentials=CredentialStore(self.player)
        self.initialize_connections()
        self.initialize_updates()
        self.backend.credential_loader=self.credentials.get
        for name, xml in v.pages():
            r.Rwidget_add_child(self.w("workspace"), r.Rwidget_create_from_xml(xml))
        r.Rwidget_add_child(self.w("workspace"), r.Rwidget_create_from_xml(coverage_page()))
        for xml in (v.start_page(),v.page('create','<VBox Name="create.content"/>'),v.page('clip','<VBox Name="clip.content"/>')):
            r.Rwidget_add_child(self.w("workspace"),r.Rwidget_create_from_xml(xml))
        self.bind_coverage()
        self.bind_sound()
        self.live = True
        r.Rtimeline_set_removed_callback(self.w("timeline"), self.on_timeline_removed)
        self.projects = [Project("empty", "Sprintwake Studios", "Choose or create a project", [])]
        self.project = self.projects[0]
        self.index = 0
        self.populate_project()
        self.initialize_conversations()
        self.mode_set("home")
        r.Rwindow_register_shortcut(self.window, r.R_KEY_K, r.R_MOD_SUPER, "command.show")
        r.Rwindow_register_shortcut(self.window, r.R_KEY_ESCAPE, 0, "modal.close")
        r.Rwindow_set_close_callback(self.window, lambda *_: self._window_close())
        self.render_home()
        self.replace_host("bible.host", v.title("Film Bible", "Give the story a firm foundation."))
        self.replace_host("library.host", v.title("Library", "All the material in your project."))
        if self.connection_error:
            self.connection_status=self.connection_error;self.show_connections()
        elif start_backend:
            self.submit("boot", self._bootstrap, self._booted, scoped=False)

    def find(self, name):
        return r.Rwidget_find(self.window, name)

    def set_attr(self, name, key, value):
        handle = self.find(name)
        if handle != r.R_INVALID_ID:
            r.Rwidget_set_attribute(handle, key, str(value).lower() if isinstance(value, bool) else str(value))

    def set_label(self, name, text):
        handle = self.find(name)
        if handle != r.R_INVALID_ID:
            r.Rlabel_set_text(handle, str(text))

    def text(self, name):
        handle = self.find(name)
        if handle == r.R_INVALID_ID:
            return ""
        kind = r.Rwidget_get_type(handle)
        return (r.Rtext_editor_get_text(handle) if kind == r.R_WIDGET_TEXT_EDITOR else r.Rtext_input_get_text(handle)) or ""

    def set_text(self, name, text):
        handle = self.find(name)
        if handle != r.R_INVALID_ID:
            if r.Rwidget_get_type(handle) == r.R_WIDGET_TEXT_EDITOR:
                r.Rtext_editor_set_text(handle, str(text or ""))
            else:
                r.Rtext_input_set_text(handle, str(text or ""))

    def selected(self, name):
        handle = self.find(name)
        return r.Rcombo_box_get_selected(handle) if handle != r.R_INVALID_ID else 0

    def checked(self, name):
        handle = self.find(name)
        return bool(handle != r.R_INVALID_ID and r.Rcheckbox_is_checked(handle))

    def replace_host(self, name, xml):
        handle = self.find(name)
        if handle == r.R_INVALID_ID:
            return
        r.Rwidget_clear_children(handle)
        child = r.Rwidget_create_from_xml(f'<VBox Spacing="16">{xml}</VBox>')
        r.Rwidget_add_child(handle, child)

    def toast(self, message, error=False):
        self.label("status", str(message)[:500])
        if self.mode == 'create':
            self.set_label('creation.status',str(message)[:500])
        if error and self.modal_kind:
            self.set_label("modal.error", str(message)[:1000])

    def submit(self, key, function, done=None, *, scoped=True):
        if key in self.pending:
            return False
        epoch = self.epoch if scoped else None
        future = self.pool.submit(function)
        self.pending[key] = future
        def completed(item):
            try:
                self.completions.put((key, epoch, done, item.result(), None))
            except Exception as error:
                self.completions.put((key, epoch, done, None, error))
        future.add_done_callback(completed)
        return True

    def _bootstrap(self):
        info = self.backend.start()
        return info, self.backend.get("/api/boards")

    def _booted(self, result):
        self.info, payload = result
        self.ready = True
        self.summaries = payload.get("boards", [])
        self.set_label("home.connection", "Production core ready · "+self.info.get("backend",{}).get("label","selected models"))
        image_id=self.info.get("connections",{}).get("imageModel","qwen-image-2.1-turbo")
        image=next((m for m in self.info.get("models",[]) if m["id"]==image_id),{})
        self.set_label("image.engine",image.get("label") or "No image model selected")
        self.set_attr("image.generate","Enabled",bool(image))
        self.render_home()
        self.render_conversation_sidebar()
        target=self.current_conversation.get("projectSlug")
        if target and target not in {item["slug"] for item in self.summaries}:
            self.current_conversation=self.conversations.bind(self.current_conversation["id"],None)
        # Restore the conversation draft, but enter it only after an explicit choice.
        # A saved project-bound conversation must not navigate away from Start.
        self.render_conversation_sidebar()
        if self.mode == 'director':
            self.activate_conversation(self.current_conversation['id'])
        self.toast("The studio is ready. Choose an image, clip, or Film Studio.")
        if getattr(self, "startup_project", None):
            source, self.startup_project = self.startup_project, None
            self.import_project(source)

    def _drain(self):
        while True:
            try:
                key, epoch, done, data, error = self.completions.get_nowait()
            except queue.Empty:
                break
            self.pending.pop(key, None)
            if epoch is not None and epoch != self.epoch:
                continue
            if error:
                if key == 'creation':self.set_attr('creation.open','Enabled',True)
                if key=="updates.prepare":self.update_failed(error)
                self.toast(error, True)
                if key=="boot":
                    self.connection_status=str(error)[:500];self.show_connections()
                if key == "open":
                    self.opening = False
                    self.modal_close()
                if key == "chat":
                    self.set_label("director.status", str(error))
                if key == "director.author":
                    self.show_modal("Script work stopped",v.label(str(error),13,True))
                if key == "save":
                    self.save_deadline = 0
                    self.save_waiters.clear()
                    self.label("save.state", "Could not save")
                    self.author_starting = False
                    self.director_starting = False
                    self.chat_starting = False
                if key == "close":
                    self.closing = False
                    self.show_modal("Shutdown is waiting", v.label(str(error), 13, True), "app.quit", "Try shutdown again")
                continue
            if done and (not self.closing or key in {"close","chat","director.execute","director.author"}):
                try:
                    done(data)
                except Exception as issue:
                    self.toast(issue, True)

    def set_dirty(self, value):
        super().set_dirty(value)
        if getattr(self, "live", False) and value and not self.form_loading:
            self.edit_version += 1
            self.save_deadline = time.monotonic() + 1.0
            self.label("save.state", "Unsaved changes")
        elif value is False:
            self.save_deadline = 0
            if getattr(self, "live", False):
                self.label("save.state", "Saved locally" if self.slug else "Sprintwake Studios")

    def sync_fields(self):
        if not getattr(self, "live", False):
            return super().sync_fields()
        if self.mode == 'clip':
            if not (self.form_loading or self.updating or self.closing or self.opening):
                self.sync_clip()
            return
        if self.mode in {"scenes", "cut", "coverage", "structure", "sound"}:
            return
        if not self.board or self.form_loading or self.updating or self.closing or self.opening or "director.author" in self.pending or "director.execute" in self.pending or "project.delete" in self.pending or "project.restore" in self.pending:
            return
        super().sync_fields()
        updated = apply_view(self.board, self.project)
        if self.mode == "brief":
            for widget, key in (("brief.description", "sceneDescription"), ("brief.style", "renderStyle"), ("brief.sound", "soundscape")):
                updated[key] = self.text(widget)
        if updated != self.board:
            self.board = updated
            self.set_dirty(True)

    def save(self, after=None):
        if not getattr(self, "live", False):
            return super().save()
        self.sync_fields()
        if after:
            self.save_waiters.append(after)
        if not self.board or not self.slug:
            return
        if not self.ready:
            self.toast("The production core is still starting.")
            return
        if "save" in self.pending:
            return
        if not self.dirty:
            waiters, self.save_waiters = self.save_waiters, []
            for callback in waiters:
                callback()
            return
        board, slug, version = copy.deepcopy(self.board), self.slug, self.edit_version
        self.label("save.state", "Saving…")
        def operation():
            result = self.backend.put(f"/api/boards/{quote(slug)}", board)
            actual = result.get("board", result)
            self.workspace.sync_script(slug, actual)
            return actual
        def saved(actual):
            if self.edit_version == version:
                self.board = actual
                self.set_dirty(False)
            else:
                if "filmAutomation" in actual:
                    self.board["filmAutomation"] = actual["filmAutomation"]
                self.save()
                return
            waiters, self.save_waiters = self.save_waiters, []
            for callback in waiters:
                callback()
        self.submit("save", operation, saved)

    def set_projects(self):
        if not getattr(self, "live", False):
            return super().set_projects()
        self.updating = True
        r.Rcombo_box_clear_items(self.w("projects"))
        for item in self.summaries:
            r.Rcombo_box_add_item(self.w("projects"), item.get("name", item.get("slug", "Project")))
        selected = next((i for i, item in enumerate(self.summaries) if item.get("slug") == self.slug), -1)
        r.Rcombo_box_set_selected(self.w("projects"), selected)
        self.updating = False

    def populate_project(self):
        super().populate_project()
        if getattr(self, "live", False):
            self.set_projects()
            self.set_label("viewer.source", "Choose an image or a produced take" if not self.shot else self.shot.take.provenance if self.shot.take else "The clip is waiting for an opening image")
            editing = self.mode == "cut"
            self.set_attr("inspector", "Visibility", "Collapsed" if editing else "Visible")
            self.set_attr("cut.inspector", "Visibility", "Visible" if editing else "Collapsed")
            self.set_attr("shot.authoring", "Visibility", "Collapsed" if editing else "Visible")
            self.set_attr("cut.toolbar", "Visibility", "Visible" if editing else "Collapsed")
            self.set_attr("timeline.hint", "Visibility", "Collapsed" if editing else "Visible")
            self.render_cut_inspector()
            self.layout_editor()
            selected=getattr(self,"connection_settings",{}).get("selected",{}).get("video")
            external=any(p.get("id")==selected and p.get("kind")=="video" for p in getattr(self,"connection_settings",{}).get("workflowProfiles",[]))
            self.set_attr("shot.workflow","Visibility","Visible" if external else "Collapsed")

    def refresh_view(self, *, populate=True):
        if not self.board:
            return
        current_id = self.shot.id if self.shot else None
        self.project = view_project(self.slug, self.board, self.workspace)
        if self.mode in CUT_MODES and self.cut_doc and self.cut_store and self.cut_store.slug == self.slug:
            self.project = self.cut_store.view(self.cut_doc, self.project)
        self.projects = [self.project]
        self.index = next((i for i, s in enumerate(self.project.shots) if s.id == current_id), min(self.index, max(0, len(self.project.shots) - 1)))
        if populate:
            dirty = self.dirty
            self.form_loading = True
            self.populate_project()
            self.form_loading = False
            self.dirty = dirty
            if dirty:
                self.label("save.state", "Unsaved changes")
            if self.mode == 'clip': self.render_clip()

    def open_project(self, slug):
        if self.editorial_io_busy() or any(k.startswith("coverage.") for k in self.pending):
            return
        self.flush_sound_gesture()
        self.finish_coverage_drag()
        if self.opening:
            return
        if self.dirty:
            self.save(after=lambda: self.open_project(slug))
            return
        if not self.ready:
            self.toast("Wait a moment while the production core starts.")
            return
        self.capture_drafts()
        self.epoch += 1
        self.opening = True
        self.show_modal("Opening project", v.label("Loading script, references, and production history…",13,True))
        self.player.close()
        self.active_video = ""
        self.playing = False
        self.clock = 0
        self.images = []
        self.image_refs = []
        self.library_records = [item for item in self.library_records if item.get("scope") == "shared"]
        self.library_items = []
        self.selected_image = None
        self.image_target = None
        self.gate_key = None
        self.gate_seen = None
        self.replace_host('clip.content','')
        self.review_played = set()
        self.film_context = {}
        self.toast("Opening project…")
        def loaded(payload):
            self.slug = slug
            self.board = payload["board"]
            self.initialize_layout()
            self.initialize_editing()
            self.initialize_coverage()
            self.initialize_story_workspace()
            self.sequence_doc = payload.get("ravenSequence")
            self.creation_kind = payload.get('creationKind','studio')
            # Opening another project must not reuse the previous cut view.
            self.mode = "overview"
            self.opening = False
            self.workspace.ensure_project(slug)
            self.index = 0
            self.dirty = False
            self.refresh_view()
            self.fill_brief()
            self.load_conversation()
            self.load_drafts()
            self.brief_view = "script" if self.board.get("shots") else "idea"
            destination = self.project_destination
            self.project_destination = "overview"
            if destination == 'overview':
                destination = {'image':'images','clip':'clip'}.get(self.creation_kind,destination)
            self.mode_set(destination)
            self.poll_deadline = 0
            self.modal_close()
            self.toast("The project is ready.")
            if payload.get("ravenSequenceError"): self.toast("The sequence list could not be read. Open Structure to check it.",True)
        def operation():
            from sequence import SequenceStore
            payload = self.backend.get(f"/api/boards/{quote(slug)}")
            payload['creationKind'] = creation_kind(self.workspace,slug)
            try: payload["ravenSequence"] = SequenceStore(self.workspace,slug).load()
            except (OSError,ValueError): payload["ravenSequenceError"] = True
            return payload
        self.submit("open", operation, loaded)

    def render_home(self):
        recent = ''.join(v.button(item.get('name',item['slug'])+'  →','project.open.'+item['slug']) for item in self.summaries[:3])
        self.replace_host('home.recent','<HBox Spacing="10">'+recent+'</HBox>' if recent else v.label('Your work will appear here once you begin.',13,True))
        cards = []
        for item in self.summaries:
            slug = item["slug"]
            cover = self.covers.get(slug, "")
            if cover and not Path(cover).is_file(): cover=""
            cards.append(f'<Border Width="340" Padding="12" Background="#1C2427" CornerRadius="11"><VBox Spacing="12"><Button OnClicked="project.open.{v.esc(slug)}" Metric.PaddingX="0" Metric.PaddingY="0"><VBox Spacing="13" HitTestable="false">{v.image(cover, height=170)}{v.label(item.get("name",slug),22,**{"Font":"/System/Library/Fonts/NewYork.ttf"})}{v.label(f"{item.get('shots',item.get('shotCount',0))} clips · {item.get('rendered',item.get('renderedCount',0))} takes",12,True)}</VBox></Button><HBox Spacing="8">{v.button("Open project","project.open."+slug,True)}<Spacer Slot.Fill="1"/>{v.button("Delete…","project.delete."+slug)}</HBox></VBox></Border>')
        if not cards:
            cards = [f'<Border Padding="28" Background="#171F22" CornerRadius="10"><VBox Spacing="12">{v.label("Your first project starts here.",24,**{"Font":"/System/Library/Fonts/NewYork.ttf"})}{v.label("Start with a title and an idea. References, images, and takes follow the project.",13,True)}<HBox>{v.button("Create your first project", "project.new", True)}</HBox></VBox></Border>']
        self.replace_host("home.cards", '<WrapBox Spacing="18">' + "".join(cards) + '</WrapBox>')
        signature = tuple((item["slug"], item.get("updatedAt")) for item in self.summaries)
        if signature != self.cover_signature and "covers" not in self.pending:
            slugs = [item["slug"] for item in self.summaries]
            def operation():
                return {slug: next((m["path"] for m in self.workspace.catalogue(slug) if m["kind"] == "image"), "") for slug in slugs}
            def done(covers):
                self.covers.update(covers)
                self.cover_signature = signature
                moving=any(k in self.pending for k in ("project.delete","project.restore","rename","archive"))
                if self.mode in {"home","projects"} and not moving: self.render_home()
            self.submit("covers", operation, done, scoped=False)

    def fill_brief(self):
        self.form_loading = True
        for name, key in (("brief.description", "sceneDescription"), ("brief.style", "renderStyle"), ("brief.sound", "soundscape")):
            self.set_text(name, (self.board or {}).get(key, ""))
        self.form_loading = False
        self.render_manuscript()

    def render_manuscript(self):
        shots = (self.board or {}).get("shots", [])
        self.set_attr("brief.form", "Visibility", "Visible" if self.brief_view == "idea" else "Collapsed")
        self.set_attr("brief.manuscript", "Visibility", "Visible" if self.brief_view == "script" else "Collapsed")
        parts = [v.label((self.board or {}).get("sceneDescription") or "Write the film idea or add clips to shape the script.",14)]
        bible = (self.board or {}).get("filmBible") or {}
        if bible.get("storyArc"):
            parts.append(v.label("Story arc",20) + v.label(bible["storyArc"],14))
        if bible.get("theme"):
            parts.append(v.label("Theme: " + bible["theme"],13,True))
        for act in bible.get("acts", []):
            parts.append(v.label(act.get("title") or act.get("name") or "Act",20) + v.label(act.get("description") or act.get("summary") or "",13,True))
        duration = sum(float(s.get("frames",121))/24 for s in shots)
        self.set_label("brief.script.summary", f"{len(shots)} clips · {duration:.1f} seconds planned · saved in Script/manus.md")
        for i, shot in enumerate(shots):
            text = "\n".join(f"{heading}: {shot.get(key, '')}" for heading,key in (("Start","start"),("Action","action"),("End","end"),("Camera","camera"),("Dialogue","dialogue"),("Sound","soundNote"),("Video description","prompt")) if shot.get(key))
            parts.append(f'<Border Background="#1C2528" Padding="20" CornerRadius="10"><VBox Spacing="12"><HBox><VBox Slot.Fill="1">{v.label(f"{i + 1:02}  {shot.get('title', 'Shot')}",20)}{v.label(f"Camera setup {shot.get('directorScene', 1)}",11,True)}</VBox>{v.button("Edit shot", f"manuscript.shot.{i}")}</HBox>{v.label(text,13)}</VBox></Border>')
        if not shots:
            parts.append(v.label("No clips have been added to the project yet. A director suggestion must be used before it becomes part of the script.",14,True))
        self.replace_host("brief.script.cards", ''.join(parts))

    def render_overview(self):
        if not self.board:
            return
        step = next_step(self.board, self.film_context)
        self.next_page = {"storyboard": "film"}.get(step["page"], step["page"])
        content = f'<Border Background="#293029" BorderColor="#626750" BorderWidth="1" CornerRadius="12" Padding="26"><HBox Spacing="24"><VBox Slot.Fill="1" Spacing="10">{v.label("NEXT STEP",10,True)}{v.label(step["label"],27,**{"Font":"/System/Library/Fonts/NewYork.ttf"})}{v.label(step["detail"],13,True)}</VBox>{v.button("Continue →", "overview.next", True, **{"Slot.VAlign":"Center"})}</HBox></Border>'
        trail = []
        for title, page, complete in (("01  Script", "brief", bool(self.board.get("sceneDescription"))), ("02  Film bible", "bible", bool(self.board.get("filmBible", {}).get("locations"))), ("03  Images", "images", bool(self.images)), ("04  Storyboard", "film", bool(self.board.get("shots"))), ("05  Production", "production", bool(self.board.get("finalVideo")))):
            trail.append(v.button(("✓  " if complete else "") + title, "page." + page, **{"Slot.Fill":"1"}))
        content += '<HBox Spacing="9">' + ''.join(trail) + '</HBox>'
        count = len(self.board.get("shots", []))
        duration = sum(s.get("frames", 121) / 24 for s in self.board.get("shots", []))
        for heading, detail in (("01  Idea and script", self.board.get("sceneDescription") or "Describe the story"), ("02  Film bible", f"{len(self.board.get('characters', []))} characters · {len((self.board.get('filmBible') or {}).get('locations', []))} locations"), ("03  Image plan and takes", f"{count} clips · {duration:.1f} seconds planned")):
            content += f'<Border Background="#1C2427" Padding="20" CornerRadius="9"><VBox Spacing="9">{v.label(heading,19)}{v.label(str(detail)[:650],13,True)}</VBox></Border>'
        content += '<HBox Spacing="10">' + v.button("Project settings", "project.settings") + v.button("Open project folder", "folder.project") + v.button("Export project", "project.export") + v.button("Delete project…", "project.delete") + '</HBox>'
        self.replace_host("overview.content", content)

    def mode_set(self, mode):
        if not getattr(self, "live", False):
            return super().mode_set(mode)
        if mode not in PAGES:
            return
        if self.editorial_io_busy() or any(k.startswith("coverage.") for k in self.pending):
            return
        self.flush_sound_gesture()
        self.stop_workspace_preview()
        if mode in {"structure", "sound"} and not getattr(self,"_entering_story",False):
            self.save(after=lambda:self.open_story_workspace(mode))
            return
        self.finish_coverage_drag()
        if mode not in {"home", "projects", "create", "library", "director"} and not self.board:
            self.show_new_project()
            return
        if mode == "cut" and not getattr(self, "_entering_cut", False):
            if any(key in self.pending for key in ("rename", "archive")):
                self.toast("Wait until the project has finished moving before opening the clip.", True)
                return
            self.save(after=self.open_cut)
            return
        self.sync_fields()
        self.capture_drafts()
        if mode not in EDITOR_MODES:
            self.playing = False
            self.player.play(False)
            r.Rbutton_set_text(self.w("play"), "▶")
        previous_mode = self.mode
        self.mode = mode
        if previous_mode in EDITOR_MODES or mode in EDITOR_MODES:
            self.cut_gesture_pending = False
            self.cut_removed.clear()
            self.player.play(False)
            self.playing = False
            self.clock = 0
            self.refresh_view()
        self.attr("workspace", "Index", PAGES[mode])
        for name in PAGES:
            if name not in EDITOR_MODES - {"film"}:
                page_name = "film.page" if name == "film" else name if name in ("library", "bible") else "page." + name
                visible = mode in EDITOR_MODES if name == "film" else mode == name
                self.set_attr(page_name, "Visibility", "Visible" if visible else "Collapsed")
            self.set_attr("nav." + name, "Class", "nav nav-active" if mode == name else "nav")
        simple = mode in {'home','projects','create','clip'} or (mode == 'images' and getattr(self,'creation_kind','studio') in {'image','clip'}) or not self.board
        self.set_attr('navigation.film','Visibility','Collapsed' if simple else 'Visible')
        self.set_attr('navigation.create','Visibility','Visible' if simple else 'Collapsed')
        self.set_attr('navigation.clip','Visibility','Visible' if self.board and getattr(self,'creation_kind','studio')=='clip' else 'Collapsed')
        self.set_attr('file.save','Enabled',bool(self.board))
        if mode in {"home","projects"}: self.render_home()
        elif mode == "overview": self.render_overview()
        elif mode == "brief": self.fill_brief()
        elif mode == "bible": self.render_bible()
        elif mode == "library": self.render_library()
        elif mode == "images": self.render_images()
        elif mode == "director": self.render_conversation()
        elif mode == "production": self.render_production()
        elif mode == "scenes": self.render_scenes()
        elif mode == "coverage": self.open_coverage()
        elif mode == "structure": self.render_structure()
        elif mode == "sound": self.render_sound()
        elif mode == 'clip': self.render_clip()
        self.layout_editor()
        self.layout_creation()

    def show_modal(self, heading, body, action=None, action_label="Save", detail="", allow_close=True):
        self.stop_workspace_preview(restore=True)
        self.modal_kind = heading
        self.replace_host("modal.content", v.modal(heading, body, action, action_label, detail, allow_close=allow_close))
        self.set_attr("app.content", "Enabled", "false")
        self.set_attr("modal.scrim", "Visibility", "Visible")

    def show_new_project(self):
        body = v.field("new.title", "Project name", height=38, multiline=False, placeholder="Give the story a name")
        body += v.field("new.brief", "What do you want to create?", height=135, placeholder="One idea is enough to begin…")
        body += '<Label Text="VISUAL STYLE" Class="eyebrow"/><ComboBox Name="new.style" Items="Custom style|Warm animated film|Cinematic realism|Illustrated fairy tale"/>'
        body += v.label("The project gets separate folders for scripts, input, output, and production history in Movies / Raven Studio.",12,True)
        self.show_modal("Start a new story", body, "project.create", "Create project")
        r.Rwidget_focus(self.find("new.title"))

    def create_project(self):
        name, brief = self.text("new.title").strip(), self.text("new.brief").strip()
        if not name:
            raise ValueError("Give the project a name first.")
        if not self.ready:
            raise ValueError("The production core is still starting. Try again in a moment.")
        styles = ["", "Warm handcrafted animation, tactile materials, gentle cinematic lighting.", "Cinematic realism, natural light, restrained camera movement.", "Illustrated storybook, expressive shapes, painterly environments."]
        style = styles[max(0, self.selected("new.style"))]
        def operation():
            result = self.backend.post("/api/boards", {"name": name})
            slug, board = result["slug"], result["board"]
            board.update(sceneDescription=brief, renderStyle=style, soundscape="")
            board.setdefault("defaults", {}).update(llm=self.backend.llm, model=next((m["id"] for m in self.info.get("models",[]) if m.get("kind")=="video"),"ltx-2.5"))
            saved = self.backend.put(f"/api/boards/{quote(slug)}", board)
            self.workspace.ensure_project(slug)
            self.workspace.sync_script(slug, saved.get("board", board))
            return slug, self.backend.get("/api/boards")
        def created(result):
            slug, listing = result
            self.summaries = listing["boards"]
            self.open_project(slug)
        self.submit("create", operation, created, scoped=False)
        self.set_label("modal.error", "Creating project and folders…")

    def capture_drafts(self):
        self.capture_session_draft()
        if not self.slug or not self.live:
            return
        self.drafts[self.slug] = {"imagePrompt": self.text("image.prompt"), "imageSeed": self.text("image.seed"),
                                  "imageAspect": self.selected("image.aspect"), "references": self.image_refs}
        temporary = self.drafts_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.drafts, ensure_ascii=False))
        temporary.replace(self.drafts_path)

    def load_drafts(self):
        draft = self.drafts.get(self.slug, {})
        fallback = self.board.get('sceneDescription','') if getattr(self,'creation_kind','studio')=='image' else ''
        self.set_text("image.prompt", draft.get("imagePrompt", fallback))
        self.set_text("image.seed", draft.get("imageSeed", ""))
        r.Rcombo_box_set_selected(self.find("image.aspect"), draft.get("imageAspect", 0))
        self.image_refs = draft.get("references", [])

    @property
    def raw_shot(self):
        shots = (self.board or {}).get("shots", [])
        if self.mode in CUT_MODES:
            return next((s for s in shots if self.shot and s.get("id") == self.shot.id), None)
        return shots[self.index] if 0 <= self.index < len(shots) else None

    def select_shot(self, index, *, seek=True):
        if getattr(self,"live",False): self.stop_workspace_preview()
        if getattr(self, "live", False):
            self.sync_fields()
        super().select_shot(index, seek=seek)
        if self.live and self.raw_shot:
            self.set_label("sidebar.scene", f"Camera setup {self.raw_shot.get('directorScene', 1):02}")
        if getattr(self, "live", False):
            self.render_cut_inspector()
            if self.shot:
                camera = ' '.join(self.shot.camera.split())
                self.set_label('viewer.camera',camera if len(camera)<=70 else camera[:67].rsplit(' ',1)[0]+'…')
            if self.mode in {"scenes", "structure"}:
                if self.shot and not self.form_loading:
                    scene = next((s for seq in self.layout_sequences for s in seq['scenes'] if self.shot.id in s['shotIds']),None)
                    if scene: self.layout_group=('scene',scene['id'])
                if self.mode == "scenes": self.render_scene_details()
                elif self.sequence_doc: self.render_structure()

    def add_shot(self, continuation=False):
        if not self.board:
            self.show_new_project()
            return
        self.sync_fields()
        shots = self.board.setdefault("shots", [])
        last = shots[-1] if shots else None
        scene = int(last.get("directorScene", 1)) if last else 1
        if last and not continuation:
            scene += 1
        if continuation and sum(s.get("directorScene") == scene for s in shots) >= 6:
            raise ValueError("A camera setup can have up to six shots. Start a new camera setup.")
        shot = new_shot(f"Shot {len(shots) + 1:02}", scene)
        shot["dialogueSource"] = "native"
        bible = self.board.setdefault("filmBible", {"acts": [], "locations": [], "props": []})
        if not bible.get("locations"):
            bible["locations"] = [{"id": "location-main", "title": "Main location", "description": self.board.get("sceneDescription", "")}]
        shot["locationId"] = bible["locations"][0]["id"]
        shot["characterIds"] = []
        if continuation and last:
            for key in ("camera", "characterIds", "locationId", "propIds", "assetStates"):
                shot[key] = copy.deepcopy(last.get(key, shot.get(key)))
            shot["frames"] = 120
            shot["start"] = last.get("end", "")
        shots.append(shot)
        self.index = len(shots) - 1
        self.refresh_view()
        self.set_dirty(True)
        self.mode_set("film")
        self.show_shot_details()

    def show_shot_details(self):
        shot = self.raw_shot
        if not shot:
            self.add_shot()
            return
        locations = (self.board.get("filmBible") or {}).get("locations", [])
        self.details_locations = locations
        body = v.field("clip.title", "Shot title", shot.get("title", ""), 36, multiline=False)
        body += v.field("clip.camera", "Camera and framing", shot.get("camera", ""), 48, placeholder="E.g. locked wide shot at eye level")
        body += v.field("clip.prompt", "Video prompt · English prose", shot.get("prompt", ""), 105, placeholder="Describe this shot's visible action, camera, light and sound.")
        body += '<HBox Spacing="14"><VBox Slot.Fill="1"><Label Text="DURATION" Class="eyebrow"/><ComboBox Name="clip.duration" Items="4 seconds|5 seconds|6 seconds"/></VBox><VBox Slot.Fill="1"><Label Text="CAMERA SETUP" Class="eyebrow"/><TextInput Name="clip.scene" Text="' + str(shot.get("directorScene", 1)) + '"/></VBox><VBox Slot.Fill="1"><Label Text="SEED" Class="eyebrow"/><TextInput Name="clip.seed" Text="' + str(shot.get("seed", 0)) + '"/></VBox></HBox>'
        body += '<Label Text="LOCATION" Class="eyebrow"/><ComboBox Name="clip.location" Items="' + v.esc("|".join(x.get("title", x["id"]) for x in locations) or "Choose a location in the film bible") + '"/>'
        body += '<Label Text="VISIBLE CHARACTERS" Class="eyebrow"/><WrapBox Spacing="12">' + "".join(f'<Checkbox Name="clip.cast.{i}" Text="{v.esc(c.get("name", "Character"))}" Checked="{str(c["id"] in shot.get("characterIds", [])).lower()}"/>' for i, c in enumerate(self.board.get("characters", []))) + '</WrapBox>'
        body += v.field("clip.state_before", "Planned state before the action", shot.get("stateBefore", ""), 55,
                        placeholder="Positions, eyelines, costume and who holds each prop.")
        body += v.field("clip.state_after", "Planned state after the action", shot.get("stateAfter", ""), 55,
                        placeholder="The concrete change the next shot must respect.")
        body += v.field("clip.dialogue", "Dialogue · write the language in the delivery description", shot.get("dialogue", ""), 65)
        self.details_speakers = [None] + [c for c in self.board.get("characters", []) if c.get("id")]
        speaker_labels = ["Automatic · one visible character"] + [c.get("name") or "Character" for c in self.details_speakers[1:]]
        body += '<Label Text="SPEAKER" Class="eyebrow"/><ComboBox Name="clip.speaker" Items="' + v.esc('|'.join(speaker_labels)) + '"/>'
        body += v.field("clip.delivery", "Spoken language and delivery", shot.get("dialogueStyle", ""), 40,
                        placeholder="E.g. in Norwegian, softly and hesitantly", multiline=False)
        body += v.field("clip.sound", "Shot sound", shot.get("soundNote", ""), 55)
        body += '<Label Text="AUDIO SOURCE" Class="eyebrow"/><ComboBox Name="clip.speech" Items="LTX · generated speech|Own takes / separate dubbing"/>'
        body += '<HBox Spacing="9">' + v.button("Image anchors", "shot.anchors") + v.button("Create opening image", "shot.image") + v.button("Advanced takes", "shot.advanced") + '</HBox>'
        body += '<HBox Spacing="9">' + v.button("Move earlier", "shot.up") + v.button("Move later", "shot.down") + v.button("Remove clip", "shot.remove") + '</HBox>'
        self.show_modal("Shape take set", body, "shot.apply", "Save clip")
        r.Rcombo_box_set_selected(self.find("clip.duration"), min(2, max(0, round(shot.get("frames", 121) / 24) - 4)))
        r.Rcombo_box_set_selected(self.find("clip.location"), next((i for i, x in enumerate(locations) if x["id"] == shot.get("locationId")), 0))
        r.Rcombo_box_set_selected(self.find("clip.speech"), 0 if shot.get("dialogueSource") == "native" else 1)
        r.Rcombo_box_set_selected(self.find("clip.speaker"), next((i for i,c in enumerate(self.details_speakers) if c and c['id']==shot.get('speakerId')),0))

    def apply_shot_details(self):
        shot = self.raw_shot
        if not shot:
            return
        scene, seed = int(self.text("clip.scene")), int(self.text("clip.seed"))
        if scene < 1 or not 0 <= seed < 2**32:
            raise ValueError("Camera setup must be positive. Seed must be between 0 and 4294967295.")
        previous = self.board["shots"][self.index - 1] if self.index else None
        continuing = bool(previous and previous.get("directorScene") == scene)
        frames = (4 + max(0, self.selected("clip.duration"))) * 24 + (0 if continuing else 1)
        location_index = self.selected("clip.location")
        updated=copy.deepcopy(shot)
        for widget, key in (("clip.title", "title"), ("clip.camera", "camera"), ("clip.prompt", "prompt"), ("clip.dialogue", "dialogue"), ("clip.sound", "soundNote"),
                            ("clip.state_before","stateBefore"),("clip.state_after","stateAfter"),("clip.delivery","dialogueStyle")):
            updated[key] = self.text(widget).strip()
        updated.update(directorScene=scene, frames=frames, seed=seed, dialogueSource="native" if self.selected("clip.speech") == 0 else "recording")
        updated["characterIds"] = [c["id"] for i, c in enumerate(self.board.get("characters", [])) if self.checked(f"clip.cast.{i}")]
        speaker_index=self.selected("clip.speaker")
        speakers=getattr(self,"details_speakers",[None])
        speaker=speakers[speaker_index] if 0<=speaker_index<len(speakers) else None
        updated["speakerId"]=speaker['id'] if speaker else ''
        if updated['dialogue'] and updated['dialogueSource']=='native' and not speaker and len(updated['characterIds'])!=1:
            raise ValueError("Choose a speaker for native dialogue, or select one visible character for automatic speaker selection.")
        if 0 <= location_index < len(self.details_locations):
            updated["locationId"] = self.details_locations[location_index]["id"]
        shot.update(updated)
        self.refresh_view()
        self.set_dirty(True)
        self.modal_close()
        self.save()

    def move_shot(self, delta):
        if not self.raw_shot:
            return
        target = self.index + delta
        if not 0 <= target < len(self.board["shots"]):
            return
        self.workspace.snapshots(self.slug, self.board)
        shots = self.board["shots"]
        shots[self.index], shots[target] = shots[target], shots[self.index]
        self.index = target
        self.refresh_view()
        self.set_dirty(True)
        self.modal_close()
        self.save()

    def remove_shot(self):
        if not self.raw_shot:
            return
        if not self.player.confirm(b"Remove the shot from the plan?", "Media and a copy of the plan remain in the project.".encode()):
            return
        self.workspace.snapshots(self.slug, self.board)
        self.board["shots"].pop(self.index)
        self.index = max(0, self.index - 1)
        self.refresh_view()
        self.set_dirty(True)
        self.modal_close()
        self.save()

    def show_anchors(self):
        shot = self.raw_shot
        if not shot:
            return
        self.anchor_choices = [x for x in self.catalogue if x["kind"] == "image"]
        existing = shot.get("imageAnchors", [])
        lines = "".join(f'<HBox>{v.label(str(a.get("frame")) + " · styrke " + str(a.get("strength")),12)}<Spacer Slot.Fill="1"/>{v.button("Remove",f"anchor.remove.{i}")}</HBox>' for i,a in enumerate(existing))
        body = v.label("Manual image anchors", 20) + (lines or v.label("No manual anchors yet.",12,True))
        body += '<Separator/><Label Text="PICTURE" Class="eyebrow"/><ComboBox Name="anchor.image" Items="' + v.esc("|".join(Path(x["path"]).name for x in self.anchor_choices) or "Import a reference image first") + '"/>'
        body += v.field("anchor.frame", "Image frame · 0 is the first new image, last is the final one", "last", 36, multiline=False)
        body += v.field("anchor.strength", "Strength · greater than 0 and up to 1", "0.65", 36, multiline=False)
        self.show_modal("Image anchors", body, "anchor.add", "Add anchor")

    def add_anchor(self):
        if not self.anchor_choices:
            raise ValueError("Import or create an image first.")
        shot = self.raw_shot
        frame = self.text("anchor.frame").strip()
        frame = "last" if frame.lower() == "last" else int(frame)
        strength = float(self.text("anchor.strength"))
        if not 0 < strength <= 1 or (frame != "last" and not 0 <= frame < shot["frames"]):
            raise ValueError("Choose a frame inside the shot and a strength between 0 and 1.")
        path = Path(self.anchor_choices[self.selected("anchor.image")]["path"])
        ref = self.ref_for(path)
        anchors = [a for a in shot.get("imageAnchors", []) if a.get("frame") != frame]
        if len(anchors) >= 32:
            raise ValueError("Maximum 32 anchors per shot.")
        anchors.append({"frame": frame, "strength": strength, "ref": ref})
        shot["imageAnchors"] = anchors
        self.set_dirty(True)
        self.save()
        self.show_anchors()

    def show_advanced(self):
        body = v.label("Selected tools for one take. Original media is preserved on retakes.",13,True)
        body += v.field("advanced.requirement", "What should be improved?", height=85, placeholder="E.g. clearer hand movement and a steadier camera")
        body += '<HBox Spacing="9">' + v.button("Three seed variants", "advanced.seeds") + v.button("Suggest a better prompt", "advanced.rewrite") + v.button("Focused improvement pass", "advanced.refine") + '</HBox>'
        body += '<Separator/>' + v.button("Prepare dialogue take", "advanced.dialogue")
        self.show_modal("Advanced takes", body)

    def advanced(self, kind):
        if not self.raw_shot:
            raise ValueError("Choose a clip first.")
        slug, shot_id = self.slug, self.raw_shot["id"]
        requirement = self.text("advanced.requirement")
        paths = {"seeds": "/api/render-seeds", "rewrite": "/api/rewrite", "refine": "/api/refine", "dialogue": "/api/prepare-dialogue"}
        payload = {"slug": slug, "shotId": shot_id}
        if kind == "seeds": payload["count"] = 3
        elif kind == "rewrite": payload.update(field="prompt", text=self.raw_shot.get("prompt", ""))
        elif kind == "refine": payload.update(requirement=requirement, maxAttempts=2, draft=True, service=self.backend.llm)
        def done(result):
            if kind == "rewrite":
                text = result.get("text") or result.get("rewritten") or result.get("prompt") or ""
                self.rewrite_text = text
                self.show_modal("Video prompt suggestion", v.label(text or str(result),13), "advanced.apply", "Use prompt")
            else:
                self.modal_close()
                self.mode_set("production")
                self.toast("The job was submitted. Follow progress in the production overview.")
        self.save(after=lambda: self.submit("advanced", lambda: self.backend.post(paths[kind], payload, timeout=1200), done))

    def ref_for(self, path):
        path = Path(path).resolve()
        relative = str(path.relative_to(self.workspace.projects_dir.resolve()))
        if self.workspace.local_media(relative) is None:
            raise ValueError("The image is outside the project library.")
        return {"kind": "upload", "path": relative, "url": "/media/" + relative, "label": path.name}

    def import_media(self):
        if not self.board:
            self.show_new_project()
            return
        raw = self.player.choose_media()
        if not raw:
            return
        source, slug = Path(raw.decode()), self.slug
        def operation():
            original = self.workspace.import_input(slug, source)
            if original.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".opus"}:
                return None
            return self.backend.upload(slug, original)
        def done(ref):
            self.toast("The reference was imported. The original is preserved in Input.")
            if ref and Path(ref.get("path", "")).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and len(self.image_refs) < 10:
                self.image_refs.append(ref)
            self.poll_deadline = 0
        self.save(after=lambda: self.submit("upload", operation, done))

    def import_project(self, source=None):
        raw = str(source).encode() if source else self.player.choose_project()
        if not raw:
            return
        source = Path(raw.decode()).resolve()
        if source.is_dir():
            source /= "storyboard.json"
        if source.is_relative_to(self.workspace.projects_dir.resolve()):
            self.open_project(source.relative_to(self.workspace.projects_dir.resolve()).parts[0])
            return
        if not self.ready:
            raise ValueError("The production core must be ready before import.")
        def operation():
            from importing import import_project
            return import_project(source, self.backend, self.workspace)
        def done(result):
            self.summaries = result[1]["boards"]
            self.open_project(result[0])
        self.submit("import", operation, done, scoped=False)
        self.toast("Importing a standalone copy of the project…")

    def show_settings(self, project=False):
        if project and self.board:
            body = v.field("settings.name", "Project name", self.board.get("name", ""), 36, multiline=False)
            body += '<Label Text="VIDEOFORMAT" Class="eyebrow"/><ComboBox Name="settings.size" Items="384 × 256 · verifisert|512 × 320|640 × 384|768 × 512"/>'
            body += v.field("settings.music", "Music description", self.board.get("soundtrackPrompt", ""), 70, placeholder="Musical style for export")
            body += '<Label Text="MODEL REVIEW AFTER TAKES" Class="eyebrow"/><ComboBox Name="settings.review" Items="Quick · manual review|Balanced · selected model checks|Thorough · additional checks"/>'
            body += '<HBox Spacing="10">' + v.button("Delete project…", "project.delete") + v.button("Archive project", "project.archive") + v.button("Export with all media", "project.export") + v.button("Create music", "soundtrack.generate") + '</HBox>'
            self.show_modal("Project settings", body, "settings.save", "Save settings")
            sizes = ["384x256", "512x320", "640x384", "768x512"]
            r.Rcombo_box_set_selected(self.find("settings.size"), sizes.index(self.board.get("defaults", {}).get("resolution", "384x256")) if self.board.get("defaults", {}).get("resolution", "384x256") in sizes else 0)
            modes = ["fast", "balanced", "thorough"]
            r.Rcombo_box_set_selected(self.find("settings.review"), modes.index(self.board.get("ravenSettings", {}).get("reviewMode", "fast")))
        else:
            body = v.label("PRODUCTION AREA",10,True) + v.label(str(self.workspace.root),15)
            body += v.label("New projects and media are saved here. Models use the existing installation.",13,True)
            body += '<HBox Spacing="10">' + v.button("Show production folder", "folder.root") + v.button("Reconnect", "backend.retry") + '</HBox>'
            body += '<Separator/>' + v.label("Productionsmotorer", 20)
            body += v.label("Images use Qwen in Comfy Desktop. Video uses LTX-2.5 Q8. Open the existing Comfy Desktop instance before creating media.",13,True)
            body += '<HBox Spacing="10">' + v.button("Open Comfy Desktop", "service.comfy") + v.button("Refresh engine status", "service.refresh") + '</HBox>'
            backend = (getattr(self, "info", {}) or {}).get("backend", {})
            body += v.label(backend.get("message") or "Production core starting…",12,True)
            for item in (getattr(self, "info", {}) or {}).get("llm", {}).get("services", []):
                body += v.label(item.get("label", item.get("id", "Language model")),13,True)
            self.show_modal("Studio settings", body)

    def open_comfy(self):
        app = Path("/Applications/Comfy Desktop.app")
        if not app.is_dir():
            raise ValueError("Comfy Desktop was not found in Applications.")
        self.submit("service.comfy", lambda: subprocess.run(["/usr/bin/open", "-a", str(app)], check=True, capture_output=True), lambda _: self.toast("Comfy Desktop is opening. Choose the LTX-2.5 Q8 instance if prompted."), scoped=False)

    def apply_settings(self):
        name = self.text("settings.name").strip()
        self.board.setdefault("defaults", {})["resolution"] = ["384x256", "512x320", "640x384", "768x512"][max(0, self.selected("settings.size"))]
        self.board["soundtrackPrompt"] = self.text("settings.music")
        self.board.setdefault("ravenSettings", {})["reviewMode"] = ["fast", "balanced", "thorough"][max(0, self.selected("settings.review"))]
        self.set_dirty(True)
        if name and name != self.board.get("name"):
            self.save(after=lambda: self.rename_project(name))
        else:
            self.save(after=self.modal_close)

    def rename_project(self, name):
        if self.editorial_io_busy() or any(key.startswith(("cut.", "coverage.")) for key in self.pending):
            raise ValueError("Wait until editing or export is finished before changing the project name.")
        slug = self.slug
        if not name.strip(): raise ValueError("The project needs a name.")
        def done(result):
            self.conversations.rebind_project(slug,result["slug"])
            self.current_conversation=self.conversations.load(self.current_conversation["id"])
            self.history=self.current_conversation["messages"]
            self.slug = result["slug"]
            self.board = result["board"]
            self.initialize_editing()
            self.initialize_coverage()
            self.initialize_story_workspace()
            if self.mode in {"structure","sound"}: self.open_story_workspace(self.mode)
            if self.mode == "coverage": self.open_coverage()
            self.summaries = self.workspace.project_summaries()
            self.refresh_view(); self.fill_brief(); self.modal_close()
        self.submit("rename", lambda: self.backend.post(f"/api/boards/{quote(slug)}/rename", {"name": name}), done)

    def archive_project(self):
        if not self.slug or not self.board:
            return
        if self.editorial_io_busy() or any(key.startswith(("cut.", "coverage.")) for key in self.pending):
            raise ValueError("Wait until editing or export is finished before archiving the project.")
        if self._busy():
            raise ValueError("Wait until production is finished before archiving the project.")
        name, slug = self.board.get("name", self.slug), self.slug
        if not self.player.confirm("Archive projectet?".encode(), f"«{name}» will move to Archive. No media will be deleted.".encode()):
            return
        def done(_):
            self.conversations.rebind_project(slug,None)
            self.current_conversation=self.conversations.load(self.current_conversation["id"])
            self.history=self.current_conversation["messages"]
            self._free_project()
            self.summaries = self.workspace.project_summaries()
            self.modal_close(); self.mode_set("home")
        self.save(after=lambda: self.submit("archive", lambda: self.workspace.archive(slug), done))

    def export_project(self):
        slug = self.slug
        def done(path):
            self.last_export = path
            self.show_modal("Export is ready", v.label(str(path),13), "export.reveal", "Show export")
        self.save(after=lambda: self.submit("export", lambda: self.workspace.export(slug), done))

    def _busy(self):
        film = (self.film_context or {}).get("project", {})
        job = (self.image_state or {}).get("job") or {}
        return bool(film.get("busy") or job.get("busy") or self.engine_state.get("busy") or self.engine_state.get("languageActive"))

    def poll(self):
        if not self.ready or not self.slug or "poll" in self.pending or self.closing or "project.delete" in self.pending or "project.restore" in self.pending:
            return
        slug, version, saving = self.slug, self.edit_version, "save" in self.pending
        def operation():
            return {"engine": self.backend.get("/api/status"),
                    "images": self.backend.get(f"/api/images?slug={quote(slug)}"),
                    "film": self.backend.get(f"/api/film/status?slug={quote(slug)}"),
                    "board": self.backend.get(f"/api/boards/{quote(slug)}"),
                    "catalogue": self.workspace.catalogue(slug)}
        def done(payload):
            self.sync_fields()
            previous_images = fingerprint(self.images)
            self.engine_state = payload["engine"]
            self.set_label("video.status",("Take in progress · "+str(self.engine_state.get("currentShotId") or self.engine_state.get("currentShot") or "working")) if self.engine_state.get("busy") else "Choose a completed take in take history.")
            clip_error = self.engine_state.get('error')
            self.set_label('quickclip.status',str(clip_error) if clip_error else 'Take in progress…' if self.engine_state.get('busy') else 'Open take history to view and choose a take.')
            self.set_attr('quickclip.generate','Enabled',not self._busy() and 'clip.generate' not in self.pending and 'video.generate' not in self.pending)
            self.set_attr('quickclip.stop','Enabled',self.engine_state.get('slug')==slug and bool(self.engine_state.get('busy')))
            self.image_state = payload["images"]
            self.images = [x for x in payload["images"].get("images", []) if x.get("status") == "done" and self._record_path(x)]
            self.film_context = payload["film"]
            self.catalogue = payload["catalogue"]
            actual = payload["board"]["board"]
            if not self.dirty and "save" not in self.pending and not saving and version == self.edit_version:
                changed = fingerprint(self.board.get("shots", [])) != fingerprint(actual.get("shots", []))
                self.board = actual
                if changed and self.mode in {"cut","structure","sound"}:
                    self.cut_refresh_pending = True
                if (changed or self.cut_refresh_pending) and not self.playing and not self.preview_media and not self.modal_kind:
                    focus=r.Rwindow_get_focused_widget(self.window)
                    editing_story=self.mode in {"structure","sound"} and (self.sound_gesture_pending or r.Rwindow_is_mouse_button_down(self.window,r.R_MOUSE_LEFT) or (focus!=r.R_INVALID_ID and r.Rwidget_get_type(focus) in {r.R_WIDGET_TEXT_INPUT,r.R_WIDGET_TEXT_EDITOR}))
                    if editing_story:
                        return
                    if self.mode in {"cut","structure","sound"} and self.cut_store and self.cut_doc:
                        self.cut_doc = self.cut_store.initialize(view_project(self.slug, self.board, self.workspace))
                        if self.mode in {"structure","sound"}:
                            self.sequence_doc=self.sequence_store.initialize(self.board,self.cut_doc["clips"])
                            if self.mode=="structure": self.render_structure()
                            else: self.render_sound()
                    self.cut_refresh_pending = False
                    self.refresh_view()
            if self.selected_image:
                self.selected_image = next((x for x in self.images if x.get("id") == self.selected_image.get("id")), None)
            if not self.selected_image and self.images:
                self.selected_image = self.images[0]
            job = payload["images"].get("job") or {}
            if job.get("busy"):
                self.set_label("image.status", f"Creating image · {job.get('phase', 'working')} · {float(job.get('progress') or 0):.0f}%")
            elif job.get("error"):
                self.set_label("image.status", "Image job stopped: " + str(job["error"])[:400])
            else:
                self.set_label("image.status", "Choose a result and review the image before using it.")
            self.set_attr("image.generate", "Enabled", "false" if self._busy() or "image.generate" in self.pending else "true")
            if previous_images != fingerprint(self.images) and self.mode == "images":
                self.render_images()
            if self.mode == "production": self.render_production()
            elif self.mode == "overview": self.render_overview()
            elif self.mode == "scenes": self.render_scenes()
        self.submit("poll", operation, done)
        self.poll_deadline = time.monotonic() + 2.0

    def preview(self, path, title="", *, inline=False):
        path = Path(path).resolve()
        in_project = path.is_relative_to(self.workspace.projects_dir.resolve())
        if not path.is_file() or not (in_project or self.library.resolve(path)):
            raise ValueError("The media file does not exist in the production area.")
        return_gate = bool(self.gate_key and self.mode == "production")
        self.player.close()
        self.active_video = ""
        self.playing = False
        self.preview_media = {"path": str(path), "return_gate": return_gate, "playing": False, "muted": False, "receipt": PlaybackReceipt()}
        self.player.mute(False)
        video = path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
        audio = path.suffix.lower() in {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"}
        if inline and self.mode in CUT_MODES:
            self.start_workspace_preview(path,title,video)
            return
        body = v.label("Audio playback",26) if audio and not video else '<Border Background="#0B1113" CornerRadius="10" Height="350"><Image Name="preview.image" Stretch="Fit"/></Border>'
        if video or audio:
            self.preview_media["time_media"] = True
            self.player.open(str(path).encode())
            body += '<HBox Spacing="12">' + v.button("Play / pause", "preview.play", True) + v.button("From the beginning", "preview.restart") + v.button("Audio on", "preview.mute", Name="preview.audio") + '<Label Name="preview.time" Text="00:00" Font="monospace" Slot.VAlign="Center"/></HBox>'
            body += v.label("Audio plays from the actual file. Review movement, speech, and the transition to the next opening.",12,True)
        self.show_modal(title or path.name, body, "preview.reveal", "Show file")
        if not (video or audio):
            r.Rimage_set_texture(self.find("preview.image"), self.texture(str(path)))

    def _tick_preview(self):
        if self.preview_media and self.preview_media.get("inline"):
            self.tick_workspace_preview()
            return
        if not self.preview_media or not self.preview_media.get("time_media"):
            return
        pixels, width, height = self.player.read_frame()
        if pixels:
            if self.video_texture == r.R_INVALID_ID:
                self.video_texture = r.Rtexture_create_from_pixels(pixels, width, height)
            else:
                r.Rtexture_update(self.video_texture, pixels, width, height)
            r.Rimage_set_texture(self.find("preview.image"), self.video_texture)
            self.frame_count += 1
        current, duration = self.player.time(), self.player.duration()
        self.set_label("preview.time", f"{current:.2f} / {duration:.2f} s")
        if self.preview_media["receipt"].update(current, duration, self.preview_media.get("playing") and not self.preview_media["muted"]):
            if not hasattr(self, "review_played"): self.review_played = set()
            self.review_played.add(self.preview_media["path"])
        if duration and current >= duration - 1 / 24:
            self.player.play(False)
            self.preview_media["playing"] = False

    def modal_close(self):
        if self.closing:
            return
        if self.preview_media:
            return_gate = self.preview_media.get("return_gate")
            self.preview_media = None
            self.player.close()
            self.player.mute(self.muted)
            self.active_video = ""
            if self.board and self.project.shots:
                super().select_shot(self.index)
            if return_gate:
                self.approve_gate(False)
                return
        self.modal_kind = ""
        self.set_attr("modal.scrim", "Visibility", "Collapsed")
        self.set_attr("app.content", "Enabled", "true")

    def reveal(self, area):
        base = self.workspace.ensure_project(self.slug) if self.slug else self.workspace.root
        path = {"root": self.workspace.root, "project": base, "script": base / "Script", "output": base / "Output", "library": self.library.library_dir}.get(area, base)
        self.player.reveal(str(path).encode())

    def command_palette(self):
        body = v.field("command.query", "Find a project or choose an action", height=36, multiline=False, placeholder="Project name or action…")
        body += '<VBox Name="command.results" Spacing="8"/>'
        self.show_modal("What do you want to do?", body)
        self.command_query = None
        r.Rwidget_focus(self.find("command.query"))

    def update_commands(self):
        if self.modal_kind != "What do you want to do?":
            return
        query = self.text("command.query").lower()
        if query == self.command_query:
            return
        self.command_query = query
        actions = [("Home", "page.home"), ("Create an image", "home.image"), ("Create a clip", "home.clip"), ("Film Studio", "home.studio"), ("New project", "project.new"), ("Import project", "project.import"), ("Open production folder", "folder.root"), ("Trash", "project.trash")]
        if self.board:
            actions += [("Sequences and duration", "page.structure"), ("Sound and mix", "page.sound")]
            actions += [("Write script", "page.brief"), ("Scene overview", "page.scenes"), ("Room map and coverage", "page.coverage"), ("Cut the film", "page.cut"), ("Create image", "page.images"), ("Add clip", "shot.add"), ("Production", "page.production"), ("AI Director", "page.director"), ("Export project", "project.export"), ("Delete project…", "project.delete")]
        actions += [(item.get("name", item["slug"]), "project.open." + item["slug"]) for item in self.summaries]
        self.replace_host("command.results", "".join(v.button(title, action) for title, action in actions if query in title.lower()) or v.label("No matches.",12,True))

    def _window_close(self):
        self.request_quit()
        return False

    def compare_images(self):
        if len(self.images) < 2:
            raise ValueError("Create or import at least two images to compare.")
        self.compare_records = list(self.images)
        choices = v.esc("|".join(f"{i + 1:02} · seed {record.get('seed', '–')}" for i, record in enumerate(self.compare_records)))
        body = '<HBox Spacing="16">'
        for side in ("a", "b"):
            body += f'<VBox Spacing="12" Slot.Fill="1"><Label Text="{"FIRST" if side == "a" else "SECOND"} PICTURE" Class="eyebrow"/><ComboBox Name="compare.{side}" Items="{choices}" OnChanged="image.compare.update"/><Border Background="#0C1113" Height="290" CornerRadius="9"><Image Name="compare.{side}.image" Stretch="Fit"/></Border><Label Name="compare.{side}.detail" Text="" Class="muted" Wrap="true"/></VBox>'
        self.show_modal("Compare looks", body + '</HBox>', detail="Choose two saved images and review framing, light, and continuity side by side.")
        r.Rcombo_box_set_selected(self.find("compare.a"), 0)
        r.Rcombo_box_set_selected(self.find("compare.b"), 1)
        self.update_comparison()

    def update_comparison(self):
        for side in ("a", "b"):
            index = self.selected("compare." + side)
            if 0 <= index < len(getattr(self, "compare_records", [])):
                record = self.compare_records[index]
                r.Rimage_set_texture(self.find("compare." + side + ".image"), self.texture(self._record_path(record)))
                self.set_label("compare." + side + ".detail", str(record.get("resolvedPrompt") or record.get("prompt") or "Retained image")[:350])

    def request_quit(self):
        if hasattr(self,"updater") and (self.update_waiting or self.updater.status()["stage"] in {"waiting","staged","installing"}):
            if not self.update_waiting:self.updater.request_install();self.begin_update_wait()
            return
        self.flush_sound_gesture()
        self.finish_coverage_drag()
        self.cancel_authoring()
        self.cancel_director()
        if self.closing:
            return
        self.sync_fields()
        self.capture_drafts()
        board, slug = copy.deepcopy(self.board), self.slug
        dirty = self.dirty
        self.closing = True
        self.playing = False
        self.player.play(False)
        self.show_modal("Closing studio", v.label("Saving the project and waiting for local production jobs to release resources. Finished takes and run history are preserved.",14,True))
        outstanding = list(self.pending.values())
        def operation():
            for future in outstanding:
                try:
                    future.result()
                except Exception:
                    pass
            if dirty and board and self.ready:
                try:
                    self.backend.put(f"/api/boards/{quote(slug)}", board)
                    self.workspace.sync_script(slug, board)
                except BackendError:
                    # A running film owns its board. Preserve pending authoring as a snapshot.
                    self.workspace.snapshots(slug, board)
            self.backend.close()
        def done(_):
            self.cleaned = True
            r.Rquit(0)
        self.submit("close", operation, done, scoped=False)

    def tick(self):
        self._drain()
        if self.closing:
            return
        self.flush_cut_gesture()
        self.flush_sound_gesture()
        self.layout_coverage()
        self.layout_editor()
        self.layout_creation()
        self.tick_coverage_drag()
        if self.preview_media:
            self.sync_fields()
            self._tick_preview()
            # Native menu actions must remain available during media review.
            menu = self.player.menu_action()
            if menu == 1: self.request_quit()
            elif menu == 3: self.save()
            elif menu == 11: self.show_updates()
        else:
            super().tick()
        self.update_commands()
        if self.mode == "director" and time.monotonic() >= self.composer_deadline:
            self.composer_deadline = time.monotonic() + 2
            self.capture_session_draft()
        if self.mode == "library" and not self.modal_kind and self.text("library.search").strip().lower() != self.library_query:
            self.render_library_results()
        if self.save_deadline and time.monotonic() >= self.save_deadline and "save" not in self.pending:
            self.save_deadline = 0
            if not (self.film_context.get("project") or {}).get("busy"):
                self.save()
        if time.monotonic() >= self.poll_deadline:
            self.poll()
        self.tick_updates()

    def close(self):
        if not self.cleaned:
            self.capture_drafts()
            try:
                self.backend.close()
            except BackendError as error:
                print(error)
        self.player.close()
        self.pool.shutdown(wait=False, cancel_futures=True)
        r.Rshutdown()

    def on_action(self, sender, action, user):
        if not getattr(self, "live", False):
            return super().on_action(sender, action, user)
        try:
            if self.updates_action(action):return
            if getattr(self,"update_waiting",False):
                if action=="app.quit":self.show_updates()
                return
            self.sync_fields()
            if self.closing:
                return
            if self.editorial_io_busy() and action != "app.quit":
                return
            if any(k.startswith("coverage.") for k in self.pending) and action != "app.quit":
                return
            if any(key in self.pending for key in ("rename", "archive")) and action != "app.quit":
                return
            if ("project.delete" in self.pending or "project.restore" in self.pending) and action != "app.quit":
                return
            if ("director.execute" in self.pending or getattr(self,"director_starting",False)) and action not in {"app.quit", "director.cancel"}:
                return
            if "director.author" in self.pending and action not in {"app.quit", "director.author.cancel"}:
                return
            if self.opening and action != "app.quit":
                return
            if self.mode=="sound":
                if action=="modal.close" and self.sound_gesture_pending:
                    self.sound_gesture_pending=False;self.sound_removed.clear();self.render_sound()
                    return
                self.flush_sound_gesture()
            if self.coverage_drag is not None:
                self.finish_coverage_drag(cancel=action == "modal.close")
            if self.mode == "cut" and self.cut_gesture_pending:
                self.flush_cut_gesture()
            if self.preview_media and self.preview_media.get("inline") and action.startswith("transport."):
                if action=="transport.play": action="preview.play"
                elif action=="transport.mute": action="preview.mute"
                elif action in {"transport.left","transport.right"}:
                    self.seek(self.player.time()+(-1 if action.endswith("left") else 1)/24)
                    return
                else: self.stop_workspace_preview(restore=True)
            if action.startswith("transport.") and self.modal_kind:
                if self.preview_media and action == "transport.play":
                    action = "preview.play"
                else:
                    return
            if action=="transport.play" and self.mode in {"sound","structure"}:
                action="sound.listen" if self.mode=="sound" else "sound.film"
            elif action=="transport.play" and self.mode=="cut" and (self.workspace.ensure_project(self.slug)/"Script/sound.json").exists():
                self.preview_cut_mix()
                return
            elif self.mode=="sound" and action in {"transport.left","transport.right"}:
                self.sound_cursor=max(0,self.sound_cursor+(-1 if action.endswith("left") else 1))
                r.Rtimeline_set_time(self.find("sound.timeline"),self.sound_cursor/24)
                return
            if action in {"edit.undo","edit.redo"} and self.mode in {"structure","sound"}:
                focus=r.Rwindow_get_focused_widget(self.window)
                if focus==r.R_INVALID_ID or r.Rwidget_get_type(focus) not in {r.R_WIDGET_TEXT_INPUT,r.R_WIDGET_TEXT_EDITOR}:
                    target="structure" if self.mode=="structure" else "sound"
                    action=target+(".redo" if action=="edit.redo" else ".undo")
            if self.creation_action(action) or self.workflow_action(action) or self.connections_action(action) or self.layout_action(action) or self.structure_action(action) or self.sound_action(action) or self.coverage_action(action) or self.editing_action(action):
                return
            if self.mode == "cut" and action in {"edit.undo", "edit.redo"}:
                focus = r.Rwindow_get_focused_widget(self.window)
                if focus == r.R_INVALID_ID or r.Rwidget_get_type(focus) not in {r.R_WIDGET_TEXT_INPUT, r.R_WIDGET_TEXT_EDITOR}:
                    self.cut_undo(action == "edit.redo")
                    return
            if action == "app.quit": self.request_quit()
            elif action == "modal.close": self.modal_close()
            elif action == "command.show": self.command_palette()
            elif action.startswith("page."):
                self.modal_close(); self.mode_set(action.split(".")[1])
            elif action in ("mode.film", "mode.library", "mode.bible"):
                self.mode_set(action.split(".")[1])
            elif action == "project.new": self.show_new_project()
            elif action == "project.create": self.create_project()
            elif action.startswith("project.open."):
                self.modal_close(); self.open_project(action.removeprefix("project.open."))
            elif action in ("file.open", "project.import"): self.import_project()
            elif action == "file.save": self.save()
            elif action == "project.select" and not self.updating:
                index = r.Rcombo_box_get_selected(sender)
                if 0 <= index < len(self.summaries): self.open_project(self.summaries[index]["slug"])
            elif action == "project.settings": self.show_settings(True)
            elif action == "settings.show": self.show_settings()
            elif action == "settings.save": self.apply_settings()
            elif action == "service.comfy": self.open_comfy()
            elif action == "service.refresh":
                def refreshed(info):
                    self.info = info
                    self.show_settings()
                self.submit("service.refresh", lambda: self.backend.get("/api/info"), refreshed, scoped=False)
            elif action == "backend.retry": self.submit("boot", self._bootstrap, self._booted, scoped=False)
            elif action == "project.trash": self.show_project_trash()
            elif action == "project.delete.confirm": self.delete_project()
            elif action == "project.delete": self.show_delete_project()
            elif action.startswith("project.delete."): self.show_delete_project(action.removeprefix("project.delete."))
            elif action.startswith("project.restore."): self.restore_deleted_project(action.removeprefix("project.restore."))
            elif action == "project.archive": self.archive_project()
            elif action == "project.export": self.export_project()
            elif action == "export.reveal": self.player.reveal(str(self.last_export).encode())
            elif action.startswith("folder."): self.reveal(action.split(".")[1])
            elif action == "overview.next": self.mode_set(self.next_page)
            elif action in {"brief.idea", "brief.script", "director.open_script"}:
                self.brief_view = "idea" if action == "brief.idea" else "script"
                if self.mode != "brief": self.mode_set("brief")
                else: self.render_manuscript()
            elif action.startswith("manuscript.shot."):
                self.mode_set("film"); self.select_shot(int(action.rsplit(".",1)[1]))
            elif action == "shot.add": self.add_shot()
            elif action == "shot.continue": self.add_shot(True)
            elif action == "shot.details": self.show_shot_details()
            elif action == "shot.apply": self.apply_shot_details()
            elif action == "shot.up": self.move_shot(-1)
            elif action == "shot.down": self.move_shot(1)
            elif action == "shot.remove": self.remove_shot()
            elif action == "shot.anchors": self.show_anchors()
            elif action == "anchor.add": self.add_anchor()
            elif action.startswith("anchor.remove."):
                self.raw_shot["imageAnchors"].pop(int(action.rsplit(".",1)[1])); self.set_dirty(True); self.save(); self.show_anchors()
            elif action == "shot.image":
                shot = self.raw_shot
                self.modal_close(); self.mode_set("images")
                self.set_text("image.prompt", " ".join(str(shot.get(key) or "") for key in ("start", "camera")) or shot.get("prompt", ""))
                self.image_refs = [c["image"] for c in self.board.get("characters", []) if c.get("image") and c["id"] in shot.get("characterIds", [])][:10]
                self.image_target = {"kind": "shot", "shot": shot["id"]}; self.render_images()
            elif action == "shot.advanced": self.show_advanced()
            elif action.startswith("advanced."):
                kind = action.split(".")[1]
                if kind == "apply":
                    self.raw_shot["prompt"] = self.rewrite_text; self.set_dirty(True); self.save(after=self.modal_close)
                else: self.advanced(kind)
            elif action.startswith("bible.add."): self.show_entity(action.split(".")[2])
            elif action.startswith("bible."):
                _, kind, ident = action.split(".", 2); self.show_entity(kind, ident)
            elif action == "entity.save": self.save_entity()
            elif action == "entity.import": self.import_entity_ref()
            elif action == "entity.generate": self.generate_entity_image()
            elif action == "entity.remove": self.remove_entity()
            elif action == "media.import": self.import_media()
            elif action == "image.generate": self.capture_drafts(); self.generate_image()
            elif action.startswith("image.select."): self.select_image(int(action.rsplit(".",1)[1]))
            elif action == "image.assign": self.assign_image()
            elif action == "image.compare": self.compare_images()
            elif action == "image.compare.update": self.update_comparison()
            elif action == "image.publish": self.publish_image()
            elif action == "image.references": self.show_references()
            elif action == "image.references.save": self.save_references()
            elif action == "image.reveal":
                path = self._record_path(self.selected_image)
                if path: self.player.reveal(path.encode())
            elif action == "image.stop":
                job = (self.image_state or {}).get("job") or {}; slug = self.slug
                if job.get("jobId") and job.get("busy"):
                    payload = {"slug":slug,"jobId":job["jobId"]}
                    self.submit("image.stop", lambda: self.backend.post("/api/images/stop",payload), lambda _: self.toast("The selected image job is stopping."))
            elif action.startswith("library.open."): self.open_library(int(action.rsplit(".",1)[1]))
            elif action.startswith("library.adopt."): self.adopt_library(int(action.rsplit(".",1)[1]))
            elif action in {"library.search", "library.refresh"}:
                if action == "library.refresh": self.refresh_library()
                else: self.render_library_results()
            elif action.startswith("library.filter."):
                self.library_scope = action.rsplit(".",1)[1]; self.render_library_results()
            elif action == "conversation.new": self.new_conversation()
            elif action == "conversation.general": self.new_conversation(True)
            elif action == "conversation.project": self.select_conversation_project()
            elif action == "conversation.archive": self.archive_conversation()
            elif action == "conversation.rename": self.rename_conversation()
            elif action == "conversation.rename.save": self.rename_conversation(True)
            elif action.startswith("conversation.open."): self.activate_conversation(action.rsplit(".",1)[1])
            elif action.startswith("director.starter."): self.starter_message(action.rsplit(".",1)[1])
            elif action.startswith("director.diagnostics."): self.show_director_diagnostics(int(action.rsplit(".",1)[1]))
            elif action.startswith("director.resume."): self.resume_director_manuscript(int(action.rsplit(".",1)[1]))
            elif action.startswith("director.result."): self.show_director_result(int(action.rsplit(".",1)[1]))
            elif action.startswith("director.artifact."): self.open_director_artifact(int(action.rsplit(".",1)[1]))
            elif action == "director.cancel": self.cancel_director()
            elif action == "director.send": self.send_chat()
            elif action == "director.author.show": self.show_authoring()
            elif action == "director.author.start": self.start_authoring()
            elif action == "director.author.cancel": self.cancel_authoring()
            elif action == "director.plan":
                self.mode_set("director"); self.set_text("director.message", "Suggest a feasible scene plan for the film idea in this project. Use short clips with a start, one action, ending, and camera. Use only known characters and places, and suggest clips in this project without starting production.")
            elif action.startswith("director.apply."): self.apply_chat(int(action.rsplit(".",1)[1]))
            elif action.startswith("director.retry."): self.retry_director(int(action.rsplit(".",1)[1]))
            elif action == "production.refresh": self.poll_deadline = 0
            elif action == "production.prepare": self.run_film("prepare")
            elif action == "production.resume": self.run_film("produce")
            elif action == "production.approve": self.approve_gate(False)
            elif action == "production.approve.confirm": self.approve_gate(True)
            elif action == "production.stop": self.stop_film()
            elif action == "production.assemble": self.assemble()
            elif action == "production.play_final": self.play_final()
            elif action.startswith("production.clip."):
                index = int(action.rsplit(".",1)[1])
                if 0 <= index < len(self.project.shots) and self.project.shots[index].take and self.project.shots[index].take.video:
                    self.preview(Path(self.project.shots[index].take.video), self.project.shots[index].title)
            elif action.startswith("production.review_media."):
                self.review_media(int(action.rsplit(".",1)[1]))
            elif action == "take.history": self.show_take_history()
            elif action.startswith("take.preview."): self.preview_seed_take(int(action.rsplit(".",1)[1]))
            elif action.startswith("take.adopt."): self.adopt_seed_take(int(action.rsplit(".",1)[1]))
            elif action == "soundtrack.generate":
                slug = self.slug
                self.save(after=lambda:self.submit("music", lambda:self.backend.post(f"/api/boards/{quote(slug)}/soundtrack",{},timeout=1200),lambda _:self.toast("Music job is complete.")))
            elif action == "preview.play":
                if self.preview_media:
                    if self.player.duration() and self.player.time() >= self.player.duration() - 1/24: self.player.seek(0)
                    playing = not self.preview_media["playing"]; self.preview_media["playing"] = playing; self.player.play(playing)
            elif action == "preview.restart": self.player.seek(0)
            elif action == "preview.mute" and self.preview_media:
                self.preview_media["muted"] = not self.preview_media["muted"]
                self.player.mute(self.preview_media["muted"])
                r.Rbutton_set_text(self.w("audio") if self.preview_media.get("inline") else self.find("preview.audio"), "Audio off" if self.preview_media["muted"] else "Audio on")
            elif action == "preview.reveal" and self.preview_media: self.player.reveal(self.preview_media["path"].encode())
            else:
                super().on_action(sender, action, user)
        except (OSError, ValueError, TypeError, KeyError, IndexError, BackendError) as error:
            self.toast(error, True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--config",type=Path,help="private Raven connection settings")
    parser.add_argument("--engine-root",type=Path)
    parser.add_argument("--runtime-python",type=Path)
    parser.add_argument("--workspace",type=Path)
    parser.add_argument("--comfyui-url")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--update-smoke",action="store_true")
    args = parser.parse_args(argv)
    qa_root=os.environ.get("SPRINTWAKE_QA_ROOT")
    if args.update_smoke and not qa_root:raise ValueError("Update smoke requires an isolated QA bundle")
    if qa_root:
        args.root=Path(qa_root)/"Productions";args.config=Path(qa_root)/"connections.json"
        args.update_smoke=True
    temporary = tempfile.TemporaryDirectory(prefix="raven-studio-qa-") if args.smoke_test and not args.root else None
    root = Path(temporary.name) if temporary else args.root
    store=ConnectionStore(args.config or (root/"System/connections.json" if args.smoke_test else None))
    try:config=store.load()
    except (OSError,ValueError):config=None
    for key,value in (("engine",args.engine_root),("python",args.runtime_python),("workspace",args.workspace)):
        if value and config is not None:config["paths"][key]=str(value.expanduser().resolve())
    if args.comfyui_url and config is not None:config["comfyUrl"]=args.comfyui_url
    from contracts.app_lifetime import AppLifetime
    if qa_root or temporary:
        os.environ['SPRINTWAKE_UPDATE_LOCK_ROOT']=str(Path(qa_root or temporary.name)/'UpdateLocks')
    guard=AppLifetime(bundle=os.environ.get('SPRINTWAKE_APP_BUNDLE'),build=os.environ.get('SPRINTWAKE_APP_BUILD')).enter_app()
    studio=None
    qa_report=None
    try:
        studio = ProductionStudio(root,connection_store=store,connection_settings=config)
        studio.update_guard=guard
        studio.startup_project = args.project
        if args.update_smoke:
            from update_smoke import run
            qa_report=run(studio,qa_root)
        elif args.smoke_test:
            from production_smoke import run
            run(studio)
        else:
            while r.Rwait_frame(1/60):
                studio.tick()
    except Exception as error:
        if args.update_smoke:(Path(qa_root)/'failure.json').write_text(json.dumps({'error':str(error)}))
        raise
    finally:
        try:
            if studio is not None:studio.close()
        finally:
            guard.close()
            if temporary: temporary.cleanup()
    if qa_report is not None:(Path(qa_root)/"result.json").write_text(json.dumps(qa_report))


if __name__ == "__main__":
    main()

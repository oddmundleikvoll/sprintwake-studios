"""Storyboard Raven: an actual Raven desktop, isolated from production."""
from __future__ import annotations

import argparse
import ctypes
from html import escape
import json
from pathlib import Path
import struct
import sys
import time
import zlib

import raven as r

from media import Player
from project import ROOT, Project, demos, import_project, load_working_copy, save_project


def e(value: object) -> str:
    return escape(str(value), quote=True)


def image_xml(path: str, **attrs) -> str:
    options = " ".join(f'{k}="{e(v)}"' for k, v in attrs.items())
    return f'<Image Source="{e(path)}" Stretch="Cover" {options}/>' if path else f'<Border Background="#252A2D" {options}><Label Text="No images" Class="muted" Slot.HAlign="Center" Slot.VAlign="Center"/></Border>'


def timecode(seconds: float) -> str:
    frames = max(0, round(seconds * 24))
    return f"{frames // 1440:02}:{frames // 24 % 60:02}:{frames % 24:02}"


def write_png(path: Path, pixels: bytes, width: int, height: int):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    scan = b"".join(b"\0" + pixels[y * width * 4:(y + 1) * width * 4] for y in range(height))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(scan, 6)) + chunk(b"IEND", b""))


class Studio:
    def __init__(self, initial_projects=None):
        self.errors: list[dict] = []
        r.Rerror_set_callback(self.on_error)
        if not r.Rinit(r.R_BACKEND_VULKAN):
            raise RuntimeError(f"Raven could not start: {r.Rerror_get_last()}")
        r.Rxml_set_live_reload(False)
        r.Rtheme_load(str(ROOT / "assets/copper.xml"))
        r.Rtheme_use("Copper")
        self.player = Player()
        self.player.prepare()
        self.projects = demos() if initial_projects is None else initial_projects
        for i, p in enumerate(self.projects if initial_projects is None else []):
            saved = ROOT / "workspace" / f"{p.id}.raven.json"
            if saved.is_file():
                try:
                    self.projects[i] = import_project(saved)
                except (OSError, ValueError, TypeError, KeyError) as error:
                    print(f"Could not read working copy {saved.name}: {error}", file=sys.stderr)
        self.project = self.projects[0]
        self.index = 0
        self.mode = "film"
        self.dirty = False
        self.playing = False
        self.clock = 0.0
        self.last_tick = time.monotonic()
        self.active_video = ""
        self.video_texture = r.R_INVALID_ID
        self.textures: dict[str, int] = {}
        self.timeline_items: dict[int, int] = {}
        self.timeline_tracks: list[int] = []
        self.updating = False
        self.muted = False
        self.frame_count = 0
        self.compact = False
        self.window = r.Rwindow_create_from_xml_file(str(ROOT / "assets/studio.xml"))
        if self.window == r.R_INVALID_ID:
            raise RuntimeError("Could not build the Raven window.")
        native = r.RNativeWindow()
        if r.Rwindow_get_native_handle(self.window, native):
            self.player.attach_window(native.display)
        r.Raction_set_default_handler(self.on_action)
        self.widgets = {name: r.Rwidget_find(self.window, name) for name in (
            "projects", "shots", "project.title", "project.subtitle", "shot.title", "shot.meta",
            "viewer", "viewer.empty", "viewer.kind", "viewer.source", "viewer.caption", "viewer.camera",
            "play", "audio", "time", "total", "timeline", "plan.summary", "save.state", "status",
            "inspector", "regi", "takes", "take.list", "start", "action", "end", "notes", "review",
            "review.state", "sidebar.count", "workspace", "library", "bible", "bible.image",
            "bible.name", "bible.description", "film.page", "nav.film", "nav.library", "nav.bible", "tab.regi", "tab.takes")}
        missing = [k for k, v in self.widgets.items() if v == r.R_INVALID_ID]
        if missing:
            raise RuntimeError(f"Manglende grensesnitt: {missing}")
        r.Rtimeline_set_time_callback(self.w("timeline"), self.on_scrub)
        r.Rtimeline_set_selection_callback(self.w("timeline"), self.on_timeline_selection)
        r.Rtimeline_set_changed_callback(self.w("timeline"), self.on_timeline_change)
        for name in ("start", "action", "end", "notes"):
            r.Rtext_editor_set_changed_callback(self.w(name), lambda _sender, _user, n=name: self.edit(n))
        self.set_projects()
        self.populate_project()

    def w(self, name):
        return self.widgets[name]

    def attr(self, name, key, value):
        r.Rwidget_set_attribute(self.w(name), key, str(value))

    def label(self, name, value):
        r.Rlabel_set_text(self.w(name), str(value))

    def on_error(self, code, severity, detail):
        self.errors.append({"code": int(code), "severity": int(severity), "detail": detail})
        print(f"Raven: {detail}", file=sys.stderr)

    def replace(self, name, xml):
        widget = self.w(name)
        r.Rwidget_clear_children(widget)
        r.Rwidget_add_child(widget, r.Rwidget_create_from_xml(xml))

    def texture(self, path):
        if not path:
            return r.R_INVALID_ID
        if path not in self.textures:
            self.textures[path] = r.Rtexture_create_from_file(path)
        return self.textures[path]

    def set_projects(self):
        # Rebuild items through the public combo interface, with user-facing labels only.
        was_updating = self.updating
        self.updating = True
        r.Rcombo_box_clear_items(self.w("projects"))
        for p in self.projects:
            r.Rcombo_box_add_item(self.w("projects"), p.title)
        r.Rcombo_box_set_selected(self.w("projects"), self.projects.index(self.project))
        self.updating = was_updating

    def sidebar_xml(self):
        cards = []
        for i, shot in enumerate(self.project.shots):
            img = shot.take.image if shot.take else ""
            cards.append(f'''<Button Name="shot.{i}" Class="shot" OnClicked="shot.{i}" Metric.PaddingX="8" Metric.PaddingY="8">
              <VBox Spacing="9" HitTestable="false">
                {image_xml(img, Height="100", CornerRadius="5")}
                <HBox><Label Text="{i + 1:02}" Class="accent" TextSize="11" Width="23"/>
                  <Label Text="{e(shot.title)}" Slot.Fill="1" TextSize="13"/>
                  <Label Text="{shot.duration:.1f}s" Class="muted" TextSize="10"/></HBox>
              </VBox></Button>''')
        empty = '<Label Text="No clips yet. The original project is open as an empty working copy." Wrap="true" Class="muted"/>'
        return f'<VBox Spacing="12">{"".join(cards) or empty}</VBox>'

    def populate_project(self):
        self.updating = True
        self.index = min(self.index, max(0, len(self.project.shots) - 1))
        self.label("project.title", self.project.title)
        self.label("project.subtitle", self.project.subtitle)
        self.label("sidebar.count", f"{len(self.project.shots):02} SHOTS")
        self.label("total", timecode(self.project.duration))
        self.label("plan.summary", f"{len(self.project.shots)} shots  /  {self.project.duration:.1f} seconds  /  24 fps")
        self.replace("shots", self.sidebar_xml())
        timeline = self.w("timeline")
        r.Rtimeline_clear(timeline)
        r.Rtimeline_set_duration(timeline, max(1, self.project.duration + (10 if self.mode == "cut" else 0)))
        r.Rtimeline_set_frame_rate(timeline, 24)
        r.Rtimeline_set_view(timeline, 0, max(5, self.project.duration + 1))
        track = r.Rtimeline_add_track(timeline, "PICTURE")
        r.Rtimeline_set_track_color(timeline, track, .77, .57, .40)
        r.Rtimeline_set_track_height(timeline, track, 42)
        r.Rtimeline_set_track_locked(timeline, track, self.mode != "cut")
        self.timeline_items = {}
        start = 0
        for i, shot in enumerate(self.project.shots):
            item = r.Rtimeline_add_clip(timeline, track, start, shot.duration, f"{i + 1:02}  {shot.title}")
            self.timeline_items[item] = i
            r.Rtimeline_set_item_color(timeline, item, .45, .34, .25)
            start += shot.duration
        audio = r.Rtimeline_add_track(timeline, "SOUND")
        self.timeline_tracks = [track, audio]
        r.Rtimeline_set_track_locked(timeline, audio, True)
        r.Rtimeline_set_track_height(timeline, audio, 28)
        r.Rtimeline_set_track_color(timeline, audio, .44, .59, .49)
        if any(s.take and s.take.video for s in self.project.shots):
            start = 0
            for shot in self.project.shots:
                if shot.take and shot.take.video:
                    r.Rtimeline_add_clip(timeline, audio, start, shot.duration, "Original audio · confirmed on playback")
                start += shot.duration
        else:
            r.Rtimeline_add_marker(timeline, 0, "Image plan · audio not produced")
        r.Rimage_set_texture(self.w("bible.image"), self.texture(self.project.character_image))
        self.label("bible.name", self.project.character_name or "No character card")
        self.label("bible.description", self.project.character_description or "Open a project with selected character references.")
        self.updating = False
        self.select_shot(self.index)
        self.set_dirty(False)

    @property
    def shot(self):
        return self.project.shots[self.index] if self.project.shots else None

    def select_shot(self, index, *, seek=True):
        previous_index = self.index
        if not self.project.shots:
            self.player.close()
            self.active_video = ""
            self.playing = False
            r.Rimage_set_texture(self.w("viewer"), r.R_INVALID_ID)
            self.attr("viewer.empty", "Visibility", "Visible")
            self.attr("inspector", "Enabled", "false")
            self.label("shot.title", "No shots")
            self.label("shot.meta", "Open an image plan to begin")
            self.label("viewer.kind", "EMPTY PROJECT")
            self.label("viewer.source", "The original project has no shots or media")
            self.label("viewer.caption", "No shot selected")
            self.label("viewer.camera", "")
            self.attr("audio", "Enabled", "false")
            self.update_clock()
            return
        self.attr("inspector", "Enabled", "true")
        self.index = max(0, min(index, len(self.project.shots) - 1))
        shot = self.shot
        self.updating = True
        for i in range(len(self.project.shots)):
            widget = r.Rwidget_find(self.window, f"shot.{i}")
            if widget != r.R_INVALID_ID:
                r.Rwidget_set_attribute(widget, "Class", "shot shot-active" if i == self.index else "shot")
        self.label("shot.title", shot.title)
        self.label("shot.meta", f"SHOT {self.index + 1:02}  /  {shot.duration:.1f} SECONDS")
        self.label("viewer.camera", shot.camera)
        for name in ("start", "action", "end", "notes"):
            r.Rtext_editor_set_text(self.w(name), getattr(shot, name))
        self.update_review()
        cards = []
        for i, take in enumerate(shot.takes):
            cards.append(f'''<Button Class="{'take-active' if i == shot.selected_take else ''}" OnClicked="take.{i}" Metric.PaddingX="7" Metric.PaddingY="7">
              <HBox Spacing="12" HitTestable="false">{image_xml(take.image, Width="84", Height="57", CornerRadius="4")}
                <VBox Slot.Fill="1" Spacing="5" Slot.VAlign="Center"><Label Text="{e(take.label)}"/><Label Text="{'Selected' if i == shot.selected_take else 'Choose this image'}" Class="muted" TextSize="11"/></VBox></HBox></Button>''')
        self.replace("take.list", f'<VBox Spacing="10">{"".join(cards) or "<Label Text='No takes in project' Class='muted'/>"}</VBox>')
        take = shot.take
        video = take.video if take else ""
        if video != self.active_video or (video and previous_index != self.index):
            self.player.close()
            self.active_video = video
            if video:
                if not self.player.open(video.encode()):
                    self.label("status", "Could not open the video")
                self.player.seek(shot.source_start)
                if self.playing:
                    self.player.play(True)
        image = take.image if take else ""
        if not video:
            r.Rimage_set_texture(self.w("viewer"), self.texture(image))
        else:
            # A new shot must never show a previous shot's decoded pixels.
            r.Rimage_set_texture(self.w("viewer"), r.R_INVALID_ID)
        self.attr("viewer.empty", "Visibility", "Collapsed" if image or video else "Visible")
        self.label("viewer.kind", "VIDEO" if video else "STORYBOARD")
        self.label("viewer.source", take.provenance if take else "No media selected")
        self.label("viewer.caption", f"{self.index + 1:02}  /  {shot.title}")
        r.Rbutton_set_text(self.w("audio"), "Audio off" if self.muted else "Audio on" if video else "No audio")
        self.attr("audio", "Enabled", "true" if video else "false")
        if seek:
            self.clock = sum(s.duration for s in self.project.shots[:self.index])
            if video:
                self.player.seek(shot.source_start)
        r.Rtimeline_clear_selection(self.w("timeline"))
        for item, i in self.timeline_items.items():
            if i == self.index:
                r.Rtimeline_select(self.w("timeline"), item, False)
                break
        self.updating = False
        self.update_clock()

    def update_review(self):
        reviewed = self.shot and self.shot.reviewed
        self.label("review.state", "Reviewed in the working copy" if reviewed else "Ready for your review")
        r.Rbutton_set_text(self.w("review"), "Open for another review" if reviewed else "Mark as reviewed")

    def edit(self, name):
        if self.updating or not self.shot:
            return
        setattr(self.shot, name, r.Rtext_editor_get_text(self.w(name)))
        self.shot.reviewed = False
        self.update_review()
        self.set_dirty(True)

    def sync_fields(self):
        # Native accessibility setters in this Raven revision do not emit the
        # editor's changed callback. Commit those edits through the same model.
        if self.updating or not self.shot:
            return
        changed = False
        for name in ("start", "action", "end", "notes"):
            value = r.Rtext_editor_get_text(self.w(name))
            if value != getattr(self.shot, name):
                setattr(self.shot, name, value)
                changed = True
        if changed:
            self.shot.reviewed = False
            self.update_review()
            self.set_dirty(True)

    def set_dirty(self, value):
        self.dirty = value
        self.label("save.state", "Unsaved changes" if value else "Working copy")

    def save(self):
        path = save_project(self.project)
        self.set_dirty(False)
        self.label("status", f"Saved locally · {path.name}")
        return path

    def switch_project(self, index):
        if not 0 <= index < len(self.projects) or self.projects[index] is self.project:
            return
        if self.dirty:
            self.save()
        self.player.close()
        self.active_video = ""
        self.playing = False
        self.clock = 0
        self.project = self.projects[index]
        self.index = 0
        self.set_projects()
        self.populate_project()
        self.label("status", "Image plan is ready" if index == 0 else "Preserved test clip · separate video study")

    def mode_set(self, mode):
        self.mode = mode
        self.attr("workspace", "Index", {"film": 0, "library": 1, "bible": 2}[mode])
        for name in ("film", "library", "bible"):
            page = "film.page" if name == "film" else name
            self.attr(page, "Visibility", "Visible" if name == mode else "Collapsed")
            self.attr("nav." + name, "Class", "nav nav-active" if name == mode else "nav")

    def on_action(self, sender, action, _user):
        try:
            self.sync_fields()
            if action.startswith("shot."):
                self.select_shot(int(action.split(".")[1]))
            elif action.startswith("take.") and self.shot:
                self.shot.selected_take = int(action.split(".")[1])
                self.shot.reviewed = False
                self.select_shot(self.index)
                self.set_dirty(True)
            elif action == "project.select" and not self.updating:
                self.switch_project(r.Rcombo_box_get_selected(sender))
            elif action.startswith("mode."):
                self.mode_set(action.split(".")[1])
            elif action == "library.probe":
                self.switch_project(1)
                self.mode_set("film")
            elif action == "library.rodbert":
                self.switch_project(0)
                self.mode_set("bible")
            elif action == "file.save":
                self.save()
            elif action == "file.open":
                path = self.player.choose_project()
                if path:
                    if self.dirty:
                        self.save()
                    project = load_working_copy(Path(path.decode()))
                    existing = next((i for i, p in enumerate(self.projects) if p.id == project.id), None)
                    if existing is None:
                        self.projects.append(project)
                        self.switch_project(len(self.projects) - 1)
                    else:
                        self.switch_project(existing)
                    self.mode_set("film")
            elif action == "review.toggle" and self.shot:
                self.shot.reviewed = not self.shot.reviewed
                self.update_review()
                self.set_dirty(True)
            elif action.startswith("tab."):
                takes = action == "tab.takes"
                self.attr("regi", "Visibility", "Collapsed" if takes else "Visible")
                self.attr("takes", "Visibility", "Visible" if takes else "Collapsed")
                self.attr("tab.regi", "Class", "nav" if takes else "nav nav-active")
                self.attr("tab.takes", "Class", "nav nav-active" if takes else "nav")
            elif action == "transport.play":
                focus = r.Rwindow_get_focused_widget(self.window)
                if focus != r.R_INVALID_ID and r.Rwidget_get_type(focus) in (r.R_WIDGET_TEXT_EDITOR, r.R_WIDGET_TEXT_INPUT):
                    return
                if self.project.shots:
                    if self.clock >= self.project.duration - .03:
                        self.seek(0)
                    self.playing = not self.playing
                    if self.active_video:
                        self.player.play(self.playing)
                    self.last_tick = time.monotonic()
            elif action == "transport.mute":
                self.muted = not self.muted
                self.player.mute(self.muted)
                r.Rtimeline_set_track_muted(self.w("timeline"), self.timeline_tracks[1], self.muted)
                r.Rbutton_set_text(self.w("audio"), "Audio off" if self.muted else "Audio on")
            elif action in ("transport.prev", "transport.next"):
                self.select_shot(self.index + (-1 if action.endswith("prev") else 1))
            elif action in ("transport.left", "transport.right"):
                focus = r.Rwindow_get_focused_widget(self.window)
                if focus == r.R_INVALID_ID or r.Rwidget_get_type(focus) not in (r.R_WIDGET_TEXT_EDITOR, r.R_WIDGET_TEXT_INPUT):
                    self.seek(self.clock + (-1 if action.endswith("left") else 1) / 24)
            elif action == "app.quit":
                if self.dirty:
                    self.save()
                r.Rquit(0)
            elif action.startswith("edit."):
                editor = r.Rwindow_get_focused_widget(self.window)
                if editor != r.R_INVALID_ID and r.Rwidget_get_type(editor) == r.R_WIDGET_TEXT_INPUT:
                    command = action.split(".")[1]
                    key = {"all": r.R_KEY_A, "copy": r.R_KEY_C, "cut": r.R_KEY_X, "paste": r.R_KEY_V, "undo": r.R_KEY_Z, "redo": r.R_KEY_Z}.get(command)
                    if key:
                        modifiers = r.R_MOD_SUPER | (r.R_MOD_SHIFT if command == "redo" else 0)
                        r.Rwindow_inject_key(self.window, key, True, modifiers)
                        r.Rwindow_inject_key(self.window, key, False, modifiers)
                    return
                if editor == r.R_INVALID_ID or r.Rwidget_get_type(editor) != r.R_WIDGET_TEXT_EDITOR:
                    return
                command = action.split(".")[1]
                if command == "all":
                    r.Rtext_editor_select_all(editor)
                elif command in ("copy", "cut"):
                    r.Rclipboard_set_text(r.Rtext_editor_get_selected_text(editor))
                    if command == "cut":
                        r.Rtext_editor_insert_text(editor, "")
                elif command == "paste":
                    r.Rtext_editor_insert_text(editor, r.Rclipboard_get_text() or "")
                elif command == "undo":
                    r.Rtext_editor_undo(editor)
                elif command == "redo":
                    r.Rtext_editor_redo(editor)
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.label("status", f"Could not complete: {error}")
            print(f"Handling {action}: {error}", file=sys.stderr)

    def seek(self, seconds):
        self.sync_fields()
        self.clock = max(0, min(self.project.duration, seconds))
        index, local = self.project.shot_at(self.clock)
        if index != self.index:
            self.select_shot(index, seek=False)
        if self.active_video:
            self.player.seek(self.shot.source_start + local)
        self.update_clock()

    def on_scrub(self, _sender, seconds, _user):
        if not self.updating:
            self.seek(seconds)

    def on_timeline_selection(self, _sender, item, _user):
        if not self.updating and item in self.timeline_items:
            self.sync_fields()
            self.select_shot(self.timeline_items[item])

    def on_timeline_change(self, _sender, _item, _user):
        # The prototype's sequence is a review surface. Reject native widget edits.
        # A later editor must validate timing/reordering as a project operation.
        if self.updating:
            return
        timeline = self.w("timeline")
        self.updating = True
        start = 0.0
        for item, index in self.timeline_items.items():
            shot = self.project.shots[index]
            r.Rtimeline_set_item(timeline, item, self.timeline_tracks[0], start, shot.duration)
            start += shot.duration
        self.updating = False
        self.label("status", "The timeline shows the plan. Change order and duration in the clip Details.")

    def update_clock(self):
        self.label("time", timecode(self.clock))
        r.Rtimeline_set_time(self.w("timeline"), self.clock)
        r.Rbutton_set_text(self.w("play"), "Ⅱ" if self.playing else "▶")

    def tick(self):
        now = time.monotonic()
        dt, self.last_tick = now - self.last_tick, now
        self.sync_fields()
        menu = self.player.menu_action()
        menu_actions = {1: "app.quit", 2: "file.open", 3: "file.save", 4: "edit.all",
                        5: "edit.copy", 6: "edit.paste", 7: "edit.cut", 8: "edit.undo", 9: "edit.redo", 10: "project.new", 11: "updates.show"}
        if menu in menu_actions:
            self.on_action(0, menu_actions[menu], None)
        if self.timeline_tracks:
            timeline = self.w("timeline")
            for i, track in enumerate(self.timeline_tracks):
                r.Rtimeline_set_track_locked(timeline, track, self.mode != "cut" or i != 0)
            muted = bool(r.Rtimeline_is_track_muted(timeline, self.timeline_tracks[1]))
            if muted != self.muted:
                self.muted = muted
                self.player.mute(muted)
                if self.active_video:
                    r.Rbutton_set_text(self.w("audio"), "Audio off" if muted else "Audio on")
            hidden = bool(r.Rtimeline_is_track_muted(timeline, self.timeline_tracks[0]))
            self.attr("viewer", "Visibility", "Hidden" if hidden else "Visible")
        if not getattr(self, 'live', False):
            compact = r.Rwindow_get_size(self.window)[1] < 840
            cut_layout = self.mode == "cut"
            if compact != self.compact or cut_layout != getattr(self, "compact_cut", False):
                self.compact = compact
                self.compact_cut = cut_layout
                r.Rlabel_set_text_size(self.w("project.title"), 26 if compact else 32)
                panel = r.Rwidget_find(self.window, "timeline.panel")
                r.Rwidget_set_attribute(panel, "Height", "184" if compact and cut_layout else "154" if compact else "192")
        if self.active_video:
            pixels, width, height = self.player.read_frame()
            if pixels:
                if self.video_texture == r.R_INVALID_ID:
                    self.video_texture = r.Rtexture_create_from_pixels(pixels, width, height)
                else:
                    r.Rtexture_update(self.video_texture, pixels, width, height)
                r.Rimage_set_texture(self.w("viewer"), self.video_texture)
                self.frame_count += 1
            if self.player.status() < 0:
                self.playing = False
                self.label("status", "Video: " + self.player.error().decode())
            if self.playing and self.player.status() == 1:
                base = sum(s.duration for s in self.project.shots[:self.index])
                local = max(0, self.player.time() - self.shot.source_start)
                self.clock = base + min(local, self.shot.duration)
                source_ended = self.player.duration() and self.player.time() >= self.player.duration() - 1 / 24
                if source_ended or local >= self.shot.duration:
                    self.player.play(False)
                    self.clock = base + self.shot.duration
                    if self.index + 1 < len(self.project.shots):
                        self.select_shot(self.index + 1, seek=False)
                        if self.active_video:
                            self.player.play(True)
                    else:
                        self.playing = False
            if self.player.has_audio():
                take = self.shot.take
                self.label("viewer.source", (take.provenance if take else "Video") + " · audio track verified")
        elif self.playing:
            self.clock = min(self.project.duration, self.clock + dt)
            index, _ = self.project.shot_at(self.clock)
            if index != self.index:
                self.select_shot(index, seek=False)
            if self.clock >= self.project.duration:
                self.playing = False
        self.update_clock()

    def capture(self, path):
        pixels, width, height = r.Rwindow_capture(self.window)
        if not pixels:
            raise RuntimeError("Raven could not capture a window image.")
        try:
            write_png(path, ctypes.string_at(pixels, width * height * 4), width, height)
        finally:
            r.Rpixels_free(pixels)

    def close(self):
        self.sync_fields()
        if self.dirty:
            self.save()
        self.player.close()
        r.Rshutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--project", type=Path)
    args = parser.parse_args()
    studio = Studio()
    try:
        if args.project:
            try:
                studio.projects.append(load_working_copy(args.project))
                studio.switch_project(len(studio.projects) - 1)
            except (OSError, ValueError, TypeError, KeyError) as error:
                parser.error(f"Could not open project: {error}")
        if args.smoke_test:
            from smoke import run
            run(studio)
        else:
            while r.Rwait_frame(1 / 60):
                studio.tick()
    finally:
        studio.close()


if __name__ == "__main__":
    main()

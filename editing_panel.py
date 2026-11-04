"""Native scene contact sheets and non-destructive editorial workspace."""
from __future__ import annotations

import copy
from urllib.parse import quote

import raven as r

from cut import CutStore, choose_take, frames, gesture, move, scene_groups, trim
from cut_media import bind_production, export_cut, preserve_take
from production_model import view_project
import views as v


class EditingMixin:
    def initialize_editing(self):
        self.cut_store = None
        self.cut_doc = None
        self.cut_gesture_pending = False
        self.cut_removed = set()
        self.cut_refresh_pending = False
        self.cut_history_takes = []

    def _free_project(self):
        self.initialize_editing()
        self.replace_host("scene.details", "")
        self.replace_host("cut.inspector.content", "")
        return super()._free_project()

    def open_cut(self):
        if any(key in self.pending for key in ("rename", "archive")):
            self.toast("Wait for the project move to finish before opening the cut.", True)
            return
        if not self.slug or not self.board or "cut.load" in self.pending:
            return
        slug = self.slug
        production = view_project(slug, self.board, self.workspace)
        self.show_modal("Opening cut", v.label("Loading cut choices and preserving available takes…", 13, True))
        def operation():
            store = CutStore(self.workspace, slug)
            first = not store.path.exists()
            doc = store.initialize(production)
            if first:
                doc = store.commit(doc, bind_production(store, doc, production), "Initial cut", remember=False)
            return store, doc
        def done(result):
            self.cut_store, self.cut_doc = result
            self.modal_close()
            self._entering_cut = True
            try:
                self.mode_set("cut")
                wanted=getattr(self,"story_cut_selection",None)
                if wanted:
                    self.select_shot(next((i for i,s in enumerate(self.project.shots) if s.id in wanted),0))
                    self.story_cut_selection=None
            finally:
                self._entering_cut = False
            self.toast("The cut is ready. Drag shots or edges, or use the in and out controls on the right.")
        self.submit("cut.load", operation, done)

    @property
    def cut_clip(self):
        if not self.cut_doc or not self.shot:
            return None
        return next((c for c in self.cut_doc["clips"] if c["shotId"] == self.shot.id), None)

    def cut_commit(self, clips, label):
        if not self.cut_store or not self.cut_doc:
            return
        self.stop_workspace_preview()
        self.playing = False
        self.player.play(False)
        self.cut_doc = self.cut_store.commit(self.cut_doc, clips, label)
        self.refresh_view()
        self.toast(f"{label} · cut saved")

    def cut_undo(self, redo=False):
        self.stop_workspace_preview()
        self.playing = False
        self.player.play(False)
        self.cut_doc = self.cut_store.history(self.cut_doc, redo)
        self.refresh_view()
        self.toast("Redone" if redo else "Undone")

    def render_cut_inspector(self):
        if self.mode != "cut":
            return
        clip = self.cut_clip
        body = v.label("CUT SELECTION", 10, True)
        if clip:
            source = clip["source"]
            body += v.label(self.shot.title, 22)
            body += v.label(source["label"] if source else "Image plan · no video selected", 12, True)
            body += v.label(f"{self.shot.duration:.2f} s in film · {clip['sourceFrames']/24:.2f} s tilgjengelig", 12, True)
            body += '<HBox Spacing="10"><VBox Width="115">' + v.field("cut.in", "In · frame", str(clip["inFrame"]), 34, multiline=False) + '</VBox><VBox Width="115">' + v.field("cut.out", "Out · frame", str(clip["outFrame"]), 34, multiline=False) + '</VBox></HBox>'
            body += v.button("Apply in and out", "cut.trim", True)
            body += '<HBox Spacing="6">' + v.button("Set in here", "cut.mark_in") + v.button("Set out here", "cut.mark_out") + '</HBox>'
            body += v.label("The out point is the first unused frame. One second is 24 frames.", 11, True)
            body += v.button("Choose take…", "cut.takes")
            body += v.button("Use production take", "cut.production")
            body += '<HBox Spacing="7">' + v.button("← Previous", "cut.up") + v.button("Next →", "cut.down") + '</HBox>'
            body += v.button("Exclude from cut", "cut.exclude")
            body += v.button("Open in storyboard", "cut.storyboard")
            if source and source["video"] and not self.shot.take.video:
                body += v.label("The preserved take is missing. Choose another take before exporting.", 12)
        else:
            body += v.label("Choose a shot or restore an excluded shot.", 13, True)
        body += '<Separator/>'
        undo = self.cut_doc["undo"] if self.cut_doc else []
        redo = self.cut_doc["redo"] if self.cut_doc else []
        self.set_attr("cut.undo", "Enabled", bool(undo))
        self.set_attr("cut.redo", "Enabled", bool(redo))
        if undo:
            body += v.label("Undo: " + undo[-1]["label"], 11, True)
        excluded = [c for c in (self.cut_doc or {}).get("clips", []) if not c["included"]]
        if excluded:
            body += v.label("EXCLUDED SHOTS", 10, True)
            titles = {s.get("id"): s.get("title", "Shot") for s in self.board.get("shots", [])}
            for i, c in enumerate(excluded):
                body += v.button("＋ " + titles.get(c["shotId"], "Preserved shot"), f"cut.restore.{i}")
        body += v.label("Original takes are preserved. Editing and undo do not start generation.", 11, True)
        self.replace_host("cut.inspector.content", body)

    def render_scenes(self):
        self.render_outline()
        self.render_scene_details()

    def on_timeline_change(self, sender, item, user):
        if self.mode != "cut":
            return super().on_timeline_change(sender, item, user)
        if not self.updating:
            # The C widget notifies on every mouse move. Commit once after
            # release, outside the callback; rebuilding here invalidates it.
            self.cut_gesture_pending = True
            self.playing = False
            self.player.play(False)

    def on_timeline_removed(self, sender, item, user):
        if self.mode == "cut" and item in self.timeline_items:
            self.cut_removed.add(self.project.shots[self.timeline_items[item]].id)
            self.cut_gesture_pending = True

    def flush_cut_gesture(self):
        if not self.cut_gesture_pending or r.Rwindow_is_mouse_button_down(self.window, r.R_MOUSE_LEFT):
            return
        self.cut_gesture_pending = False
        if self.mode != "cut" or not self.cut_doc:
            return
        timeline = self.w("timeline")
        changes = {self.project.shots[index].id: (frames(r.Rtimeline_get_item_start(timeline, item)), frames(r.Rtimeline_get_item_length(timeline, item)))
                   for item, index in self.timeline_items.items() if self.project.shots[index].id not in self.cut_removed}
        try:
            clips = gesture(self.cut_doc["clips"], changes)
            for clip in clips:
                if clip["shotId"] in self.cut_removed:
                    clip["included"] = False
            self.cut_removed.clear()
            if clips != self.cut_doc["clips"]:
                self.cut_commit(clips, "Move or trim shot")
            else:
                self.refresh_view()
        except (OSError, ValueError) as error:
            self.refresh_view()
            self.toast(error, True)

    def use_cut_take(self, video, image="", *, take_id="production", label="Production take"):
        if any(key in self.pending for key in ("rename", "archive")):
            raise ValueError("Wait for the project move to finish before choosing a take.")
        clip = self.cut_clip
        if not clip or "cut.take" in self.pending:
            return
        store, revision, sid = self.cut_store, self.cut_doc["revision"], clip["shotId"]
        def operation():
            return preserve_take(store, video, image, take_id=take_id, label=label)
        def done(result):
            if not self.cut_doc or self.cut_store.slug != store.slug or self.cut_doc["revision"] != revision:
                self.toast("The cut changed. Choose the take again.", True)
                return
            source, total = result
            self.cut_commit(choose_take(self.cut_doc["clips"], sid, source, total), "Choose " + label)
            if self.modal_kind == "Choose a take for the shot" and self.cut_clip and self.cut_clip["shotId"] == sid:
                self.modal_close()
        self.submit("cut.take", operation, done)

    def show_cut_takes(self):
        if not self.cut_clip:
            return
        sid, slug = self.cut_clip["shotId"], self.slug
        self.show_modal("Choose a take for the shot", v.label("Loading preserved takes…", 13, True))
        def done(payload):
            if not self.cut_clip or self.cut_clip["shotId"] != sid or self.modal_kind != "Choose a take for the shot":
                return
            self.cut_history_takes = [t for t in payload.get("takes", []) if t.get("clipUrl")]
            body = v.button("Use production take", "cut.production", True)
            for i, take in enumerate(self.cut_history_takes):
                body += '<Separator/>' + v.label(f"Seed {take.get('seed', '?')} · {take.get('status', '')}", 15)
                body += '<HBox Spacing="10">' + v.button("View take", f"cut.preview.{i}") + v.button("Use in cut", f"cut.take.{i}") + '</HBox>'
            if not self.cut_history_takes:
                body += v.label("No additional seed takes are preserved for this shot.", 13, True)
            self.show_modal("Choose a take for the shot", body)
        self.submit("cut.takes", lambda: self.backend.get(f"/api/boards/{quote(slug)}/shots/{quote(sid)}/seed-takes"), done)

    def editing_action(self, action):
        if action.startswith("scene.shot."):
            self.mode_set("film")
            self.select_shot(int(action.rsplit(".", 1)[1]))
            return True
        if not action.startswith("cut."):
            return False
        if self.mode != "cut" or not self.cut_doc:
            return True
        if action == "cut.undo": self.cut_undo()
        elif action == "cut.redo": self.cut_undo(True)
        elif action == "cut.trim" and self.cut_clip:
            self.cut_commit(trim(self.cut_doc["clips"], self.shot.id, int(self.text("cut.in")), int(self.text("cut.out"))), "Trim in and out")
        elif action in {"cut.mark_in", "cut.mark_out"} and self.cut_clip:
            clip = self.cut_clip
            base = sum(s.duration for s in self.project.shots[:self.index])
            position = clip["inFrame"] + frames(self.clock - base)
            start, end = (position, clip["outFrame"]) if action.endswith("in") else (clip["inFrame"], position)
            self.cut_commit(trim(self.cut_doc["clips"], self.shot.id, start, end), "Set in point" if action.endswith("in") else "Set out point")
        elif action in {"cut.up", "cut.down"} and self.cut_clip:
            self.cut_commit(move(self.cut_doc["clips"], self.shot.id, -1 if action.endswith("up") else 1), "Change order")
        elif action == "cut.exclude" and self.cut_clip:
            clips = copy.deepcopy(self.cut_doc["clips"])
            next(c for c in clips if c["shotId"] == self.shot.id)["included"] = False
            self.cut_commit(clips, "Exclude shot")
        elif action.startswith("cut.restore."):
            clips = copy.deepcopy(self.cut_doc["clips"])
            [c for c in clips if not c["included"]][int(action.rsplit(".", 1)[1])]["included"] = True
            self.cut_commit(clips, "Restore shot")
        elif action == "cut.storyboard" and self.shot:
            sid = self.shot.id
            self.mode_set("film")
            self.select_shot(next((i for i, s in enumerate(self.project.shots) if s.id == sid), 0))
        elif action == "cut.production" and self.cut_clip:
            production = view_project(self.slug, self.board, self.workspace)
            shot = next((s for s in production.shots if s.id == self.shot.id), None)
            if not shot or not shot.take or not shot.take.video:
                raise ValueError("This shot does not have a production take yet.")
            self.use_cut_take(shot.take.video, shot.take.image)
        elif action == "cut.takes": self.show_cut_takes()
        elif action.startswith(("cut.take.", "cut.preview.")):
            take = self.cut_history_takes[int(action.rsplit(".", 1)[1])]
            video = self.workspace.local_media(take["clipUrl"])
            if not video: raise ValueError("The take is missing locally.")
            if action.startswith("cut.preview."):
                self.preview(video, f"Seed {take.get('seed', '?')}")
            else:
                image = self.workspace.local_media(take.get("thumbUrl", ""))
                self.use_cut_take(video, image or "", take_id=str(take.get("seed")), label=f"Seed {take.get('seed', '?')}")
        elif action == "cut.export":
            store, doc = self.cut_store, copy.deepcopy(self.cut_doc)
            production = view_project(self.slug, self.board, self.workspace)
            self.toast("Exporting rough cut with picture and audio…")
            self.submit("cut.export", lambda: export_cut(store, doc, production), lambda path: self.preview(path, "Exported rough cut"))
        return True

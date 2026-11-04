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
            self.toast("Vent til prosjektet er ferdig flyttet før du åpner klippen.", True)
            return
        if not self.slug or not self.board or "cut.load" in self.pending:
            return
        slug = self.slug
        production = view_project(slug, self.board, self.workspace)
        self.show_modal("Åpner klippen", v.label("Henter klippevalg og bevarer tilgjengelige opptak…", 13, True))
        def operation():
            store = CutStore(self.workspace, slug)
            first = not store.path.exists()
            doc = store.initialize(production)
            if first:
                doc = store.commit(doc, bind_production(store, doc, production), "Første klipp", remember=False)
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
            self.toast("Klippelaget er klart. Dra klipp eller kanter, eller bruk inn og ut til høyre.")
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
        self.toast(f"{label} · klippen er lagret")

    def cut_undo(self, redo=False):
        self.stop_workspace_preview()
        self.playing = False
        self.player.play(False)
        self.cut_doc = self.cut_store.history(self.cut_doc, redo)
        self.refresh_view()
        self.toast("Gjort om" if redo else "Angret")

    def render_cut_inspector(self):
        if self.mode != "cut":
            return
        clip = self.cut_clip
        body = v.label("KLIPPEVALG", 10, True)
        if clip:
            source = clip["source"]
            body += v.label(self.shot.title, 22)
            body += v.label(source["label"] if source else "Bildeplan · video ikke valgt", 12, True)
            body += v.label(f"{self.shot.duration:.2f} s i filmen · {clip['sourceFrames']/24:.2f} s tilgjengelig", 12, True)
            body += '<HBox Spacing="10"><VBox Width="115">' + v.field("cut.in", "Inn · ramme", str(clip["inFrame"]), 34, multiline=False) + '</VBox><VBox Width="115">' + v.field("cut.out", "Ut · ramme", str(clip["outFrame"]), 34, multiline=False) + '</VBox></HBox>'
            body += v.button("Bruk inn og ut", "cut.trim", True)
            body += '<HBox Spacing="6">' + v.button("Sett inn her", "cut.mark_in") + v.button("Sett ut her", "cut.mark_out") + '</HBox>'
            body += v.label("Utpunktet er første ramme som ikke brukes. Ett sekund er 24 rammer.", 11, True)
            body += v.button("Velg take…", "cut.takes")
            body += v.button("Bruk produksjonsopptaket", "cut.production")
            body += '<HBox Spacing="7">' + v.button("← Tidligere", "cut.up") + v.button("Senere →", "cut.down") + '</HBox>'
            body += v.button("Utelat fra klippen", "cut.exclude")
            body += v.button("Åpne i storyboard", "cut.storyboard")
            if source and source["video"] and not self.shot.take.video:
                body += v.label("Det bevarte opptaket mangler. Velg et annet take før eksport.", 12)
        else:
            body += v.label("Velg et klipp eller legg tilbake et utelatt klipp.", 13, True)
        body += '<Separator/>'
        undo = self.cut_doc["undo"] if self.cut_doc else []
        redo = self.cut_doc["redo"] if self.cut_doc else []
        self.set_attr("cut.undo", "Enabled", bool(undo))
        self.set_attr("cut.redo", "Enabled", bool(redo))
        if undo:
            body += v.label("Angre: " + undo[-1]["label"], 11, True)
        excluded = [c for c in (self.cut_doc or {}).get("clips", []) if not c["included"]]
        if excluded:
            body += v.label("UTELATTE KLIPP", 10, True)
            titles = {s.get("id"): s.get("title", "Klipp") for s in self.board.get("shots", [])}
            for i, c in enumerate(excluded):
                body += v.button("＋ " + titles.get(c["shotId"], "Bevart klipp"), f"cut.restore.{i}")
        body += v.label("Originalopptakene beholdes. Redigering og angre starter ingen generering.", 11, True)
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
                self.cut_commit(clips, "Flytt eller trim klipp")
            else:
                self.refresh_view()
        except (OSError, ValueError) as error:
            self.refresh_view()
            self.toast(error, True)

    def use_cut_take(self, video, image="", *, take_id="production", label="Produksjonsopptak"):
        if any(key in self.pending for key in ("rename", "archive")):
            raise ValueError("Vent til prosjektet er ferdig flyttet før du velger opptak.")
        clip = self.cut_clip
        if not clip or "cut.take" in self.pending:
            return
        store, revision, sid = self.cut_store, self.cut_doc["revision"], clip["shotId"]
        def operation():
            return preserve_take(store, video, image, take_id=take_id, label=label)
        def done(result):
            if not self.cut_doc or self.cut_store.slug != store.slug or self.cut_doc["revision"] != revision:
                self.toast("Klippingen endret seg. Velg opptaket igjen.", True)
                return
            source, total = result
            self.cut_commit(choose_take(self.cut_doc["clips"], sid, source, total), "Velg " + label)
            if self.modal_kind == "Velg take til klippen" and self.cut_clip and self.cut_clip["shotId"] == sid:
                self.modal_close()
        self.submit("cut.take", operation, done)

    def show_cut_takes(self):
        if not self.cut_clip:
            return
        sid, slug = self.cut_clip["shotId"], self.slug
        self.show_modal("Velg take til klippen", v.label("Henter beholdte opptak…", 13, True))
        def done(payload):
            if not self.cut_clip or self.cut_clip["shotId"] != sid or self.modal_kind != "Velg take til klippen":
                return
            self.cut_history_takes = [t for t in payload.get("takes", []) if t.get("clipUrl")]
            body = v.button("Bruk produksjonsopptaket", "cut.production", True)
            for i, take in enumerate(self.cut_history_takes):
                body += '<Separator/>' + v.label(f"Seed {take.get('seed', '?')} · {take.get('status', '')}", 15)
                body += '<HBox Spacing="10">' + v.button("Se opptaket", f"cut.preview.{i}") + v.button("Bruk i klippen", f"cut.take.{i}") + '</HBox>'
            if not self.cut_history_takes:
                body += v.label("Ingen ekstra seed-takes er bevart for dette klippet.", 13, True)
            self.show_modal("Velg take til klippen", body)
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
            self.cut_commit(trim(self.cut_doc["clips"], self.shot.id, int(self.text("cut.in")), int(self.text("cut.out"))), "Trim inn og ut")
        elif action in {"cut.mark_in", "cut.mark_out"} and self.cut_clip:
            clip = self.cut_clip
            base = sum(s.duration for s in self.project.shots[:self.index])
            position = clip["inFrame"] + frames(self.clock - base)
            start, end = (position, clip["outFrame"]) if action.endswith("in") else (clip["inFrame"], position)
            self.cut_commit(trim(self.cut_doc["clips"], self.shot.id, start, end), "Sett innpunkt" if action.endswith("in") else "Sett utpunkt")
        elif action in {"cut.up", "cut.down"} and self.cut_clip:
            self.cut_commit(move(self.cut_doc["clips"], self.shot.id, -1 if action.endswith("up") else 1), "Endre rekkefølge")
        elif action == "cut.exclude" and self.cut_clip:
            clips = copy.deepcopy(self.cut_doc["clips"])
            next(c for c in clips if c["shotId"] == self.shot.id)["included"] = False
            self.cut_commit(clips, "Utelat klipp")
        elif action.startswith("cut.restore."):
            clips = copy.deepcopy(self.cut_doc["clips"])
            [c for c in clips if not c["included"]][int(action.rsplit(".", 1)[1])]["included"] = True
            self.cut_commit(clips, "Legg tilbake klipp")
        elif action == "cut.storyboard" and self.shot:
            sid = self.shot.id
            self.mode_set("film")
            self.select_shot(next((i for i, s in enumerate(self.project.shots) if s.id == sid), 0))
        elif action == "cut.production" and self.cut_clip:
            production = view_project(self.slug, self.board, self.workspace)
            shot = next((s for s in production.shots if s.id == self.shot.id), None)
            if not shot or not shot.take or not shot.take.video:
                raise ValueError("Dette klippet har ikke et produksjonsopptak ennå.")
            self.use_cut_take(shot.take.video, shot.take.image)
        elif action == "cut.takes": self.show_cut_takes()
        elif action.startswith(("cut.take.", "cut.preview.")):
            take = self.cut_history_takes[int(action.rsplit(".", 1)[1])]
            video = self.workspace.local_media(take["clipUrl"])
            if not video: raise ValueError("Opptaket mangler lokalt.")
            if action.startswith("cut.preview."):
                self.preview(video, f"Seed {take.get('seed', '?')}")
            else:
                image = self.workspace.local_media(take.get("thumbUrl", ""))
                self.use_cut_take(video, image or "", take_id=str(take.get("seed")), label=f"Seed {take.get('seed', '?')}")
        elif action == "cut.export":
            store, doc = self.cut_store, copy.deepcopy(self.cut_doc)
            production = view_project(self.slug, self.board, self.workspace)
            self.toast("Eksporterer grovklippen med bilde og lyd…")
            self.submit("cut.export", lambda: export_cut(store, doc, production), lambda path: self.preview(path, "Eksportert grovklipp"))
        return True

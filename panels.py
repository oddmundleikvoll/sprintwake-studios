"""Production-facing Raven panels.

The mixin intentionally contains presentation and action orchestration only.
Network calls, media copies, and model work are always submitted to the
Studio worker pool; the canonical board is changed only after an explicit
user action and a successful save.
"""
from __future__ import annotations

import copy
from pathlib import Path
from urllib.parse import quote

import raven as r

import views as v
from production_model import film_action
from review_gate import canonical_ref, continuity_review_notes, plan_images, plan_images_complete, receipt_snapshot, review_images, review_images_complete, review_videos, review_videos_complete


class PanelMixin:
    """Native panels used by :class:`studio.ProductionStudio`."""

    def _record_path(self, record):
        if not isinstance(record, dict):
            return ""
        ref = record.get("ref") if isinstance(record.get("ref"), dict) else record
        value = ref.get("path") or ref.get("url") or ref.get("ref") or ""
        local = self.workspace.local_media(value) if value else None
        return str(local) if local else ""

    def _panel_widget(self, name):
        """Resolve dynamically-created page widgets as well as legacy widgets."""
        return self.find(name)

    def _show_media(self, path, title=""):
        path = str(path or "")
        if not path:
            return
        local = self.workspace.local_media(path) or Path(path) if Path(path).is_file() else None
        if not local:
            return
        suffix = local.suffix.lower()
        if suffix in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}:
            preview = getattr(self, "preview", None)
            if preview:
                preview(local, title or local.name)
            else:
                self.player.close(); self.active_video = str(local)
                if not self.player.open(str(local).encode()): self.toast("Could not open the take", True)
            self.playing = False
        elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
            r.Rimage_set_texture(self._panel_widget("image.preview"), self.texture(str(local)))
        self.set_label("image.description", title or local.name)

    def _entity_lists(self, kind):
        bible = self.board.setdefault("filmBible", {})
        key = {"character": "characters", "location": "locations", "prop": "props"}[kind]
        # Characters historically lived at board.characters; retain that shape.
        items = self.board.setdefault("characters", []) if kind == "character" else bible.setdefault(key, [])
        return items, key

    def render_bible(self):
        if not self.board:
            return
        parts = [v.title("02 / Film bible", "People, places, and things that must stay consistent.", "References from here carry into the image plan and takes.")]
        for kind, heading in (("character", "Characters"), ("location", "Locations"), ("prop", "Props")):
            items, _ = self._entity_lists(kind)
            cards = []
            for item in items:
                ident = str(item.get("id") or item.get("name") or item.get("title") or "item")
                name = item.get("name") or item.get("title") or "Untitled"
                desc = item.get("description") or item.get("notes") or "No description yet."
                image = self._record_path(item.get("image" if kind == "character" else "referenceImage"))
                cards.append(f'<Button OnClicked="bible.{kind}.{v.esc(ident)}" Metric.PaddingX="12" Metric.PaddingY="12"><HBox Spacing="14" HitTestable="false">{v.image(image, width=104, height=78, fit=True)}<VBox Slot.Fill="1" Spacing="6">{v.label(name, 19)}{v.label(str(desc)[:260], 12, True)}</VBox></HBox></Button>')
            cards.append(v.button("+ Add", f"bible.add.{kind}"))
            parts.append(f'<VBox Spacing="10"><Label Text="{v.esc(heading.upper())}" Class="eyebrow"/><WrapBox Spacing="10">{"".join(cards)}</WrapBox></VBox>')
        self.replace_host("bible.host", "".join(parts))

    def show_entity(self, kind, ident=None):
        if kind not in {"character", "location", "prop"}:
            return
        items, _ = self._entity_lists(kind)
        item = next((x for x in items if str(x.get("id")) == str(ident)), None) if ident else None
        self.entity_target = {"kind": kind, "id": item.get("id") if item else None}
        heading = {"character": "Character card", "location": "Location", "prop": "Prop"}[kind]
        name = (item or {}).get("name", "") if kind == "character" else (item or {}).get("title", (item or {}).get("name", ""))
        body = v.field("entity.name", "Name", name, height=38, multiline=False)
        body += v.field("entity.description", "Description", (item or {}).get("description", ""), height=130)
        body += v.label("A reference image is optional. A generated character card stays as a draft until you choose to use it.", 12, True)
        body += v.button("Import reference", "entity.import")
        if kind == "character":
            body += v.button("Create four-view character card", "entity.generate")
        if item:
            body += v.button("Remove from film bible", "entity.remove")
        self.show_modal(heading, body, "entity.save", "Save")

    def remove_entity(self):
        target = self.entity_target or {}
        kind, ident = target.get("kind"), target.get("id")
        if kind not in {"character", "location", "prop"} or not ident:
            return
        field = {"character": "characterIds", "location": "locationId", "prop": "propIds"}[kind]
        for shot in self.board.get("shots", []):
            value = shot.get(field)
            if (isinstance(value, list) and ident in value) or value == ident:
                self.toast("This item is still used by a shot. Remove the link first.", True)
                return
        items, _ = self._entity_lists(kind)
        items[:] = [item for item in items if str(item.get("id")) != str(ident)]
        self.set_dirty(True)
        self.save(after=lambda: (self.modal_close(), self.render_bible()))

    def save_entity(self):
        target = self.entity_target or {}
        kind = target.get("kind")
        if kind not in {"character", "location", "prop"}:
            return
        name = self.text("entity.name").strip()
        if not name:
            self.toast("Give the item a name first.", True)
            return
        items, _ = self._entity_lists(kind)
        item = next((x for x in items if str(x.get("id")) == str(target.get("id"))), None)
        if item is None:
            import uuid
            ident = f"{kind[:1]}{uuid.uuid4().hex[:8]}"
            item = {"id": ident}
            items.append(item)
            target["id"] = ident
        if kind == "character":
            item["name"] = name
        else:
            item["title"] = name
        item["description"] = self.text("entity.description").strip()
        self.entity_target = target
        self.set_dirty(True)
        self.save(after=lambda: (self.modal_close(), self.render_bible()))

    def import_entity_ref(self):
        if not self.slug or not self.entity_target:
            return
        if not self.entity_target.get("id"):
            import uuid
            kind = self.entity_target.get("kind")
            items, _ = self._entity_lists(kind)
            ident = f"{kind[:1]}{uuid.uuid4().hex[:8]}"
            item = {"id": ident}
            item["name" if kind == "character" else "title"] = self.text("entity.name").strip() or "Untitled"
            item["description"] = self.text("entity.description").strip()
            items.append(item); self.entity_target["id"] = ident; self.set_dirty(True)
            self.save(after=self.import_entity_ref)
            return
        chosen = self.player.choose_media()
        if not chosen:
            return
        source = Path(chosen.decode() if isinstance(chosen, (bytes, bytearray)) else chosen)
        target = copy.deepcopy(self.entity_target)
        slug = self.slug
        def operation():
            imported = self.workspace.import_input(slug, source)
            uploaded = self.backend.upload(slug, imported)
            return imported, uploaded
        def done(result):
            imported, uploaded = result
            items, _ = self._entity_lists(target["kind"])
            item = next((x for x in items if str(x.get("id")) == str(target.get("id"))), None)
            if item is not None:
                key = "image" if target["kind"] == "character" else "referenceImage"
                item[key] = uploaded if isinstance(uploaded, dict) else {"path": str(imported.relative_to(self.workspace.projects_dir))}
                self.set_dirty(True)
                self.save()
                self.render_bible()
                self.toast("Reference added.")
        self.submit("entity.import", operation, done)

    def generate_entity_image(self):
        target = self.entity_target or {}
        if target.get("kind") != "character" or not self.slug:
            return
        if not target.get("id"):
            import uuid
            items, _ = self._entity_lists("character")
            ident = f"c{uuid.uuid4().hex[:8]}"
            items.append({"id": ident, "name": self.text("entity.name").strip() or "Character", "description": self.text("entity.description").strip()})
            self.entity_target = target = {"kind": "character", "id": ident}
            self.set_dirty(True)
            self.save(after=self.generate_entity_image)
            return
        name = self.text("entity.name").strip() or "Character"
        description = self.text("entity.description").strip()
        prompt = f"Character reference sheet, four consistent views (front, three-quarter, side, back), {name}. {description}. Neutral studio background, full body, clear costume and facial identity, no text."
        self.image_target = {"kind": "entity", "entity": copy.deepcopy(target)}
        slug = self.slug
        items, _ = self._entity_lists("character")
        entity = next((x for x in items if str(x.get("id")) == str(target.get("id"))), None)
        if entity is not None and (entity.get("name") != name or entity.get("description", "") != description):
            entity["name"], entity["description"] = name, description
            self.set_dirty(True)
            self.save(after=lambda: self._queue_entity_image(slug, prompt))
            return
        self._queue_entity_image(slug, prompt)

    def _queue_entity_image(self, slug, prompt):
        def operation():
            return self.backend.post("/api/images", {"slug": slug, "prompt": prompt, "references": [], "width": 1536, "height": 1024, "aspect": "3:2", "inheritStyle": False})
        def done(result):
            self.image_state = {"job": result.get("job") if isinstance(result, dict) else result, "busy": True, "target": self.image_target}
            self.toast("The character card is being created. Review it before using it.")
            self.modal_close()
            self.mode_set("images")
            self.render_images()
        self.submit("image.generate", operation, done)

    def render_images(self):
        if not self.board:
            return
        self.replace_host("image.references", "".join(v.label(Path(self._record_path(x)).name, 11, True) for x in self.image_refs) or v.label("No references selected", 11, True))
        cards = []
        for i, record in enumerate(self.images):
            path = self._record_path(record)
            status = record.get("status", "") if isinstance(record, dict) else ""
            cards.append(f'<Button OnClicked="image.select.{i}" Metric.PaddingX="7" Metric.PaddingY="7"><VBox Spacing="6" HitTestable="false">{v.image(path, width=160, height=100, fit=True)}{v.label(f"{i + 1:02}  {status or Path(path).name}", 11, True)}</VBox></Button>')
        self.replace_host("image.gallery", '<WrapBox Spacing="12">' + ("".join(cards) or v.label("Create an image to build the gallery.", 13, True)) + '</WrapBox>')
        if self.selected_image:
            self._show_media(self._record_path(self.selected_image), self.selected_image.get("resolvedPrompt") or self.selected_image.get("prompt", ""))
        self.set_attr("image.empty", "Visibility", "Collapsed" if self.selected_image else "Visible")
        for name in ("image.assign", "image.publish", "image.reveal"):
            self.set_attr(name, "Enabled", bool(self.selected_image))
        self.set_attr('image.assign','Visibility','Visible' if self.shot or (self.image_target or {}).get('kind')=='entity' else 'Collapsed')

    def show_references(self):
        records = [x for x in self.catalogue if x.get("kind") == "image"]
        body = v.label("Choose up to ten real images from the project. They will be used as visual references in the next image job.", 13, True)
        selected = {self._ref_identity(x) for x in self.image_refs}
        body += "".join(f'<CheckBox Name="reference.{i}" Text="{v.esc(Path(x["path"]).name)}" Checked="{"true" if self._ref_identity(x) in selected else "false"}" />' for i, x in enumerate(records))
        self.reference_records = records
        self.show_modal("Choose references", body, "image.references.save", "Use references")

    def _ref_identity(self, ref):
        value = ref.get("path") if isinstance(ref, dict) else ref
        return canonical_ref(value, self.workspace)

    def toggle_reference(self, index):
        records = getattr(self, "reference_records", [])
        if not 0 <= index < len(records):
            return
        record = records[index]
        path = record.get("path")
        existing = next((x for x in self.image_refs if x.get("path") == path), None)
        if existing:
            self.image_refs = [x for x in self.image_refs if x.get("path") != path]
        elif len(self.image_refs) < 10:
            self.image_refs.append({"kind": "image", "path": path})
        self.render_images()

    def save_references(self):
        records = getattr(self, "reference_records", [])
        chosen = []
        resolver = getattr(self, "ref_for", None)
        for i, record in enumerate(records):
            if self.checked(f"reference.{i}"):
                path = record.get("path")
                chosen.append(resolver(path) if resolver else {"kind": "image", "path": path})
        if len(chosen) > 10:
            self.toast("Choose at most ten references.", True); return
        self.image_refs = [x for x in chosen if x]
        self.capture_drafts(); self.modal_close(); self.render_images()

    def generate_image(self):
        if not self.slug:
            return
        prompt = self.text("image.prompt").strip()
        if not prompt:
            self.toast("Describe the image first.", True)
            return
        aspects = ["3:2", "16:9", "2:3", "1:1"]
        aspect = aspects[max(0, min(self.selected("image.aspect"), len(aspects) - 1))]
        dimensions = {"3:2": (768, 512), "16:9": (768, 432), "2:3": (512, 768), "1:1": (640, 640)}[aspect]
        seed_text = self.text("image.seed").strip()
        slug = self.slug
        payload = {"slug": slug, "prompt": prompt, "references": copy.deepcopy(self.image_refs[:10]), "width": dimensions[0], "height": dimensions[1], "aspect": aspect, "inheritStyle": True}
        if seed_text:
            try: payload["seed"] = int(seed_text)
            except ValueError: self.toast("Seed must be an integer.", True); return
        self.image_target = {"kind": "shot", "shot": self.shot.id if self.shot else None}
        def operation(): return self.backend.post("/api/images", payload)
        def done(result):
            self.image_state = {"job": result.get("job") if isinstance(result, dict) else result, "busy": True, "target": self.image_target}
            self.toast("Creating the image in the production engine.")
            self.render_images()
        self.submit("image.generate", operation, done)

    def select_image(self, index):
        if 0 <= index < len(self.images):
            self.selected_image = self.images[index]
            self._show_media(self._record_path(self.selected_image), self.selected_image.get("resolvedPrompt") or self.selected_image.get("prompt", ""))
            self.render_images()

    def assign_image(self):
        if not self.selected_image:
            self.toast("Choose a real image and where it should be used first.", True); return
        if not self.image_target:
            shot = getattr(self, "shot", None)
            if shot:
                self.image_target = {"kind": "shot", "shot": shot.id}
        if not self.image_target:
            self.toast("Choose an image target first.", True); return
        ref = self.selected_image.get("ref") if isinstance(self.selected_image.get("ref"), dict) else {"path": self._record_path(self.selected_image)}
        target = self.image_target
        if target.get("kind") == "entity":
            items, _ = self._entity_lists(target["entity"]["kind"])
            item = next((x for x in items if str(x.get("id")) == str(target["entity"].get("id"))), None)
            if item is not None:
                item["image" if target["entity"]["kind"] == "character" else "referenceImage"] = copy.deepcopy(ref)
        elif target.get("kind") == "shot":
            shot = next((x for x in self.board.get("shots", []) if str(x.get("id")) == str(target.get("shot"))), None)
            if shot is not None: shot["startRef"] = copy.deepcopy(ref)
        self.set_dirty(True)
        target_label = "the character card" if target.get("kind") == "entity" else "the shot opening"
        self.save(after=lambda: (self.refresh_view(), self.toast(f"Image linked to {target_label}.")))

    def publish_image(self):
        path = self._record_path(self.selected_image)
        if not path or not self.slug:
            self.toast("Choose a real image first.", True); return
        self.submit("image.publish", lambda: self.workspace.publish_image(self.slug, path), lambda p: (self.render_library(), self.toast(f"Saved to Output · {p.name}")))

    def render_production(self):
        context = self.film_context or {}
        project = context.get("project") if isinstance(context.get("project"), dict) else context
        phase_raw = str(project.get("phase") or project.get("status") or "ready")
        phase = {"unplanned": "Ready to prepare", "ready": "Ready to prepare", "planning": "Preparing image plan", "images": "Image plan ready for review", "rendering": "Creating takes", "review": "Waiting for review", "assembling": "Assembling film", "done": "Film complete", "failed": "Production needs attention", "stopped": "Production stopped", "idle": "Ready for the next step"}.get(phase_raw, phase_raw)
        self.set_label("production.phase", str(phase))
        shots = (self.board or {}).get("shots", [])
        duration = sum(float(s.get("frames", 121)) / 24 for s in shots)
        resolution = (self.board or {}).get("defaults", {}).get("resolution", "384x256")
        scope = f"{len(shots)} shots · {duration:.1f} seconds planned · {resolution.replace('x', ' × ')}"
        if duration >= 30 and resolution == "384x256":
            scope += f" · Rough video estimate {duration / (169 / 24) * 121 / 60:.0f} min, based on 121 s measured for 7 s of video. Images and review are additional."
        self.set_label("production.scope", scope)
        self.set_label("production.detail", str(project.get("error") or project.get("productionError") or project.get("detail") or project.get("message") or project.get("reason") or "Review the image plan before starting video."))
        completed, total = project.get("completed"), project.get("total")
        progress = project.get("progress", 0) or 0
        if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and total:
            progress = max(0, min(1, completed / total))
        self.set_attr("production.progress", "Value", progress)
        plan = project.get("imagePlan") or {}
        gate = []
        if project.get("imageApprovalRequired"):
            gate.append(v.label("IMAGE PLAN AWAITS YOUR REVIEW", 12) + v.label("Review the actual opening images before approving.", 13, True) + v.button("Review and approve image plan", "production.approve", True))
        if project.get("reviewRequired"):
            gate.append(v.label("REVIEW AWAITS YOUR DECISION", 12) + v.label("Play the latest take and the next shot opening before continuing.", 13, True) + v.button("Approve review", "production.approve", True))
        plan_count = len(plan.get("cards") or plan.get("images") or [])
        if plan_count:
            gate.append(v.label(f"Image plan · {plan_count} images · revision {plan.get('revision', '')}", 13, True))
        self.replace_host("production.gate", '<Border Background="#1D2525" Padding="22" CornerRadius="11"><VBox Spacing="10">' + "".join(gate or [v.label("No approval is pending. Prepare or continue production when you are ready.", 13, True)]) + "</VBox></Border>")
        gated = bool(project.get("imageApprovalRequired") or project.get("reviewRequired"))
        busy = bool(project.get("busy") or context.get("gpuBusy") or self._busy())
        native = (getattr(self,"info",{}) or {}).get("connections",{}).get("nativeFilm",True)
        self.set_attr("production.prepare", "Enabled", native and not busy and bool(project.get("canProduce") or project.get("canResume")))
        self.set_attr("production.resume", "Enabled", native and not busy and bool(project.get("canResume", False)) and not gated)
        if not native:
            video=getattr(self,"connection_settings",{}).get("selected",{}).get("video","none")
            detail="No video profile is selected. Choose one in Models, or import and edit existing takes in Cut and Sound."
            if video=="ltx-local":detail="With a custom image profile, create LTX variants through Direction → Advanced takes. The automatic image plan and film flow require local Qwen/LTX."
            elif video!="none":detail="The selected video profile creates single takes through Direction → Create workflow take. Choose takes and build the film in Cut and Sound. The automatic film flow requires local Qwen/LTX."
            self.set_label("production.detail", detail)
        self.set_attr("production.stop", "Enabled", bool(project.get("busy") and project.get("jobId")))
        has_final = bool(self.workspace.local_media(f"{self.slug}/final.mp4"))
        all_clips = bool(self.project.shots) and all(shot.take and shot.take.video for shot in self.project.shots)
        self.set_attr("production.assemble", "Enabled", not busy and not gated and all_clips)
        self.set_attr("production.play_final", "Enabled", has_final)
        clips = []
        for i, clip in enumerate(project.get("clips") or []):
            shot = next((s for s in self.project.shots if s.id == clip.get("shotId")), None)
            take = shot.take if shot else None
            path = take.image if take else ""
            state = {"draft": "Planned", "published": "Take ready", "checkpoint": "Saved for continuation", "running": "Creating take", "failed": "Needs attention", "done": "Take ready"}.get(clip.get("status"), clip.get("status", ""))
            clips.append(f'<Border Background="#1B2225" Padding="12" CornerRadius="8"><HBox Spacing="10">{v.image(path, width=120, height=72, fit=True)}<VBox Slot.Fill="1">{v.label(clip.get("title", f"Shot {i + 1:02}"), 15)}{v.label(state, 11, True)}</VBox>{v.button("Play", f"production.clip.{i}", Enabled=str(bool(take and take.video)).lower())}</HBox></Border>')
        self.replace_host("production.clips", "".join(clips) or v.label("No actual takes yet.", 13, True))

    def run_film(self, kind):
        if not (getattr(self,"info",{}) or {}).get("connections",{}).get("nativeFilm",True):
            raise ValueError("The automatic film flow requires local Qwen/LTX. Use a single take with the selected video profile.")
        if not self.slug:
            self.toast("Open a project first.", True); return
        # Save canonical edits before taking the revision snapshot used by film_action.
        self.save(after=lambda: self._run_film_after_save(kind))

    def _run_film_after_save(self, kind):
        slug = self.slug
        review_mode = (self.board or {}).get("ravenSettings", {}).get("reviewMode", "fast")
        def operation():
            context = self.backend.get(f"/api/film/status?slug={quote(slug)}")
            action = film_action(context, "prepare" if kind == "prepare" else "produce")
            action["reviewMode"] = review_mode
            return self.backend.post("/api/film/action", {"slug": slug, "action": action}, timeout=1200)
        def done(result):
            self.film_context = result if isinstance(result, dict) else self.film_context
            self.render_production(); self.toast("Production job submitted.")
        self.submit("film.action", operation, done)

    def approve_gate(self, confirm=False):
        context = self.film_context or {}; project = context.get("project") if isinstance(context.get("project"), dict) else context
        kind = "approve_images" if project.get("imageApprovalRequired") else "approve_review"
        snapshot = receipt_snapshot(context)
        if getattr(self, "gate_seen", None) != snapshot:
            self.gate_seen = snapshot
            self.review_media_paths = []
            self.review_played = set()
        if not confirm:
            self.gate_key = snapshot
            body = v.label("This is an explicit decision. Play the actual media and confirm that you want to continue.", 13, True)
            body += v.label(f"The production plan contains {len((self.board or {}).get('shots', []))} shots. Approval lets production continue to the next review.",12,True)
            if kind == "approve_images":
                cards = [v.image(str(path), width=180, height=110, fit=True) for path in plan_images(project.get("imagePlan"), self.workspace)]
                if not plan_images_complete(project.get("imagePlan"), self.workspace):
                    self.toast("The image plan contains no actual images.", True); return
                body += '<WrapBox Spacing="10">' + "".join(cards) + '</WrapBox>'
            else:
                preview = project.get("reviewPreview") or {}
                stills = review_images(preview, self.workspace)
                self.review_media_paths = [str(path) for path in review_videos(preview, self.board or {}, self.workspace, self.slug)]
                if stills:
                    boundary_cards=[]
                    for key,label in (("tail","Actual video tail"),("nextOpening","Next planned opening")):
                        for path in review_images({key:preview.get(key)},self.workspace):
                            boundary_cards.append('<VBox Spacing="6">'+v.label(label,12,True)+v.image(str(path),width=180,height=110,fit=True)+'</VBox>')
                    body += '<WrapBox Spacing="10">' + ''.join(boundary_cards) + '</WrapBox>'
                if not review_images_complete(preview, self.workspace, self.board) or not review_videos_complete(preview, self.board or {}, self.workspace, self.slug):
                    self.review_media_paths = []
                    self.toast("The review is missing takes or boundary images. Refresh production before approving.", True); return
                body += '<HBox Spacing="10">' + "".join(v.button(f"Play take {i + 1}", f"production.review_media.{i}") for i in range(len(self.review_media_paths))) + '</HBox>'
                body += v.label("Review both the latest actual take and the next opening before approving.", 12, True)
                body += ''.join(v.label(note,12,True) for note in continuity_review_notes(preview,self.board))
                body += v.field("production.review_note", "Review observations (optional)", height=60,
                    placeholder="Record what you actually saw and heard. Correct mismatches before approving.")
            body += '<CheckBox Name="production.confirm" Text="I have reviewed the material and approve this revision."/>'
            self.show_modal("Approve production step", body, "production.approve.confirm", "Approve")
            return
        if not self.checked("production.confirm") or self.gate_key != snapshot:
            self.toast("This approval is no longer valid. Refresh and review again.", True); return
        if kind == "approve_review":
            played = {str(Path(p).resolve()) for p in getattr(self, "review_played", set())}
            required = {str(Path(p).resolve()) for p in getattr(self, "review_media_paths", [])}
            if not required or not required.issubset(played):
                self.toast("Play every take through before approving.", True); return
        note=self.text("production.review_note").strip() if kind=="approve_review" else ""
        if len(note)>4000:
            self.toast("Keep review observations within 4000 characters.", True); return
        try: action = film_action(context, kind, accepted=True, note=note)
        except ValueError as exc: self.toast(str(exc), True); return
        self.gate_key = None; self.modal_close()
        slug = self.slug
        self.submit("film.action", lambda: self.backend.post("/api/film/action", {"slug": slug, "action": action}, timeout=1200), lambda result: (setattr(self, "film_context", result if isinstance(result, dict) else context), self.render_production(), self.toast("Review recorded.")))

    def stop_film(self):
        project = (self.film_context or {}).get("project") or self.film_context or {}
        job = project.get("jobId") or project.get("job", {}).get("id")
        if not job: self.toast("No active film job to stop.", True); return
        slug = self.slug
        payload = {"slug": slug, "jobId": job}
        self.submit("film.stop", lambda: self.backend.post("/api/film/stop", payload), lambda result: self.toast("Film job stopped."))

    def review_media(self, index):
        media = getattr(self, "review_media_paths", [])
        if 0 <= index < len(media):
            preview = getattr(self, "preview", None)
            if preview:
                preview(Path(media[index]), "Production review")
            else:
                self._show_media(media[index], "Production review")

    def assemble(self):
        if not self.slug: return
        project = (self.film_context or {}).get("project") or self.film_context or {}
        if project.get("busy") or project.get("imageApprovalRequired") or project.get("reviewRequired"):
            self.toast("Approve the image plan and review before assembling the film.", True)
            return
        slug = self.slug
        def operation():
            result = self.backend.post("/api/assemble", {"slug": slug}, timeout=1200)
            candidates = []
            if isinstance(result, dict):
                for key in ("finalVideo", "output", "path", "video"):
                    value = result.get(key)
                    if isinstance(value, dict): value = value.get("path") or value.get("url")
                    if value: candidates.append(value)
            candidates += [str(self.workspace.projects_dir / slug / "final.mp4"), str(self.workspace.projects_dir / slug / "Output" / "Video" / "final.mp4")]
            source = next((self.workspace.local_media(x) or Path(x) for x in candidates if Path(x).is_file()), None)
            if not source or not source.is_file():
                raise ValueError("Assembly did not produce an actual video file")
            published = self.workspace.publish_video(slug, source)
            return published
        self.submit("assemble", operation, lambda path: self.toast(f"Film published to Output · {path.name}"))

    def play_final(self):
        project = self.workspace.projects_dir / self.slug / "Output" / "Video"
        candidates = sorted((p for p in project.glob("*") if p.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}), key=lambda p: p.stat().st_mtime, reverse=True) if project.is_dir() else []
        board_final = (self.board or {}).get("finalVideo")
        if isinstance(board_final, dict):
            board_final = board_final.get("path") or board_final.get("url")
        if board_final:
            local = self.workspace.local_media(board_final)
            if local: candidates.insert(0, local)
        canonical = self.workspace.local_media(f"{self.slug}/final.mp4")
        if canonical: candidates.insert(0, canonical)
        if not candidates:
            self.toast("No finished film has been published yet.", True); return
        self.preview(candidates[0], "Finished film")

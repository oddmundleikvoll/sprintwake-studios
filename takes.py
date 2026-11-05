"""Seed-take history panel for Raven's production studio.

The mixin owns only the UI orchestration.  Seed rendering is never started
here: the Storyboard server has already retained the takes, and this panel
only reads them or adopts one after the normal save/busy checks.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import views as v


class TakePanelMixin:
    """Actions backing ``take.history``, ``take.preview.N`` and ``take.adopt.N``."""

    def _take_shot_id(self):
        raw = getattr(self, "raw_shot", None)
        if isinstance(raw, dict) and raw.get("id"):
            return str(raw["id"])
        shot = getattr(self, "shot", None)
        return str(getattr(shot, "id", "")) if shot else ""

    def take_action(self, action: str):
        if action == "take.history":
            return self.show_take_history()
        if action.startswith("take.preview."):
            return self.preview_seed_take(int(action.rsplit(".", 1)[1]))
        if action.startswith("take.adopt."):
            return self.adopt_seed_take(int(action.rsplit(".", 1)[1]))
        return False

    def show_take_history(self):
        slug, shot_id = str(getattr(self, "slug", "")), self._take_shot_id()
        if not slug or not shot_id:
            return False
        epoch = getattr(self, "epoch", 0)
        self._take_context = (slug, shot_id, epoch)
        self.show_modal("Take history", v.label("Loading preserved takes…", 13, True))

        def operation():
            return self.backend.get(f"/api/boards/{quote(slug)}/shots/{quote(shot_id)}/seed-takes")

        def done(payload):
            if (getattr(self, "slug", ""), self._take_shot_id(), getattr(self, "epoch", 0)) != self._take_context:
                return
            takes = payload.get("takes", []) if isinstance(payload, dict) else []
            self._seed_takes = takes if isinstance(takes, list) else []
            self._render_take_history()

        return self.submit("take.history", operation, done)

    def _render_take_history(self):
        takes = getattr(self, "_seed_takes", [])
        if not takes:
            selected = getattr(getattr(self, "shot", None), "take", None)
            selected_text = "The selected take remains unchanged." if selected else "The shot does not have a selected take yet."
            body = v.label("No preserved seed takes", 17) + v.label(selected_text, 13, True)
            body += v.label("Seed history appears here after a comparison run. Opening history does not start a new job.", 12, True)
            self.show_modal("Take history", body, "modal.close", "Close")
            return
        cards = []
        for index, take in enumerate(takes):
            seed = take.get("seed", "?")
            status = take.get("status") or "unknown"
            current = " · matches scene" if take.get("current") else " · older scene revision"
            recipe = f"Seed {seed} · {status}{current} · recipe: {take.get('recipe') or 'preserved server recipe'}"
            if take.get("runtimeSeconds") is not None:
                recipe += f" · {take['runtimeSeconds']} s"
            cards.append(v.button(f"View take {seed}", f"take.preview.{index}") + v.label(recipe, 12, True) + v.button("Use this take", f"take.adopt.{index}"))
        self.show_modal("Take history", "<VBox Spacing=\"14\">" + "".join(cards) + "</VBox>", "modal.close", "Close")

    def preview_seed_take(self, index: int):
        takes = getattr(self, "_seed_takes", [])
        if not 0 <= index < len(takes):
            return False
        take = takes[index]
        ref = take.get("clipUrl") or take.get("thumbUrl")
        if not ref:
            self.toast("This take has no preview.", True)
            return False
        local = self.workspace.local_media(ref) if hasattr(self, "workspace") else None
        if local and hasattr(self, "preview"):
            self.preview(Path(local), f"Seed {take.get('seed', '?')}")
        elif hasattr(self, "toast"):
            self.toast("The preview is missing locally.", True)
        return True

    def adopt_seed_take(self, index: int):
        takes = getattr(self, "_seed_takes", [])
        if not 0 <= index < len(takes):
            return False
        if getattr(self, "_busy", lambda: False)():
            self.toast("Wait for active production to finish before choosing a take.", True)
            return False
        slug, shot_id = str(getattr(self, "slug", "")), self._take_shot_id()
        seed = takes[index].get("seed")
        if not slug or not shot_id or not isinstance(seed, int):
            self.toast("This take can no longer be selected.", True)
            return False
        context = (slug, shot_id, getattr(self, "epoch", 0))

        def after_save():
            def operation():
                return self.backend.post(f"/api/boards/{quote(slug)}/shots/{quote(shot_id)}/seed-takes/{seed}/adopt", {})

            def done(payload):
                if (getattr(self, "slug", ""), self._take_shot_id(), getattr(self, "epoch", 0)) != context:
                    return
                if isinstance(payload, dict) and isinstance(payload.get("board"), dict):
                    self.board = payload["board"]
                self.modal_close()
                self.refresh_view()
                self.poll_deadline = 0
                self.toast(f"Seed {seed} selected as the take.")

            self.submit("take.adopt", operation, done)

        self.save(after=after_save)
        return True

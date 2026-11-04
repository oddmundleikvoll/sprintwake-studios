"""Load independent narrative and sound documents against the saved cut."""
import copy

import raven as r

from cut import CutStore
from cut_media import bind_production
from production_model import view_project
from sequence import SequenceStore
from sound import SoundStore
from structure_panel import StructurePanelMixin
from sound_panel import SoundPanelMixin


class StoryWorkspaceMixin(StructurePanelMixin, SoundPanelMixin):
    def initialize_story_workspace(self):
        self.sequence_store = self.sequence_doc = None
        self.sound_store = self.sound_doc = None
        self.sound_selection = None
        self.sound_items = {}
        self.sound_tracks = {}
        self.sound_gesture_pending = False
        self.sound_removed = set()
        self.sound_loading = False
        self.sound_cursor = 0
        self.sound_selected_track = "dialogue"
        self.story_cut_selection = None

    def _free_project(self):
        self.initialize_story_workspace()
        self.replace_host("structure.content", "")
        self.replace_host("sound.inspector", "")
        r.Rtimeline_clear(self.find("sound.timeline"))
        return super()._free_project()

    def editorial_io_busy(self):
        return any(k.startswith(("editorial.", "sound.")) or k in {"rename","archive","project.delete","project.restore","open"} for k in self.pending)

    def open_story_workspace(self, mode):
        if not self.board or self.editorial_io_busy() or any(k in self.pending for k in ("rename", "archive")):
            return
        slug, board = self.slug, copy.deepcopy(self.board)
        production = view_project(slug, board, self.workspace)
        self.toast("Opening sequences and soundtracks…")
        def operation():
            cut = CutStore(self.workspace, slug)
            first = not cut.path.exists()
            doc = cut.initialize(production)
            if first:
                doc = cut.commit(doc, bind_production(cut, doc, production), "First cut", remember=False)
            structure, sound = SequenceStore(self.workspace, slug), SoundStore(self.workspace, slug)
            return cut, doc, structure, structure.initialize(board, doc["clips"]), sound, sound.load()
        def done(result):
            self.cut_store, self.cut_doc, self.sequence_store, self.sequence_doc, self.sound_store, self.sound_doc = result
            self._entering_story = True
            try:
                self.mode_set(mode)
            finally:
                self._entering_story = False
        self.submit("editorial.load", operation, done)

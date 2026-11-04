import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from cut import CutStore, choose_take, move, trim
from cut_media import bind_production, executable, export_cut, preserve_take, probe
from project import Project, Shot, Take
from workspace import Workspace


class CutMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.source = Path(cls.temporary.name) / "red-blue.mp4"
        subprocess.run([executable("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "color=red:s=64x64:r=24:d=1",
                        "-f", "lavfi", "-i", "color=blue:s=64x64:r=24:d=1", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
                        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.source)],
                       capture_output=True, check=True, timeout=30)

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(Path(self.temp.name))
        self.store = CutStore(self.workspace, "film")
        self.project = Project("film", "Film", "", [Shot("a", "A", 2, takes=[Take("original", "Original", video=str(self.source))])])
        self.doc = self.store.initialize(self.project)

    def test_binding_preserves_original_and_does_not_adopt_or_generate(self):
        board = self.store.project / "storyboard.json"
        board.write_text('{"shots":[{"id":"a"}]}')
        before = board.read_bytes(), self.source.read_bytes()
        clips = bind_production(self.store, self.doc, self.project)
        source = self.store.project / clips[0]["source"]["video"]
        self.assertEqual(source.read_bytes(), before[1])
        self.assertEqual(clips[0]["sourceFrames"], 48)
        self.assertEqual((board.read_bytes(), self.source.read_bytes()), before)
        self.assertEqual(bind_production(self.store, {**self.doc, "clips": clips}, self.project), clips)

    def test_export_uses_trimmed_picture_and_linked_audio(self):
        source, total = preserve_take(self.store, self.source)
        doc = self.store.commit(self.doc, choose_take(self.doc["clips"], "a", source, total), "Take")
        doc = self.store.commit(doc, trim(doc["clips"], "a", 24, 42), "Trim")
        original = self.source.read_bytes()
        output = export_cut(self.store, doc, self.project)
        info = probe(output)
        self.assertEqual(info["frames"], 18)
        self.assertTrue(info["audio"])
        pixels = subprocess.run([executable("ffmpeg"), "-v", "error", "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        means = [sum(pixels[channel::3]) / (len(pixels) / 3) for channel in range(3)]
        self.assertGreater(means[2], 200)  # The blue second, never the red opening.
        self.assertLess(means[0], 30)
        audio = subprocess.run([executable("ffmpeg"), "-v", "error", "-i", str(output), "-vn", "-f", "s16le", "-ac", "1", "-ar", "48000", "-"], capture_output=True, check=True).stdout
        self.assertGreater(len(audio), 48000)
        self.assertNotEqual(set(audio), {0})
        self.assertEqual(self.source.read_bytes(), original)
        self.assertTrue(output.with_suffix(".cut.json").is_file())

    def test_export_preserves_clip_order_and_total_frames(self):
        self.project.shots.append(Shot("b", "B", 2, takes=[Take("original", "Original", video=str(self.source))]))
        doc = self.store.initialize(self.project)
        doc = self.store.commit(doc, bind_production(self.store, doc, self.project), "Takes")
        clips = trim(doc["clips"], "a", 0, 12)
        clips = trim(clips, "b", 24, 36)
        doc = self.store.commit(doc, move(clips, "b", -1), "Klipp")
        output = export_cut(self.store, doc, self.project)
        self.assertEqual(probe(output)["frames"], 24)
        pixels = subprocess.run([executable("ffmpeg"), "-v", "error", "-i", str(output), "-vf", "select=eq(n\\,0)+eq(n\\,12)", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        first, second = pixels[:64*64*3], pixels[64*64*3:]
        self.assertGreater(sum(first[2::3]), sum(first[0::3]))
        self.assertGreater(sum(second[0::3]), sum(second[2::3]))

    def test_placeholder_export_is_visible_and_has_silent_audio(self):
        output = export_cut(self.store, self.doc, self.project)
        info = probe(output)
        self.assertEqual(info["frames"], 48)
        self.assertTrue(info["audio"])

    def test_missing_selected_video_stops_export_without_silently_omitting_it(self):
        source = {"id": "x", "label": "Take", "video": "Input/missing.mp4", "image": ""}
        doc = self.store.commit(self.doc, choose_take(self.doc["clips"], "a", source, 48), "Take")
        with self.assertRaisesRegex(ValueError, "mangler"):
            export_cut(self.store, doc, self.project)
        self.assertFalse(list((self.store.project / "Output/Video").glob("*.mp4")))

    def test_project_zip_retains_cut_history_and_pinned_sources(self):
        doc = self.store.commit(self.doc, bind_production(self.store, self.doc, self.project), "Take")
        self.store.commit(doc, trim(doc["clips"], "a", 12, 36), "Trim")
        with zipfile.ZipFile(self.workspace.export("film")) as archive:
            self.assertIn("film/Script/cut.json", archive.namelist())
            self.assertIn("film/" + doc["clips"][0]["source"]["video"], archive.namelist())


if __name__ == "__main__": unittest.main()

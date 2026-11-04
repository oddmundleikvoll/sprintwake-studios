"""Regression checks for bounded-input long-film assembly."""

from pathlib import Path
import tempfile
import unittest
import subprocess
import shutil
from unittest.mock import patch

from server import assemble


class AssemblyScalingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_crossfade_uses_temporal_tail_content(self):
        """A white tail fading to a black head catches start-vs-tail regressions."""
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            clips = []
            for i, seconds in enumerate((1.6, 2.1, 1.8, 2.3)):
                clip = root / f"temporal-{i}.mp4"
                half = seconds / 2
                subprocess.run([
                    shutil.which("ffmpeg"), "-y", "-v", "error",
                    "-f", "lavfi", "-i", f"color=black:s=64x64:r=24:d={half}",
                    "-f", "lavfi", "-i", f"color=white:s=64x64:r=24:d={half}",
                    "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={half}",
                    "-f", "lavfi", "-i", f"sine=f=880:r=48000:d={half}",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(clip),
                ], check=True)
                clips.append(clip)
            out = root / "temporal-final.mp4"
            result = assemble.assemble([(str(i), clip) for i, clip in enumerate(clips)], out, 64, 64,
                                       options={"transitionSeconds": 0.4})
            self.assertTrue(result.ok, result.error)
            # First transition begins at 1.2s; its midpoint should blend the
            # white tail of clip 0 with black head of clip 1 (mid-grey).
            probe = subprocess.run([
                shutil.which("ffmpeg"), "-v", "error", "-ss", "1.4", "-i", str(out),
                "-frames:v", "1", "-vf", "signalstats,metadata=mode=print:file=-", "-f", "null", "-",
            ], capture_output=True, text=True, check=True)
            values = [float(line.rsplit("=", 1)[1]) for line in probe.stdout.splitlines()
                      if "lavfi.signalstats.YAVG" in line]
            self.assertTrue(values)
            self.assertGreater(values[-1], 70)
            self.assertLess(values[-1], 190)
            # The same tail/head window must carry the late tone from clip 0.
            audio = subprocess.run([
                shutil.which("ffmpeg"), "-v", "info", "-ss", "1.4", "-t", "0.2", "-i", str(out),
                "-vn", "-af", "volumedetect", "-f", "null", "-",
            ], capture_output=True, text=True, check=True).stderr
            peak = next(float(line.split("max_volume:", 1)[1].split("dB", 1)[0])
                        for line in audio.splitlines() if "max_volume:" in line)
            self.assertGreater(peak, -35)
            # The final transition is also present and the total duration uses
            # every overlap exactly once.
            expected = sum((1.6, 2.1, 1.8, 2.3)) - 3 * 0.4
            self.assertAlmostEqual(result.seconds, expected, delta=0.15)
            last_mid = expected - 2.3 + 0.2
            last = subprocess.run([
                shutil.which("ffmpeg"), "-v", "error", "-ss", str(last_mid), "-i", str(out),
                "-frames:v", "1", "-vf", "signalstats,metadata=mode=print:file=-", "-f", "null", "-",
            ], capture_output=True, text=True, check=True)
            last_values = [float(line.rsplit("=", 1)[1]) for line in last.stdout.splitlines()
                           if "lavfi.signalstats.YAVG" in line]
            self.assertTrue(last_values)
            self.assertGreater(last_values[-1], 70)
            self.assertLess(last_values[-1], 190)

    def test_hundred_clip_hard_cut_never_opens_more_than_one_clip(self):
        """The old graph opened every shot at once; the pipeline stays bounded."""
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            clips = [root / f"clip-{i:03d}.mp4" for i in range(100)]
            for clip in clips:
                clip.write_bytes(b"source")
            parts = [(str(i), clip) for i, clip in enumerate(clips)]
            seen = []

            def fake_run(argv, cancel, timeout):
                seen.append(sum(1 for i, arg in enumerate(argv) if arg == "-i"))
                Path(argv[-1]).write_bytes(b"x" * 2048)
                return assemble.subprocess.CompletedProcess(argv, 0, stderr="")

            with patch.object(assemble, "_duration", return_value=1.0), \
                 patch.object(assemble, "_has_audio", return_value=False), \
                 patch.object(assemble, "_run_ffmpeg", side_effect=fake_run), \
                 patch.object(assemble, "_validate_output"):
                result = assemble.assemble(parts, root / "final.mp4", 64, 64)
            self.assertTrue(result.ok, result.error)
            self.assertLessEqual(max(seen), 1)
            self.assertLessEqual(len(seen), 101)  # cache may collapse identical sources
            self.assertGreaterEqual(len(seen), 2)  # normalization plus concat

    def test_balanced_crossfade_never_opens_more_than_two_inputs(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            clips = [root / f"clip-{i:02d}.mp4" for i in range(8)]
            for clip in clips:
                clip.write_bytes(b"source")
            parts = [(str(i), clip) for i, clip in enumerate(clips)]
            seen = []

            def fake_run(argv, cancel, timeout):
                seen.append(sum(1 for i, arg in enumerate(argv) if arg == "-i"))
                Path(argv[-1]).write_bytes(b"x" * 2048)
                return assemble.subprocess.CompletedProcess(argv, 0, stderr="")

            with patch.object(assemble, "_duration", return_value=1.0), \
                 patch.object(assemble, "_has_audio", return_value=False), \
                 patch.object(assemble, "_run_ffmpeg", side_effect=fake_run), \
                 patch.object(assemble, "_validate_output"):
                result = assemble.assemble(parts, root / "final.mp4", 64, 64,
                                           options={"transitionSeconds": 0.1})
            self.assertTrue(result.ok, result.error)
            self.assertLessEqual(max(seen), 2)


if __name__ == "__main__":
    unittest.main()

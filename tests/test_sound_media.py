import subprocess
import tempfile
from pathlib import Path
import unittest
import wave
import array
import math
import copy
from unittest.mock import patch

from sound import SoundStore, default_state
from sound_media import preserve_audio, render_mix, _run_mix
from workspace import Workspace
from media_tools import executable as media_executable


def executable(name):
    try:
        return media_executable(name)
    except ValueError:
        return None


@unittest.skipUnless(executable("ffmpeg") and executable("ffprobe"), "FFmpeg-verktøy mangler")
class SoundMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name); self.source = root / "tone.wav"
        subprocess.run([executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(self.source)], check=True)
        self.store = SoundStore(Workspace(root / "workspace"), "film")

    def tearDown(self): self.tmp.cleanup()

    def test_preserve_and_mix_exact_film_length_and_cache_invalidation(self):
        clip = preserve_audio(self.store, self.source)
        state = default_state(); state["clips"] = [clip]
        doc = self.store.commit(self.store.load(), state, "tone")
        cut = {"clips": [{"shotId": "a", "source": None, "inFrame": 0, "outFrame": 24, "sourceFrames": 24, "included": True}, {"shotId": "b", "source": None, "inFrame": 0, "outFrame": 24, "sourceFrames": 24, "included": True}]}
        output = render_mix(self.store, doc, cut, include_original=False)
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.getnframes(), 2 * 48000)
        self.assertEqual(render_mix(self.store, doc, cut, include_original=False), output)
        output.write_bytes(output.read_bytes()[:-4] + b"\x00\x00\x00\x00")
        self.assertTrue(render_mix(self.store, doc, cut, include_original=False).is_file())

    def _samples(self, path):
        with wave.open(str(path), "rb") as audio:
            return array.array("h", audio.readframes(audio.getnframes()))[::2]

    def _cut(self,frames):
        return {"clips":[{"shotId":"a","source":None,"inFrame":0,"outFrame":frames,"sourceFrames":frames,"included":True}]}

    def _rms(self,samples):
        return math.sqrt(sum(x*x for x in samples)/len(samples))

    def test_timeline_frame_onset_is_sample_exact_and_trailing_silence_retained(self):
        clip=preserve_audio(self.store,self.source)
        clip.update(inFrame=1,outFrame=13,atFrame=25)
        state=default_state();state["clips"]=[clip]
        actual=self._samples(render_mix(self.store,{"state":state},self._cut(72),include_original=False))
        source=self._samples(self.store.project/clip["path"])
        self.assertEqual(len(actual),144000)
        self.assertEqual(max(abs(x) for x in actual[:50000]),0)
        self.assertLess(max(abs(actual[50000+i]-source[2000+i]) for i in range(1000)),3)
        self.assertEqual(max(abs(x) for x in actual[74000:]),0)

    def test_streamed_batches_equal_one_batch_without_normalization_or_extra_latency(self):
        original=preserve_audio(self.store,self.source)
        state=default_state()
        state["clips"]=[dict(original,id=str(i),outFrame=18,atFrame=i*6,gainDb=-6) for i in range(5)]
        cut=self._cut(72)
        whole=render_mix(self.store,{"state":state},cut,include_original=False)
        expected=self._samples(whole)
        whole.unlink();whole.with_suffix(".json").unlink()
        with patch("sound_media.BATCH_SIZE",2),patch("sound_media._run_mix",wraps=_run_mix) as calls:
            actual=self._samples(render_mix(self.store,{"state":state},cut,include_original=False))
        self.assertEqual(calls.call_count,4)
        self.assertTrue(all(args.args[0].count("-i")<=3 for args in calls.call_args_list))
        self.assertLess(max(abs(a-b) for a,b in zip(actual,expected)),3)

    def test_overlaps_sum_and_fade_out_is_not_moved_by_film_truncation(self):
        clip=preserve_audio(self.store,self.source)
        state=default_state();state["clips"]=[clip]
        one=self._samples(render_mix(self.store,{"state":state},self._cut(24),include_original=False))
        state["clips"].append(dict(clip,id="second"))
        two=self._samples(render_mix(self.store,{"state":state},self._cut(24),include_original=False))
        self.assertAlmostEqual(self._rms(two[1000:20000])/self._rms(one[1000:20000]),2,places=2)
        state["clips"]=[dict(clip,fadeOut=12)]
        truncated=self._samples(render_mix(self.store,{"state":state},self._cut(24),include_original=False))
        self.assertLess(max(abs(a-b) for a,b in zip(one,truncated)),3)
        full=self._samples(render_mix(self.store,{"state":state},self._cut(48),include_original=False))
        self.assertLess(self._rms(full[-5000:]),self._rms(full[5000:10000])*.2)

    def test_solo_mutes_other_buses_and_missing_media_or_symlinks_fail(self):
        clip=preserve_audio(self.store,self.source)
        state=default_state();state["clips"]=[clip,dict(clip,id="music",track="music",gainDb=-6)]
        state["tracks"][1]["solo"]=True
        solo=self._samples(render_mix(self.store,{"state":state},self._cut(48),include_original=False))
        state["clips"]=[dict(clip,track="music",gainDb=-6)]
        only=self._samples(render_mix(self.store,{"state":state},self._cut(48),include_original=False))
        self.assertEqual(solo,only)
        cached=render_mix(self.store,{"state":state},self._cut(48),include_original=False)
        outside=Path(self.tmp.name)/"foreign.wav";outside.write_bytes(cached.read_bytes())
        cached.unlink();cached.symlink_to(outside)
        with self.assertRaises(ValueError): render_mix(self.store,{"state":state},self._cut(48),include_original=False)
        state["clips"][0]["path"]="Input/missing.wav"
        with self.assertRaises(ValueError): render_mix(self.store,{"state":state},self._cut(48),include_original=False)

    def test_original_audio_uses_picture_source_offsets_and_dialogue_mute(self):
        clip=preserve_audio(self.store,self.source)
        cut=self._cut(24);cut["clips"][0].update(source={"id":"take","label":"Take","video":clip["path"],"image":""},inFrame=12,outFrame=36,sourceFrames=48)
        state=default_state()
        actual=self._samples(render_mix(self.store,{"state":state},cut))
        self.assertGreater(self._rms(actual[2000:20000]),100)
        state["tracks"][0]["muted"]=True
        muted=self._samples(render_mix(self.store,{"state":state},cut))
        self.assertEqual(max(abs(x) for x in muted),0)
        video=self.store.project/"Input/silent.mp4"
        subprocess.run([executable("ffmpeg"),"-v","error","-y","-f","lavfi","-i","color=s=64x64:r=24:d=1","-an","-c:v","libx264",str(video)],check=True)
        cut["clips"][0].update(inFrame=0,outFrame=24,sourceFrames=24);cut["clips"][0]["source"]["video"]="Input/silent.mp4"
        state=default_state()
        silence=self._samples(render_mix(self.store,{"state":state},cut))
        self.assertEqual(max(abs(x) for x in silence),0)

    def test_source_trim_and_absolute_onset_are_measurable(self):
        clip = preserve_audio(self.store, self.source)
        clip.update(inFrame=12, outFrame=36, atFrame=24)
        state = default_state(); state["clips"] = [clip]
        doc = self.store.commit(self.store.load(), state, "trim")
        cut = {"clips": [{"shotId": "a", "source": None, "inFrame": 0, "outFrame": 72, "sourceFrames": 72, "included": True}]}
        samples = self._samples(render_mix(self.store, doc, cut, include_original=False))
        self.assertLess(max(abs(x) for x in samples[:30000]), 5000)
        self.assertGreater(max(abs(x) for x in samples[48000:50000]), 1000)

    def test_gain_is_not_normalized_by_number_of_nonoverlapping_cues(self):
        first = preserve_audio(self.store, self.source); second = dict(first, id="b", atFrame=48, gainDb=-6.0)
        state = default_state(); state["clips"] = [first, second]
        doc = self.store.commit(self.store.load(), state, "two cues")
        cut = {"clips": [{"shotId": "a", "source": None, "inFrame": 0, "outFrame": 96, "sourceFrames": 96, "included": True}]}
        samples = self._samples(render_mix(self.store, doc, cut, include_original=False))
        rms_a = math.sqrt(sum(x*x for x in samples[1000:20000]) / 19000)
        rms_b = math.sqrt(sum(x*x for x in samples[100000:115000]) / 15000)
        self.assertGreater(rms_a / rms_b, 1.7)

    def test_muted_bus_and_corrupt_cache_are_honored(self):
        clip = preserve_audio(self.store, self.source)
        state = default_state(); state["clips"] = [clip]; state["tracks"][0]["muted"] = True
        doc = self.store.commit(self.store.load(), state, "mute")
        cut = {"clips": [{"shotId": "a", "source": None, "inFrame": 0, "outFrame": 48, "sourceFrames": 48, "included": True}]}
        output = render_mix(self.store, doc, cut, include_original=False)
        output.write_bytes(b"not a wav")
        rebuilt = render_mix(self.store, doc, cut, include_original=False)
        self.assertEqual(max(abs(x) for x in self._samples(rebuilt)), 0)


if __name__ == "__main__": unittest.main()

import array
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from cut import CutStore
from cut_media import executable, export_cut, preserve_take
from project import Project, Shot
from sound import SoundStore
from sound_media import preserve_audio
from workspace import Workspace, _digest


class EditorialExportTests(unittest.TestCase):
    def test_picture_export_uses_same_cross_cut_mix_and_preserves_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);workspace=Workspace(root/"studio")
            video=root/"take.mp4";voice=root/"voice.wav"
            subprocess.run([executable("ffmpeg"),"-v","error","-y","-f","lavfi","-i","color=c=red:s=64x64:r=24:d=2","-f","lavfi","-i","sine=frequency=200:sample_rate=48000:duration=2","-c:v","libx264","-c:a","aac","-shortest",str(video)],check=True)
            subprocess.run([executable("ffmpeg"),"-v","error","-y","-f","lavfi","-i","sine=frequency=900:sample_rate=48000:duration=2","-ac","2",str(voice)],check=True)
            original=(_digest(video),_digest(voice))
            store=CutStore(workspace,"film")
            source,_=preserve_take(store,video)
            doc=store.load()
            clips=[{"shotId":sid,"source":source,"inFrame":start,"outFrame":start+24,"sourceFrames":48,"included":True} for sid,start in (("a",0),("b",24))]
            doc=store.commit(doc,clips,"Klipp")
            production=Project("film","Film","",[Shot("a","A",1),Shot("b","B",1)])
            sound=SoundStore(workspace,"film")
            audio=preserve_audio(sound,voice);audio.update(atFrame=12,outFrame=36)
            sound_doc=sound.load();state=sound_doc["state"];state["clips"]=[audio];state["original"]["enabled"]=False
            sound_doc=sound.commit(sound_doc,state,"Dialog over klippgrensen")
            output=export_cut(store,doc,production,sound_store=sound,sound_doc=sound_doc)
            decoded=subprocess.run([executable("ffmpeg"),"-v","error","-i",str(output),"-map","0:a:0","-ac","1","-ar","48000","-f","s16le","pipe:1"],capture_output=True,check=True).stdout
            samples=array.array("h",decoded)
            self.assertLess(max(abs(v) for v in samples[3000:18000]),30)
            for start in (30000,60000):
                self.assertGreater(math.sqrt(sum(v*v for v in samples[start:start+4000])/4000),500)
                crossings=sum(samples[i]<=0<samples[i+1] for i in range(start,start+4000-1))
                self.assertTrue(70<crossings<80,crossings)
            recipe=json.loads(output.with_suffix(".cut.json").read_text())
            self.assertEqual(recipe["cut"],doc);self.assertEqual(recipe["sound"],sound_doc)
            self.assertEqual((_digest(video),_digest(voice)),original)
            preview=export_cut(store,doc,production,sound_store=sound,sound_doc=sound_doc,preview=True)
            stamp=preview.stat().st_mtime_ns
            self.assertEqual(export_cut(store,doc,production,sound_store=sound,sound_doc=sound_doc,preview=True),preview)
            self.assertEqual(preview.stat().st_mtime_ns,stamp)
            state=sound_doc["state"];state["clips"][0]["gainDb"]=-3
            sound_doc=sound.commit(sound_doc,state,"Endre nivå")
            actual_run=subprocess.run
            with patch("subprocess.run",wraps=actual_run) as calls:
                export_cut(store,doc,production,sound_store=sound,sound_doc=sound_doc,preview=True)
            self.assertFalse(any("libx264" in call.args[0] for call in calls.call_args_list),"Audio edits must reuse the cached picture")
            from sequence import SequenceStore
            SequenceStore(workspace,"film").initialize({"shots":[{"id":"a"},{"id":"b"}]},doc["clips"])
            with zipfile.ZipFile(workspace.export("film")) as archive:
                names=archive.namelist()
                self.assertIn("film/Script/structure.json",names)
                self.assertIn("film/Script/sound.json",names)
                self.assertIn("film/"+audio["path"],names)
                self.assertFalse(any("sound-preview" in n or "editorial-preview" in n for n in names))

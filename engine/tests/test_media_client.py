import unittest,tempfile,subprocess,shutil,os
from pathlib import Path
from PIL import Image
from engine.server.media import media_metadata,validate_decoded,trim_carried_frames,join_clips
class MediaTests(unittest.TestCase):
 def setUp(self): self.ffmpeg=str(Path(__file__).parents[2]/'runtime/tools/ffmpeg'); self.assertTrue(Path(self.ffmpeg).is_file()); os.environ['RAVEN_FFMPEG']=self.ffmpeg
 def clip(self,path,frames=30):
  subprocess.run([self.ffmpeg,'-y','-f','lavfi','-i','color=c=blue:s=32x32:r=24','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-frames:v',str(frames),'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 def test_corrupt_and_frame_mismatch_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   bad=Path(d)/'bad.mp4'; bad.write_bytes(b'not media')
   with self.assertRaises(ValueError): media_metadata(bad)
   clip=Path(d)/'clip.mp4'; self.clip(clip,10)
   with self.assertRaises(ValueError): validate_decoded(clip,kind='video',frames=11)
 def test_trim25_and_join_preserve_exact_frames_and_audio_rate(self):
  with tempfile.TemporaryDirectory() as d:
   a=Path(d)/'a.mp4'; b=Path(d)/'b.mp4'; trimmed=Path(d)/'trim.mp4'; joined=Path(d)/'joined.mp4'; self.clip(a,40); self.clip(b,40)
   trim_carried_frames(a,trimmed,25); validate_decoded(trimmed,kind='video',frames=15,fps=24,audio_hz=48000)
   join_clips([a,b],joined); validate_decoded(joined,kind='video',frames=80,fps=24,audio_hz=48000)
 def test_decoded_png_is_required(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'pixel.png'; Image.new('RGBA',(2,2),(1,2,3,255)).save(p); meta=validate_decoded(p,kind='image'); self.assertEqual(meta['streams'][0]['width'],2)

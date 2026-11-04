import copy
import unittest

from editorial_timing import sound_gesture, time_frames, time_text
from sound import default_state, validate_state


class EditorialTimingTests(unittest.TestCase):
    def setUp(self):
        self.state=default_state()
        self.state["clips"]=[{"id":"voice","track":"dialogue","label":"Linje","path":"Input/voice.wav","sourceFrames":240,"inFrame":24,"outFrame":120,"atFrame":48,"gainDb":0,"fadeIn":12,"fadeOut":12,"muted":False}]

    def test_move_crosses_picture_boundaries_without_source_trim_changes(self):
        before=copy.deepcopy(self.state)
        result=sound_gesture(self.state,{"voice":(108,96,"music")})
        self.assertEqual((result["clips"][0]["atFrame"],result["clips"][0]["track"]),(108,"music"))
        self.assertEqual((result["clips"][0]["inFrame"],result["clips"][0]["outFrame"]),(24,120))
        self.assertEqual(before,self.state)

    def test_leading_trim_keeps_absolute_end_and_source_samples_aligned(self):
        result=sound_gesture(self.state,{"voice":(72,72,"dialogue")})
        self.assertEqual((result["clips"][0]["atFrame"],result["clips"][0]["inFrame"],result["clips"][0]["outFrame"]),(72,48,120))
        result=sound_gesture(self.state,{"voice":(0,144,"dialogue")})
        self.assertEqual((result["clips"][0]["atFrame"],result["clips"][0]["inFrame"]),(24,0))
        self.assertEqual(result["clips"][0]["atFrame"]+result["clips"][0]["outFrame"]-result["clips"][0]["inFrame"],144)

    def test_tail_clamps_source_and_shortened_fades_and_delete_retains_state(self):
        result=sound_gesture(self.state,{"voice":(48,500,"dialogue")})
        self.assertEqual(result["clips"][0]["outFrame"],240)
        result=sound_gesture(self.state,{"voice":(48,3,"dialogue")})
        self.assertEqual((result["clips"][0]["fadeIn"],result["clips"][0]["fadeOut"]),(3,3))
        validate_state(result)
        self.assertFalse(sound_gesture(self.state,{},["voice"])["clips"])
        self.assertTrue(self.state["clips"])

    def test_timecode_roundtrips_frames_and_rejects_invalid_targets(self):
        for frame in (0,1,23,24,121,12345,86400*24):
            self.assertEqual(time_frames(time_text(frame)),frame)
        self.assertEqual(time_frames("1:30"),90*24)
        self.assertEqual(time_frames("1:00:00"),3600*24)
        for text in ("nan","inf","-1","1:60","::","1:2:3:4"):
            with self.assertRaises(ValueError): time_frames(text)

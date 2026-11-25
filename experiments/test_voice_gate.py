import unittest
from dataclasses import replace
from struct import pack
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from voice_gate import Config,VoiceGate

LOUD=pack("<320h",*([1000]*320))
QUIET=bytes(640)
class VoiceGateTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("local_audio_device",(),"allow"),),clock=profiles.PolicyClock())
        self.c=Config("fixture",600,3,2,12)
        self.v=VoiceGate(self.g,self.ctx,self.c)
        self.grant=self.g.fixture_grant("v",Request(self.ctx,"capture",self.c.target(),"local","private"),60)
    def test_default_off_explicit_review_and_no_auto_restart(self):
        with self.assertRaises(Refused):
            self.v.feed(LOUD)
        self.v.arm(self.grant)
        for f in (LOUD,QUIET,LOUD,LOUD,LOUD,QUIET,QUIET):
            self.v.feed(f)
        self.assertEqual(self.v.state,"review")
        self.assertEqual(self.v.take(),LOUD*3)
        for fn in (lambda:self.v.feed(LOUD),self.v.take,lambda:self.v.arm(self.grant)):
            with self.assertRaises(Refused):
                fn()
    def test_timeout_silence_clears_all_frames(self):
        self.v.arm(self.grant)
        for _ in range(12):
            self.v.feed(QUIET)
        self.assertEqual(self.v.state,"timeout")
        self.assertEqual(self.v._frames,[])
        with self.assertRaises(Refused):
            self.v.take()
    def test_revocation_before_review_clears_pcm(self):
        self.v.arm(self.grant)
        for f in (LOUD,LOUD,LOUD,QUIET,QUIET):
            self.v.feed(f)
        self.g.revoke("v")
        with self.assertRaises(Refused):
            self.v.take()
        self.assertEqual(self.v._frames,[])
        self.assertEqual(self.v.state,"held")
    def test_stop_and_malformed_frame(self):
        self.v.arm(self.grant)
        self.v.feed(LOUD)
        with self.assertRaises(Refused):
            self.v.feed(b"bad")
        self.assertEqual(self.v._lead,[])
        self.v.stop()
        self.assertEqual(self.v.state,"stopped")
    def test_config_and_cross_instance_grant_binding(self):
        other=VoiceGate(self.g,self.ctx,replace(self.c,threshold=700))
        with self.assertRaises(Refused):
            other.arm(self.grant)
        self.v.arm(self.grant)
        with self.assertRaises(Refused):
            VoiceGate(self.g,self.ctx,self.c).arm(self.grant)
    def test_continuous_speech_is_bounded(self):
        self.v.arm(self.grant)
        for _ in range(12):
            self.v.feed(LOUD)
        self.assertEqual(self.v.state,"timeout")
        self.assertEqual(self.v._frames,[])

    def test_idle_wall_deadline_and_revocation_poll(self):
        from unittest.mock import patch
        with patch("voice_gate.monotonic",return_value=10):
            self.v.arm(self.grant)
            self.v.feed(LOUD)
        with patch("voice_gate.monotonic",return_value=11):
            self.assertEqual(self.v.poll(),"timeout")
        self.assertEqual(self.v._lead,[])
        grant=self.g.fixture_grant("next",Request(self.ctx,"capture",self.c.target(),"local","private"),60)
        other=VoiceGate(self.g,self.ctx,self.c)
        other.arm(grant)
        self.g.revoke("next")
        self.assertEqual(other.poll(),"held")
        self.assertEqual(other._frames,[])

    def test_final_frame_crossing_wall_deadline_discards_review(self):
        from unittest.mock import patch
        with patch("voice_gate.monotonic",return_value=10):
            self.v.arm(self.grant)
            for f in (LOUD,LOUD,LOUD,QUIET):
                self.v.feed(f)
        with patch("voice_gate.monotonic",side_effect=[10.1,10.3]):
            self.assertEqual(self.v.feed(QUIET),"timeout")
        self.assertEqual(self.v._frames,[])
        with self.assertRaises(Refused):
            self.v.take()

# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Context,FixtureGateway,Refused
from tihulu_jarvis import profiles
from vision_private_frames import PrivateFrames
from test_vision_image_input import png

class PrivateFrameTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("vision","session","profile",1,"local","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),
                                       profiles.Rule("host_shell",(),"allow")),clock=profiles.PolicyClock())
        self.f=PrivateFrames(self.g,"surface","window")
        self.frame=self.f.replace(png())
    def grant(self,agent,model="vision"):
        return self.g.fixture_grant("review-"+agent,self.f.request(self.frame,agent,model),30)
    def test_exact_private_review_and_separate_agents(self):
        for a in ("a","b"):
            g=self.grant(a)
            self.assertEqual(self.f.review(self.frame,a,"vision",g),b"\1\2\3\4\5\6")
            self.f.checkpoint(self.frame,a)
        self.assertEqual(self.f.request(self.frame,"a","vision").classification,"private")
    def test_wrong_model_agent_or_copied_frame_refused(self):
        g=self.grant("a")
        for frame,a,m in ((replace(self.frame),"a","vision"),(self.frame,"b","vision"),(self.frame,"a","other")):
            with self.assertRaises(Refused):
                self.f.review(frame,a,m,g)
    def test_frame_change_revokes_review_even_same_pixels(self):
        g=self.grant("a")
        self.f.review(self.frame,"a","vision",g)
        next_frame=self.f.replace(png())
        self.assertEqual(next_frame.pixel_digest,self.frame.pixel_digest)
        self.assertNotEqual(next_frame.epoch,self.frame.epoch)
        with self.assertRaises(Refused):
            self.f.checkpoint(self.frame,"a")
        with self.assertRaises(Refused):
            self.f.review(next_frame,"a","vision",g)
    def test_sensitive_or_malformed_input_clears_previous_frame(self):
        for raw,sensitive in ((png(),True),(b"bad",False)):
            self.frame=self.f.replace(png())
            with self.assertRaises(Refused):
                self.f.replace(raw,sensitive=sensitive)
            with self.assertRaises(Refused):
                self.f.current(self.frame)
    def test_cloud_actor_and_cloud_chat_denied(self):
        req=self.f.request(self.frame,"a","vision")
        with self.assertRaises(Refused):
            self.g.fixture_grant("cloud",replace(req,actor="cloud"),30)
        ctx=replace(self.ctx,mode="CLOUD_CHAT")
        g=FixtureGateway(ctx,(profiles.Rule("filesystem_read",(),"allow"),
                             profiles.Rule("host_shell",(),"allow")),clock=profiles.PolicyClock())
        f=PrivateFrames(g,"surface","image")
        frame=f.replace(png())
        with self.assertRaises(Refused):
            g.fixture_grant("cloud-mode",f.request(frame,"a","vision"),30)
    def test_revoke_and_close_refuse_checkpoint(self):
        g=self.grant("a")
        self.f.review(self.frame,"a","vision",g)
        self.g.revoke(g.identity)
        with self.assertRaises(Refused):
            self.f.checkpoint(self.frame,"a")
        self.f.close()
        self.f.close()
        with self.assertRaises(Refused):
            self.f.replace(png())

    def test_duplicate_frame_scope_cannot_reuse_unclaimed_grant(self):
        g=self.grant("a")
        with self.assertRaises(Refused):
            PrivateFrames(self.g,"surface","window")
        self.f.close()
        with self.assertRaises(Refused):
            PrivateFrames(self.g,"surface","window")
        other=PrivateFrames(self.g,"other","window")
        frame=other.replace(png())
        with self.assertRaises(Refused):
            other.review(frame,"a","vision",g)

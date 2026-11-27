# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Context,FixtureGateway,Refused
from tihulu_jarvis import profiles
from vision_private_frames import PrivateFrames
from vision_pointer_arbiter import PointerArbiter
from test_vision_image_input import png

class PointerArbiterTests(unittest.TestCase):
    def setUp(self):
        ctx=Context("vision","session","profile",1,"local","LOCAL_ONLY")
        self.g=FixtureGateway(ctx,(profiles.Rule("filesystem_read",(),"allow"),
                                   profiles.Rule("host_shell",(),"allow")),clock=profiles.PolicyClock())
        self.f=PrivateFrames(self.g,"window","window")
        self.frame=self.f.replace(png())
        self.a=PointerArbiter(self.g,self.f,self.frame)
        for agent in ("one","two"):
            req=self.f.request(self.frame,agent,"vision")
            self.f.review(self.frame,agent,"vision",self.g.fixture_grant("review-"+agent,req,30))
    def grant(self,p):
        return self.g.fixture_grant("pointer-"+str(p.sequence),self.a.request(p),30)
    def test_two_independent_markers_no_control(self):
        p=self.a.mark("one",0,0)
        q=self.a.mark("two",1,0)
        self.assertNotEqual(p.color,q.color)
        self.assertEqual(self.a.marks(),(p,q))
        with self.assertRaises(Refused):
            self.a.apply(p)
    def test_single_controller_exact_one_shot(self):
        p=self.a.propose("one","click",1,0)
        with self.assertRaises(Refused):
            self.a.propose("two","move",0,0)
        g=self.grant(p)
        with self.assertRaises(Refused):
            self.a.confirm(replace(p),g)
        self.a.confirm(p,g)
        with self.assertRaises(Refused):
            self.a.propose("two","click",0,0)
        self.assertEqual(self.a.apply(p),("SIMULATED_ONLY","one","click",1,0))
        with self.assertRaises(Refused):
            self.a.apply(p)
        q=self.a.propose("two","move",0,0)
        self.a.confirm(q,self.grant(q))
        self.assertEqual(self.a.apply(q)[1],"two")
    def test_takeover_revokes_confirmed_action_and_closes(self):
        p=self.a.propose("one","click",0,0)
        self.a.confirm(p,self.grant(p))
        self.a.takeover()
        with self.assertRaises(Refused):
            self.a.apply(p)
        with self.assertRaises(Refused):
            self.a.mark("two",0,0)
    def test_stale_frame_denies_action_even_same_pixels(self):
        p=self.a.propose("one","click",1,0)
        self.a.confirm(p,self.grant(p))
        self.f.replace(png())
        with self.assertRaises(Refused):
            self.a.apply(p)
    def test_review_revoke_removes_marker_and_blocks_control(self):
        self.a.mark("one",0,0)
        self.a.mark("two",1,0)
        p=self.a.propose("one","click",0,0)
        self.a.confirm(p,self.grant(p))
        self.g.revoke("review-one")
        self.assertEqual(tuple(m.agent for m in self.a.marks()),("two",))
        with self.assertRaises(Refused):
            self.a.apply(p)
    def test_image_not_window_and_duplicate_controller_denied(self):
        image=PrivateFrames(self.g,"image","image")
        with self.assertRaises(Refused):
            PointerArbiter(self.g,image,image.replace(png()))
        with self.assertRaises(Refused):
            PointerArbiter(self.g,self.f,self.frame)
    def test_bounds_unsupported_operation_and_unknown_agent_denied(self):
        for agent,x,y in (("one",True,0),("one",2,0),("one",0,-1),("unknown",0,0)):
            with self.assertRaises(Refused):
                self.a.mark(agent,x,y)
        with self.assertRaises(Refused):
            self.a.propose("one","shell",0,0)
    def test_observation_grant_cannot_confirm_input(self):
        p=self.a.propose("one","click",0,0)
        review,_=self.f.checkpoint(self.frame,"one")
        with self.assertRaises(Refused):
            self.a.confirm(p,review)

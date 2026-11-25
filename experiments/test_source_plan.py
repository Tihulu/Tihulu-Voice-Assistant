import unittest
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from research import canonical
from source_plan import SourcePlan, RedirectPlan

class SourcePlanTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY",False,True)
        self.g=FixtureGateway(self.ctx,(profiles.Rule("external_network",(),"allow"),),clock=profiles.PolicyClock())
        self.root="https://example.com/docs"
        self.urls=(self.root+"/a",self.root+"/b")
        self.grants=[self.g.fixture_grant(str(i),Request(self.ctx,"research",canonical(u).identity,"local","public"),60) for i,u in enumerate(self.urls)]
        self.p=RedirectPlan(self.g,self.ctx,SourcePlan(self.root,self.urls))
    def test_each_hop_separate_and_final_rechecked(self):
        for u,g in zip(self.urls,self.grants):
            self.assertEqual(self.p.hop(u,g),u)
        self.assertEqual(self.p.final_target(),self.urls[-1])
        self.g.revoke("0")
        with self.assertRaises(Refused):
            self.p.final_target()
    def test_scope_and_loop_rejected(self):
        for urls in ((self.urls[0],self.urls[0]),(self.urls[0],"https://other.com/docs/a"),
                     (self.root+"2/a",)):
            with self.assertRaises(Refused):
                RedirectPlan(self.g,self.ctx,SourcePlan(self.root,urls))
    def test_no_inherited_grant_or_unapproved_transition(self):
        self.p.hop(self.urls[0],self.grants[0])
        with self.assertRaises(Refused):
            self.p.hop(self.urls[1],self.grants[0])
        self.assertEqual(self.p.state,"held")
        p=RedirectPlan(self.g,self.ctx,SourcePlan(self.root,self.urls))
        with self.assertRaises(Refused):
            p.hop(self.urls[1],self.grants[1])
    def test_close_and_revocation(self):
        self.p.hop(self.urls[0],self.grants[0])
        self.g.revoke("0")
        with self.assertRaises(Refused):
            self.p.hop(self.urls[1],self.grants[1])
        self.p.close()
        with self.assertRaises(Refused):
            self.p.final_target()

    def test_same_hop_grant_cannot_start_another_plan(self):
        self.p.hop(self.urls[0],self.grants[0])
        other=RedirectPlan(self.g,self.ctx,SourcePlan(self.root,self.urls))
        with self.assertRaisesRegex(Refused,"grant_already_claimed"):
            other.hop(self.urls[0],self.grants[0])
        self.p.hop(self.urls[1],self.grants[1])
        self.assertEqual(self.p.final_target(),self.urls[-1])

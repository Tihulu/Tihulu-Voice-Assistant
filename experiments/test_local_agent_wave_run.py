# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Context,FixtureGateway,Refused
from inference import Candidate
from tihulu_jarvis import profiles
from local_agent_planner import Worker,plan_agents
from local_agent_wave_run import WaveRun

class WaveRunTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.models=(Candidate("qwen","llm","local","jan",True,True,2048,80),)
        self.workers=(Worker("plan",(),0,"plan"),Worker("a",("plan",),0,"execute"),
                      Worker("b",("plan",),0,"execute"),Worker("review",("a","b"),0,"review"))
        self.plan=plan_agents(self.ctx,self.workers,self.models,4096,2)
        self.g=FixtureGateway(self.ctx,(profiles.Rule("host_shell",(),"allow"),),clock=profiles.PolicyClock())
        self.run=WaveRun(self.g,self.plan,self.workers,self.models,4096,2)
        self.grants={w.identity:self.g.fixture_grant(w.identity,self.run.request(w.identity),60)
                     for w in self.workers}
    def start(self,w):
        self.run.start(w,self.grants[w],self.ctx,self.models)
    def finish(self,w):
        self.run.complete(w,self.ctx,self.models)
    def test_exact_wave_barrier_and_no_duplicate(self):
        with self.assertRaises(Refused):
            self.start("a")
        self.start("plan")
        with self.assertRaises(Refused):
            self.start("plan")
        self.finish("plan")
        self.start("a")
        self.start("b")
        self.finish("a")
        with self.assertRaises(Refused):
            self.start("review")
        self.finish("b")
        self.start("review")
        self.finish("review")
        self.assertEqual(self.run.result(self.ctx,self.models),("plan","a","b","review"))
    def test_wrong_worker_grant_and_unclaimed_completion_denied(self):
        with self.assertRaises(Refused):
            self.run.start("plan",self.grants["a"],self.ctx,self.models)
        with self.assertRaises(Refused):
            self.finish("plan")
        self.start("plan")
    def test_inventory_and_context_change_before_start(self):
        for ctx,models in ((replace(self.ctx,generation=2),self.models),
                           (self.ctx,(replace(self.models[0],rank=81),))):
            self.setUp()
            with self.assertRaises(Refused):
                self.run.start("plan",self.grants["plan"],ctx,models)
            with self.assertRaises(Refused):
                self.start("plan")
    def test_parent_revocation_blocks_child_and_final_result(self):
        self.start("plan")
        self.finish("plan")
        self.start("a")
        self.g.revoke("plan")
        with self.assertRaises(Refused):
            self.finish("a")
        with self.assertRaises(Refused):
            self.start("b")
        with self.assertRaises(Refused):
            self.run.result(self.ctx,self.models)
    def test_cancel_revokes_owned_claims_not_unstarted_grants(self):
        self.start("plan")
        self.run.cancel()
        self.run.cancel()
        with self.assertRaises(Refused):
            self.finish("plan")
        with self.assertRaises(Refused):
            self.g.checkpoint(self.grants["plan"],self.run.request("plan"))
        self.g.checkpoint(self.grants["a"],self.run.request("a"))
    def test_exact_expiry_does_not_complete(self):
        from unittest.mock import patch
        with patch("tihulu_jarvis.profiles.time.time",return_value=1000),patch("tihulu_jarvis.profiles.time.monotonic",return_value=200):
            self.setUp()
            self.start("plan")
        with patch("tihulu_jarvis.profiles.time.time",return_value=1060),patch("tihulu_jarvis.profiles.time.monotonic",return_value=260):
            with self.assertRaises(Refused):
                self.finish("plan")

    def test_completed_peer_not_a_parent_until_next_wave(self):
        self.start("plan")
        self.finish("plan")
        self.start("a")
        self.finish("a")
        self.g.revoke("a")
        # Same-wave peer is independent of the revoked sibling. The final
        # barrier still requires all earlier waves, including that sibling.
        self.start("b")
        self.finish("b")
        with self.assertRaises(Refused):
            self.start("review")

    def test_changed_inventory_during_running_revokes_owned_and_cannot_resume(self):
        self.start("plan")
        changed=(replace(self.models[0],healthy=False),)
        with self.assertRaises(Refused):
            self.run.complete("plan",self.ctx,changed)
        with self.assertRaises(Refused):
            self.finish("plan")
        with self.assertRaises(Refused):
            self.g.checkpoint(self.grants["plan"],self.run.request("plan"))
        self.g.checkpoint(self.grants["a"],self.run.request("a"))

    def test_changed_context_during_running_is_terminal(self):
        self.start("plan")
        with self.assertRaises(Refused):
            self.run.complete("plan",replace(self.ctx,generation=2),self.models)
        with self.assertRaises(Refused):
            self.finish("plan")

# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Context,FixtureGateway,Refused
from tihulu_jarvis import profiles
from agent_custom_profiles import AgentProfile
from agent_model_capability import ModelOffer
from heterogeneous_team import TeamTask
from heterogeneous_team_admission import TeamAdmission

class TeamAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(),clock=profiles.PolicyClock())
        self.a=TeamAdmission(self.g,"team")
        p=AgentProfile("planner",1,"planner","local",("reasoning",),"economy","","",0,128)
        self.tasks=(TeamTask("a",p,("reasoning",)),)
        self.models=(ModelOffer("text","jan","local",("text",),1024,80,True,True),)
    def args(self,**kwargs):
        opts=dict(context=self.ctx,tasks=self.tasks,available=("reasoning",),models=self.models,memory_budget=2048,token_budget=128)
        opts.update(kwargs)
        return opts
    def test_exact_offer_one_shot(self):
        p=self.a.prepare(**self.args())
        with self.assertRaises(Refused):
            self.a.confirm(replace(p),**self.args())
        self.assertEqual(self.a.confirm(p,**self.args()),p.plan)
        with self.assertRaises(Refused):
            self.a.confirm(p,**self.args())
    def test_inventory_or_budget_drift_consumes_offer(self):
        p=self.a.prepare(**self.args())
        with self.assertRaisesRegex(Refused,"team_plan_changed"):
            self.a.confirm(p,**self.args(models=(replace(self.models[0],rank=81),)))
        with self.assertRaises(Refused):
            self.a.confirm(p,**self.args())
    def test_failed_prepare_invalidates_previous(self):
        p=self.a.prepare(**self.args())
        with self.assertRaises(Refused):
            self.a.prepare(**self.args(token_budget=1))
        with self.assertRaises(Refused):
            self.a.confirm(p,**self.args())
    def test_context_change_and_close_terminal(self):
        p=self.a.prepare(**self.args())
        with self.assertRaises(Refused):
            self.a.confirm(p,**self.args(context=replace(self.ctx,generation=2)))
        with self.assertRaises(Refused):
            self.a.prepare(**self.args())
    def test_duplicate_controller_and_stale_offer(self):
        with self.assertRaises(Refused):
            TeamAdmission(self.g,"team")
        p=self.a.prepare(**self.args())
        q=self.a.prepare(**self.args())
        with self.assertRaises(Refused):
            self.a.confirm(p,**self.args())
        self.assertEqual(self.a.confirm(q,**self.args()),q.plan)
    def test_no_grant_created_by_confirmation(self):
        p=self.a.prepare(**self.args())
        self.a.confirm(p,**self.args())
        self.assertEqual(self.g._grants,{})

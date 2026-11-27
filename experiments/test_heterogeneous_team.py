# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Context,Refused
from agent_custom_profiles import AgentProfile
from agent_model_capability import ModelOffer
from heterogeneous_team import TeamTask,plan_team

class HeterogeneousTeamTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.text=ModelOffer("text","jan","local",("text","structured_tools"),1024,80,True,True)
        self.vision=ModelOffer("vision","jan","local",("text","vision","gui_grounding","structured_tools"),2048,60,True,True)
        self.models=(self.text,self.vision)
        self.available=("reasoning","typed_engine","image_review","desktop_input")
    def task(self,name,role,channel,depends=(),**kwargs):
        p=AgentProfile(name,1,role,"local",(channel,),"economy","","",0,128)
        return TeamTask(name,replace(p,**kwargs),(channel,),depends)
    def plan(self,tasks,**kwargs):
        opts=dict(memory_budget=4096,token_budget=1024)
        opts.update(kwargs)
        return plan_team(self.ctx,tasks,self.available,self.models,**opts)
    def test_mixed_non_screen_and_vision_dependencies(self):
        tasks=(self.task("plan","planner","reasoning"),self.task("app","application","typed_engine",("plan",)),
               self.task("image","vision","image_review",("plan",)),self.task("review","reviewer","reasoning",("app","image")))
        p=self.plan(tasks)
        self.assertEqual(tuple(tuple(r.route.agent for r in w) for w in p.waves),(("plan",),("app","image"),("review",)))
        self.assertEqual(p.output_ceiling,512)
        self.assertEqual(tuple(r.model for r in p.waves[1]),("text","vision"))
    def test_desktop_input_serialized_even_with_ram(self):
        tasks=tuple(self.task(n,"desktop","desktop_input") for n in ("a","b"))
        p=self.plan(tasks)
        self.assertEqual(tuple(len(w) for w in p.waves),(1,1))
    def test_memory_serializes_without_switching_model(self):
        tasks=tuple(self.task(n,"vision","image_review") for n in ("a","b"))
        p=self.plan(tasks,memory_budget=3000)
        self.assertEqual(tuple(len(w) for w in p.waves),(1,1))
        self.assertTrue(all(w[0].model=="vision" for w in p.waves))
    def test_token_budget_rejected_before_plan(self):
        with self.assertRaises(Refused):
            self.plan((self.task("a","planner","reasoning"),self.task("b","planner","reasoning")),token_budget=255)
    def test_cycles_missing_and_duplicate_rejected(self):
        a=self.task("a","planner","reasoning",("b",))
        b=self.task("b","planner","reasoning",("a",))
        for tasks in ((a,),(a,b),(a,a)):
            with self.assertRaises(Refused):
                self.plan(tasks)
    def test_cloud_dependency_refused_even_if_approved(self):
        a=self.task("a","planner","reasoning")
        b=self.task("b","planner","reasoning",("a",),locality="cloud")
        with self.assertRaisesRegex(Refused,"team_cloud_dependency"):
            self.plan((a,b),approved_cloud=("remote",))
    def test_digest_order_stable_and_inventory_bound(self):
        tasks=(self.task("a","planner","reasoning"),self.task("b","vision","image_review"))
        p=self.plan(tasks)
        self.assertEqual(p.digest,self.plan(tuple(reversed(tasks))).digest)
        self.models=(self.text,replace(self.vision,rank=61))
        self.assertNotEqual(p.digest,self.plan(tasks).digest)
    def test_limit_and_infeasible_model(self):
        tasks=(self.task("a","planner","reasoning"),self.task("b","vision","image_review"))
        self.assertEqual(len(self.plan(tasks,limit=1).waves),2)
        with self.assertRaises(Refused):
            self.plan(tasks,memory_budget=1024)

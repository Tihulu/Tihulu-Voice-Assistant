# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Context,Refused
from agent_custom_profiles import AgentProfile
from agent_model_capability import ModelOffer,select

class AgentModelCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.p=AgentProfile("vision",1,"vision","local",("image_review","screen_observe"),"balanced","","",128,256)
        self.text=ModelOffer("text","jan","local",("text","structured_tools"),1024,100,True,True)
        self.vision=ModelOffer("vision","jan","local",("text","vision"),2048,60,True,True)
    def pick(self,p=None,models=None,needs=("image_review",),budget=4096,ctx=None,cloud=()):
        return select(ctx or self.ctx,p or self.p,"worker",needs,needs,models or (self.text,self.vision),budget,approved_cloud=cloud)
    def test_vision_requires_tested_vision_model_not_high_rank_text(self):
        r=self.pick()
        self.assertEqual((r.model,r.memory_mib,r.route.requires_screen),("vision",2176,False))
    def test_no_silent_text_or_cloud_fallback(self):
        for m in (self.text,replace(self.vision,tested=False),replace(self.vision,healthy=False)):
            with self.assertRaises(Refused):
                self.pick(models=(m,))
    def test_pinned_model_and_provider_strict(self):
        for p in (replace(self.p,model="text"),replace(self.p,provider="other")):
            with self.assertRaises(Refused):
                self.pick(p=p)
    def test_budget_includes_profile_overhead(self):
        with self.assertRaises(Refused):
            self.pick(budget=2175)
        self.assertEqual(self.pick(budget=2176).memory_mib,2176)
    def test_gui_requires_grounding_and_structured_tools(self):
        p=replace(self.p,role="desktop",channels=("desktop_input",))
        with self.assertRaises(Refused):
            self.pick(p=p,needs=("desktop_input",))
        m=replace(self.vision,capabilities=("text","vision","gui_grounding","structured_tools"))
        self.assertTrue(self.pick(p=p,models=(m,),needs=("desktop_input",)).route.effect_review)
    def test_reasoning_prefers_no_screen(self):
        p=replace(self.p,role="planner",channels=("reasoning",))
        self.assertEqual(self.pick(p=p,needs=("reasoning",)).model,"text")
    def test_cloud_advice_provider_explicit_and_no_host_channel(self):
        p=replace(self.p,role="planner",channels=("reasoning",),locality="cloud",memory_mib=0)
        m=replace(self.text,locality="cloud",memory_mib=0,provider="remote")
        ctx=replace(self.ctx,mode="SECURE_HYBRID",offline=False)
        with self.assertRaises(Refused):
            self.pick(p=p,models=(m,),needs=("reasoning",),ctx=ctx)
        r=self.pick(p=p,models=(m,),needs=("reasoning",),ctx=ctx,cloud=("remote",))
        self.assertEqual(r.route.locality,"cloud")
    def test_duplicate_inventory_and_invalid_type(self):
        for models in ((self.vision,self.vision),(replace(self.vision,rank=True),)):
            with self.assertRaises(Refused):
                self.pick(models=models)

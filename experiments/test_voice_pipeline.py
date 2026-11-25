import unittest
from dataclasses import replace
from gateway import Context,Refused
from inference import Candidate
from voice_pipeline import plan_voice,revalidate

class VoicePipelineTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.c=tuple(Candidate(i,t,"local",p,True,True,m,r) for i,t,p,m,r in (
            ("whisper-small","stt","whisper",2,65),
            ("whisper-large","stt","whisper",5,95),
            ("jan-small","llm","jan",3,60),
            ("jan-large","llm","jan",7,95),
            ("piper","tts","piper",1,90)))
    def ids(self,p):
        return tuple(c.identity for c in p.models)
    def test_joint_budget_not_independent_greedy(self):
        p=plan_voice(self.ctx,self.c,9,strategy="performance")
        self.assertEqual(self.ids(p),("whisper-large","jan-small","piper"))
        self.assertEqual(p.peak_mib,9)
    def test_memory_strategy(self):
        p=plan_voice(self.ctx,self.c,20,strategy="memory",reserve_mib=2)
        self.assertEqual(self.ids(p),("whisper-small","jan-small","piper"))
        self.assertEqual(p.peak_mib,8)
    def test_sequential_only_when_explicit(self):
        with self.assertRaises(Refused):
            plan_voice(self.ctx,self.c,7,pinned=(("llm","jan-large"),))
        p=plan_voice(self.ctx,self.c,7,residency="sequential",pinned=(("llm","jan-large"),))
        self.assertEqual(p.peak_mib,7)
        self.assertEqual(self.ids(p)[1],"jan-large")
    def test_pinned_model_never_silently_replaced(self):
        for pin in ((("llm","unknown"),),(("llm","piper"),),(("llm","jan-large"),("llm","jan-small"))):
            with self.assertRaises(Refused):
                plan_voice(self.ctx,self.c,20,pinned=pin)
    def test_cloud_and_unhealthy_stage_refused(self):
        cloud=replace(self.c[0],locality="cloud",identity="cloud-stt")
        candidates=(cloud,replace(self.c[1],healthy=False),*self.c[2:])
        with self.assertRaises(Refused):
            plan_voice(self.ctx,candidates,20)
        with self.assertRaises(Refused):
            plan_voice(replace(self.ctx,mode="CLOUD_CHAT"),self.c,20)
    def test_order_invariance_and_context_binding(self):
        p=plan_voice(self.ctx,self.c,20)
        self.assertEqual(p,plan_voice(self.ctx,tuple(reversed(self.c)),20))
        with self.assertRaises(Refused):
            revalidate(p,replace(self.ctx,generation=2),self.c)
        self.assertEqual(revalidate(p,self.ctx,self.c),p)
    def test_changed_health_and_metadata_require_replan(self):
        p=plan_voice(self.ctx,self.c,20)
        for change in (replace(self.c[1],healthy=False),replace(self.c[1],memory_mib=6)):
            with self.assertRaises(Refused):
                revalidate(p,self.ctx,(self.c[0],change,*self.c[2:]))
    def test_strict_numeric_and_duplicate_validation(self):
        for args in ({"budget_mib":True},{"budget_mib":9,"reserve_mib":True},
                     {"budget_mib":9,"residency":"automatic"}):
            with self.assertRaises(Refused):
                plan_voice(self.ctx,self.c,**args)
        with self.assertRaises(Refused):
            plan_voice(self.ctx,(*self.c,self.c[0]),20)

    def test_pins_are_bound_to_plan_and_preserved_on_revalidation(self):
        p=plan_voice(self.ctx,self.c,20,pinned=(("llm","jan-small"),))
        self.assertEqual(revalidate(p,self.ctx,self.c),p)
        self.assertNotEqual(p.digest,plan_voice(self.ctx,self.c,20).digest)
        with self.assertRaises(Refused):
            revalidate(p,self.ctx,tuple(c for c in self.c if c.identity!="jan-small"))

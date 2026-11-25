import unittest
from dataclasses import replace
from inventory import Entry, Inventory
from gateway import Refused
class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.i=Inventory()
        self.e=Entry("m","a"*64,"llm","jan",True,False)
        self.i.register(self.e)
    def test_shared_reference_and_lease_prevent_removal(self):
        self.i.register(replace(self.e,identity="alias"))
        self.i.select("profile","llm","alias")
        with self.assertRaises(Refused):
            self.i.removal_plan("m")
        self.i.unselect("profile","llm")
        h=self.i.lease("alias")
        with self.assertRaises(Refused):
            self.i.removal_plan("m")
        self.i.release(h)
        self.assertEqual(self.i.removal_plan("m"),"REFERENCE_ONLY")
    def test_compatibility_and_conflicting_ownership(self):
        self.i.register(Entry("bad","b"*64,"tts","piper",False,True))
        with self.assertRaises(Refused):
            self.i.select("profile","tts","bad")
        with self.assertRaises(Refused):
            self.i.select("profile","stt","m")
        with self.assertRaises(Refused):
            self.i.register(replace(self.e,identity="owned",owned=True))
    def test_owned_removal_is_only_plan(self):
        self.i.register(Entry("own","c"*64,"llm","native",True,True))
        self.assertEqual(self.i.removal_plan("own"),"REVIEW_OWNED_ARTIFACT")
        self.i.select("profile","llm","own")
        with self.assertRaises(Refused):
            self.i.removal_plan("own")

    def test_lease_candidate_binds_task_runtime_and_health(self):
        from inference import Candidate
        c=Candidate("m","llm","local","jan",True,True,4,80)
        h=self.i.lease_candidate(c)
        with self.assertRaises(Refused):
            self.i.removal_plan("m")
        self.i.release(h)
        for invalid in (replace(c,task="stt"),replace(c,provider="native"),
                        replace(c,locality="cloud"),replace(c,healthy=False)):
            with self.assertRaises(Refused):
                self.i.lease_candidate(invalid)
        self.assertEqual(self.i.removal_plan("m"),"REFERENCE_ONLY")

    def pipeline(self, include_tts=True):
        from inference import Candidate
        from gateway import Context
        from voice_pipeline import plan_voice
        ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        candidates=(Candidate("s","stt","local","whisper",True,True,2,80),
                    Candidate("m","llm","local","jan",True,True,4,80),
                    Candidate("t","tts","local","piper",True,True,1,80))
        self.i.register(Entry("s","b"*64,"stt","whisper",True,True))
        if include_tts:
            self.i.register(Entry("t","c"*64,"tts","piper",True,True))
        return ctx,candidates,plan_voice(ctx,candidates,10)

    def test_pipeline_group_pins_all_models_and_releases_once(self):
        ctx,c,p=self.pipeline()
        g=self.i.lease_pipeline(p,ctx,c)
        for identity in ("s","m","t"):
            with self.assertRaisesRegex(Refused,"model_in_use"):
                self.i.removal_plan(identity)
        with self.assertRaises(Refused):
            self.i.release_pipeline(object())
        self.i.release_pipeline(g)
        with self.assertRaises(Refused):
            self.i.release_pipeline(g)
        self.assertEqual(self.i.removal_plan("m"),"REFERENCE_ONLY")
        self.assertEqual(self.i.removal_plan("t"),"REVIEW_OWNED_ARTIFACT")

    def test_pipeline_missing_stage_rolls_back_previous_reservations(self):
        ctx,c,p=self.pipeline(False)
        with self.assertRaises(Refused):
            self.i.lease_pipeline(p,ctx,c)
        self.assertEqual(self.i.removal_plan("s"),"REVIEW_OWNED_ARTIFACT")
        self.assertEqual(self.i.removal_plan("m"),"REFERENCE_ONLY")

    def test_pipeline_revoked_plan_and_concurrent_capacity(self):
        from concurrent.futures import ThreadPoolExecutor
        ctx,c,p=self.pipeline()
        with self.assertRaises(Refused):
            self.i.lease_pipeline(p,replace(ctx,generation=2),c)
        def start(_):
            try:
                return self.i.lease_pipeline(p,ctx,c)
            except Refused:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            groups=[g for g in pool.map(start,range(24)) if g is not None]
        self.assertEqual(len(groups),21)
        for g in groups:
            self.i.release_pipeline(g)
        self.assertEqual(self.i.removal_plan("m"),"REFERENCE_ONLY")

    def metered(self,capacity):
        self.i=Inventory(capacity)
        self.i.register(self.e)
        return self.pipeline()

    def test_metered_group_and_individual_share_budget(self):
        ctx,c,p=self.metered(10)
        g=self.i.lease_pipeline(p,ctx,c)
        self.assertEqual(self.i.capacity_snapshot(),(10,7,3))
        with self.assertRaisesRegex(Refused,"aggregate_capacity_exceeded"):
            self.i.lease_candidate(c[1])
        with self.assertRaisesRegex(Refused,"candidate_required"):
            self.i.lease("m")
        h=self.i.lease_candidate(c[0])
        self.assertEqual(self.i.capacity_snapshot(),(10,9,4))
        self.i.release_pipeline(g)
        self.assertEqual(self.i.capacity_snapshot(),(10,2,1))
        self.i.release(h)
        self.assertEqual(self.i.capacity_snapshot(),(10,0,0))

    def test_metered_sequential_plan_charged_conservatively(self):
        from voice_pipeline import plan_voice
        ctx,c,_=self.metered(6)
        p=plan_voice(ctx,c,6,residency="sequential",reserve_mib=1)
        self.assertEqual(p.peak_mib,5)
        with self.assertRaisesRegex(Refused,"aggregate_capacity_exceeded"):
            self.i.lease_pipeline(p,ctx,c)
        self.assertEqual(self.i.capacity_snapshot(),(6,0,0))

    def test_metered_concurrent_group_admission_never_overbooks(self):
        from concurrent.futures import ThreadPoolExecutor
        ctx,c,p=self.metered(14)
        def start(_):
            try:
                return self.i.lease_pipeline(p,ctx,c)
            except Refused:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            groups=[g for g in pool.map(start,range(12)) if g is not None]
        self.assertEqual(len(groups),2)
        self.assertEqual(self.i.capacity_snapshot(),(14,14,6))
        for g in groups:
            self.i.release_pipeline(g)
        self.assertEqual(self.i.capacity_snapshot(),(14,0,0))

    def test_metered_failed_group_rolls_back_without_charge(self):
        self.i=Inventory(10)
        self.i.register(self.e)
        ctx,c,p=self.pipeline(False)
        with self.assertRaises(Refused):
            self.i.lease_pipeline(p,ctx,c)
        self.assertEqual(self.i.capacity_snapshot(),(10,0,0))

    def test_metered_capacity_validation(self):
        for bad in (True,0,-1,1.5,1048577):
            with self.assertRaises(Refused):
                Inventory(bad)

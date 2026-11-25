import unittest
from hashlib import sha256
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from market_fixture import Manifest
from version_book import VersionBook

class VersionBookTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("publish_artifact",(),"allow"),),clock=profiles.PolicyClock())
        self.v=VersionBook(self.g,self.ctx)
        self.a=Manifest("model","v1","w.bin",1,sha256(b"a").hexdigest(),"fixture")
        self.b=Manifest("model","v2","w.bin",1,sha256(b"b").hexdigest(),"fixture")
        self.v.record(self.a,b"a")
        self.v.record(self.b,b"b")
    def grant(self,c,i):
        return self.g.fixture_grant(i,Request(self.ctx,"publish",c.target(),"local"),60)
    def test_explicit_upgrade_and_rollback(self):
        for i,m in enumerate((self.a,self.b,self.a)):
            c=self.v.plan("model",m.identity)
            self.v.apply(c,self.grant(c,str(i)))
            self.assertEqual(self.v.current("model"),m.identity)
    def test_stale_generation_and_wrong_grant(self):
        a=self.v.plan("model",self.a.identity)
        b=self.v.plan("model",self.b.identity)
        ga=self.grant(a,"a")
        gb=self.grant(b,"b")
        self.v.apply(a,ga)
        with self.assertRaises(Refused):
            self.v.apply(b,gb)
        with self.assertRaises(Refused):
            self.v.apply(self.v.plan("model",self.b.identity),ga)
        self.assertEqual(self.v.current("model"),self.a.identity)
    def test_integrity_and_cross_model(self):
        with self.assertRaises(Refused):
            self.v.record(replace(self.a,revision="v3"),b"x")
        with self.assertRaises(Refused):
            self.v.plan("other",self.a.identity)
    def test_revoked_change_leaves_pointer(self):
        a=self.v.plan("model",self.a.identity)
        g=self.grant(a,"a")
        self.g.revoke("a")
        with self.assertRaises(Refused):
            self.v.apply(a,g)
        self.assertIsNone(self.v.current("model"))

    def test_revision_name_cannot_be_rebound_to_different_digest(self):
        altered=replace(self.a,sha256=sha256(b"x").hexdigest())
        with self.assertRaisesRegex(Refused,"revision_identity_conflict"):
            self.v.record(altered,b"x")
        self.assertEqual(self.v.record(self.a,b"a"),self.a.identity)

    def test_concurrent_stale_generation_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        changes=(self.v.plan("model",self.a.identity),self.v.plan("model",self.b.identity))
        grants=tuple(self.grant(c,str(i)) for i,c in enumerate(changes))
        barrier=Barrier(2)
        def apply(i):
            barrier.wait(timeout=5)
            try:
                self.v.apply(changes[i],grants[i])
                return changes[i].next
            except Refused:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            winners=[x for x in pool.map(apply,range(2)) if x is not None]
        self.assertEqual(len(winners),1)
        self.assertEqual(self.v.current("model"),winners[0])

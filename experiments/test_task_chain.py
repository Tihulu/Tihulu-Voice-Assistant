import unittest
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from task_chain import Chain, Step

class ChainTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("r4","session","profile",1,"route","SECURE_HYBRID")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),),clock=profiles.PolicyClock())
        self.steps=(Step("a","compute","csv","table"),Step("b","render","table","chart"))
        self.c=Chain(self.g,self.ctx,self.steps,"csv",b"csv")
        self.initial=self.c.current
        self.a=self.grant("a",self.initial)

    def grant(self,identity,artifact):
        return self.g.fixture_grant(identity,Request(self.ctx,"engine",self.c.target(artifact),"local"),60)

    def test_chain_lineage_and_separate_grants(self):
        h=self.c.begin(self.a,self.initial)
        self.assertEqual(self.c.input_bytes(h),b"csv")
        a=self.c.complete(h,"table",b"table")
        self.assertEqual(a.parent,self.initial.digest)
        with self.assertRaises(Refused):
            self.c.begin(self.a,a)
        b=self.c.complete(self.c.begin(self.grant("b",a),a),"chart",b"chart")
        self.assertEqual(b.parent,a.digest)
        self.assertEqual(self.c.state,"completed")

    def test_invalid_chain_and_artifact(self):
        with self.assertRaises(Refused):
            Chain(self.g,self.ctx,(self.steps[1],self.steps[0]),"table",b"table")
        h=self.c.begin(self.a,self.initial)
        with self.assertRaises(Refused):
            self.c.complete(h,"chart",b"wrong")
        self.assertEqual(self.c.state,"held")

    def test_revoke_and_stale_handle(self):
        h=self.c.begin(self.a,self.initial)
        self.g.revoke("a")
        with self.assertRaises(Refused):
            self.c.complete(h,"table",b"late")
        self.assertEqual(self.c.state,"held")
        with self.assertRaises(Refused):
            self.c.complete(h,"table",b"late")

    def test_cancel_prevents_late_publication(self):
        h=self.c.begin(self.a,self.initial)
        self.c.cancel()
        with self.assertRaises(Refused):
            self.c.complete(h,"table",b"late")
        self.assertEqual(self.c.state,"cancelled")

    def test_forged_and_stale_artifact_rejected(self):
        with self.assertRaises(Refused):
            self.c.begin(self.a,replace(self.initial))
        a=self.c.complete(self.c.begin(self.a,self.initial),"table",b"table")
        with self.assertRaises(Refused):
            self.c.begin(self.grant("b",a),self.initial)
        with self.assertRaises(Refused):
            self.c.target(replace(a))

    def test_grant_cannot_apply_to_other_source_digest(self):
        c=Chain(self.g,self.ctx,self.steps,"csv",b"different")
        with self.assertRaises(Refused):
            c.begin(self.a,c.current)
        self.assertEqual(c.state,"ready")

    def test_same_permission_cannot_start_two_chains(self):
        c=Chain(self.g,self.ctx,self.steps,"csv",b"csv")
        self.c.begin(self.a,self.initial)
        with self.assertRaisesRegex(Refused,"grant_already_claimed"):
            c.begin(self.a,c.current)

    def test_predecessor_revocation_invalidates_running_successor(self):
        a=self.c.complete(self.c.begin(self.a,self.initial),"table",b"table")
        b=self.grant("b",a)
        h=self.c.begin(b,a)
        self.g.revoke("a")
        with self.assertRaises(Refused):
            self.c.complete(h,"chart",b"chart")
        self.assertEqual(self.c.state,"held")
        with self.assertRaises(Refused):
            self.g.checkpoint(b,b.request)

    def test_predecessor_revocation_invalidates_completed_metadata(self):
        a=self.c.complete(self.c.begin(self.a,self.initial),"table",b"table")
        self.c.complete(self.c.begin(self.grant("b",a),a),"chart",b"chart")
        self.g.revoke("a")
        with self.assertRaises(Refused):
            _=self.c.current
        self.assertEqual(self.c.state,"held")

    def test_cancel_revokes_active_grant(self):
        self.c.begin(self.a,self.initial)
        self.c.cancel()
        with self.assertRaises(Refused):
            self.g.checkpoint(self.a,self.a.request)

    def test_concurrent_start_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier=Barrier(2)
        def begin(_):
            barrier.wait(timeout=5)
            try:
                self.c.begin(self.a,self.initial)
                return True
            except Refused:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(begin,range(2))),[False,True])

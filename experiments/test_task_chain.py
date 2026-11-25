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

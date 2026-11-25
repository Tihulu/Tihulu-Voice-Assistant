import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Request,Refused
from release_rehearsal import Package,Rehearsal,rehearsal_target,SLOTS

RETAINED=(("user-profile",b"preferences"),("shared-model",b"model"),("stable-launcher",b"stable"))
def pkg(version,marker):
    return Package("a"*40,"b"*40,version,tuple((s,marker+s.encode()) for s in SLOTS))

class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("publish_artifact",(),"allow"),),clock=profiles.PolicyClock())
        self.n=0
        self.root=self.grant(rehearsal_target("release",RETAINED))
        self.r=Rehearsal(self.g,self.ctx,"release",RETAINED,self.root)
        self.addCleanup(self.r.close)
        self.a=pkg("v1",b"a")
        self.b=pkg("v2",b"b")
        for p in (self.a,self.b):
            self.r.register(p,p.identity)
    def grant(self,target):
        self.n+=1
        return self.g.fixture_grant("g"+str(self.n),Request(self.ctx,"publish",target,"local"),60)
    def change(self,op,p=None,ok=True):
        c=self.r.plan(op,p.identity if p else "")
        return self.r.apply(c,self.grant(c.target),confirmed=True,fixture_runtime_ok=ok)
    def test_clean_install_upgrade_rollback_uninstall_preserve_user_shared_stable(self):
        self.assertEqual(self.r.snapshot(),(0,"","",(),RETAINED))
        self.assertEqual(self.change("install",self.a)[3],self.a.files)
        self.assertEqual(self.change("upgrade",self.b)[3],self.b.files)
        self.assertEqual(self.change("rollback",self.a)[3],self.a.files)
        self.assertEqual(self.change("uninstall"),(4,"","",(),RETAINED))
    def test_failed_upgrade_keeps_exact_previous_state(self):
        before=self.change("install",self.a)
        with self.assertRaises(Refused):
            self.change("upgrade",self.b,ok=False)
        self.assertEqual(self.r.snapshot(),before)
    def test_tampered_package_and_reused_version_refused(self):
        with self.assertRaises(Refused):
            self.r.register(self.b,self.a.identity)
        p=pkg("v1",b"changed")
        with self.assertRaises(Refused):
            self.r.register(p,p.identity)
        with self.assertRaises(Refused):
            replace(self.a,files=(("user-profile",b"x"),)*5).identity
    def test_wrong_confirmation_grant_and_stale_generation(self):
        c=self.r.plan("install",self.a.identity)
        other=self.r.plan("install",self.b.identity)
        g=self.grant(c.target)
        with self.assertRaises(Refused):
            self.r.apply(c,g,fixture_runtime_ok=True)
        with self.assertRaises(Refused):
            self.r.apply(other,g,confirmed=True,fixture_runtime_ok=True)
        self.r.apply(c,g,confirmed=True,fixture_runtime_ok=True)
        with self.assertRaises(Refused):
            self.r.apply(c,g,confirmed=True,fixture_runtime_ok=True)
    def test_cross_instance_scope_and_root_replay(self):
        root=self.grant(rehearsal_target("release",RETAINED))
        other=Rehearsal(self.g,self.ctx,"release",RETAINED,root)
        self.addCleanup(other.close)
        other.register(self.a,self.a.identity)
        c=self.r.plan("install",self.a.identity)
        with self.assertRaises(Refused):
            other.apply(other.plan("install",self.a.identity),self.grant(c.target),confirmed=True,fixture_runtime_ok=True)
        with self.assertRaises(Refused):
            Rehearsal(self.g,self.ctx,"release",RETAINED,self.root)
    def test_revocation_at_claim_prevents_commit(self):
        c=self.r.plan("install",self.a.identity)
        g=self.grant(c.target)
        original=self.g.claim
        def revoke(grant,req):
            original(grant,req)
            self.g.revoke(grant.identity)
        with patch.object(self.g,"claim",side_effect=revoke):
            with self.assertRaises(Refused):
                self.r.apply(c,g,confirmed=True,fixture_runtime_ok=True)
        self.assertEqual(self.r.snapshot(),(0,"","",(),RETAINED))
    def test_concurrent_install_one_winner(self):
        plans=[self.r.plan("install",p.identity) for p in (self.a,self.b)]
        grants=[self.grant(c.target) for c in plans]
        barrier=Barrier(2)
        def run(i):
            barrier.wait(timeout=5)
            try:
                self.r.apply(plans[i],grants[i],confirmed=True,fixture_runtime_ok=True)
                return True
            except Refused:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(run,range(2))),1)
        self.assertEqual(self.r.snapshot()[0],1)
    def test_unverified_rollback_and_close(self):
        self.change("install",self.a)
        with self.assertRaises(Refused):
            self.r.plan("rollback",self.b.identity)
        self.r.close()
        with self.assertRaises(Refused):
            self.r.snapshot()

    def test_root_revocation_discards_owned_rehearsal_state(self):
        self.change("install",self.a)
        self.g.revoke(self.root.identity)
        with self.assertRaises(Refused):
            self.r.snapshot()
        self.assertTrue(self.r._closed)
        self.assertEqual(self.r._packages,{})
        self.assertEqual(self.r._retained,())

    def test_commit_does_not_call_fallible_postcommit_snapshot(self):
        c=self.r.plan("install",self.a.identity)
        g=self.grant(c.target)
        with patch.object(self.r,"snapshot",side_effect=Refused("late_read")):
            result=self.r.apply(c,g,confirmed=True,fixture_runtime_ok=True)
        self.assertEqual(result,self.r.snapshot())

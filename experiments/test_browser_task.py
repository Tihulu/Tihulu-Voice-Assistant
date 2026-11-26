# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway, Refused
from browser_task import BrowserPlan, BrowserTask


class BrowserTaskTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("task","session","profile",1,"route","SECURE_HYBRID",False)
        self.plan = BrowserPlan("firefox","fixture","owned1","tab1",
                                ("https://example.org/a","https://example.org/b"))
        self.g = FixtureGateway(self.ctx, tuple(profiles.Rule(a,(),"allow") for a in
                               ("host_shell","browser_navigate","external_network")),
                               clock=profiles.PolicyClock())
        self.task = BrowserTask(self.g,self.ctx,self.plan)
        self.data = b"Public synthetic page"
        self.digest = sha256(self.data).hexdigest()

    def grant(self,name,kind,page=0,plan=None,digest=None):
        plan = self.plan if plan is None else plan
        call = plan.call(kind,plan.pages[page],digest)
        return self.g.fixture_grant(name,call.request(self.ctx),60)

    def test_order_and_exact_snapshot_content(self):
        with self.assertRaises(Refused):
            self.task.navigate(self.plan.pages[1],self.grant("wrong","navigate",1))
        nav = self.grant("nav","navigate")
        self.task.navigate(self.plan.pages[0],nav)
        snap = self.grant("snap","snapshot",digest=self.digest)
        with self.assertRaisesRegex(Refused,"request_mismatch"):
            self.task.snapshot(self.plan.pages[0],b"changed",snap)
        self.task.snapshot(self.plan.pages[0],self.data,snap)
        self.assertEqual(self.task.result()[1],(self.plan.pages[0],self.digest,self.data.decode()))
        self.task.navigate(self.plan.pages[1],self.grant("nav2","navigate",1))
        with self.assertRaises(Refused):
            self.task.result()

    def test_no_snapshot_before_nav_or_cross_browser_profile(self):
        snap = self.grant("snap","snapshot",digest=self.digest)
        with self.assertRaises(Refused):
            self.task.snapshot(self.plan.pages[0],self.data,snap)
        for i,plan in enumerate((replace(self.plan,browser="brave"),replace(self.plan,profile="other"))):
            with self.assertRaisesRegex(Refused,"request_mismatch"):
                self.task.navigate(self.plan.pages[0],self.grant("bad"+str(i),"navigate",plan=plan))
        self.task.navigate(self.plan.pages[0],self.grant("nav","navigate"))
        self.task.snapshot(self.plan.pages[0],self.data,snap)

    def test_automatic_traffic_always_denied_and_close_revokes(self):
        nav = self.grant("nav","navigate")
        self.task.navigate(self.plan.pages[0],nav)
        for kind in ("redirect","subresource","websocket","download","submit","upload"):
            with self.assertRaisesRegex(Refused,"automatic_browser_traffic_denied"):
                self.task.automatic_request(kind,self.plan.pages[0])
        snap = self.grant("snap","snapshot",digest=self.digest)
        self.task.snapshot(self.plan.pages[0],self.data,snap)
        self.task.close()
        with self.assertRaises(Refused):
            self.task.result()
        for grant in (nav,snap):
            with self.assertRaises(Refused):
                self.g.checkpoint(grant,grant.request)

    def test_revocation_before_publication_and_offline(self):
        self.task.navigate(self.plan.pages[0],self.grant("nav","navigate"))
        self.task.snapshot(self.plan.pages[0],self.data,self.grant("snap","snapshot",digest=self.digest))
        self.g.revoke("nav")
        with self.assertRaises(Refused):
            self.task.result()
        ctx = replace(self.ctx,offline=True)
        g = FixtureGateway(ctx,tuple(profiles.Rule(a,(),"allow") for a in
                           ("host_shell","browser_navigate","external_network")),clock=profiles.PolicyClock())
        with self.assertRaisesRegex(Refused,"offline"):
            g.fixture_grant("nav",self.plan.call("navigate",self.plan.pages[0]).request(ctx),60)

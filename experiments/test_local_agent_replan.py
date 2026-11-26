# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from local_agent_resources import ResourceLedger
from local_agent_replan import AttemptCoordinator,ReplanPreview
import test_local_agent_wave_run as fixtures

class AgentReplanTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.WaveRunTests()
        f.setUp()
        self.ctx,self.models,self.g=f.ctx,f.models,f.g
        self.ledger=ResourceLedger(4096)
        self.c=AttemptCoordinator(self.g,self.ledger,"coordinator",f.workers,4096,2)

    def prepare(self):
        return self.c.prepare(self.ctx,self.models)

    def confirm(self,p):
        return self.c.confirm(p,self.ctx,self.models)

    def test_explicit_stop_and_fresh_grants_for_replan(self):
        p=self.prepare()
        a=self.confirm(p)
        ga=self.g.fixture_grant("a-plan",a.request("plan"),60)
        a.start("plan",ga,self.ctx,self.models)
        with self.assertRaises(Refused):
            self.prepare()
        self.c.stop()
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
        b=self.confirm(self.prepare())
        self.assertNotEqual(a.request("plan"),b.request("plan"))
        with self.assertRaises(Refused):
            b.start("plan",ga,self.ctx,self.models)
        gb=self.g.fixture_grant("b-plan",b.request("plan"),60)
        b.start("plan",gb,self.ctx,self.models)
        with self.assertRaises(Refused):
            b.complete("plan",self.ctx,self.models,payload=b"old",completion_grant=ga)
        b.complete("plan",self.ctx,self.models,payload=b"new",completion_grant=gb)

    def test_replaced_or_forged_preview_cannot_confirm(self):
        first=self.prepare()
        second=self.prepare()
        for p in (first,ReplanPreview(second.attempt,second.plan)):
            with self.assertRaises(Refused):
                self.confirm(p)
        self.confirm(second)
        with self.assertRaises(Refused):
            self.confirm(second)

    def test_changed_inventory_consumes_preview_without_start(self):
        p=self.prepare()
        changed=(replace(self.models[0],rank=81),)
        with self.assertRaises(Refused):
            self.c.confirm(p,self.ctx,changed)
        with self.assertRaises(Refused):
            self.confirm(p)
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
        self.confirm(self.prepare())

    def test_attempt_cap_is_not_reset_by_stop(self):
        for i in range(3):
            self.confirm(self.prepare())
            self.c.stop()
        with self.assertRaises(Refused):
            self.prepare()

    def test_context_change_closes_and_revokes_active(self):
        a=self.confirm(self.prepare())
        grant=self.g.fixture_grant("a-plan",a.request("plan"),60)
        a.start("plan",grant,self.ctx,self.models)
        with self.assertRaises(Refused):
            self.c.prepare(replace(self.ctx,generation=2),self.models)
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
        with self.assertRaises(Refused):
            self.prepare()
        with self.assertRaises(Refused):
            self.g.checkpoint(grant,a.request("plan"))

    def test_no_eligible_route_is_hold_not_cloud_fallback(self):
        unavailable=(replace(self.models[0],healthy=False),)
        with self.assertRaises(Refused):
            self.c.prepare(self.ctx,unavailable)
        self.confirm(self.prepare())

    def test_stop_invalidates_unconfirmed_preview(self):
        p=self.prepare()
        self.c.stop()
        with self.assertRaises(Refused):
            self.confirm(p)
        self.confirm(self.prepare())

# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
import test_local_agent_owned_drain as base

class ManagedInputsTests(unittest.TestCase):
    def setUp(self):
        f=base.ManagedWaveTests()
        f.setUp()
        self.f=f
        f.exit(f.start("plan"),payload=b"plan")
        self.a=f.start("a")
    def test_exact_running_root_receives_only_direct_dependency(self):
        receipts=self.f.run.inputs(self.a,self.f.ctx,self.f.models)
        self.assertEqual(tuple(data for receipt,data in receipts),(b"plan",))
        self.assertEqual(receipts[0][0].worker,"plan")
    def test_forged_handle_does_not_access_or_invalidate(self):
        with self.assertRaises(Refused):
            self.f.run.inputs(replace(self.a),replace(self.f.ctx,generation=2),self.f.models)
        self.f.run.inputs(self.a,self.f.ctx,self.f.models)
    def test_exited_root_cannot_read_even_while_member_remains(self):
        m=self.f.run.register_member(self.a,"descendant",self.f.ctx,self.f.models)
        self.assertEqual(self.f.exit(self.a),"PENDING")
        with self.assertRaises(Refused):
            self.f.run.inputs(self.a,self.f.ctx,self.f.models)
        self.assertEqual(self.f.run.observe_member_exit(m,self.f.ctx,self.f.models),"COMPLETED")
    def test_stop_blocks_inputs_while_reservation_remains(self):
        self.f.run.stop()
        with self.assertRaises(Refused):
            self.f.run.inputs(self.a,self.f.ctx,self.f.models)
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.f.exit(self.a)

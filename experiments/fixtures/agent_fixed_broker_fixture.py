# SPDX-License-Identifier: AGPL-3.0-only
"""Fixed public CI process evidence, no model or user environment."""
import unittest
from dataclasses import replace
from gateway import Refused
from local_agent_launch import LaunchBoundary
from agent_fixed_launch_broker import FixedChildBroker
import test_local_agent_owned_drain as base

class FixedBrokerTests(unittest.TestCase):
    def setUp(self):
        self.f=base.ManagedWaveTests()
        self.f.setUp()
        self.boundary=LaunchBoundary(self.f.run)
        self.b=FixedChildBroker(self.boundary)
        self.addCleanup(self.cleanup)
    def cleanup(self):
        self.b.stop()
        for t in self.b.pending():
            self.b.reap(t,self.f.ctx,self.f.models,force=True)
    def launch(self,w="plan"):
        return self.b.launch(w,self.f.grants[w],self.f.ctx,self.f.models)
    def reap(self,t,**kw):
        return self.b.reap(t,self.f.ctx,self.f.models,**kw)
    def test_timeout_then_normal_exit(self):
        t=self.launch()
        self.assertEqual(self.reap(t),"PENDING")
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.assertEqual(self.reap(t,finish=True),"COMPLETED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))
    def test_stop_holds_until_reap_and_refuses_new_launch(self):
        t=self.launch()
        self.b.stop()
        self.assertEqual(self.reap(t),"PENDING")
        with self.assertRaises(Refused):
            self.launch("a")
        self.assertEqual(self.reap(t,finish=True),"DRAINED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))
    def test_foreign_ticket_cannot_kill_owned_child(self):
        t=self.launch()
        with self.assertRaises(Refused):
            self.reap(replace(t),force=True)
        self.assertEqual(self.reap(t),"PENDING")
        self.assertEqual(self.reap(t,force=True),"DRAINED")
    def test_two_workers_drain_independently(self):
        self.reap(self.launch(),finish=True)
        a,b=self.launch("a"),self.launch("b")
        self.b.stop()
        self.assertEqual(self.reap(a,finish=True),"DRAINED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.assertEqual(self.reap(b,finish=True),"DRAINED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))

    def test_real_child_bytes_bound_to_dependency_receipt(self):
        self.reap(self.launch(),finish=True)
        a=self.launch("a")
        rows=self.b.inputs(a,self.f.ctx,self.f.models)
        self.assertEqual(len(rows),1)
        receipt,payload=rows[0]
        self.assertEqual(payload,b"fixed-child-exited")
        from hashlib import sha256
        self.assertEqual(receipt.payload_digest,sha256(payload).hexdigest())
        self.assertEqual(receipt.size,len(payload))
        self.reap(a,finish=True)

    def test_injected_launch_exception_quarantines_reservation(self):
        from unittest.mock import patch
        # Synthetic exception injection; does not establish OS absence.
        with patch("agent_fixed_launch_broker.subprocess.Popen",side_effect=OSError("fixture launch failure")):
            with self.assertRaises(OSError):
                self.launch()
        self.assertEqual(self.b.pending(),())
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.assertFalse(self.f.run.drained())
        with self.assertRaises(Refused):
            self.launch()
        self.b.stop()
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))

    def test_post_spawn_permission_loss_keeps_exact_child_for_drain(self):
        from unittest.mock import patch
        original=self.boundary.started
        def revoked(ticket,context,candidates):
            self.f.g.revoke("owned-plan")
            return original(ticket,context,candidates)
        with patch.object(self.boundary,"started",side_effect=revoked):
            with self.assertRaises(Refused):
                self.launch()
        self.assertEqual(len(self.b.pending()),1)
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        t,=self.b.pending()
        self.assertEqual(self.reap(t,finish=True),"DRAINED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))

if __name__=="__main__":
    unittest.main(verbosity=2)

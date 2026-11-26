# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from local_agent_resources import ResourceLedger
from local_agent_owned_drain import ManagedWave,OwnedWorker
import test_local_agent_wave_run as fixtures

class ManagedWaveTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.WaveRunTests()
        f.setUp()
        self.ctx,self.models,self.g=f.ctx,f.models,f.g
        self.ledger=ResourceLedger(4096)
        self.run=ManagedWave(self.g,f.plan,f.workers,self.models,4096,self.ledger,"owned",2)
        self.grants={w.identity:self.g.fixture_grant("owned-"+w.identity,self.run.request(w.identity),60)
                     for w in f.workers}

    def start(self,w):
        return self.run.start(w,self.grants[w],self.ctx,self.models)

    def exit(self,h,code=0,payload=b"ok"):
        return self.run.observe_exit(h,code,self.ctx,self.models,payload=payload)

    def test_cancel_retains_reservation_until_exact_exit(self):
        h=self.start("plan")
        self.assertEqual(self.run.stop(),(h,))
        self.assertEqual(self.ledger.snapshot(),(4096,2048,2048))
        with self.assertRaises(Refused):
            self.g.checkpoint(self.grants["plan"],self.run.request("plan"))
        with self.assertRaises(Refused):
            self.exit(replace(h))
        self.assertEqual(self.exit(h),"DRAINED")
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))
        with self.assertRaises(Refused):
            self.exit(h)

    def test_failure_keeps_sibling_reservation_until_exit(self):
        self.exit(self.start("plan"))
        a,b=self.start("a"),self.start("b")
        self.assertEqual(self.exit(a,1),"FAILED")
        self.assertEqual(self.run.pending(),(b,))
        self.assertEqual(self.ledger.snapshot(),(4096,2048,2048))
        self.assertEqual(self.exit(b),"DRAINED")
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))

    def test_normal_exact_results_and_releases(self):
        self.exit(self.start("plan"))
        self.exit(self.start("a"))
        self.exit(self.start("b"))
        self.exit(self.start("review"),payload=b"reviewed")
        self.assertEqual(self.run.output("review",self.ctx,self.models)[1],b"reviewed")
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))

    def test_invalid_exit_does_not_release_or_cancel(self):
        h=self.start("plan")
        for code in (True,256,"0"):
            with self.assertRaises(Refused):
                self.exit(h,code)
        self.assertEqual(self.run.pending(),(h,))
        self.assertEqual(self.ledger.snapshot(),(4096,2048,2048))
        self.exit(h)

    def test_stale_owned_exit_stops_and_releases_only_exited_worker(self):
        self.exit(self.start("plan"))
        a,b=self.start("a"),self.start("b")
        with self.assertRaises(Refused):
            self.run.observe_exit(a,0,replace(self.ctx,generation=2),self.models,payload=b"stale")
        self.assertEqual(self.run.pending(),(b,))
        self.assertEqual(self.ledger.snapshot(),(4096,2048,2048))
        self.exit(b)

    def test_wrong_grant_rolls_back_without_touching_other_reservation(self):
        other=self.ledger.reserve("other","worker",1024)
        with self.assertRaises(Refused):
            self.run.start("plan",self.grants["a"],self.ctx,self.models)
        self.assertEqual(self.ledger.snapshot(),(4096,1024,3072))
        self.ledger.release(other)

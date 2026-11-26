# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from threading import Barrier,Thread
from gateway import Refused
from local_agent_resources import ResourceLedger,Reservation

class ResourceLedgerTests(unittest.TestCase):
    def test_shared_budget_across_runs_and_exact_release(self):
        ledger=ResourceLedger(4096)
        a=ledger.reserve("run-a","worker",2048)
        b=ledger.reserve("run-b","worker",2048)
        self.assertEqual(ledger.snapshot(),(4096,4096,0))
        with self.assertRaises(Refused):
            ledger.reserve("run-c","worker",1)
        ledger.release(a)
        self.assertEqual(ledger.snapshot(),(4096,2048,2048))
        ledger.release(b)

    def test_duplicate_forged_and_old_reservation_refused(self):
        ledger=ResourceLedger(4096)
        a=ledger.reserve("run","worker",1024)
        with self.assertRaises(Refused):
            ledger.reserve("run","worker",1024)
        with self.assertRaises(Refused):
            ledger.release(replace(a))
        ledger.release(a)
        b=ledger.reserve("run","worker",1024)
        with self.assertRaises(Refused):
            ledger.release(a)
        self.assertEqual(ledger.snapshot(),(4096,1024,3072))
        ledger.release(b)

    def test_shrink_preserves_reservations_and_blocks_new_work(self):
        ledger=ResourceLedger(4096)
        a=ledger.reserve("run","a",3072)
        ledger.resize(2048)
        self.assertEqual(ledger.snapshot(),(2048,3072,0))
        with self.assertRaises(Refused):
            ledger.reserve("run","b",1)
        ledger.release(a)
        b=ledger.reserve("run","b",2048)
        ledger.release(b)

    def test_close_never_pretends_active_memory_is_released(self):
        ledger=ResourceLedger(4096)
        a=ledger.reserve("run","a",1024)
        ledger.close()
        ledger.close()
        self.assertEqual(ledger.snapshot(),(4096,1024,3072))
        for action in (lambda:ledger.reserve("run","b",1),lambda:ledger.resize(8192)):
            with self.assertRaises(Refused):
                action()
        ledger.release(a)
        self.assertEqual(ledger.snapshot(),(4096,0,4096))

    def test_concurrent_competition_never_overcommits(self):
        ledger=ResourceLedger(2048)
        barrier=Barrier(3)
        outcomes=[]
        def attempt(run):
            barrier.wait()
            try:
                outcomes.append(ledger.reserve(run,"worker",2048))
            except Refused:
                outcomes.append(None)
        threads=[Thread(target=attempt,args=("run-"+str(i),)) for i in range(2)]
        for t in threads:t.start()
        barrier.wait()
        for t in threads:t.join()
        winners=[r for r in outcomes if r is not None]
        self.assertEqual(len(winners),1)
        self.assertEqual(ledger.snapshot(),(2048,2048,0))
        ledger.release(winners[0])

    def test_invalid_sizes_and_capacity_rejected(self):
        for n in (True,-1,1048577,1.5):
            with self.assertRaises(Refused):
                ResourceLedger(n)
        ledger=ResourceLedger(2048)
        for n in (False,0,-1,2049,1.5):
            with self.assertRaises(Refused):
                ledger.reserve("run","worker",n)
        self.assertEqual(ledger.snapshot(),(2048,0,2048))

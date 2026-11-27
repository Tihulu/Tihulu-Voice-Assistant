# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from hashlib import sha256
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor

from gateway import Refused
from heterogeneous_team_token_budget import TeamTokenBudget,Attempt
from local_agent_retry_budget_session import RetryBudgetSession
import test_local_agent_retry_budget_session as retry


SCOPE="a"*64
WIRE=b"fixed fixture"


def spec(name,generated=16,prompt=112):
    return Attempt(name,name,sha256(WIRE).hexdigest(),generated,prompt)


class DualTokenBudgetTests(unittest.TestCase):
    def test_atomic_prompt_refusal_leaves_generation_and_identities_untouched(self):
        b=TeamTokenBudget(SCOPE,64,prompt_budget=150)
        with self.assertRaises(Refused):
            b.reserve_wave(SCOPE,(spec("a"),spec("b")))
        self.assertEqual(b.snapshot(),(64,0,0,64))
        self.assertEqual(b.prompt_snapshot(),(150,0,0,150))
        lease,=b.reserve_wave(SCOPE,(spec("a"),))
        b.dispatch(lease,WIRE)
        b.settle_reaped(lease,12,prompt=35)
        self.assertEqual(b.snapshot(),(64,12,0,52))
        self.assertEqual(b.prompt_snapshot(),(150,35,0,115))

    def test_atomic_generation_refusal_leaves_prompt_untouched(self):
        b=TeamTokenBudget(SCOPE,16,prompt_budget=300)
        with self.assertRaises(Refused):
            b.reserve_wave(SCOPE,(spec("a"),spec("b")))
        self.assertEqual(b.prompt_snapshot(),(300,0,0,300))

    def test_invalid_measured_prompt_keeps_both_reservations(self):
        b=TeamTokenBudget(SCOPE,16,prompt_budget=112)
        lease,=b.reserve_wave(SCOPE,(spec("a"),))
        b.dispatch(lease,WIRE)
        for value in (None,True,-1,113,3.0):
            with self.assertRaises(Refused):
                b.settle_reaped(lease,4,prompt=value)
            self.assertEqual(b.snapshot(),(16,0,16,0))
            self.assertEqual(b.prompt_snapshot(),(112,0,112,0))
        b.abandon_reaped(lease)
        self.assertEqual(b.snapshot(),(16,16,0,0))
        self.assertEqual(b.prompt_snapshot(),(112,112,0,0))

    def test_cancel_keeps_both_reservations_and_no_send_voids_both(self):
        b=TeamTokenBudget(SCOPE,32,prompt_budget=224)
        a,c=b.reserve_wave(SCOPE,(spec("a"),spec("c")))
        b.dispatch(a,WIRE)
        b.cancel(a)
        b.cancel(c)
        self.assertEqual(b.prompt_snapshot(),(224,0,224,0))
        b.void_not_dispatched(c)
        b.settle_reaped(a,8,prompt=25)
        self.assertEqual(b.snapshot(),(32,8,0,24))
        self.assertEqual(b.prompt_snapshot(),(224,25,0,199))

    def test_prompt_mode_cannot_be_silently_disabled(self):
        for limit in (True,0,655361,1.5):
            with self.assertRaises(Refused):
                TeamTokenBudget(SCOPE,16,prompt_budget=limit)
        dual=TeamTokenBudget(SCOPE,16,prompt_budget=112)
        plain=TeamTokenBudget(SCOPE,16)
        for b,a in ((dual,spec("a",prompt=0)),(plain,spec("a"))):
            with self.assertRaises(Refused):
                b.reserve_wave(SCOPE,(a,))
        self.assertIsNone(plain.prompt_snapshot())

    def test_competing_threads_cannot_oversubscribe_prompt_capacity(self):
        b=TeamTokenBudget(SCOPE,64,prompt_budget=112)
        barrier=Barrier(2,timeout=5)
        def reserve(name):
            barrier.wait()
            try:
                return b.reserve_wave(SCOPE,(spec(name),))[0]
            except Refused:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows=tuple(pool.map(reserve,("a","b")))
        winners=[r for r in rows if r is not None]
        self.assertEqual(len(winners),1)
        self.assertEqual(b.prompt_snapshot(),(112,0,112,0))
        b.void_not_dispatched(winners[0])
        self.assertEqual(b.snapshot(),(64,0,0,64))


class DualRetryBudgetTests(unittest.TestCase):
    def fixture(self,prompt_total):
        f=retry.RetryBudgetSessionTests()
        f.setUp()
        # A new coordinator identity on the same synthetic gateway.
        from local_agent_replan import AttemptCoordinator
        f.c=AttemptCoordinator(f.g,f.memory,"dual-retry",f.f.workers,4096,2,
                               owned_drain=True,strict_results=True)
        f.s=RetryBudgetSession(f.c,80,prompt_total=prompt_total)
        return f

    def test_whole_plan_prompt_refusal_before_new_attempt(self):
        f=self.fixture(447)
        with self.assertRaises(Refused):
            f.prepare()
        self.assertEqual(f.c._issued,0)
        self.assertEqual(f.s.prompt_snapshot(),(447,0,0,447))

    def test_actual_envelope_count_carries_across_retry(self):
        f=self.fixture(462)
        offer,run=f.begin()
        self.assertEqual(offer.prompt_remaining,462)
        row,=f.wave(offer,run,("plan",))
        self.assertEqual(f.s.prompt_snapshot(),(462,0,112,350))
        f.finish(run,row)
        f.s.stop()
        self.assertEqual(f.s.prompt_snapshot(),(462,14,0,448))
        second,next_run=f.begin()
        self.assertEqual(second.prompt_remaining,448)
        for names in (("plan",),("a","b"),("review",)):
            for r in f.wave(second,next_run,names):
                f.finish(next_run,r)
        self.assertEqual(f.s.snapshot(),(80,80,0,0))
        self.assertEqual(f.s.prompt_snapshot(),(462,70,0,392))

    def test_unknown_prompt_charge_blocks_retry_even_with_generation_capacity(self):
        f=self.fixture(500)
        offer,run=f.begin()
        lease,wire,handle=f.wave(offer,run,("plan",))[0]
        f.s.abandon_reaped(lease)
        run.observe_exit(handle,1,f.ctx,f.models)
        self.assertEqual(f.s.prompt_snapshot(),(500,112,0,388))
        self.assertEqual(f.s.snapshot(),(80,16,0,64))
        with self.assertRaises(Refused):
            f.prepare("standard")
        self.assertEqual(f.c._issued,1)

    def test_bad_envelope_charges_both_ceilings(self):
        f=self.fixture(600)
        offer,run=f.begin()
        lease,wire,handle=f.wave(offer,run,("plan",))[0]
        with self.assertRaises(Refused):
            f.s.settle_reaped(lease,b"bad")
        self.assertEqual(f.s.prompt_snapshot(),(600,112,0,488))
        self.assertEqual(f.s.snapshot(),(80,16,0,64))
        self.assertEqual(run.observe_exit(handle,1,f.ctx,f.models),"DRAINED")

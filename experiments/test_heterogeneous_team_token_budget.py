# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from threading import Barrier

from gateway import Refused
from heterogeneous_team_token_budget import Attempt, TeamTokenBudget


PLAN = "a" * 64
WIRE = b"fixed fixture request"


def attempt(identity, worker=None, ceiling=16, wire=WIRE):
    return Attempt(identity, worker or identity, sha256(wire).hexdigest(), ceiling)


class TeamTokenBudgetTests(unittest.TestCase):
    def test_parallel_wave_and_partial_actual_usage(self):
        b = TeamTokenBudget(PLAN, 40)
        a, c = b.reserve_wave(PLAN, (attempt("a"), attempt("c")))
        self.assertEqual(b.snapshot(), (40, 0, 32, 8))
        b.dispatch(a, WIRE)
        b.dispatch(c, WIRE)
        b.settle_reaped(a, 7)
        b.settle_reaped(c, 12)
        self.assertEqual(b.snapshot(), (40, 19, 0, 21))

    def test_atomic_overbudget_does_not_consume_attempt_identity(self):
        b = TeamTokenBudget(PLAN, 24)
        a, c = attempt("a"), attempt("c")
        with self.assertRaises(Refused):
            b.reserve_wave(PLAN, (a, c))
        self.assertEqual(b.snapshot(), (24, 0, 0, 24))
        (lease,) = b.reserve_wave(PLAN, (a,))
        b.void_not_dispatched(lease)
        self.assertEqual(b.snapshot(), (24, 0, 0, 24))

    def test_retry_charged_again_under_same_total_budget(self):
        b = TeamTokenBudget(PLAN, 32)
        (first,) = b.reserve_wave(PLAN, (attempt("try-1", "review"),))
        b.dispatch(first, WIRE)
        b.abandon_reaped(first)
        (second,) = b.reserve_wave(PLAN, (attempt("try-2", "review"),))
        b.dispatch(second, WIRE)
        b.settle_reaped(second, 9)
        self.assertEqual(b.snapshot(), (32, 25, 0, 7))
        with self.assertRaises(Refused):
            b.reserve_wave(PLAN, (attempt("try-3", "review"),))

    def test_cancelled_dispatched_retains_and_charges(self):
        b = TeamTokenBudget(PLAN, 16)
        (a,) = b.reserve_wave(PLAN, (attempt("a"),))
        b.dispatch(a, WIRE)
        b.cancel(a)
        b.cancel(a)
        self.assertEqual(b.snapshot(), (16, 0, 16, 0))
        with self.assertRaises(Refused):
            b.void_not_dispatched(a)
        b.settle_reaped(a, 4)
        self.assertEqual(b.snapshot(), (16, 4, 0, 12))

    def test_cancel_before_dispatch_only_explicit_absence_refunds(self):
        b = TeamTokenBudget(PLAN, 16)
        (a,) = b.reserve_wave(PLAN, (attempt("a"),))
        b.cancel(a)
        with self.assertRaises(Refused):
            b.dispatch(a, WIRE)
        with self.assertRaises(Refused):
            b.settle_reaped(a, 0)
        self.assertEqual(b.snapshot(), (16, 0, 16, 0))
        b.void_not_dispatched(a)
        self.assertEqual(b.snapshot(), (16, 0, 0, 16))
        with self.assertRaises(Refused):
            b.reserve_wave(PLAN, (attempt("a"),))

    def test_wrong_plan_wire_forged_lease_and_replay(self):
        b = TeamTokenBudget(PLAN, 32)
        with self.assertRaises(Refused):
            b.reserve_wave("b" * 64, (attempt("a"),))
        (a,) = b.reserve_wave(PLAN, (attempt("a"),))
        for fake, wire in ((replace(a), WIRE), (a, WIRE + b"!")):
            with self.assertRaises(Refused):
                b.dispatch(fake, wire)
        b.dispatch(a, WIRE)
        with self.assertRaises(Refused):
            b.dispatch(a, WIRE)
        b.settle_reaped(a, 0)
        with self.assertRaises(Refused):
            b.settle_reaped(a, 0)

    def test_invalid_usage_retains_until_trusted_unknown_charge(self):
        b = TeamTokenBudget(PLAN, 16)
        (a,) = b.reserve_wave(PLAN, (attempt("a"),))
        b.dispatch(a, WIRE)
        for value in (True, -1, 17, 1.0, None):
            with self.assertRaises(Refused):
                b.settle_reaped(a, value)
            self.assertEqual(b.snapshot(), (16, 0, 16, 0))
        b.abandon_reaped(a)
        self.assertEqual(b.snapshot(), (16, 16, 0, 0))

    def test_close_blocks_admission_and_dispatch_but_allows_drain(self):
        b = TeamTokenBudget(PLAN, 32)
        a, c = b.reserve_wave(PLAN, (attempt("a"), attempt("c")))
        b.dispatch(a, WIRE)
        b.close()
        with self.assertRaises(Refused):
            b.reserve_wave(PLAN, (attempt("d", ceiling=1),))
        with self.assertRaises(Refused):
            b.dispatch(c, WIRE)
        self.assertEqual(b.snapshot(), (32, 0, 32, 0))
        b.void_not_dispatched(c)
        b.abandon_reaped(a)
        self.assertEqual(b.snapshot(), (32, 16, 0, 16))

    def test_duplicate_worker_and_identity_refusal(self):
        b = TeamTokenBudget(PLAN, 64)
        for wave in ((attempt("a"), attempt("a")),
                     (attempt("a", "w"), attempt("b", "w"))):
            with self.assertRaises(Refused):
                b.reserve_wave(PLAN, wave)
        (a,) = b.reserve_wave(PLAN, (attempt("a", "w"),))
        with self.assertRaises(Refused):
            b.reserve_wave(PLAN, (attempt("b", "w"),))
        b.void_not_dispatched(a)
        (c,) = b.reserve_wave(PLAN, (attempt("b", "w"),))
        b.void_not_dispatched(c)

    def test_competing_threads_cannot_oversubscribe(self):
        b = TeamTokenBudget(PLAN, 16)
        barrier = Barrier(2, timeout=5)
        def reserve(identity):
            barrier.wait()
            try:
                return b.reserve_wave(PLAN, (attempt(identity),))[0]
            except Refused:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(reserve, name) for name in ("a", "b")]
            results = [f.result(timeout=10) for f in futures]
        winners = [x for x in results if x is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(b.snapshot(), (16, 0, 16, 0))
        b.void_not_dispatched(winners[0])
        self.assertEqual(b.snapshot(), (16, 0, 0, 16))

    def test_dispatch_cancel_race_keeps_correct_reservation(self):
        b = TeamTokenBudget(PLAN, 16)
        (lease,) = b.reserve_wave(PLAN, (attempt("a"),))
        barrier = Barrier(2, timeout=5)
        def dispatch():
            barrier.wait()
            try:
                b.dispatch(lease, WIRE)
                return True
            except Refused:
                return False
        def cancel():
            barrier.wait()
            b.cancel(lease)
        with ThreadPoolExecutor(max_workers=2) as pool:
            d, c = pool.submit(dispatch), pool.submit(cancel)
            dispatched = d.result(timeout=10)
            c.result(timeout=10)
        self.assertEqual(b.snapshot(), (16, 0, 16, 0))
        if dispatched:
            b.abandon_reaped(lease)
            self.assertEqual(b.snapshot(), (16, 16, 0, 0))
        else:
            b.void_not_dispatched(lease)
            self.assertEqual(b.snapshot(), (16, 0, 0, 16))

    def test_invalid_limits_and_attempts(self):
        for limit in (True, 0, 655361, 1.0):
            with self.assertRaises(Refused):
                TeamTokenBudget(PLAN, limit)
        b = TeamTokenBudget(PLAN, 32)
        for item in (attempt("a", ceiling=True), attempt("a", ceiling=0),
                     replace(attempt("a"), wire_digest="x" * 64), attempt("bad id")):
            with self.assertRaises(Refused):
                b.reserve_wave(PLAN, (item,))
        self.assertEqual(b.snapshot(), (32, 0, 0, 32))

    def test_cross_team_lease_and_lifetime_bound(self):
        b = TeamTokenBudget(PLAN, 1)
        other = TeamTokenBudget(PLAN, 1)
        (a,) = b.reserve_wave(PLAN, (attempt("a", ceiling=1),))
        with self.assertRaises(Refused):
            other.void_not_dispatched(a)
        b.void_not_dispatched(a)
        for i in range(1023):
            (a,) = b.reserve_wave(PLAN, (attempt("try-" + str(i), ceiling=1),))
            b.void_not_dispatched(a)
        with self.assertRaises(Refused):
            b.reserve_wave(PLAN, (attempt("overflow", ceiling=1),))
        self.assertEqual(b.snapshot(), (1, 0, 0, 1))

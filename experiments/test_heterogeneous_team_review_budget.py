# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from hashlib import sha256

from gateway import Refused
from heterogeneous_model_owned_review import OwnedMixedReview
from heterogeneous_team_token_budget import Attempt
import test_heterogeneous_model_review as base


class SharedReviewBudgetTests(unittest.TestCase):
    def setUp(self):
        self.f = base.MixedModelReviewTests()
        self.f.setUp()
        self.team = self.f.r._team
        self.budget = self.team.generated_budget()
        self.plan = self.team._plan.digest
        self.r = OwnedMixedReview(self.f.r, use_team_budget=True)
        self.args = self.f.args
        self.memory = self.team._ledger
        self.limit = self.budget.snapshot()[0]

    def prior_usage(self, amount):
        wire = b"prior fixed fixture"
        spec = Attempt("prior-attempt", "planner", sha256(wire).hexdigest(), amount)
        (lease,) = self.budget.reserve_wave(self.plan, (spec,))
        self.budget.dispatch(lease, wire)
        self.budget.settle_reaped(lease, amount)

    def prepare(self):
        return self.r.prepare(self.f.g, *self.args)

    def dispatch(self):
        ticket, wire = self.prepare()
        self.r.dispatch(ticket, wire, *self.args)
        return ticket, wire

    def test_prior_usage_and_review_share_exact_limit(self):
        self.assertIs(self.team.generated_budget(), self.budget)
        self.assertEqual(self.limit, 512)
        self.prior_usage(self.limit - 16)
        ticket, wire = self.dispatch()
        self.assertEqual(self.budget.snapshot(), (512, 496, 16, 0))
        self.assertEqual(self.r.usage(), (16, 0, 16, 0))
        self.assertEqual(self.r.observe_reaped(ticket, 0, self.f.envelope(wire), *self.args),
                         "COMPLETED")
        self.assertEqual(self.budget.snapshot(), (512, 512, 0, 0))
        self.assertEqual(self.r.output(*self.args)[1], b"bird")
        self.assertEqual(self.memory.snapshot(), (4096, 0, 4096))

    def test_exhaustion_refuses_before_claim_and_releases_memory(self):
        self.prior_usage(self.limit - 15)
        with self.assertRaises(Refused):
            self.prepare()
        self.assertIsNone(self.f.r._grant)
        self.assertIsNone(self.r.pending())
        self.assertEqual(self.budget.snapshot(), (512, 497, 0, 15))
        self.assertEqual(self.memory.snapshot(), (4096, 0, 4096))

    def test_bad_grant_voids_undispatched_reservation(self):
        with self.assertRaises(Refused):
            self.r.prepare(self.f.f.grants["csv"], *self.args)
        self.assertEqual(self.budget.snapshot(), (512, 0, 0, 512))
        self.assertEqual(self.r.usage(), (16, 0, 0, 16))
        self.assertEqual(self.memory.snapshot(), (4096, 0, 4096))

    def test_stop_retains_both_resources_until_trusted_exit(self):
        ticket, wire = self.dispatch()
        self.r.stop()
        self.assertEqual(self.budget.snapshot(), (512, 0, 16, 496))
        self.assertEqual(self.memory.snapshot(), (4096, 16, 4080))
        self.assertEqual(self.r.observe_reaped(ticket, 1, None, *self.args), "DRAINED")
        self.assertEqual(self.budget.snapshot(), (512, 16, 0, 496))
        self.assertEqual(self.memory.snapshot(), (4096, 0, 4096))
        with self.assertRaises(Refused):
            self.r.output(*self.args)

    def test_parent_revocation_after_dispatch_charges_actual_without_output(self):
        ticket, wire = self.dispatch()
        self.f.f.g.revoke("grant-csv")
        with self.assertRaises(Refused):
            self.r.observe_reaped(ticket, 0, self.f.envelope(wire), *self.args)
        self.assertEqual(self.budget.snapshot(), (512, 16, 0, 496))
        self.assertEqual(self.memory.snapshot(), (4096, 0, 4096))
        with self.assertRaises(Refused):
            self.r.output(*self.args)
        with self.assertRaises(Refused):
            self.team.generated_budget()

    def test_team_stop_before_dispatch_requires_definite_no_send(self):
        ticket, wire = self.prepare()
        self.team.stop()
        with self.assertRaises(Refused):
            self.r.dispatch(ticket, wire, *self.args)
        self.assertEqual(self.budget.snapshot(), (512, 0, 16, 496))
        self.assertEqual(self.memory.snapshot(), (4096, 16, 4080))
        self.r.not_started(ticket)
        self.assertEqual(self.budget.snapshot(), (512, 0, 0, 512))
        self.assertEqual(self.memory.snapshot(), (4096, 0, 4096))
        spec = Attempt("other", "planner", "a" * 64, 1)
        with self.assertRaises(Refused):
            self.budget.reserve_wave(self.plan, (spec,))

    def test_malformed_envelope_conservatively_charges_shared_limit(self):
        ticket, wire = self.dispatch()
        with self.assertRaises(Refused):
            self.r.observe_reaped(ticket, 0, b"bad", *self.args)
        self.assertEqual(self.budget.snapshot(), (512, 16, 0, 496))
        self.assertEqual(self.memory.snapshot(), (4096, 0, 4096))
        with self.assertRaises(Refused):
            self.r.output(*self.args)

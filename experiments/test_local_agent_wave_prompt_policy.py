# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit wave-only admission: no guarantee of completing the full plan."""
import unittest
from dataclasses import replace
from gateway import Refused
from local_agent_retry_budget_session import RetryBudgetSession
from local_agent_replan import AttemptCoordinator
import test_local_agent_retry_budget_session as retry


class WavePromptPolicyTests(unittest.TestCase):
    def fixture(self,prompt):
        f=retry.RetryBudgetSessionTests()
        f.setUp()
        f.c=AttemptCoordinator(f.g,f.memory,"wave-policy",f.f.workers,4096,2,
                               owned_drain=True,strict_results=True)
        f.s=RetryBudgetSession(f.c,80,prompt_total=prompt)
        return f

    def offer(self,f,policy="wave",profile="standard"):
        return f.s.prepare(f.ctx,f.models,profile,prompt_policy=policy)

    def begin(self,f):
        offer=self.offer(f)
        return offer,f.s.confirm(offer,f.ctx,f.models)

    def test_default_remains_whole_plan_and_does_not_issue_preview(self):
        f=self.fixture(238)
        with self.assertRaisesRegex(Refused,"retry_budget_prompt_unaffordable"):
            f.prepare()
        self.assertEqual(f.c._issued,0)
        offer=self.offer(f)
        self.assertEqual(offer.prompt_required_at_admission,112)
        self.assertEqual(offer.prompt_policy,"wave")
        self.assertEqual(offer.prompt_remaining,238)

    def test_wave_exact_boundary_completes_with_measured_release(self):
        f=self.fixture(238)
        offer,run=self.begin(f)
        for names in (("plan",),("a","b"),("review",)):
            rows=f.wave(offer,run,names)
            self.assertEqual(f.s.prompt_snapshot()[2],112*len(rows))
            for row in rows:
                f.finish(run,row)
        self.assertEqual(f.s.prompt_snapshot(),(238,56,0,182))
        self.assertEqual(f.s.snapshot(),(80,64,0,16))
        self.assertEqual(run.output("review",f.ctx,f.models)[1],b"bird")

    def test_one_below_peer_boundary_stops_and_never_claims_peers(self):
        f=self.fixture(237)
        offer,run=self.begin(f)
        f.finish(run,f.wave(offer,run,("plan",))[0])
        with self.assertRaisesRegex(Refused,"team_budget_prompt_exhausted"):
            f.s.reserve_wave(offer,("a","b"),f.ctx,f.models)
        self.assertTrue(run.drained())
        self.assertEqual(f.s.prompt_snapshot(),(237,14,0,223))
        self.assertEqual(f.s.snapshot(),(80,16,0,64))
        self.assertEqual(f.memory.snapshot(),(4096,0,4096))
        self.assertEqual(set(run._run._claims),{"plan"})
        with self.assertRaises(Refused):
            run.output("plan",f.ctx,f.models)
        with self.assertRaises(Refused):
            f.s.reserve_wave(offer,("a","b"),f.ctx,f.models)

    def test_first_wave_insufficient_refuses_before_preview(self):
        f=self.fixture(111)
        with self.assertRaisesRegex(Refused,"retry_budget_prompt_unaffordable"):
            self.offer(f)
        self.assertEqual(f.c._issued,0)

    def test_mode_must_be_explicit_and_offer_exact(self):
        f=self.fixture(500)
        wave=self.offer(f)
        for fake in (replace(wave,prompt_policy="whole-plan"),
                     replace(wave,prompt_required_at_admission=1)):
            with self.assertRaises(Refused):
                f.s.confirm(fake,f.ctx,f.models)
        whole=self.offer(f,"whole-plan")
        self.assertEqual(whole.prompt_required_at_admission,448)
        with self.assertRaises(Refused):
            f.s.confirm(wave,f.ctx,f.models)
        f.s.confirm(whole,f.ctx,f.models)

    def test_invalid_policy_consumes_offer(self):
        f=self.fixture(500)
        valid=self.offer(f)
        with self.assertRaisesRegex(Refused,"retry_budget_prompt_policy"):
            self.offer(f,"auto")
        with self.assertRaises(Refused):
            f.s.confirm(valid,f.ctx,f.models)

    def test_generated_only_cannot_select_wave_prompt_policy(self):
        f=retry.RetryBudgetSessionTests()
        f.setUp()
        with self.assertRaisesRegex(Refused,"retry_budget_prompt_policy"):
            self.offer(f)

    def test_revoke_parent_stops_before_next_wave(self):
        f=self.fixture(500)
        offer,run=self.begin(f)
        f.finish(run,f.wave(offer,run,("plan",))[0])
        f.g.revoke(offer.attempt+"-plan")
        with self.assertRaises(Refused):
            f.s.reserve_wave(offer,("a","b"),f.ctx,f.models)
        self.assertTrue(run.drained())
        self.assertEqual(f.s.prompt_snapshot(),(500,14,0,486))

    def test_changed_inventory_stops_before_next_wave(self):
        f=self.fixture(500)
        offer,run=self.begin(f)
        f.finish(run,f.wave(offer,run,("plan",))[0])
        changed=(replace(f.models[0],rank=1),)
        with self.assertRaises(Refused):
            f.s.reserve_wave(offer,("a","b"),f.ctx,changed)
        self.assertTrue(run.drained())
        self.assertEqual(f.s.snapshot(),(80,16,0,64))

    def test_retry_after_hold_needs_fresh_offer_and_keeps_spent(self):
        f=self.fixture(237)
        offer,run=self.begin(f)
        f.finish(run,f.wave(offer,run,("plan",))[0])
        with self.assertRaises(Refused):
            f.s.reserve_wave(offer,("a","b"),f.ctx,f.models)
        second=self.offer(f,profile="economy")
        self.assertNotEqual(second.attempt,offer.attempt)
        self.assertEqual(second.prompt_remaining,223)
        self.assertEqual(second.remaining,64)
        with self.assertRaises(Refused):
            f.s.confirm(offer,f.ctx,f.models)
        f.s.confirm(second,f.ctx,f.models)

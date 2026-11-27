# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
from local_agent_replan import AttemptCoordinator
from local_agent_resources import ResourceLedger
from local_agent_model_budget_admission import BudgetAdmission
from local_agent_model_fixture_contract import ECONOMY_CEILINGS
import test_local_agent_wave_run as base

class BudgetAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f=base.WaveRunTests()
        self.f.setUp()
        f=self.f
        self.c=AttemptCoordinator(f.g,ResourceLedger(4096),"budget-admission",f.workers,4096,2,
                                  owned_drain=True,strict_results=True)
    def prepare(self,a,profile):
        return a.prepare(self.f.ctx,self.f.models,profile)
    def confirm(self,a,p):
        return a.confirm(p,self.f.ctx,self.f.models)
    def test_standard_never_silently_downgrades(self):
        a=BudgetAdmission(self.c,48)
        with self.assertRaises(Refused):
            self.prepare(a,"standard")
        p=self.prepare(a,"economy")
        self.assertEqual(p.ceilings,ECONOMY_CEILINGS)
        self.assertEqual(p.required,48)
        _,l=self.confirm(a,p)
        self.assertEqual(l.snapshot(),(48,0,0,48))
    def test_no_plan_when_both_profiles_unaffordable(self):
        a=BudgetAdmission(self.c,32)
        for profile in ("standard","economy"):
            with self.assertRaises(Refused):
                self.prepare(a,profile)
        self.assertEqual(self.c._issued,0)
    def test_exact_offer_and_one_shot_confirmation(self):
        a=BudgetAdmission(self.c,64)
        p=self.prepare(a,"standard")
        with self.assertRaises(Refused):
            self.confirm(a,replace(p))
        self.confirm(a,p)
        with self.assertRaises(Refused):
            self.confirm(a,p)
        with self.assertRaises(Refused):
            self.prepare(a,"economy")
    def test_new_offer_invalidates_old_and_failed_prepare_does_too(self):
        a=BudgetAdmission(self.c,64)
        p=self.prepare(a,"standard")
        q=self.prepare(a,"economy")
        with self.assertRaises(Refused):
            self.confirm(a,p)
        with self.assertRaises(Refused):
            self.prepare(a,"unknown")
        with self.assertRaises(Refused):
            self.confirm(a,q)
    def test_context_change_consumes_offer(self):
        a=BudgetAdmission(self.c,64)
        p=self.prepare(a,"standard")
        with self.assertRaises(Refused):
            a.confirm(p,replace(self.f.ctx,generation=2),self.f.models)
        with self.assertRaises(Refused):
            self.confirm(a,p)

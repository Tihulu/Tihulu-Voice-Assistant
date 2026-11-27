# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from gateway import Refused
from local_agent_replan import AttemptCoordinator
from local_agent_retry_budget_session import RetryBudgetSession
from local_agent_vector_resources import Capacity
import test_local_agent_unified_wave_budget as fixtures

class UnifiedOwnerBindingTests(unittest.TestCase):
    def fixture(self):
        return fixtures.UnifiedWaveBudgetTests().fixture()

    def test_second_session_cannot_attach_before_first_prepare(self):
        f,ledger,owner,clock=self.fixture()
        other=AttemptCoordinator(f.g,f.memory,"other-unified",f.f.workers,4096,2,
                                 owned_drain=True,strict_results=True)
        with self.assertRaisesRegex(Refused,"retry_vector_config"):
            RetryBudgetSession(other,80,vector_owner=owner,vector_memory=f.s._vector_memory)
        p=f.prepare()
        self.assertIsNotNone(p.vector_digest)

    def test_direct_mutations_without_binding_denied(self):
        f,ledger,owner,clock=self.fixture()
        p,run=fixtures.UnifiedWaveBudgetTests().begin(f)
        (lease,wire),=f.s.reserve_wave(p,("plan",),f.ctx,f.models)
        row=f.s._vector_rows["plan"][1]
        for operation in (lambda:owner.prepare(f.ctx,f.models,f.s._vector_memory),
                          lambda:owner.confirm(owner._active,f.ctx,f.models,f.s._vector_memory),
                          lambda:owner.reserve_next(owner._active,f.ctx,f.models,f.s._vector_memory),
                          lambda:owner.stop(),lambda:owner.close(),
                          lambda:owner.void_unstarted(row),
                          lambda:owner.observe_reaped(row,success=True),
                          lambda:owner.observe_reaped(row,success=True,owner=object())):
            with self.assertRaisesRegex(Refused,"vector_admission_owner"):
                operation()
        self.assertEqual(ledger.snapshot()[1],Capacity(2048,0))
        f.s.stop()
        f.s.void_not_dispatched(lease)
        f.s.release_vector_after_exit(lease)
        self.assertEqual(ledger.snapshot()[1],Capacity(0,0))

    def test_closed_bound_owner_cannot_be_reused(self):
        f,ledger,owner,clock=self.fixture()
        f.s.close()
        other=AttemptCoordinator(f.g,f.memory,"closed-other",f.f.workers,4096,2,
                                 owned_drain=True,strict_results=True)
        with self.assertRaises(Refused):
            RetryBudgetSession(other,80,vector_owner=owner,vector_memory=f.s._vector_memory)

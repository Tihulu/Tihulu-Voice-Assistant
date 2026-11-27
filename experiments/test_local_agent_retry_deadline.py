# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from gateway import Refused
from local_agent_retry_deadline import RetryDeadlines
from local_agent_retry_budget_session import RetryBudgetSession
from local_agent_replan import AttemptCoordinator
import test_local_agent_retry_budget_session as retry


class Clock:
    def __init__(self):
        self.now=100
    def __call__(self):
        return self.now


class DeadlineTests(unittest.TestCase):
    def test_worker_deadline_equality_expires_and_terminal_event_drains(self):
        c=Clock()
        d=RetryDeadlines(100,20,clock=c)
        t=d.start("a"*64)
        c.now=119
        self.assertEqual(d.remaining_ns(t),1)
        c.now=120
        with self.assertRaisesRegex(Refused,"retry_deadline_worker_expired"):
            d.check()
        d.finish(t)
        self.assertEqual(d.check(),120)
        with self.assertRaises(Refused):
            d.start("a"*64)

    def test_session_cap_and_clock_regression(self):
        c=Clock()
        d=RetryDeadlines(100,80,clock=c)
        c.now=190
        t=d.start("a"*64)
        self.assertEqual(t.expires_ns,200)
        c.now=189
        with self.assertRaisesRegex(Refused,"retry_deadline_clock"):
            d.check()
        c.now=195
        with self.assertRaises(Refused):
            d.check()
        d.finish(t)

    def test_foreign_ticket_and_thread_cannot_release(self):
        c=Clock()
        d=RetryDeadlines(100,20,clock=c)
        t=d.start("a"*64)
        with self.assertRaises(Refused):
            d.finish(replace(t))
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(Refused):
                pool.submit(d.finish,t).result()
        self.assertEqual(d.remaining_ns(t),20)
        d.finish(t)

    def test_bad_configuration_and_clock(self):
        c=Clock()
        for total,worker in ((True,1),(0,1),(10,11),(10,True),(3600000000001,1)):
            with self.assertRaises(Refused):
                RetryDeadlines(total,worker,clock=c)
        c.now=float("nan")
        with self.assertRaises(Refused):
            RetryDeadlines(100,20,clock=c)


class RetryDeadlineTests(unittest.TestCase):
    def fixture(self,total=100,worker=20):
        f=retry.RetryBudgetSessionTests()
        f.setUp()
        f.c=AttemptCoordinator(f.g,f.memory,"timed-retry",f.f.workers,4096,2,
                               owned_drain=True,strict_results=True)
        c=Clock()
        f.s=RetryBudgetSession(f.c,80,prompt_total=600,total_ns=total,worker_ns=worker,clock=c)
        return f,c

    def test_expired_confirmation_consumes_offer_before_new_attempt(self):
        f,c=self.fixture()
        offer=f.prepare()
        c.now=200
        with self.assertRaisesRegex(Refused,"retry_deadline_session_expired"):
            f.s.confirm(offer,f.ctx,f.models)
        self.assertEqual(f.c._accepted,0)
        self.assertEqual(f.s.snapshot(),(80,0,0,80))
        with self.assertRaises(Refused):
            f.s.confirm(offer,f.ctx,f.models)

    def test_timeout_retains_both_token_and_memory_until_exit(self):
        f,c=self.fixture()
        offer,run=f.begin()
        lease,wire,handle=f.wave(offer,run,("plan",))[0]
        self.assertEqual(f.s.remaining_ns(lease),20)
        c.now=120
        with self.assertRaisesRegex(Refused,"retry_deadline_worker_expired"):
            f.s.poll_deadline()
        self.assertEqual(f.s.snapshot(),(80,0,16,64))
        self.assertEqual(f.s.prompt_snapshot(),(600,0,112,488))
        self.assertEqual(f.memory.snapshot()[1],2048)
        with self.assertRaises(Refused):
            f.prepare()
        f.s.settle_reaped(lease,retry.envelope(lease,wire))
        self.assertEqual(f.s.snapshot(),(80,16,0,64))
        with self.assertRaises(Refused):
            f.prepare()
        self.assertEqual(run.observe_exit(handle,0,f.ctx,f.models),"DRAINED")
        second=f.prepare()
        self.assertEqual(second.remaining,64)

    def test_late_result_stops_even_without_prior_poll(self):
        f,c=self.fixture()
        offer,run=f.begin()
        lease,wire,handle=f.wave(offer,run,("plan",))[0]
        c.now=120
        self.assertEqual(f.s.settle_reaped(lease,retry.envelope(lease,wire)),16)
        self.assertEqual(run.observe_exit(handle,0,f.ctx,f.models,payload=b"bird"),"DRAINED")
        self.assertEqual(f.s.prompt_snapshot(),(600,14,0,586))
        with self.assertRaises(Refused):
            run.output("plan",f.ctx,f.models)

    def test_total_deadline_does_not_reset_with_retry(self):
        f,c=self.fixture()
        offer,run=f.begin()
        f.finish(run,f.wave(offer,run,("plan",))[0])
        f.s.stop()
        c.now=199
        offer,run=f.begin()
        lease,wire,handle=f.wave(offer,run,("plan",))[0]
        self.assertEqual(f.s.remaining_ns(lease),1)
        c.now=200
        f.s.abandon_reaped(lease)
        self.assertEqual(run.observe_exit(handle,1,f.ctx,f.models),"DRAINED")
        with self.assertRaisesRegex(Refused,"retry_deadline_session_expired"):
            f.prepare("economy")

    def test_expired_before_dispatch_keeps_reservation_until_void(self):
        f,c=self.fixture()
        offer,run=f.begin()
        (lease,wire),=f.s.reserve_wave(offer,("plan",),f.ctx,f.models)
        grant=f.g.fixture_grant("timed-plan",run.request("plan"),60)
        handle=run.start("plan",grant,f.ctx,f.models)
        c.now=200
        with self.assertRaises(Refused):
            f.s.dispatch(lease,wire,f.ctx,f.models)
        self.assertEqual(f.s.snapshot(),(80,0,16,64))
        f.s.void_not_dispatched(lease)
        self.assertEqual(run.observe_exit(handle,0,f.ctx,f.models),"DRAINED")
        self.assertEqual(f.s.snapshot(),(80,0,0,80))

    def test_clock_failure_stops_and_unknown_charge_remains(self):
        f,c=self.fixture()
        offer,run=f.begin()
        lease,wire,handle=f.wave(offer,run,("plan",))[0]
        c.now=99
        with self.assertRaisesRegex(Refused,"retry_deadline_clock"):
            f.s.poll_deadline()
        f.s.abandon_reaped(lease)
        self.assertEqual(run.observe_exit(handle,1,f.ctx,f.models),"DRAINED")
        self.assertEqual(f.s.prompt_snapshot(),(600,112,0,488))
        with self.assertRaises(Refused):
            f.prepare()

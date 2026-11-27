# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from hashlib import sha256
from gateway import Refused
from local_agent_model_stream_contract import stream_wire
import test_local_agent_retry_deadline as fixtures
import test_local_agent_retry_budget_session as retry

class StreamDeadlineTests(unittest.TestCase):
    def setup_stream(self,dispatch=True):
        f,c=fixtures.RetryDeadlineTests().fixture()
        offer,run=f.begin()
        (lease,wire),=f.s.reserve_wave(offer,("plan",),f.ctx,f.models,streaming=True)
        grant=f.g.fixture_grant("stream-plan",run.request("plan"),60)
        handle=run.start("plan",grant,f.ctx,f.models)
        if dispatch:
            f.s.dispatch(lease,wire,f.ctx,f.models)
        marker={"state":"CANCELLED","worker":"plan","wire_digest":sha256(stream_wire("plan",wire)).hexdigest(),"server_reaped":True}
        return f,c,run,lease,wire,handle,marker

    def test_exact_stream_digest_and_expiry(self):
        f,c,run,l,w,h,m=self.setup_stream()
        self.assertEqual(l.attempt.wire_digest,m["wire_digest"])
        self.assertNotEqual(l.attempt.wire_digest,sha256(w).hexdigest())
        c.now=120
        with self.assertRaisesRegex(Refused,"retry_deadline_worker_expired"):
            f.s.poll_deadline()
        self.assertEqual(f.s.snapshot(),(80,0,16,64))
        self.assertEqual(f.s.prompt_snapshot(),(600,0,112,488))
        f.s.settle_cancelled_stream_reaped(l,m)
        self.assertEqual(f.s.snapshot(),(80,16,0,64))
        self.assertEqual(f.s.prompt_snapshot(),(600,112,0,488))
        self.assertEqual(f.memory.snapshot()[1],2048)
        self.assertEqual(run.observe_exit(h,0,f.ctx,f.models),"DRAINED")
        self.assertEqual(f.prepare().remaining,64)

    def test_forged_cancel_retains_both_reservations(self):
        f,c,run,l,w,h,m=self.setup_stream()
        f.s.stop()
        for bad in (dict(m,server_reaped=False),dict(m,wire_digest="0"*64),dict(m,worker="a")):
            with self.assertRaises(Refused):
                f.s.settle_cancelled_stream_reaped(l,bad)
            self.assertEqual(f.s.snapshot(),(80,0,16,64))
            self.assertEqual(f.s.prompt_snapshot(),(600,0,112,488))
        f.s.settle_cancelled_stream_reaped(l,m)
        with self.assertRaises(Refused):
            f.s.settle_cancelled_stream_reaped(l,m)

    def test_normal_reply_cannot_settle_stream(self):
        f,c,run,l,w,h,m=self.setup_stream()
        with self.assertRaises(Refused):
            f.s.settle_reaped(l,retry.envelope(l,w))
        self.assertEqual(f.s.snapshot(),(80,0,16,64))
        with self.assertRaises(Refused):
            f.s.settle_cancelled_stream_reaped(l,m)
        f.s.stop()
        f.s.settle_cancelled_stream_reaped(l,m)

    def test_definite_no_dispatch_voids_stream(self):
        f,c,run,l,w,h,m=self.setup_stream(False)
        f.s.stop()
        f.s.void_not_dispatched(l)
        self.assertEqual(f.s.snapshot(),(80,0,0,80))
        self.assertEqual(f.s.prompt_snapshot(),(600,0,0,600))
        self.assertFalse(f.s._streaming)
        run.observe_exit(h,0,f.ctx,f.models)

    def test_stream_requires_deadline_configuration(self):
        f=retry.RetryBudgetSessionTests()
        f.setUp()
        offer,run=f.begin()
        with self.assertRaisesRegex(Refused,"retry_budget_stream_mode"):
            f.s.reserve_wave(offer,("plan",),f.ctx,f.models,streaming=True)
        self.assertEqual(f.s.snapshot(),(80,0,0,80))

    def test_non_stream_marker_cannot_settle(self):
        f,c=fixtures.RetryDeadlineTests().fixture()
        offer,run=f.begin()
        l,w,h=f.wave(offer,run,("plan",))[0]
        f.s.stop()
        with self.assertRaises(Refused):
            f.s.settle_cancelled_stream_reaped(l,{"state":"CANCELLED"})
        f.s.abandon_reaped(l)
        self.assertEqual(run.observe_exit(h,0,f.ctx,f.models),"DRAINED")

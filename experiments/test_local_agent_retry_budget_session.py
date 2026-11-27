# SPDX-License-Identifier: AGPL-3.0-only
import json
import unittest
from dataclasses import replace
from hashlib import sha256
from gateway import Refused
from inference import Candidate
from llama_engine_acceptance import MODEL,MODEL_SHA
from local_agent_replan import AttemptCoordinator
from local_agent_resources import ResourceLedger
from local_agent_retry_budget_session import RetryBudgetSession
import test_local_agent_wave_run as base


def envelope(lease,wire):
    ceiling=lease.attempt.ceiling
    reply={"content":"bird","tokens":[2]*ceiling,"stop":True,"stop_type":"limit","model":MODEL,
           "truncated":False,"tokens_evaluated":14}
    return json.dumps({"worker":lease.attempt.worker,"wire_digest":sha256(wire).hexdigest(),
        "model_digest":MODEL_SHA,"reply":json.dumps(reply),"server_reaped":True,"prompt_tokens":14},
        separators=(",",":")).encode()


class RetryBudgetSessionTests(unittest.TestCase):
    def setUp(self):
        self.f=base.WaveRunTests()
        self.f.setUp()
        self.ctx,self.models,self.g=self.f.ctx,self.f.models,self.f.g
        self.memory=ResourceLedger(4096)
        self.c=AttemptCoordinator(self.g,self.memory,"retry-budget",self.f.workers,4096,2,
                                  owned_drain=True,strict_results=True)
        self.s=RetryBudgetSession(self.c,80)

    def prepare(self,profile="standard",models=None):
        return self.s.prepare(self.ctx,models or self.models,profile)

    def begin(self,profile="standard",models=None):
        models=models or self.models
        offer=self.prepare(profile,models)
        run=self.s.confirm(offer,self.ctx,models)
        return offer,run

    def wave(self,offer,run,names,models=None):
        models=models or self.models
        rows=self.s.reserve_wave(offer,names,self.ctx,models)
        handles=[]
        for lease,wire in rows:
            name=lease.attempt.worker
            grant=self.g.fixture_grant(offer.attempt+"-"+name,run.request(name),60)
            handle=run.start(name,grant,self.ctx,models)
            self.s.dispatch(lease,wire,self.ctx,models)
            handles.append((lease,wire,handle))
        return handles

    def finish(self,run,row,models=None):
        models=models or self.models
        lease,wire,handle=row
        n=self.s.settle_reaped(lease,envelope(lease,wire))
        self.assertEqual(n,lease.attempt.ceiling)
        return run.observe_exit(handle,0,self.ctx,models,payload=b"bird")

    def test_retry_after_realistic_charge_keeps_total_and_new_plan(self):
        offer,run=self.begin()
        row,=self.wave(offer,run,("plan",))
        self.finish(run,row)
        self.s.stop()
        fallback=(replace(self.models[0],healthy=False),
                  Candidate("fallback","llm","local","lan-fixture",True,True,2048,70))
        next_offer,next_run=self.begin("standard",fallback)
        self.assertNotEqual(offer.plan_digest,next_offer.plan_digest)
        self.assertNotEqual(run.request("plan"),next_run.request("plan"))
        self.assertEqual(next_offer.remaining,64)
        self.assertEqual(self.s.snapshot(),(80,16,0,64))
        self.assertEqual(next_run._assignments["plan"].model,"fallback")
        for names in (("plan",),("a","b"),("review",)):
            for r in self.wave(next_offer,next_run,names,fallback):
                self.finish(next_run,r,fallback)
        self.assertEqual(self.s.snapshot(),(80,80,0,0))
        self.assertEqual(next_run.output("review",self.ctx,fallback)[1],b"bird")
        self.s.stop()
        with self.assertRaises(Refused):
            self.prepare("economy",fallback)

    def test_cancel_keeps_usage_and_memory_until_both_drain(self):
        offer,run=self.begin()
        lease,wire,handle=self.wave(offer,run,("plan",))[0]
        self.s.stop()
        self.assertEqual(self.s.snapshot(),(80,0,16,64))
        self.assertEqual(self.memory.snapshot()[1],2048)
        with self.assertRaises(Refused):
            self.prepare()
        self.s.settle_reaped(lease,envelope(lease,wire))
        with self.assertRaises(Refused):
            self.prepare()
        self.assertEqual(run.observe_exit(handle,0,self.ctx,self.models),"DRAINED")
        self.assertEqual(self.prepare().remaining,64)

    def test_unknown_charge_blocks_automatic_economy_downgrade(self):
        offer,run=self.begin()
        lease,wire,handle=self.wave(offer,run,("plan",))[0]
        self.s.abandon_reaped(lease)
        run.observe_exit(handle,1,self.ctx,self.models)
        next_offer,next_run=self.begin()
        lease,wire,handle=self.wave(next_offer,next_run,("plan",))[0]
        self.s.abandon_reaped(lease)
        next_run.observe_exit(handle,1,self.ctx,self.models)
        self.assertEqual(self.s.snapshot(),(80,32,0,48))
        with self.assertRaises(Refused):
            self.prepare()
        self.assertEqual(self.prepare("economy").ceilings,(16,8,8,16))

    def test_no_cloud_fallback_when_local_unavailable(self):
        offer,run=self.begin()
        self.s.stop()
        cloud=(Candidate("remote","llm","cloud","cloud-fixture",True,True,1,100),)
        with self.assertRaises(Refused):
            self.prepare(models=cloud)
        self.assertEqual(self.s.snapshot(),(80,0,0,80))

    def test_stale_offer_and_changed_inventory(self):
        a=self.prepare()
        b=self.prepare("economy")
        for x in (a,replace(b)):
            with self.assertRaises(Refused):
                self.s.confirm(x,self.ctx,self.models)
        with self.assertRaises(Refused):
            self.s.confirm(b,self.ctx,(replace(self.models[0],rank=81),))
        with self.assertRaises(Refused):
            self.s.confirm(b,self.ctx,self.models)

    def test_wrong_wave_and_unclaimed_dispatch_refused(self):
        offer,run=self.begin()
        with self.assertRaises(Refused):
            self.s.reserve_wave(offer,("a","b"),self.ctx,self.models)
        (lease,wire),=self.s.reserve_wave(offer,("plan",),self.ctx,self.models)
        with self.assertRaises(Refused):
            self.s.dispatch(lease,wire,self.ctx,self.models)
        with self.assertRaises(Refused):
            self.s.void_not_dispatched(lease)
        self.s.stop()
        self.s.void_not_dispatched(lease)
        self.assertEqual(self.s.snapshot(),(80,0,0,80))
        self.assertEqual(self.prepare().remaining,80)

    def test_forged_lease_and_wrong_wire_do_not_dispatch(self):
        offer,run=self.begin()
        (lease,wire),=self.s.reserve_wave(offer,("plan",),self.ctx,self.models)
        for l,w in ((replace(lease),wire),(lease,wire+b" ")):
            with self.assertRaises(Refused):
                self.s.dispatch(l,w,self.ctx,self.models)
        self.assertEqual(self.s.snapshot(),(80,0,16,64))
        self.s.stop()
        self.s.void_not_dispatched(lease)

    def test_bad_reply_charges_full_and_stops(self):
        offer,run=self.begin()
        lease,wire,handle=self.wave(offer,run,("plan",))[0]
        with self.assertRaises(Refused):
            self.s.settle_reaped(lease,b"bad")
        self.assertEqual(self.s.snapshot(),(80,16,0,64))
        self.assertEqual(run.observe_exit(handle,1,self.ctx,self.models),"DRAINED")
        with self.assertRaises(Refused):
            self.s.settle_reaped(lease,envelope(lease,wire))

    def test_duplicate_session_refused_and_close_is_terminal(self):
        with self.assertRaises(Refused):
            RetryBudgetSession(self.c,80)
        offer,run=self.begin()
        lease,wire,handle=self.wave(offer,run,("plan",))[0]
        self.s.close()
        with self.assertRaises(Refused):
            self.prepare()
        self.s.settle_reaped(lease,envelope(lease,wire))
        run.observe_exit(handle,0,self.ctx,self.models)
        self.assertEqual(self.s.snapshot(),(80,16,0,64))

    def test_partial_parallel_reservation_not_allowed(self):
        offer,run=self.begin()
        self.finish(run,self.wave(offer,run,("plan",))[0])
        with self.assertRaises(Refused):
            self.s.reserve_wave(offer,("a",),self.ctx,self.models)
        rows=self.s.reserve_wave(offer,("a","b"),self.ctx,self.models)
        self.assertEqual(self.s.snapshot(),(80,16,32,32))
        self.s.stop()
        for lease,_ in rows:
            self.s.void_not_dispatched(lease)
        self.assertEqual(self.s.snapshot(),(80,16,0,64))

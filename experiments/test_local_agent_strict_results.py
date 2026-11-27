# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from gateway import Refused
from local_agent_wave_run import WaveRun
import test_local_agent_wave_run as base

class StrictResultTests(unittest.TestCase):
    def setUp(self):
        f=base.WaveRunTests()
        f.setUp()
        self.f=f
        self.run=WaveRun(f.g,f.plan,f.workers,f.models,4096,2,attempt="strict",strict_results=True)
        self.grants={w.identity:f.g.fixture_grant("strict-"+w.identity,self.run.request(w.identity),60)
                     for w in f.workers}
    def start(self,w):
        self.run.start(w,self.grants[w],self.f.ctx,self.f.models)
    def done(self,w,payload=b"result"):
        self.run.complete(w,self.f.ctx,self.f.models,payload=payload,completion_grant=self.grants[w])
    def inputs(self,w):
        return self.run.inputs(w,self.f.ctx,self.f.models)
    def test_root_requires_nonempty_result(self):
        self.start("plan")
        for p in (None,b"",b"x"*16385):
            with self.assertRaises(Refused):
                self.done("plan",p)
        self.done("plan")
    def test_child_cannot_complete_without_direct_read(self):
        self.start("plan")
        self.done("plan")
        self.start("a")
        with self.assertRaises(Refused):
            self.done("a")
        self.inputs("a")
        self.done("a")
    def test_sibling_read_does_not_authorize_peer(self):
        self.start("plan")
        self.done("plan")
        self.start("a")
        self.start("b")
        self.inputs("a")
        with self.assertRaises(Refused):
            self.done("b")
    def test_complete_chain_binds_transitive_receipts(self):
        self.start("plan")
        self.done("plan",b"plan")
        for w in ("a","b"):
            self.start(w)
            self.assertEqual(self.inputs(w)[0][1],b"plan")
            self.done(w,w.encode())
        self.start("review")
        self.assertEqual(tuple(p for r,p in self.inputs("review")),(b"a",b"b"))
        self.done("review",b"review")
        a,_=self.run.output("a",self.f.ctx,self.f.models)
        b,_=self.run.output("b",self.f.ctx,self.f.models)
        r,p=self.run.output("review",self.f.ctx,self.f.models)
        self.assertEqual(p,b"review")
        self.assertEqual(len(r.input_digest),64)
        self.assertNotEqual(a.input_digest,b.input_digest)
        self.assertEqual(r.input_digest,self.run._input_digest("review"))
    def test_revoked_parent_after_read_denies_completion(self):
        self.start("plan")
        self.done("plan")
        self.start("a")
        self.inputs("a")
        self.f.g.revoke("strict-plan")
        with self.assertRaises(Refused):
            self.done("a")
    def test_cancel_clears_input_reads_and_results(self):
        self.start("plan")
        self.done("plan")
        self.start("a")
        self.inputs("a")
        self.run.cancel()
        self.assertEqual(self.run._input_reads,{})
        self.assertEqual(self.run._outputs,{})
    def test_strict_policy_requires_exact_bool_and_attempt(self):
        f=self.f
        for value in (True,1,"yes"):
            with self.assertRaises(Refused):
                WaveRun(f.g,f.plan,f.workers,f.models,4096,2,strict_results=value)

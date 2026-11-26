# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Refused
import test_local_agent_wave_run as base

class AgentResultTests(unittest.TestCase):
    def setUp(self):
        f=base.WaveRunTests()
        f.setUp()
        self.ctx,self.models,self.run,self.g,self.grants=f.ctx,f.models,f.run,f.g,f.grants

    def start(self,w):
        self.run.start(w,self.grants[w],self.ctx,self.models)

    def finish(self,w,payload=None):
        self.run.complete(w,self.ctx,self.models,payload=payload)

    def test_exact_direct_inputs_and_final_output(self):
        self.start("plan")
        self.finish("plan",b"plan")
        self.start("a")
        self.start("b")
        a,=self.run.inputs("a",self.ctx,self.models)
        self.assertEqual(a[1],b"plan")
        self.assertEqual(a[0].grant_identity,self.grants["plan"].identity)
        self.assertEqual(a[0].request_target,self.run.request("plan").target)
        self.finish("a",b"first")
        self.finish("b",b"second")
        self.start("review")
        self.assertEqual(tuple(p for r,p in self.run.inputs("review",self.ctx,self.models)),(b"first",b"second"))
        self.finish("review",b"reviewed")
        receipt,payload=self.run.output("review",self.ctx,self.models)
        self.assertEqual(payload,b"reviewed")
        self.assertEqual(receipt.size,8)

    def test_unstarted_and_completed_worker_cannot_read_inputs(self):
        with self.assertRaises(Refused):
            self.run.inputs("a",self.ctx,self.models)
        self.start("plan")
        self.finish("plan",b"plan")
        self.start("a")
        self.finish("a",b"a")
        with self.assertRaises(Refused):
            self.run.inputs("a",self.ctx,self.models)

    def test_missing_parent_payload_is_not_invented(self):
        self.start("plan")
        self.finish("plan")
        self.start("a")
        with self.assertRaises(Refused):
            self.run.inputs("a",self.ctx,self.models)

    def test_payload_type_size_and_duplicate_completion(self):
        self.start("plan")
        for bad in (b"",b"x"*16385,bytearray(b"x"),"text"):
            with self.assertRaises(Refused):
                self.finish("plan",bad)
        self.finish("plan",b"x")
        with self.assertRaises(Refused):
            self.finish("plan",b"replacement")

    def test_revoked_parent_blocks_result_read(self):
        self.start("plan")
        self.finish("plan",b"plan")
        self.start("a")
        self.g.revoke("plan")
        with self.assertRaises(Refused):
            self.run.inputs("a",self.ctx,self.models)

    def test_cancel_and_context_change_destroy_owned_outputs(self):
        self.start("plan")
        self.finish("plan",b"plan")
        self.start("a")
        with self.assertRaises(Refused):
            self.run.inputs("a",replace(self.ctx,generation=2),self.models)
        self.assertEqual(self.run._outputs,{})
        with self.assertRaises(Refused):
            self.run.inputs("a",self.ctx,self.models)

    def test_final_output_not_available_before_complete_run(self):
        self.start("plan")
        self.finish("plan",b"plan")
        with self.assertRaises(Refused):
            self.run.output("plan",self.ctx,self.models)

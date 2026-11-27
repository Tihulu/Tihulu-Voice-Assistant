# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from hashlib import sha256
from dataclasses import replace
import json
from gateway import Refused
from local_agent_wave_run import AgentResult
from local_agent_model_fixture_contract import wire_for,reply_for,MODEL

def row(worker,data=b"little bird"):
    return (AgentResult(worker,"p","g","r",sha256(data).hexdigest(),len(data),"a"*64),data)

class ModelFixtureContractTests(unittest.TestCase):
    def test_root_fixed_wire(self):
        w=json.loads(wire_for("plan",()))
        self.assertEqual(w["n_predict"],16)
        self.assertFalse(w["stream"])
        self.assertFalse(w["cache_prompt"])
        self.assertTrue(w["prompt"].startswith("Once upon a time"))
    def test_exact_ordered_dependency_bytes(self):
        a,b=row("a",b"first"),row("b",b"second")
        self.assertTrue(json.loads(wire_for("review",(a,b)))["prompt"].endswith("first second "))
        with self.assertRaises(Refused):
            wire_for("review",(b,a))
    def test_tampered_receipt_and_payload_rejected(self):
        r,p=row("plan")
        for pair in ((r,b"other"),(replace(r,size=0),p),(replace(r,input_digest=""),p)):
            with self.assertRaises(Refused):
                wire_for("a",(pair,))
    def test_missing_extra_and_unknown_role_refused(self):
        for worker,rows in (("a",()),("plan",(row("plan"),)),("shell",())):
            with self.assertRaises(Refused):
                wire_for(worker,rows)
    def test_bad_utf8_control_or_oversized_result(self):
        for data in (b"\xff",b"\0",b" "*3,b"x"*513):
            with self.assertRaises(Refused):
                wire_for("a",(row("plan",data),))
    def test_native_reply_contract(self):
        r={"content":"bird","tokens":[2],"stop":True,"stop_type":"eos","model":MODEL,"truncated":False}
        self.assertEqual(reply_for(json.dumps(r).encode()),(b"bird",(2,)))
        r["model"]="wrong"
        with self.assertRaises(ValueError):
            reply_for(json.dumps(r).encode())

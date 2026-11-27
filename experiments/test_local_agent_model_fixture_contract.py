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

    def test_worker_role_and_canonical_wire_are_exact(self):
        from local_agent_model_fixture_contract import validate_wire
        wire=wire_for("a",(row("plan"),))
        self.assertEqual(validate_wire("a",wire),sha256(wire).hexdigest())
        for name,data in (("b",wire),("a",wire+b" "),("plan",wire),("a",wire.replace(b'"n_predict":16',b'"n_predict":17'))):
            with self.assertRaises(Refused):
                validate_wire(name,data)
    def test_bound_reply_exact_worker_wire_model_and_reap(self):
        from local_agent_model_fixture_contract import bound_reply,MODEL_SHA
        w=wire_for("a",(row("plan"),))
        response={"content":"bird","tokens":[2],"stop":True,"stop_type":"eos","model":MODEL,"truncated":False}
        obj={"worker":"a","wire_digest":sha256(w).hexdigest(),"model_digest":MODEL_SHA,
             "reply":json.dumps(response),"server_reaped":True}
        pack=lambda value:json.dumps(value,separators=(",",":")).encode("ascii")
        self.assertEqual(bound_reply("a",w,pack(obj)),(b"bird",(2,)))
        for key,value in (("worker","b"),("wire_digest","0"*64),("model_digest","0"*64),("server_reaped",False)):
            with self.assertRaises(Refused):
                bound_reply("a",w,pack(dict(obj,**{key:value})))
        with self.assertRaises(Refused):
            bound_reply("a",w,pack(obj)+b" ")

    def test_valid_multiline_and_tab_model_result_roundtrip(self):
        from local_agent_model_fixture_contract import validate_wire
        data="bird\n\tfriend".encode("utf-8")
        wire=wire_for("a",(row("plan",data),))
        self.assertTrue(json.loads(wire)["prompt"].endswith("bird\n\tfriend "))
        self.assertEqual(validate_wire("a",wire),sha256(wire).hexdigest())
    def test_invalid_unicode_surrogate_is_refused(self):
        from local_agent_model_fixture_contract import validate_wire
        wire=wire_for("a",(row("plan"),))
        obj=json.loads(wire)
        obj["prompt"]+="\ud800"
        bad=json.dumps(obj,separators=(",",":")).encode("ascii")
        with self.assertRaises(Refused):
            validate_wire("a",bad)
    def test_nonhex_dependency_digest_and_boolean_size_refused(self):
        receipt,data=row("plan",b"x")
        for bad in (replace(receipt,input_digest="z"*64),replace(receipt,size=True)):
            with self.assertRaises(Refused):
                wire_for("a",((bad,data),))

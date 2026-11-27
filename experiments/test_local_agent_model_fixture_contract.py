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
        response["tokens_evaluated"]=12
        obj={"worker":"a","wire_digest":sha256(w).hexdigest(),"model_digest":MODEL_SHA,
             "reply":json.dumps(response),"server_reaped":True,"prompt_tokens":12}
        pack=lambda value:json.dumps(value,separators=(",",":")).encode("ascii")
        self.assertEqual(bound_reply("a",w,pack(obj)),(b"bird",(2,),12))
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

    def test_tokenizer_wire_uses_exact_prompt_and_special_options(self):
        from local_agent_model_fixture_contract import tokenizer_wire
        w=wire_for("a",(row("plan"),))
        self.assertEqual(json.loads(tokenizer_wire("a",w)),{"content":json.loads(w)["prompt"],
            "add_special":True,"parse_special":True,"with_pieces":False})
        with self.assertRaises(Refused):
            tokenizer_wire("b",w)
    def test_context_reserves_full_generation_ceiling(self):
        from local_agent_model_fixture_contract import prompt_count
        self.assertEqual(prompt_count(json.dumps({"tokens":[1]*112}).encode()),112)
        for n in (0,113,128):
            with self.assertRaises(Refused):
                prompt_count(json.dumps({"tokens":[1]*n}).encode())
    def test_tokenizer_malformed_and_duplicate_fields_refused(self):
        from local_agent_model_fixture_contract import prompt_count
        for raw in (b'{"tokens":[true]}',b'{"tokens":[512]}',b'{"tokens":[1],"tokens":[2]}',b'{"tokens":[1],"extra":0}'):
            with self.assertRaises(Refused):
                prompt_count(raw)
    def test_native_evaluated_count_must_match_preflight(self):
        from local_agent_model_fixture_contract import measured_reply
        response={"content":"bird","tokens":[2],"stop":True,"stop_type":"eos","model":MODEL,
                  "truncated":False,"tokens_evaluated":112}
        raw=json.dumps(response).encode()
        self.assertEqual(measured_reply(raw,112),(b"bird",(2,),112))
        for n in (111,113,True):
            with self.assertRaises(Refused):
                measured_reply(raw,n)
        response["tokens_evaluated"]=True
        with self.assertRaises(Refused):
            measured_reply(json.dumps(response).encode(),1)

    def test_reduced_ceiling_only_for_fixed_parallel_roles(self):
        from local_agent_model_fixture_contract import wire_ceiling
        w=wire_for("a",(row("plan"),),ceiling=8)
        self.assertEqual(wire_ceiling("a",w),8)
        for worker,rows,n in (("plan",(),8),("a",(row("plan"),),True),("a",(row("plan"),),12)):
            with self.assertRaises(Refused):
                wire_for(worker,rows,ceiling=n)
    def test_native_limit_matches_requested_ceiling(self):
        from local_agent_model_fixture_contract import measured_reply
        r={"content":"bird","tokens":[2]*8,"stop":True,"stop_type":"limit","model":MODEL,
           "truncated":False,"tokens_evaluated":12}
        raw=json.dumps(r).encode()
        self.assertEqual(measured_reply(raw,12,ceiling=8),(b"bird",(2,)*8,12))
        with self.assertRaises(ValueError):
            measured_reply(raw,12)
        r["tokens"]=[2]*9
        with self.assertRaises(ValueError):
            measured_reply(json.dumps(r).encode(),12,ceiling=8)
    def test_bound_reply_cannot_exceed_reduced_wire(self):
        from local_agent_model_fixture_contract import bound_reply,MODEL_SHA
        w=wire_for("b",(row("plan"),),ceiling=8)
        response={"content":"bird","tokens":[2]*16,"stop":True,"stop_type":"limit","model":MODEL,
                  "truncated":False,"tokens_evaluated":12}
        obj={"worker":"b","wire_digest":sha256(w).hexdigest(),"model_digest":MODEL_SHA,
             "reply":json.dumps(response),"server_reaped":True,"prompt_tokens":12}
        with self.assertRaises(ValueError):
            bound_reply("b",w,json.dumps(obj,separators=(",",":")).encode())

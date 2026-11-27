# SPDX-License-Identifier: AGPL-3.0-only
import unittest
import json
from hashlib import sha256
from gateway import Refused
from heterogeneous_model_review import MixedModelReview,review_wire
from llama_engine_acceptance import MODEL,MODEL_SHA
import test_heterogeneous_data_team as data_tests

class MixedModelReviewTests(unittest.TestCase):
    def setUp(self):
        self.f=data_tests.DataTeamRunTests()
        self.f.setUp()
        self.f.start()
        for t in ("csv","image","text","review"):
            self.f.execute(t)
        self.args=(self.f.ctx,self.f.available,self.f.models)
        self.r=MixedModelReview(self.f.run,*self.args)
        self.g=self.f.g.fixture_grant("model-review",self.r.request(),60)
    def begin(self):
        return self.r.begin(self.g,*self.args)
    def envelope(self,w):
        reply={"content":"bird","tokens":[2]*16,"stop":True,"stop_type":"limit","model":MODEL,
               "truncated":False,"tokens_evaluated":35}
        return json.dumps({"worker":"a","wire_digest":sha256(w).hexdigest(),"model_digest":MODEL_SHA,
            "reply":json.dumps(reply),"server_reaped":True,"prompt_tokens":35},separators=(",",":")).encode()
    def test_exact_numeric_projection_and_receipt(self):
        w=self.begin()
        self.assertIn("3 numbers, total 15, 2 words, and a 2 by 1 picture",json.loads(w)["prompt"])
        self.assertNotIn("public fixture",json.loads(w)["prompt"])
        self.r.complete(self.envelope(w),*self.args)
        receipt,data=self.r.output(*self.args)
        self.assertEqual(data,b"bird")
        self.assertEqual(receipt.source_digest,self.f.output("review")[0].payload_digest)
        self.assertEqual(receipt.wire_digest,sha256(w).hexdigest())
        with self.assertRaises(Refused):
            self.r.complete(self.envelope(w),*self.args)
    def test_no_replay_or_duplicate_bridge(self):
        self.begin()
        with self.assertRaises(Refused):
            self.begin()
        with self.assertRaises(Refused):
            MixedModelReview(self.f.run,*self.args)
    def test_wrong_grant_refused(self):
        with self.assertRaises(Refused):
            self.r.begin(self.f.grants["csv"],*self.args)
    def test_revoked_parent_prevents_dispatch(self):
        self.f.g.revoke("grant-csv")
        with self.assertRaises(Refused):
            self.begin()
    def test_revoked_parent_after_dispatch_prevents_publication(self):
        w=self.begin()
        self.f.g.revoke("grant-text")
        with self.assertRaises(Refused):
            self.r.complete(self.envelope(w),*self.args)
        with self.assertRaises(Refused):
            self.r.output(*self.args)
    def test_forged_envelope_refused(self):
        w=self.begin()
        obj=json.loads(self.envelope(w))
        obj["server_reaped"]=False
        with self.assertRaises(Refused):
            self.r.complete(json.dumps(obj,separators=(",",":")).encode(),*self.args)
    def test_stop_leaves_source_owner(self):
        self.begin()
        self.r.stop()
        self.f.output("review")
        with self.assertRaises(Refused):
            self.r.output(*self.args)
    def test_projection_refuses_noncanonical_or_wrong_digest(self):
        payload=self.f.output("review")[1]
        obj=json.loads(payload)
        obj["results"][0]["digest"]="0"*64
        from heterogeneous_data_tools import encode
        for p in (payload+b" ",encode(obj)):
            with self.assertRaises(Refused):
                review_wire(p)

# SPDX-License-Identifier: AGPL-3.0-only
import unittest
import json
from gateway import Refused
from local_agent_model_fixture_contract import wire_for
from local_agent_model_stream_contract import stream_wire,first_event,marker,verify_marker,verify_cancel
from hashlib import sha256

class StreamContractTests(unittest.TestCase):
    def setUp(self):
        self.w=wire_for("plan",())
        self.line=b'data: {"content":"bird","tokens":[2],"stop":false}\n'
    def test_stream_derivation_only_changes_stream_and_response_fields(self):
        base=json.loads(self.w)
        actual=json.loads(stream_wire("plan",self.w))
        base["stream"]=True
        base["response_fields"]=["content","tokens","stop"]
        self.assertEqual(actual,base)
    def test_event_sanitizes_text_and_token_ids(self):
        self.assertEqual(first_event(self.line),(1,sha256(self.line).hexdigest()))
        m=marker("plan",self.w,self.line)
        self.assertEqual(verify_marker("plan",self.w,m),1)
        self.assertNotIn("bird",json.dumps(m))
    def test_terminal_or_empty_token_event_denied(self):
        for line in (self.line.replace(b"false",b"true"),self.line.replace(b"[2]",b"[]"),
                     self.line.replace(b"[2]",b"[true]"),self.line.replace(b"[2]",b"[512]")):
            with self.assertRaises(Refused):
                first_event(line)
    def test_malformed_or_oversized_frame_denied(self):
        for line in (b"",b"data: {}\n",self.line[:-1],b"x"*8193,
                     self.line.replace(b'"stop":false',b'"stop":true,"stop":false'),
                     self.line.replace(b"bird",b"\\ud800")):
            with self.assertRaises(Refused):
                first_event(line)
    def test_wrong_marker_denied(self):
        m=marker("plan",self.w,self.line)
        for key,value in (("worker","a"),("wire_digest","0"*64),("observed_tokens",True),("event_digest","z"*64)):
            with self.assertRaises(Refused):
                verify_marker("plan",self.w,dict(m,**{key:value}))
    def test_cancel_requires_owned_wire_and_reaped_attestation(self):
        m={"state":"CANCELLED","worker":"plan","wire_digest":sha256(stream_wire("plan",self.w)).hexdigest(),
           "server_reaped":True}
        verify_cancel("plan",self.w,m)
        for key,value in (("worker","a"),("server_reaped",False),("wire_digest","0"*64)):
            with self.assertRaises(Refused):
                verify_cancel("plan",self.w,dict(m,**{key:value}))

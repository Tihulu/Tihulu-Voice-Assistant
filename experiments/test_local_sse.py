# SPDX-License-Identifier: AGPL-3.0-only
import json
import unittest
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway, Refused
from inference import Candidate
from adapter_protocol import LocalAdapter, prepare, local_request
from local_sse import LocalSSE

class LocalSSETests(unittest.TestCase):
    def make(self):
        ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        g=FixtureGateway(ctx,(profiles.Rule("filesystem_read",(),"allow"),),clock=profiles.PolicyClock())
        c=Candidate("qwen-fixture","llm","local","jan",True,True,4096,80)
        req=local_request(ctx,prepare(c,"job","llm",b"public fixture"))
        grant=g.fixture_grant("stream",req,60)
        adapter=LocalAdapter(g,ctx,c,"job","llm",b"public fixture",grant)
        return g,adapter,LocalSSE(adapter,"response1")
    def event(self,delta,reason=None,**changes):
        obj={"id":"response1","model":"qwen-fixture","choices":[{"index":0,"delta":delta,"finish_reason":reason}]}
        obj.update(changes)
        return b"data: "+json.dumps(obj,ensure_ascii=False).encode()+b"\r\n\r\n"
    def complete(self):
        return self.event({},"stop")+b"data: [DONE]\n\n"
    def test_fragmented_multilingual_bytes(self):
        _,a,s=self.make()
        raw=self.event({"content":"İstanbul München Hello"})+self.complete()
        for byte in raw:
            s.feed(bytes([byte]))
        self.assertEqual(s.finish(),"İstanbul München Hello".encode())
    def test_no_result_before_eof_and_done(self):
        _,a,s=self.make()
        s.feed(self.event({"content":"partial"})+self.complete())
        with self.assertRaises(Refused):
            a.result()
        self.assertEqual(s.finish(),b"partial")
    def test_truncated_and_missing_finish_denied(self):
        for data in (self.event({"content":"partial"}),self.event({"content":"partial"})+b"data: [DONE]\n",self.event({"content":"partial"})+self.event({},"stop")):
            _,a,s=self.make()
            with self.assertRaises(Refused):
                s.feed(data)
                s.finish()
            with self.assertRaises(Refused):
                a.result()
    def test_wrong_identity_model_and_tool_delta_denied(self):
        for data in (self.event({"content":"x"},id="old"),self.event({"content":"x"},model="other"),self.event({"tool_calls":[]})):
            _,a,s=self.make()
            with self.assertRaises(Refused):
                s.feed(data)
            self.assertEqual(a.state,"cancelled")
    def test_duplicate_fields_and_extra_data_after_done(self):
        for data in (b'data: {"id":"a","id":"b"}\n\n',self.event({"content":"x"})+self.complete()+self.event({"content":"late"})):
            _,a,s=self.make()
            with self.assertRaises(Refused):
                s.feed(data)
            with self.assertRaises(Refused):
                a.result()
    def test_oversize_and_revocation(self):
        for revoke in (False,True):
            g,a,s=self.make()
            s.feed(self.event({"content":"x"}))
            if revoke:
                g.revoke("stream")
                s.feed(self.complete())
                with self.assertRaises(Refused):
                    s.finish()
            else:
                with self.assertRaises(Refused):
                    s.feed(b"x"*32769)
            with self.assertRaises(Refused):
                a.result()

    def test_missing_blank_separator_and_multiple_data_lines_denied(self):
        for data in (self.event({"content":"x"})+self.complete().rstrip(b"\n")+b"\n",
                     self.event({"content":"x"}).rstrip(b"\r\n")+b"\n"+self.event({},"stop")):
            _,a,s=self.make()
            with self.assertRaises(Refused):
                s.feed(data)
                s.finish()
            with self.assertRaises(Refused):
                a.result()

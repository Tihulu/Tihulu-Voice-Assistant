import unittest
import json
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from inference import Candidate
from adapter_protocol import prepare
from jan_replay import Turn,request_bytes,replay

def frame(events,chunked=False):
    body=b"".join(b"data: "+x+b"\n\n" for x in events)
    head=b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
    if chunked:
        parts=[body[i:i+7] for i in range(0,len(body),7)]
        return head+b"Transfer-Encoding: chunked\r\n\r\n"+b"".join(
            ("%x"%len(p)).encode()+b"\r\n"+p+b"\r\n" for p in parts)+b"0\r\n\r\n"
    return head+b"Content-Length: "+str(len(body)).encode()+b"\r\n\r\n"+body

def event(piece,model="jan"):
    return json.dumps({"model":model,"choices":[{"index":0,"delta":{"content":piece}}]},ensure_ascii=False).encode()

class JanReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),),clock=profiles.PolicyClock())
        self.c=Candidate("jan","llm","local","jan",True,True,4,80)
        self.url="http://127.0.0.1:1337/v1"
        self.turns=(Turn("user","Merhaba"),)
        self.counter=0
    def grant(self):
        self.counter+=1
        raw=request_bytes(self.c,self.url,self.turns)
        call=prepare(self.c,"job","llm",raw)
        return self.g.fixture_grant("g"+str(self.counter),Request(self.ctx,"engine",call.target,"local"),60)
    async def run_replay(self,response,grant=None,**kw):
        args=dict(gateway=self.g,grant=self.grant() if grant is None else grant,context=self.ctx,
                  candidate=self.c,identity="job",url=self.url,turns=self.turns,response=response)
        args.update(kw)
        return await replay(**args)
    async def test_existing_framing_unicode_chunked_and_fixed(self):
        for chunked in (False,True):
            out=await self.run_replay(frame([event("Türkçe "),event("Grüße"),b"[DONE]"],chunked))
            self.assertEqual(out,"Türkçe Grüße".encode())
    async def test_tool_calls_multiple_choices_wrong_model_duplicate_keys_refused(self):
        invalid=(b'{"choices":[{"delta":{"tool_calls":[]}}]}',
                 b'{"choices":[{"delta":{}},{"delta":{}}]}',
                 event("text","other"),
                 b'{"choices":[{"delta":{"content":"a","content":"b"}}]}')
        for e in invalid:
            with self.assertRaises(Refused):
                await self.run_replay(frame([e,b"[DONE]"]))
    async def test_incomplete_trailing_or_unframed_response_refused(self):
        for response in (frame([event("text")]),frame([event("text"),b"[DONE]"])+b"trailing",
                         b"HTTP/1.1 200 OK\r\n\r\ndata: [DONE]\n\n",
                         frame([event("text"),b"[DONE]",event("late")])):
            with self.assertRaises(Refused):
                await self.run_replay(response)
    async def test_endpoint_and_history_are_bound_to_grant(self):
        g=self.grant()
        for args in ({"url":"http://127.0.0.1:1338/v1"},
                     {"turns":(Turn("user","changed"),)},
                     {"url":"https://example.com/v1"}):
            with self.assertRaises(Refused):
                await self.run_replay(frame([event("text"),b"[DONE]"]),grant=g,**args)
        self.assertEqual(await self.run_replay(frame([event("text"),b"[DONE]"]),grant=g),b"text")
    async def test_replay_and_revocation_refused(self):
        g=self.grant()
        response=frame([event("text"),b"[DONE]"])
        await self.run_replay(response,grant=g)
        with self.assertRaises(Refused):
            await self.run_replay(response,grant=g)
        g=self.grant()
        self.g.revoke(g.identity)
        with self.assertRaises(Refused):
            await self.run_replay(response,grant=g)

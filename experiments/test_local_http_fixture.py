# SPDX-License-Identifier: AGPL-3.0-only
"""Actual HTTP bytes over an owned AF_UNIX socketpair, entirely synthetic.
No TCP connect, DNS, proxy, redirect following, Jan process or real inference.
"""
import http.client
import json
import socket
from threading import Thread
from hashlib import sha256
import time
import unittest
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway, Refused
from inference import Candidate
from adapter_protocol import LocalAdapter, prepare, local_request
from local_sse import LocalSSE, MAX_STREAM

class NoConnect(http.client.HTTPConnection):
    def connect(self):
        raise Refused("external_connect_denied")

def exchange(adapter, payload, response_id, reply):
    """Trusted CI fixture peer; no caller-selected address or credentials."""
    adapter.begin_fixture_transport(payload)
    stream=LocalSSE(adapter,response_id)
    captured=[]
    errors=[]
    client,peer=socket.socketpair()
    client.settimeout(2)
    peer.settimeout(2)
    body=json.dumps({"model":adapter.call.model,"stream":True,
                     "messages":[{"role":"user","content":payload.decode("utf-8")}]},
                    ensure_ascii=False,separators=(",",":")).encode("utf-8")
    def serve():
        try:
            with peer:
                data=bytearray()
                while b"\r\n\r\n" not in data and len(data)<65536:
                    chunk=peer.recv(4096)
                    if not chunk:
                        raise RuntimeError("truncated fixture request")
                    data.extend(chunk)
                headers,_,content=bytes(data).partition(b"\r\n\r\n")
                while len(content)<len(body):
                    chunk=peer.recv(4096)
                    if not chunk:
                        raise RuntimeError("truncated fixture body")
                    content+=chunk
                captured.append((headers,content))
                peer.sendall(reply)
                peer.shutdown(socket.SHUT_WR)
        except (OSError,RuntimeError) as exc:
            errors.append(str(exc))
    thread=Thread(target=serve,daemon=True)
    thread.start()
    conn=NoConnect("fixture.invalid",timeout=2)
    conn.sock=client
    try:
        conn.request("POST","/v1/chat/completions",body=body,headers={
            "Content-Type":"application/json","Accept":"text/event-stream","Connection":"close"})
        response=conn.getresponse()
        lengths=response.headers.get_all("Content-Length") or []
        if (response.status!=200 or response.getheader("Content-Type")!="text/event-stream"
                or response.getheader("Transfer-Encoding") is not None or len(lengths)!=1
                or not lengths[0].isascii() or not lengths[0].isdigit()
                or len(lengths[0])>6 or not 0<int(lengths[0])<=MAX_STREAM):
            raise Refused("fixture_http_response_denied")
        remaining=int(lengths[0])
        deadline=time.monotonic()+2
        while remaining:
            if time.monotonic()>=deadline:
                raise Refused("fixture_http_deadline")
            data=response.read(min(31,remaining))
            if not data:
                raise Refused("fixture_http_truncated")
            remaining-=len(data)
            stream.feed(data)
        thread.join(timeout=3)
        if thread.is_alive() or errors or len(captured)!=1:
            raise Refused("fixture_peer_incomplete")
        headers,actual=captured[0]
        if not headers.startswith(b"POST /v1/chat/completions HTTP/1.1\r\n") or actual!=body:
            raise Refused("fixture_http_request_mismatch")
        return stream.finish(),actual
    except (OSError,http.client.HTTPException,Refused):
        stream.abort()
        raise
    finally:
        conn.close()
        client.close()
        thread.join(timeout=3)

class LocalHTTPFixtureTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),),clock=profiles.PolicyClock())
        self.c=Candidate("qwen-fixture","llm","local","jan",True,True,4096,80)
        self.payload="Türkçe / Deutsch / English".encode()
        self.call=prepare(self.c,"job","llm",self.payload)
        self.req=local_request(self.ctx,self.call)
        self.grant=self.g.fixture_grant("stream",self.req,60)
        self.a=LocalAdapter(self.g,self.ctx,self.c,"job","llm",self.payload,self.grant)
    def sse(self,identity="response1"):
        def line(delta,reason):
            obj={"id":identity,"model":self.c.identity,"choices":[{"index":0,"delta":delta,"finish_reason":reason}]}
            return b"data: "+json.dumps(obj,ensure_ascii=False).encode()+b"\n\n"
        return line({"content":"Merhaba, München"},None)+line({},"stop")+b"data: [DONE]\n\n"
    def reply(self,body,status="200 OK",length=None,extra=""):
        n=len(body) if length is None else length
        return (f"HTTP/1.1 {status}\r\nContent-Type: text/event-stream\r\nContent-Length: {n}\r\n"
                f"Connection: close\r\n{extra}\r\n").encode()+body
    def test_exact_request_and_multilingual_http_response(self):
        data,request=exchange(self.a,self.payload,"response1",self.reply(self.sse()))
        self.assertEqual(data,"Merhaba, München".encode())
        self.assertEqual(json.loads(request)["messages"],[{"role":"user","content":self.payload.decode()}])
        with self.assertRaises(Refused):
            exchange(self.a,self.payload,"response1",self.reply(self.sse()))
    def test_wrong_input_denied_before_send(self):
        with self.assertRaisesRegex(Refused,"transport_input_mismatch"):
            exchange(self.a,b"wrong","response1",self.reply(self.sse()))
        self.assertEqual(self.a.state,"ready")
    def test_redirect_is_not_followed(self):
        with self.assertRaisesRegex(Refused,"fixture_http_response_denied"):
            exchange(self.a,self.payload,"response1",self.reply(b"x","302 Found",extra="Location: https://example.org/\r\n"))
        with self.assertRaises(Refused):
            self.a.result()
    def test_oversized_content_length_denied(self):
        with self.assertRaises(Refused):
            exchange(self.a,self.payload,"response1",self.reply(b"x",length=131073))
        with self.assertRaises(Refused):
            self.a.result()
    def test_truncated_body_denied(self):
        body=self.sse()
        with self.assertRaises((Refused,http.client.HTTPException)):
            exchange(self.a,self.payload,"response1",self.reply(body,length=len(body)+10))
        with self.assertRaises(Refused):
            self.a.result()
    def test_wrong_response_identity_denied(self):
        with self.assertRaises(Refused):
            exchange(self.a,self.payload,"response1",self.reply(self.sse("old")))
        with self.assertRaises(Refused):
            self.a.result()
    def test_revoked_request_denied_before_socketpair(self):
        from unittest.mock import patch
        self.g.revoke("stream")
        with patch("socket.socketpair") as pair:
            with self.assertRaises(Refused):
                exchange(self.a,self.payload,"response1",self.reply(self.sse()))
            pair.assert_not_called()

# SPDX-License-Identifier: AGPL-3.0-only
"""Actual HTTP bytes over an owned AF_UNIX socketpair, entirely synthetic.
No TCP connect, DNS, proxy, redirect following, Jan process or real inference.
"""
import http.client
import json
import socket
from threading import Thread
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
    # Validate input and expected response identity before acquiring a socket.
    if type(payload) is not bytes:
        raise Refused("transport_input_mismatch")
    try:
        text=payload.decode("utf-8")
    except UnicodeError:
        raise Refused("invalid_fixture_utf8") from None
    stream=LocalSSE(adapter,response_id)
    adapter.begin_fixture_transport(payload)
    stream.start()
    captured=[]
    errors=[]
    try:
        client,peer=socket.socketpair()
    except OSError:
        stream.abort()
        raise
    client.settimeout(2)
    peer.settimeout(2)
    body=json.dumps({"model":adapter.call.model,"stream":True,
                     "messages":[{"role":"user","content":text}]},
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
        adapter.checkpoint_fixture_transport()
        conn.request("POST","/v1/chat/completions",body=body,headers={
            "Content-Type":"application/json","Accept":"text/event-stream","Connection":"close"})
        # Closed HTTP fixture response: bound the whole byte stream, including
        # headers, and require exact EOF. No chunking, redirects, trailers or
        # extra bytes hidden after Content-Length. This is not general HTTP.
        deadline=time.monotonic()+2
        raw=bytearray()
        while True:
            adapter.checkpoint_fixture_transport()
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise Refused("fixture_http_deadline")
            client.settimeout(remaining)
            data=client.recv(4096)
            adapter.checkpoint_fixture_transport()
            if not data:
                break
            raw.extend(data)
            if len(raw)>MAX_STREAM+8192:
                raise Refused("fixture_http_limit")
            if b"\r\n\r\n" not in raw and len(raw)>8192:
                raise Refused("fixture_http_header_limit")
        header,sep,body_bytes=bytes(raw).partition(b"\r\n\r\n")
        if not sep or len(header)>8192:
            raise Refused("fixture_http_response_denied")
        lines=header.split(b"\r\n")
        if lines[0]!=b"HTTP/1.1 200 OK":
            raise Refused("fixture_http_response_denied")
        fields={}
        for line in lines[1:]:
            name,colon,value=line.partition(b": ")
            key=name.lower()
            if (not colon or not key or key in fields
                    or key not in (b"content-type",b"content-length",b"connection")):
                raise Refused("fixture_http_response_denied")
            fields[key]=value
        length=fields.get(b"content-length",b"")
        if (fields.get(b"content-type")!=b"text/event-stream"
                or fields.get(b"connection")!=b"close" or not length.isdigit()
                or len(length)>6 or not 0<int(length)<=MAX_STREAM
                or len(body_bytes)!=int(length)):
            raise Refused("fixture_http_response_denied")
        for offset in range(0,len(body_bytes),31):
            stream.feed(body_bytes[offset:offset+31])
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

    def test_trailing_http_body_bytes_denied(self):
        body=self.sse()
        with self.assertRaises(Refused):
            exchange(self.a,self.payload,"response1",self.reply(body+b"late",length=len(body)))
        with self.assertRaises(Refused):
            self.a.result()
    def test_duplicate_length_and_transfer_encoding_denied(self):
        for extra in ("Content-Length: 1\r\n","Transfer-Encoding: chunked\r\n"):
            self.setUp()
            with self.assertRaises(Refused):
                exchange(self.a,self.payload,"response1",self.reply(self.sse(),extra=extra))
    def test_invalid_utf8_or_response_identity_never_opens_socket(self):
        from unittest.mock import patch
        with patch("socket.socketpair") as pair:
            for payload,identity in ((b"\xff","response1"),(self.payload,"bad id")):
                with self.assertRaises(Refused):
                    exchange(self.a,payload,identity,self.reply(self.sse()))
            pair.assert_not_called()
        self.assertEqual(self.a.state,"ready")

    def test_socket_setup_failure_cancels_claimed_transport(self):
        from unittest.mock import patch
        with patch("socket.socketpair",side_effect=OSError("fixture allocation")):
            with self.assertRaises(OSError):
                exchange(self.a,self.payload,"response1",self.reply(self.sse()))
        self.assertEqual(self.a.state,"cancelled")
        with self.assertRaises(Refused):
            self.a.result()
    def test_revocation_after_socket_acquisition_blocks_send(self):
        from unittest.mock import patch
        original=socket.socketpair
        def revoke():
            pair=original()
            self.g.revoke("stream")
            return pair
        with patch("socket.socketpair",side_effect=revoke),patch.object(NoConnect,"request") as send:
            with self.assertRaises(Refused):
                exchange(self.a,self.payload,"response1",self.reply(self.sse()))
            send.assert_not_called()
        self.assertEqual(self.a.state,"cancelled")
    def test_revocation_during_receive_discards_reply(self):
        from unittest.mock import patch
        original=self.a.checkpoint_fixture_transport
        calls=0
        def check():
            nonlocal calls
            calls+=1
            if calls==3:
                self.g.revoke("stream")
            return original()
        with patch.object(self.a,"checkpoint_fixture_transport",side_effect=check):
            with self.assertRaises(Refused):
                exchange(self.a,self.payload,"response1",self.reply(self.sse()))
        self.assertGreaterEqual(calls,3)
        with self.assertRaises(Refused):
            self.a.result()

    def test_wrong_input_preflight_does_not_reserve_stream_owner(self):
        with self.assertRaises(Refused):
            exchange(self.a,b"wrong","response1",self.reply(self.sse()))
        data,_=exchange(self.a,self.payload,"response1",self.reply(self.sse()))
        self.assertEqual(data,"Merhaba, München".encode())

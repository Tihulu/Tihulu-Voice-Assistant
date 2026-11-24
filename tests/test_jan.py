# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from tihulu_jarvis.voice.jan import Jan, LIMIT, body, endpoint
from tihulu_jarvis.voice.m1 import Hold, Message


class EndpointTests(unittest.TestCase):
    def test_numeric_loopback_only(self):
        self.assertEqual(str(endpoint("http://[::1]:9876/v1")[0]), "::1")
        for url in ("https://api.example.test:443/v1", "http://127.0.0.1", "http://u@127.0.0.1:99",
                    "http://localhost:9876/v1", "http://192.0.2.1:99", "http://127.0.0.1:99/v1?q=x",
                    "http://127.0.0.1:99/other", "http://[::1%25lo]:99"):
            with self.subTest(url=url), self.assertRaises(Hold):
                endpoint(url)


class JanTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_chat_over_loopback(self):
        seen = []
        async def serve(reader, writer):
            try:
                h = await reader.readuntil(b"\r\n\r\n")
                n = int(next(l.split(b":")[1] for l in h.splitlines() if l.lower().startswith(b"content-length:")))
                seen.append((h.splitlines()[0], json.loads(await reader.readexactly(n))))
                payload = json.dumps({"choices":[{"message":{"role":"assistant","content":"Merhaba!"}}]}).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" +
                             ("%x\r\n" % len(payload)).encode() + payload + b"\r\n0\r\n\r\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        async with server:
            jan = Jan("http://127.0.0.1:%s" % server.sockets[0].getsockname()[1], "fixture")
            self.assertEqual(await jan((Message("user","naber nasilsin"),)), "Merhaba!")
        self.assertEqual(seen, [(b"POST /v1/chat/completions HTTP/1.1",
                         {"model":"fixture","stream":False,"messages":[{"role":"user","content":"naber nasilsin"}]})])

    async def test_timeout_and_cancellation_do_not_retry(self):
        jan = Jan("http://127.0.0.1:9876", "fixture", timeout=.05)
        entered = asyncio.Event()
        async def stall(*args):
            entered.set()
            await asyncio.Future()
        with patch.object(jan, "_exchange", AsyncMock(side_effect=stall)) as exchange:
            with self.assertRaisesRegex(Hold,"jan_timeout"):
                await jan.models()
            entered.clear()
            task = asyncio.create_task(jan.models())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(exchange.await_count, 2)

    async def test_bad_framing_size_and_redirect(self):
        for h in (b"HTTP/1.1 302 Found\r\nLocation: https://example.test/\r\n\r\n",
                  b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nTransfer-Encoding: chunked\r\n\r\n",
                  ("HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % (LIMIT+1)).encode()):
            reader = asyncio.StreamReader()
            reader.feed_data(h)
            reader.feed_eof()
            with self.assertRaises(Hold):
                await body(reader)

    async def test_inventory_no_model_guess(self):
        jan = Jan("http://127.0.0.1:9876")
        with patch.object(jan,"_request",AsyncMock(return_value={"data":[{"id":"fixture"},{"id":"fixture"},{},None,{"id":"bad\nentry"}]})) as req:
            self.assertEqual(await jan.models(), ("fixture",))
            with self.assertRaisesRegex(Hold, "select_installed_model"):
                await jan((Message("user","hi"),))
            self.assertEqual(req.await_count, 1)


class JanStreamTests(unittest.IsolatedAsyncioTestCase):
    def events(self, *pieces, done=True):
        out = b""
        for piece in pieces:
            payload = json.dumps({"choices": [{"delta": {"content": piece}}]}).encode()
            out += b"data: " + payload + b"\r\n\r\n"
        if done:
            out += b"data: [DONE]\r\n\r\n"
        return out

    async def serve(self, chunks, status=b"HTTP/1.1 200 OK"):
        state = {"requests": []}

        async def handler(reader, writer):
            head = await reader.readuntil(b"\r\n\r\n")
            n = int(next(l.split(b":")[1] for l in head.splitlines()
                         if l.lower().startswith(b"content-length:")))
            body = await reader.readexactly(n)
            state["requests"].append(json.loads(body))
            for chunk in chunks:
                writer.write(chunk)
                await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        return server, state

    def client(self, server):
        return Jan("http://127.0.0.1:%s" % server.sockets[0].getsockname()[1], "fixture")

    async def test_a_stream_is_joined_and_progressive(self):
        head = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n"
        server, state = await self.serve([head + self.events("Merha", "ba!")])
        async with server:
            seen = []
            jan = self.client(server)
            text = await jan.chat((Message("user", "naber nasilsin"),), on_delta=seen.append)
        self.assertEqual(text, "Merhaba!")
        self.assertEqual(seen, ["Merha", "ba!"])
        # The request asks for a stream, with the same messages as a call.
        self.assertTrue(state["requests"][0]["stream"] is True)
        self.assertEqual(state["requests"][0]["messages"][0]["content"], "naber nasilsin")

    async def test_a_stream_without_done_still_ends_at_the_close(self):
        head = b"HTTP/1.1 200 OK\r\n\r\n"
        server, _ = await self.serve([head + self.events("Merhaba", "!")])
        async with server:
            self.assertEqual(await self.client(server).chat((Message("user", "hi"),), on_delta=lambda p: None),
                             "Merhaba!")

    async def test_a_non_streaming_call_is_unchanged(self):
        answer = json.dumps({"choices": [{"message": {"role": "assistant",
                                                      "content": "Merhaba!"}}]}).encode()
        server, state = await self.serve([b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n"
                                          % len(answer) + answer])
        async with server:
            self.assertEqual(await self.client(server)((Message("user", "hi"),)), "Merhaba!")
        self.assertTrue(state["requests"][0]["stream"] is False)

    async def test_a_stream_is_bounded(self):
        head = b"HTTP/1.1 200 OK\r\n\r\n"
        # More events than the cap, with no closing marker at all.
        endless = b"".join(b"data: " + json.dumps(
            {"choices": [{"delta": {"content": "x"}}]}).encode() + b"\r\n\r\n"
            for _ in range(5000))
        server, _ = await self.serve([head + endless])
        async with server:
            with self.assertRaisesRegex(Hold, "jan_stream_unbounded"):
                await self.client(server).chat((Message("user", "hi"),),
                                               on_delta=lambda p: None)
        # A reply past the character cap is refused, never truncated silently.
        big = b"".join(b"data: " + json.dumps(
            {"choices": [{"delta": {"content": "y" * 100}}]}).encode() + b"\r\n\r\n"
            for _ in range(400))
        server, _ = await self.serve([head + big])
        async with server:
            with self.assertRaisesRegex(Hold, "jan_response_too_large"):
                await self.client(server).chat((Message("user", "hi"),), on_delta=lambda p: None)

    async def test_a_stream_without_content_is_empty_not_invented(self):
        head = b"HTTP/1.1 200 OK\r\n\r\n"
        server, _ = await self.serve([head + self.events("", "", done=True)])
        async with server:
            with self.assertRaisesRegex(Hold, "jan_invalid_response"):
                await self.client(server).chat((Message("user", "hi"),), on_delta=lambda p: None)

    async def test_a_broken_event_is_a_bounded_reason(self):
        head = b"HTTP/1.1 200 OK\r\n\r\n"
        server, _ = await self.serve([head + b"data: {not json}\r\n\r\n"])
        async with server:
            with self.assertRaisesRegex(Hold, "jan_invalid_response"):
                await self.client(server).chat((Message("user", "hi"),), on_delta=lambda p: None)


class JanAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, operation):
        from types import SimpleNamespace
        allowed = True
        entered, release = asyncio.Event(), asyncio.Event()
        writes, closed = [], []
        def checkpoint():
            if not allowed:
                raise Hold("fixture_revoked")
        jan = Jan("http://127.0.0.1:9876", "fixture", checkpoint=checkpoint)
        class Writer:
            def write(self, data):
                writes.append(data)
            async def drain(self):
                pass
            def close(self):
                closed.append(True)
        async def connect():
            entered.set()
            await release.wait()
            return asyncio.StreamReader(), Writer()
        with patch.object(jan, "_connect", side_effect=connect):
            task = asyncio.create_task(operation(jan))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                allowed = False
            finally:
                release.set()
            with self.assertRaisesRegex(Hold, "fixture_revoked"):
                await asyncio.wait_for(task, 1)
        self.assertEqual(writes, [])
        self.assertEqual(closed, [True])

    async def test_revoke_while_connecting_full_chat_has_no_write(self):
        await self.exercise(lambda jan: jan((Message("user", "SYNTHETIC-CANARY"),)))

    async def test_revoke_while_connecting_stream_has_no_write(self):
        await self.exercise(lambda jan: jan.chat((Message("user", "SYNTHETIC-CANARY"),),
                                                on_delta=lambda _: None))

    async def test_revoke_while_connecting_discovery_has_no_write(self):
        await self.exercise(lambda jan: jan.models())

    async def test_denial_before_connect_has_no_connection(self):
        def deny():
            raise Hold("fixture_revoked")
        jan = Jan("http://127.0.0.1:9876", "fixture", checkpoint=deny)
        with patch.object(jan, "_connect") as connect:
            with self.assertRaisesRegex(Hold, "fixture_revoked"):
                await jan.models()
            connect.assert_not_called()


class JanFramingTests(unittest.IsolatedAsyncioTestCase):
    def reader(self, data):
        reader = asyncio.StreamReader(limit=16384)
        reader.feed_data(data)
        reader.feed_eof()
        return reader

    def event(self):
        return b'data: {"choices":[{"delta":{"content":"Ready."}}]}\n\ndata: [DONE]\n\n'

    async def parse(self, headers, data):
        client = Jan("http://127.0.0.1:9876", "fixture")
        seen = []
        result = await client._stream_body(
            self.reader(b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + data), seen.append)
        return result, seen

    async def test_stream_header_budget_and_count(self):
        for header in ((b"X-fixture: " + b"x" * 1000 + b"\r\n") * 32,
                       b"X: y\r\n" * 129):
            with self.subTest(size=len(header)), self.assertRaisesRegex(Hold, "jan_invalid_http"):
                await self.parse(header, self.event())

    async def test_stream_conflicting_or_duplicate_framing(self):
        for header in (b"Content-Length: 1\r\nTransfer-Encoding: chunked\r\n",
                       b"Content-Length: 1\r\nContent-Length: 1\r\n",
                       b"Transfer-Encoding: gzip\r\n",
                       b"Content-Encoding: gzip\r\n"):
            with self.subTest(header=header), self.assertRaisesRegex(Hold, "jan_invalid_http"):
                await self.parse(header, self.event())

    async def test_stream_malformed_delta_types_are_bounded(self):
        for delta in (None, [], 7, {"content": []}):
            raw = b"data: " + json.dumps({"choices": [{"delta": delta}]}).encode() + b"\n"
            with self.subTest(delta=delta), self.assertRaisesRegex(Hold, "jan_invalid_response"):
                await self.parse(b"", raw)

    async def test_chunked_stream_reassembles_split_events(self):
        data = self.event()
        chunks = [data[:5], data[5:17], data[17:]]
        wire = b"".join(("%x\r\n" % len(c)).encode() + c + b"\r\n" for c in chunks)
        self.assertEqual(await self.parse(b"Transfer-Encoding: chunked\r\n",
                                          wire + b"0\r\n\r\n"), ("Ready.", ["Ready."]))

    async def test_fixed_length_and_close_delimited_lf_streams(self):
        data = self.event()
        self.assertEqual(await self.parse(b"Content-Length: %d\r\n" % len(data), data),
                         ("Ready.", ["Ready."]))
        self.assertEqual(await self.parse(b"", data), ("Ready.", ["Ready."]))

    async def test_incomplete_frames_and_missing_done_refused(self):
        data = self.event()
        with self.assertRaisesRegex(Hold, "jan_invalid_http"):
            await self.parse(b"Content-Length: %d\r\n" % (len(data)+1), data)
        with self.assertRaisesRegex(Hold, "jan_invalid_response"):
            await self.parse(b"", data.split(b"data: [DONE]")[0])
        wire = ("%x\r\n" % len(data)).encode() + data + b"XX"
        with self.assertRaisesRegex(Hold, "jan_invalid_http"):
            await self.parse(b"Transfer-Encoding: chunked\r\n", wire)


class JanActiveRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, stream=False, cancel=False):
        allowed = True
        written, closed = asyncio.Event(), asyncio.Event()
        seen, writes = [], []
        def checkpoint():
            if not allowed:
                raise Hold("fixture_revoked")
        reader = asyncio.StreamReader()
        class Writer:
            def write(self, data):
                writes.append(data)
                written.set()
            async def drain(self):
                pass
            def close(self):
                closed.set()
        client = Jan("http://127.0.0.1:9876", "fixture", timeout=5, checkpoint=checkpoint)
        async def connect():
            return reader, Writer()
        from tihulu_jarvis.voice import jan as transport
        with patch.object(client, "_connect", side_effect=connect), patch.object(transport, "AUTH_POLL", .01):
            task = asyncio.create_task(client.chat((Message("user", "SYNTHETIC"),),
                                                   on_delta=seen.append if stream else None))
            try:
                await asyncio.wait_for(written.wait(), 1)
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 1)
                else:
                    allowed = False
                    with self.assertRaisesRegex(Hold, "fixture_revoked"):
                        await asyncio.wait_for(task, 1)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(closed.is_set())
        self.assertEqual(len(writes), 1)  # Already written bytes cannot be recalled.
        self.assertEqual(seen, [])
        self.assertEqual(client._slots._value, 2)

    async def test_full_response_stall_revocation_closes_transport(self):
        await self.exercise()

    async def test_stream_stall_revocation_closes_transport(self):
        await self.exercise(stream=True)

    async def test_cancel_stalled_stream_closes_transport(self):
        await self.exercise(stream=True, cancel=True)

    async def test_revocation_waiting_for_slot_does_not_connect(self):
        allowed = True
        def checkpoint():
            if not allowed:
                raise Hold("fixture_revoked")
        client = Jan("http://127.0.0.1:9876", "fixture", checkpoint=checkpoint)
        await client._slots.acquire()
        await client._slots.acquire()
        from tihulu_jarvis.voice import jan as transport
        try:
            with patch.object(client, "_connect") as connect, patch.object(transport, "AUTH_POLL", .01):
                task = asyncio.create_task(client.models())
                await asyncio.sleep(.02)
                allowed = False
                with self.assertRaisesRegex(Hold, "fixture_revoked"):
                    await asyncio.wait_for(task, 1)
                connect.assert_not_called()
        finally:
            client._slots.release()
            client._slots.release()
        self.assertEqual(client._slots._value, 2)

    async def test_unrepresentable_stream_content_never_published(self):
        for value in ("\ud800", "a\x00b"):
            reader = asyncio.StreamReader()
            reader.feed_data(b"HTTP/1.1 200 OK\r\n\r\n" + b"data: " +
                             json.dumps({"choices": [{"delta": {"content": value}}]}).encode() +
                             b"\n\ndata: [DONE]\n\n")
            reader.feed_eof()
            seen = []
            with self.assertRaisesRegex(Hold, "jan_invalid_response"):
                await Jan("http://127.0.0.1:9876")._stream_body(reader, seen.append)
            self.assertEqual(seen, [])

    async def test_unrepresentable_full_content_rejected(self):
        client = Jan("http://127.0.0.1:9876", "fixture")
        for value in ("\ud800", "a\x00b"):
            answer = {"choices": [{"message": {"role": "assistant", "content": value}}]}
            with patch.object(client, "_request", AsyncMock(return_value=answer)):
                with self.assertRaisesRegex(Hold, "jan_invalid_response"):
                    await client((Message("user", "fixture"),))

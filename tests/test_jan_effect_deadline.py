# SPDX-License-Identifier: AGPL-3.0-only
"""Reject new Jan effects after an observable monotonic operation deadline."""
import asyncio
import json
import unittest
from unittest.mock import patch
from tihulu_jarvis.voice.jan import Jan
from tihulu_jarvis.voice.m1 import Hold, Message


class JanEffectDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, mode, late=None, *, revoke=False):
        # Move the event-loop clock forward at a controlled boundary instead
        # of depending on a scheduler race or a millisecond wall-clock sleep.
        loop = asyncio.get_running_loop()
        original_time = loop.time
        offset = 0
        allowed = True
        writes, deltas, closed, connects = [], [], [], []
        def expire():
            nonlocal offset, allowed
            offset += 20
            if revoke:
                allowed = False
        def checkpoint():
            if not allowed:
                raise Hold("fixture_revoked")
        if mode == "stream":
            payload = b"".join(b"data: " + json.dumps(
                {"choices": [{"delta": {"content": piece}}]}).encode() + b"\n\n"
                for piece in ("First", " second")) + b"data: [DONE]\n\n"
        elif mode == "models":
            payload = json.dumps({"data": [{"id": "fixture"}]}).encode()
        else:
            payload = json.dumps({"choices": [{"message": {
                "role": "assistant", "content": "First second"}}]}).encode()
        class Reader(asyncio.StreamReader):
            async def read(self, n=-1):
                if late == "read":
                    expire()
                return await super().read(n)
        class Writer:
            def write(self, data):
                writes.append(data)
            async def drain(self):
                if late == "drain":
                    expire()
            def close(self):
                closed.append(True)
        async def connect():
            connects.append(True)
            reader = Reader()
            reader.feed_data(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n"
                             % len(payload) + payload)
            reader.feed_eof()
            if late == "connect":
                expire()
            return reader, Writer()
        def publish(piece):
            deltas.append(piece)
            if late == "callback":
                expire()
        client = Jan("http://127.0.0.1:1", "fixture", timeout=10,
                     checkpoint=checkpoint)
        async def request():
            if mode == "models":
                return await client.models()
            return await client.chat((Message("user", "synthetic"),),
                                     on_delta=publish if mode == "stream" else None)
        with patch.object(loop, "time", side_effect=lambda: original_time() + offset), \
             patch.object(client, "_connect", side_effect=connect):
            if late:
                with self.assertRaisesRegex(Hold, "fixture_revoked" if revoke else "jan_timeout"):
                    await request()
            else:
                result = await request()
                self.assertEqual(result, ("fixture",) if mode == "models" else "First second")
        self.assertEqual(connects, [True])
        self.assertEqual(closed, [True])
        self.assertEqual(client._slots._value, 2)
        self.assertEqual(len(writes), 0 if late == "connect" else 1)
        expected = [] if mode != "stream" or late in ("connect", "drain", "read") else (
            ["First"] if late == "callback" else ["First", " second"])
        self.assertEqual(deltas, expected)

    async def test_full_late_connect_no_write(self):
        await self.exercise("full", "connect")

    async def test_stream_late_connect_no_write_or_delta(self):
        await self.exercise("stream", "connect")

    async def test_discovery_late_connect_no_write(self):
        await self.exercise("models", "connect")

    async def test_stream_late_drain_no_delta(self):
        await self.exercise("stream", "drain")

    async def test_stream_late_read_no_delta(self):
        await self.exercise("stream", "read")

    async def test_stream_callback_crosses_deadline_no_next_delta(self):
        await self.exercise("stream", "callback")

    async def test_revocation_priority_no_write(self):
        await self.exercise("full", "connect", revoke=True)

    async def test_revocation_priority_no_delta(self):
        await self.exercise("stream", "read", revoke=True)

    async def test_full_authorized_before_deadline(self):
        await self.exercise("full")

    async def test_stream_authorized_before_deadline(self):
        await self.exercise("stream")

    async def test_discovery_authorized_before_deadline(self):
        await self.exercise("models")

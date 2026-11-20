# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Loopback transport tests. The server is a fixture on this machine only."""
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

from tihulu_jarvis.market import transport_for
from tihulu_jarvis.transfer_http import loopback_fetch
from tihulu_jarvis.voice.m1 import Hold

PAYLOAD = b"synthetic weights fixture over loopback\n"


async def serve(reader, writer, body=PAYLOAD, status=b"HTTP/1.1 200 OK", extra=b""):
    try:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(status + b"\r\nContent-Length: " + str(len(body)).encode()
                     + b"\r\nContent-Type: application/octet-stream\r\n" + extra + b"\r\n" + body)
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def serve_on(self, **kw):
        handler = kw.pop("handler", serve)
        async def route(reader, writer):
            await handler(reader, writer, **kw)
        server = await asyncio.start_server(route, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        return "http://127.0.0.1:%d/weights.bin" % port, server

    async def fetch_into(self, url, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "weights.bin"
            written = await loopback_fetch(url, **kw)(str(target), lambda count: None)
            return written, target.read_bytes()

    async def test_approved_loopback_source_transfers_the_body(self):
        url, server = await self.serve_on()
        async with server:
            written, body = await self.fetch_into(url)
            self.assertEqual(written, len(PAYLOAD))
            self.assertEqual(body, PAYLOAD)

    async def test_progress_reports_the_written_bytes(self):
        url, server = await self.serve_on()
        async with server:
            seen = []
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "weights.bin"
                await loopback_fetch(url)(str(target), seen.append)
            self.assertEqual(seen, [len(PAYLOAD)])

    def test_non_loopback_sources_have_no_transport(self):
        for ref in ("https://provider.example/weights.bin", "http://localhost:8000/w.bin",
                    "http://10.0.0.5:8000/w.bin", "http://127.0.0.1:8000/w.bin?token=x",
                    "catalog:fixture", "", "/home/owner/weights.bin", "http://127.0.0.1:8000"):
            with self.subTest(ref=ref):
                self.assertIsNone(transport_for(ref))

    async def test_loopback_reference_builds_a_transport(self):
        url, server = await self.serve_on()
        async with server:
            self.assertTrue(callable(loopback_fetch(url)))
        for bad in ("http://127.0.0.1:8000/../../etc/passwd", "http://127.0.0.1:8000/a b",
                    "http://user:pw@127.0.0.1:8000/w.bin", "ftp://127.0.0.1:8000/w.bin",
                    "http://[::1]:8000/w.bin%2f.."):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(Hold, "transport_endpoint_not_approved"):
                    loopback_fetch(bad)

    async def test_redirect_is_refused_and_never_followed(self):
        url, server = await self.serve_on(extra=b"Location: https://example.test/\r\n")
        async with server:
            with self.assertRaisesRegex(Hold, "transfer_redirect_or_stream_rejected"):
                await self.fetch_into(url)

    async def test_non_200_status_is_refused(self):
        url, server = await self.serve_on(status=b"HTTP/1.1 302 Found")
        async with server:
            with self.assertRaisesRegex(Hold, "transfer_rejected_by_source"):
                await self.fetch_into(url)

    async def test_a_closed_port_is_a_bounded_reason(self):
        url, server = await self.serve_on()
        server.close()
        await server.wait_closed()
        with self.assertRaisesRegex(Hold, "transfer_unavailable"):
            await self.fetch_into(url)

    async def test_a_body_larger_than_the_limit_is_refused(self):
        url, server = await self.serve_on()
        async with server:
            with self.assertRaisesRegex(Hold, "transfer_too_large"):
                await self.fetch_into(url, limit=4)

    async def test_digest_of_the_transferred_body_matches_the_payload(self):
        url, server = await self.serve_on()
        async with server:
            written, body = await self.fetch_into(url)
            self.assertEqual(hashlib.sha256(body).hexdigest(),
                             hashlib.sha256(PAYLOAD).hexdigest())


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    async def serve_on(self, ranges=True):
        state = {"served": 0}

        async def route(reader, writer):
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                line = request.split(b"\r\n", 1)[0]
                want = 0
                for field in request.split(b"\r\n"):
                    if field.lower().startswith(b"range:"):
                        want = int(field.split(b"=")[1].split(b"-")[0])
                honoured = want and ranges
                body = PAYLOAD[want:] if honoured else PAYLOAD
                status = b"HTTP/1.1 206 Partial Content" if honoured else b"HTTP/1.1 200 OK"
                extra = (b"Content-Range: bytes %d-%d/%d\r\n"
                         % (want, len(PAYLOAD) - 1, len(PAYLOAD))) if want and ranges else b""
                writer.write(status + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n"
                             + extra + b"Content-Type: application/octet-stream\r\n\r\n" + body)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(route, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        return "http://127.0.0.1:%d/weights.bin" % port, server

    async def test_a_resumed_transfer_reassembles_the_payload(self):
        url, server = await self.serve_on()
        async with server:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "weights.bin"
                seen = []
                first = await loopback_fetch(url)(str(target), seen.append, start=0)
                partial = target.read_bytes()
                with open(target, "wb") as f:
                    f.write(partial[:5])
                written = await loopback_fetch(url)(str(target), seen.append, start=5)
                self.assertEqual(written, len(PAYLOAD))
                self.assertEqual(target.read_bytes(), PAYLOAD)
                self.assertEqual(seen[-1], len(PAYLOAD))

    async def test_a_source_without_range_restarts_from_zero(self):
        url, server = await self.serve_on(ranges=False)
        async with server:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "weights.bin"
                with open(target, "wb") as f:
                    f.write(PAYLOAD[:5])
                written = await loopback_fetch(url)(str(target), lambda count: None, start=5)
                self.assertEqual(written, len(PAYLOAD))
                self.assertEqual(target.read_bytes(), PAYLOAD)

    async def test_a_range_mismatch_is_refused(self):
        async def wrong_range(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                body = PAYLOAD[9:]
                writer.write(b"HTTP/1.1 206 Partial Content\r\nContent-Length: "
                             + str(len(body)).encode() + b"\r\nContent-Range: bytes 9"
                             + b"-%d/%d\r\nContent-Type: application/octet-stream\r\n\r\n"
                             % (len(PAYLOAD) - 1, len(PAYLOAD)) + body)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(wrong_range, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "weights.bin"
                with open(target, "wb") as f:
                    f.write(PAYLOAD[:5])
                with self.assertRaisesRegex(Hold, "transfer_range_not_honoured"):
                    await loopback_fetch("http://127.0.0.1:%d/weights.bin" % port)(
                        str(target), lambda count: None, start=5)

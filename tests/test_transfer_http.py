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

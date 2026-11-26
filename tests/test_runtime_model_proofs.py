# SPDX-License-Identifier: AGPL-3.0-only
"""Registered audio model reference verification and cache ownership."""
import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from tihulu_jarvis import market
from tihulu_jarvis.voice.m1 import Hold


class RuntimeModelProofTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "weights.bin"
        self.path.write_bytes(b"original")
        self.card = market.from_file(str(self.path), "stt")
        self.refs = {self.card.storage_ref: str(self.path)}
        self.cache = market.RuntimeModelProofs()

    async def bind(self):
        return await self.cache.bind(str(self.path), self.refs, checkpoint=lambda: None)

    async def test_verified_identity_is_cached_and_rechecked(self):
        with patch.object(market, "inspect_file", wraps=market.inspect_file) as inspect:
            guard = await self.bind()
            guard()
            await self.bind()
            inspect.assert_called_once()
            self.path.write_bytes(b"modified")
            with self.assertRaisesRegex(Hold, "model_file_changed"):
                guard()
            with self.assertRaisesRegex(Hold, "model_digest_mismatch"):
                await self.bind()
            self.assertEqual(inspect.call_count, 2)

    async def test_changed_content_before_first_use_refused(self):
        self.path.write_bytes(b"modified")
        with self.assertRaisesRegex(Hold, "model_digest_mismatch"):
            await self.bind()

    async def test_identical_replacement_requires_fresh_hash(self):
        await self.bind()
        replacement = self.path.with_suffix(".new")
        replacement.write_bytes(self.path.read_bytes())
        replacement.replace(self.path)
        with patch.object(market, "inspect_file", wraps=market.inspect_file) as inspect:
            await self.bind()
            inspect.assert_called_once()

    async def test_ambiguous_reference_refused(self):
        self.refs["sha256-" + "0" * 32] = str(self.path)
        with self.assertRaisesRegex(Hold, "model_reference_unverified"):
            await self.bind()

    async def test_legacy_path_retains_original_checkpoint(self):
        checkpoint = lambda: None
        self.assertIs(await self.cache.bind(str(self.path), {}, checkpoint=checkpoint), checkpoint)

    async def test_cancel_drains_worker_before_return(self):
        entered, release, drained = threading.Event(), threading.Event(), threading.Event()
        def inspect(*args, **kw):
            entered.set()
            try:
                if not release.wait(5):
                    raise AssertionError("fixture deadline")
                kw["checkpoint"]()
            finally:
                drained.set()
        with patch.object(market, "inspect_file", side_effect=inspect):
            task = asyncio.create_task(self.bind())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                task.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(drained.is_set())
        self.assertEqual(self.cache._proofs, {})

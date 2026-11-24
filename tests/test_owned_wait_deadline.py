# SPDX-License-Identifier: AGPL-3.0-only
"""Owned waits must not start or return work past their monotonic deadline."""
import asyncio
import time
import unittest
from tihulu_jarvis.owned_wait import await_authorized


class Denied(Exception):
    pass


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_loop_late_result_is_rejected(self):
        async def factory():
            time.sleep(.04)
            return 'late'
        with self.assertRaises(asyncio.TimeoutError):
            await await_authorized(factory, .005, lambda: None)

    async def test_expired_at_factory_start_prevents_effect(self):
        calls = 0
        started = []
        def checkpoint():
            nonlocal calls
            calls += 1
            if calls == 3:
                time.sleep(.04)
        async def factory():
            started.append(True)
            return 7
        with self.assertRaises(asyncio.TimeoutError):
            await await_authorized(factory, .005, checkpoint)
        self.assertEqual(started, [])

    async def test_yielding_factory_timeout_drains(self):
        done = []
        async def factory():
            try:
                await asyncio.sleep(1)
            finally:
                done.append(True)
        with self.assertRaises(asyncio.TimeoutError):
            await await_authorized(factory, .01, lambda: None)
        self.assertEqual(done, [True])

    async def test_fast_result(self):
        async def factory():
            return 7
        self.assertEqual(await await_authorized(factory, 1, lambda: None), 7)

    async def test_revocation_discards_result(self):
        allowed = True
        def checkpoint():
            if not allowed:
                raise Denied
        async def factory():
            nonlocal allowed
            allowed = False
            return 7
        with self.assertRaises(Denied):
            await await_authorized(factory, 1, checkpoint)

    async def test_external_cancel_drains(self):
        entered = asyncio.Event()
        done = []
        async def factory():
            entered.set()
            try:
                await asyncio.sleep(1)
            finally:
                done.append(True)
        task = asyncio.create_task(await_authorized(factory, 2, lambda: None))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(done, [True])

    async def test_factory_exception_preserved(self):
        async def factory():
            raise ValueError('fixture')
        with self.assertRaisesRegex(ValueError, 'fixture'):
            await await_authorized(factory, 1, lambda: None)

    async def test_initial_denial_prevents_factory(self):
        called = []
        async def factory():
            called.append(True)
        def checkpoint():
            raise Denied
        with self.assertRaises(Denied):
            await await_authorized(factory, 1, checkpoint)
        self.assertEqual(called, [])

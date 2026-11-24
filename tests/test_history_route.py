# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic history canary; no network or owner data."""
import unittest

from tihulu_jarvis.voice.session import Session
from tihulu_jarvis.voice.m1 import Hold

A = ("local-a", 1, "LOCAL_ONLY", "loopback-A", "model-A")
CANARY = "SYNTHETIC-PRIVATE-CASE-9E37"


class RouteBindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = Session()
        self.requests = []

    async def backend(self, messages):
        self.requests.append(tuple(messages))
        return "The note is ready."

    async def reply(self, text, route, **kwargs):
        return await self.session.own(lambda epoch: self.session.reply(
            text, self.backend, epoch, route=route, **kwargs))

    async def seed(self):
        await self.reply("Please summarize the following English note.\n"
                         "[attached file: note.txt]\n" + CANARY +
                         "\n[end of attached file]", A)
        self.assertTrue(any(CANARY in m.content for m in self.session.history))

    async def test_same_route_keeps_followup_context(self):
        await self.seed()
        await self.reply("What does the note say?", A)
        self.assertTrue(any(CANARY in m.content for m in self.requests[-1]))

    async def test_each_changed_route_component_denies_before_backend(self):
        await self.seed()
        for index, replacement in enumerate(("local-b", 2, "CLOUD_CHAT", "loopback-B", "model-B")):
            route = list(A)
            route[index] = replacement
            with self.subTest(index=index):
                with self.assertRaisesRegex(Hold, "^conversation_route_changed_end_conversation$"):
                    await self.reply("What is the weather?", tuple(route))
                self.assertEqual(len(self.requests), 1)
        self.assertTrue(any(CANARY in m.content for m in self.session.history))

    async def test_unbound_call_cannot_bypass_bound_history(self):
        await self.seed()
        with self.assertRaisesRegex(Hold, "conversation_route_changed_end_conversation"):
            await self.reply("What is the weather?", None)
        self.assertEqual(len(self.requests), 1)

    async def test_cancel_without_clear_does_not_drop_route_binding(self):
        await self.seed()
        self.session.invalidate(clear=False)
        with self.assertRaisesRegex(Hold, "conversation_route_changed_end_conversation"):
            await self.reply("What is the weather?", (*A[:-1], "model-B"))
        self.assertEqual(len(self.requests), 1)

    async def test_end_conversation_permits_new_route_without_old_canary(self):
        await self.seed()
        self.session.invalidate(clear=True)
        await self.reply("What is the weather?", (*A[:-1], "model-B"))
        self.assertFalse(any(CANARY in m.content for m in self.requests[-1]))

# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Lease tests: no device, GPU or process is touched."""
import unittest

from tihulu_jarvis.locks import RESOURCES, Leases
from tihulu_jarvis.voice.m1 import Hold


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.leases = Leases()

    def test_one_holder_per_resource(self):
        self.leases.acquire("microphone", "turn-1")
        self.assertEqual(self.leases.owner("microphone"), "turn-1")
        with self.assertRaisesRegex(Hold, "resource_busy:microphone"):
            self.leases.acquire("microphone", "turn-2")
        # The same holder may re-acquire its own lease.
        self.leases.acquire("microphone", "turn-1")
        self.assertTrue(self.leases.release("microphone", "turn-1"))
        self.assertFalse(self.leases.release("microphone", "turn-1"))
        self.leases.acquire("microphone", "turn-2")

    def test_only_the_holder_may_release(self):
        self.leases.acquire("audio_playback", "turn-1")
        with self.assertRaisesRegex(Hold, "lease_not_owned:audio_playback"):
            self.leases.release("audio_playback", "turn-2")
        self.leases.release("audio_playback", "turn-1")

    def test_unknown_resources_and_holders_are_refused(self):
        for resource in ("speaker", "", None):
            with self.subTest(resource=resource):
                with self.assertRaisesRegex(Hold, "unknown_resource"):
                    self.leases.acquire(resource, "turn-1")
        for holder in ("", None):
            with self.subTest(holder=holder):
                with self.assertRaisesRegex(Hold, "invalid_lease_holder"):
                    self.leases.acquire("gpu", holder)

    def test_the_exclusive_resources_are_the_hardware_ones(self):
        self.assertEqual(RESOURCES, ("microphone", "audio_playback", "gpu", "worktree", "browser"))
        self.assertEqual(self.leases.held(), {})


class LeaseContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_turn_releases_its_lease(self):
        leases = Leases()
        with self.assertRaises(RuntimeError):
            async with leases.hold("gpu", "task-a"):
                self.assertEqual(leases.held()["gpu"], "task-a")
                with self.assertRaisesRegex(Hold, "resource_busy:gpu"):
                    leases.acquire("gpu", "task-b")
                raise RuntimeError("task failed")
        self.assertIsNone(leases.owner("gpu"))
        leases.acquire("gpu", "task-b")

    async def test_a_release_by_another_owner_is_refused(self):
        leases = Leases()
        async with leases.hold("microphone", "turn-1"):
            with self.assertRaisesRegex(Hold, "lease_not_owned:microphone"):
                leases.release("microphone", "turn-2")
        self.assertIsNone(leases.owner("microphone"))

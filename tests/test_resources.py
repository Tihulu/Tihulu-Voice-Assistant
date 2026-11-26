# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Resource-profile tests: no process is started and nothing is measured."""
import unittest

from tihulu_jarvis import resources
from tihulu_jarvis.voice.m1 import Hold


class ProfileTests(unittest.TestCase):
    def test_the_four_profiles_and_their_defaults(self):
        self.assertEqual(resources.PROFILE_IDS,
                         ("performance", "balance", "resource_optimized", "custom"))
        self.assertEqual(resources.IDLE_RELEASE["performance"], 0)     # never releases
        self.assertEqual(resources.IDLE_RELEASE["resource_optimized"], 15)
        self.assertTrue(resources.preset("performance").keep_warm)
        self.assertFalse(resources.preset("balance").keep_warm)

    def test_threads_follow_the_profile_on_this_machine(self):
        balance = resources.preset("balance")
        self.assertEqual(balance.threads_for(8), 6)          # 0.75 of 8
        self.assertEqual(resources.preset("performance").threads_for(8), 8)
        self.assertEqual(resources.preset("resource_optimized").threads_for(8), 4)
        self.assertEqual(balance.threads_for(1), 1)          # never zero
        tight = resources.preset("performance")
        tight = resources.replace(tight, threads=3)
        self.assertEqual(tight.threads_for(8), 3)            # an explicit cap wins

    def test_thread_counts_are_bounded_by_affinity_and_missing_affinity_is_safe(self):
        profile = resources.effective({"profile": "custom", "threads": 256})
        self.assertEqual(profile.threads_for(2), 2)
        self.assertEqual(profile.threads_for(None), 4)
        self.assertEqual(resources.preset("performance").threads_for(1024), 256)
        self.assertEqual(resources.preset("balance").threads_for(None), 3)

    def test_the_environment_is_bounded_and_names_the_device(self):
        quiet = resources.environment(resources.preset("balance"), 8)
        self.assertEqual(quiet["OMP_NUM_THREADS"], "6")
        self.assertNotIn("TIHULU_DEVICE", quiet)             # auto is not a choice
        gpu = resources.environment(resources.preset("performance"), 8)
        self.assertEqual(gpu["TIHULU_DEVICE"], "cuda")
        self.assertEqual(resources.environment(resources.preset("resource_optimized"), 8),
                         {"OMP_NUM_THREADS": "4", "TIHULU_THREADS": "4"})


class CustomTests(unittest.TestCase):
    def test_a_custom_profile_carries_only_its_own_overrides(self):
        profile = resources.effective({"profile": "custom", "idle_release": 5,
                                       "keep_warm": True, "gpu": "cuda"})
        self.assertEqual((profile.id, profile.idle_release, profile.keep_warm, profile.gpu),
                         ("custom", 5, True, "cuda"))
        self.assertEqual(profile.cpu_share, resources.preset("balance").cpu_share)

    def test_invalid_stored_overrides_and_boolean_numbers_are_refused(self):
        with self.assertRaises(Hold):
            resources.stored("balance", {"threads": 3})
        for key in ("threads", "idle_release", "cpu_share"):
            with self.subTest(key=key), self.assertRaises(Hold):
                resources.effective({"profile": "custom", key: True})
        with self.assertRaises(Hold):
            resources.preset([])

    def test_an_unknown_or_impossible_profile_is_refused(self):
        for bad in ("huge", "", None, 3, "Balance"):
            with self.subTest(bad=bad):
                with self.assertRaises(Hold):
                    resources.preset(bad)
        for overrides in ({"idle_release": -1}, {"cpu_share": 4}, {"gpu": "tpu"},
                          {"threads": 999}, {"turbo": True}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(Hold):
                    resources.effective({"profile": "custom", **overrides})
        with self.assertRaises(Hold):
            resources.effective({"profile": "performance", "gpu": "cuda"})

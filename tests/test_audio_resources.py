# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Resource-profile handover tests: no local engine is started."""
import unittest

from tihulu_jarvis import resources
from tihulu_jarvis.voice import audio


class ConfigureTests(unittest.TestCase):
    def tearDown(self):
        audio.configure(None)

    def test_without_a_profile_nothing_is_set_on_the_engines_behalf(self):
        audio.configure(None)
        self.assertEqual(audio.spawn_environment(), {})

    def test_a_profile_decides_threads_and_device_only(self):
        audio.configure(resources.preset("performance"))
        env = audio.spawn_environment()
        self.assertEqual(env["OMP_NUM_THREADS"], str(resources.cores() or 1))
        self.assertEqual(env["TIHULU_DEVICE"], "cuda")
        audio.configure(resources.preset("balance"))
        self.assertNotIn("TIHULU_DEVICE", audio.spawn_environment())

    def test_a_custom_profile_carries_its_own_settings(self):
        profile = resources.effective({"profile": "custom", "threads": 3,
                                       "gpu": "vulkan"})
        audio.configure(profile)
        self.assertEqual(audio.spawn_environment(),
                         {"OMP_NUM_THREADS": "3", "TIHULU_THREADS": "3",
                          "TIHULU_DEVICE": "vulkan"})

    def test_the_executable_and_flags_are_never_selected_by_a_profile(self):
        import inspect
        source = inspect.getsource(audio.child)
        self.assertIn("shutil.which(name)", source)
        self.assertNotIn("PROFILE", source.split("binary = ")[1].split("spawn =")[0])

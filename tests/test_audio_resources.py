# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Resource-profile handover tests: no local engine is started."""
import unittest
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import patch

from tihulu_jarvis import resources
from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.m1 import Hold


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

    def test_fixed_whisper_flags_follow_cpu_and_thread_choice(self):
        self.assertEqual(audio.whisper_resource_args(None, 8), ())
        profile = resources.effective({"profile": "custom", "threads": 3, "gpu": "cpu"})
        self.assertEqual(audio.whisper_resource_args(profile, 8), ("-t", "3", "-ng"))
        for gpu in ("auto", "cuda", "vulkan"):
            profile = resources.effective({"profile": "custom", "threads": 3, "gpu": gpu})
            self.assertEqual(audio.whisper_resource_args(profile, 8), ("-t", "3"))

    def test_the_executable_and_flags_are_never_selected_by_a_profile(self):
        import inspect
        source = inspect.getsource(audio.child)
        self.assertIn("shutil.which(name)", source)
        self.assertNotIn("PROFILE", source.split("binary = ")[1].split("spawn =")[0])


class WhisperResourceDispatchTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        audio.configure(None)

    async def test_cpu_and_thread_options_reach_the_real_adapter(self):
        audio.configure(resources.effective({"profile": "custom", "gpu": "cpu", "threads": 3}))
        observed = []

        @asynccontextmanager
        async def child(name, *args, **kwargs):
            observed.append((name, args))
            class Process:
                async def wait(self):
                    return 0
            yield Process()

        with patch.object(audio, "child", child), patch.object(audio, "tools_ready", return_value=True), \
             patch.object(resources, "cores", return_value=8):
            await audio._whisper(Path("/fixture/model.bin"), Path("/fixture/input.wav"),
                                 Path("/fixture/output"), checkpoint=lambda: None)
        self.assertEqual(observed, [("whisper-cli", ("-m", Path("/fixture/model.bin"),
            "-f", Path("/fixture/input.wav"), "-l", "auto", "-t", "3", "-ng",
            "-otxt", "-of", Path("/fixture/output")))])


    async def test_explicit_input_language_reaches_whisper_and_invalid_is_refused(self):
        observed = []
        @asynccontextmanager
        async def child(name, *args, **kwargs):
            observed.append(args[args.index("-l") + 1])
            class Process:
                async def wait(self):
                    return 0
            yield Process()
        with patch.object(audio, "child", child), patch.object(audio, "tools_ready", return_value=True):
            for lang in ("auto", "tr", "en", "de"):
                await audio._whisper(Path("/fixture/model.bin"), Path("/fixture/input.wav"),
                                     Path("/fixture/output"), checkpoint=lambda: None, language=lang)
            for bad in ("th", "--translate", None):
                with self.assertRaisesRegex(Hold, "invalid_stt_language"):
                    await audio._whisper(Path("/fixture/model.bin"), Path("/fixture/input.wav"),
                                         Path("/fixture/output"), checkpoint=lambda: None, language=bad)
        self.assertEqual(observed, ["auto", "tr", "en", "de"])

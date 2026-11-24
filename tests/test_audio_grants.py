# SPDX-License-Identifier: AGPL-3.0-only
"""Audio substage authorization: real control flow, fake devices and children."""
import asyncio
import struct
import tempfile
import unittest
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.m1 import Hold, SpeechRequest


class AudioGrantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.model = self.root / "fixture.bin"
        self.model.touch()
        self.voice = self.root / "fixture.onnx"
        self.voice.touch()
        self.voice.with_name("fixture.onnx.json").write_text('{"language":{"code":"en"}}')
        self.allowed = True
        self.calls = []

    def check(self):
        if not self.allowed:
            raise Hold("approval_expired")

    def wav(self, path):
        with wave.open(str(path), "wb") as out:
            out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            out.writeframes(struct.pack("<hh", 1250, -1250) * 1600)

    async def test_capture_to_whisper_revocation_refuses_new_child(self):
        owner = self
        @asynccontextmanager
        async def child(name, *args, **kw):
            owner.calls.append(name)
            owner.wav(args[-1])
            class Process:
                returncode = 0
                async def wait(self):
                    return 0
            yield Process()
            owner.allowed = False
        with patch.object(audio, "child", child), patch.object(audio, "tools_ready", return_value=True):
            with self.assertRaisesRegex(Hold, "approval_expired"):
                await audio.listen(str(self.model), asyncio.Event(), lambda _: None, checkpoint=self.check)
        self.assertEqual(self.calls, ["arecord"])

    async def test_piper_to_aplay_revocation_refuses_new_child(self):
        owner = self
        @asynccontextmanager
        async def child(name, *args, **kw):
            owner.calls.append(name)
            owner.wav(args[-1])
            class Process:
                returncode = 0
                async def communicate(self, data):
                    return b"", b""
            yield Process()
            owner.allowed = False
        with patch.object(audio, "child", child), patch.object(audio, "tools_ready", return_value=True):
            with self.assertRaisesRegex(Hold, "approval_expired"):
                await audio.speak(SpeechRequest("Hello", "en", str(self.voice)), lambda _: None, checkpoint=self.check)
        self.assertEqual(self.calls, ["piper"])

    async def test_active_wait_revocation_drains_owned_task(self):
        entered, drained = asyncio.Event(), asyncio.Event()
        async def work():
            try:
                entered.set()
                await asyncio.Future()
            finally:
                drained.set()
        with patch.object(audio, "AUTH_POLL", .01):
            task = asyncio.create_task(audio.await_authorized(work, 2, self.check))
            await asyncio.wait_for(entered.wait(), 1)
            self.allowed = False
            with self.assertRaisesRegex(Hold, "approval_expired"):
                await asyncio.wait_for(task, 1)
        self.assertTrue(drained.is_set())

    async def test_refused_selected_recording_never_calls_runner(self):
        recording = self.root / "fixture.wav"
        self.wav(recording)
        async def runner(*args):
            self.fail("unauthorized runner")
        self.allowed = False
        with self.assertRaisesRegex(Hold, "approval_expired"):
            await audio.transcribe(str(self.model), str(recording), runner, checkpoint=self.check)

    async def test_spawn_task_rechecks_before_actual_subprocess(self):
        checks = 0
        def checkpoint():
            nonlocal checks
            checks += 1
            if checks >= 2:
                raise Hold("approval_expired")
        with patch.object(audio.shutil, "which", return_value="/fixture/arecord"), \
             patch.object(audio.asyncio, "create_subprocess_exec") as spawn:
            with self.assertRaisesRegex(Hold, "approval_expired"):
                async with audio.child("arecord", checkpoint=checkpoint):
                    self.fail("unauthorized child")
            spawn.assert_not_called()

    async def test_active_capture_revocation_drains_context(self):
        owner = self
        entered, drained = asyncio.Event(), asyncio.Event()
        @asynccontextmanager
        async def child(name, *args, **kw):
            owner.calls.append(name)
            owner.wav(args[-1])
            class Process:
                returncode = None
            try:
                entered.set()
                yield Process()
            finally:
                drained.set()
        with patch.object(audio, "child", child), patch.object(audio, "tools_ready", return_value=True), \
             patch.object(audio, "AUTH_POLL", .01):
            task = asyncio.create_task(audio.listen(str(self.model), asyncio.Event(),
                                                    lambda _: None, checkpoint=self.check))
            await asyncio.wait_for(entered.wait(), 1)
            self.allowed = False
            with self.assertRaisesRegex(Hold, "approval_expired"):
                await asyncio.wait_for(task, 1)
        self.assertTrue(drained.is_set())
        self.assertEqual(self.calls, ["arecord"])

    async def test_successful_wait_and_cancellation(self):
        async def done():
            return 7
        self.assertEqual(await audio.await_authorized(done, 1, self.check), 7)
        entered, drained = asyncio.Event(), asyncio.Event()
        async def pending():
            try:
                entered.set()
                await asyncio.Future()
            finally:
                drained.set()
        task = asyncio.create_task(audio.await_authorized(pending, 2, self.check))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(drained.is_set())

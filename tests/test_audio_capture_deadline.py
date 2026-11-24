# SPDX-License-Identifier: AGPL-3.0-only
"""Capture must not publish or signal after an observable stage deadline."""
import asyncio
import struct
import tempfile
import unittest
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.m1 import Hold


class CaptureDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, late=None):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "fixture.bin"
            model.touch()
            loop = asyncio.get_running_loop()
            original_time = loop.time
            offset = 0
            events, signals, children, closed = [], [], [], []
            finish = asyncio.Event()
            finish.set()
            def advance(seconds):
                nonlocal offset
                offset += seconds
            class Recorder:
                first = True
                @property
                def returncode(self):
                    if self.first:
                        self.first = False
                        if late == "first_sample":
                            advance(5)
                        if late == "total":
                            advance(34)
                    return None
                def send_signal(self, sig):
                    signals.append(sig)
                async def wait(self):
                    return 0
            class Whisper:
                async def wait(self):
                    return 0
            @asynccontextmanager
            async def child(name, *args, **kw):
                children.append(name)
                try:
                    if name == "arecord":
                        with wave.open(str(args[-1]), "wb") as out:
                            out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                            out.writeframes(struct.pack("<hh", 1250, -1250) * 1600)
                        if late == "spawn":
                            advance(5)
                        yield Recorder()
                    else:
                        args[-1].with_suffix(".txt").write_text("fixture transcript")
                        yield Whisper()
                finally:
                    closed.append(name)
            def publish(value):
                events.append(value)
                if late == "callback" and value == "listening":
                    advance(34)
            with patch.object(loop, "time", side_effect=lambda: original_time() + offset), \
                 patch.object(audio, "child", child), patch.object(audio, "tools_ready", return_value=True):
                if late:
                    with self.assertRaisesRegex(Hold, "capture_timeout"):
                        await audio.listen(str(model), finish, publish, checkpoint=lambda: None)
                else:
                    self.assertEqual(await audio.listen(str(model), finish, publish,
                                                       checkpoint=lambda: None), "fixture transcript")
            if late:
                self.assertEqual(children, ["arecord"])
                self.assertEqual(closed, children)
                self.assertEqual(signals, [])
                self.assertEqual(events, ["listening"] if late == "callback" else [])
            else:
                self.assertEqual(children, ["arecord", "whisper-cli"])
                self.assertEqual(closed, children)
                self.assertEqual(len(signals), 1)
                self.assertEqual(events, ["listening", "transcribing"])

    async def test_late_spawn_refuses_capture(self):
        await self.exercise("spawn")

    async def test_late_first_sample_refuses_publication(self):
        await self.exercise("first_sample")

    async def test_total_deadline_refuses_publication_and_signal(self):
        await self.exercise("total")

    async def test_callback_crossing_deadline_refuses_signal(self):
        await self.exercise("callback")

    async def test_normal_capture_and_transcription(self):
        await self.exercise()


class SpawnCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_effect_check_after_environment_preparation(self):
        allowed = True
        def checkpoint():
            if not allowed:
                raise Hold("fixture_revoked")
        def environment():
            nonlocal allowed
            allowed = False
            return {}
        with patch.object(audio.shutil, "which", return_value="/fixture/arecord"), \
             patch.object(audio, "spawn_environment", side_effect=environment), \
             patch.object(audio.asyncio, "create_subprocess_exec") as spawn, \
             patch.object(audio, "terminate"):
            spawn.return_value = object()
            with self.assertRaisesRegex(Hold, "fixture_revoked"):
                async with audio.child("arecord", checkpoint=checkpoint):
                    self.fail("unexpected child")
            spawn.assert_not_called()

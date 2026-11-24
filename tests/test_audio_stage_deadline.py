# SPDX-License-Identifier: AGPL-3.0-only
"""Stage budgets include publication, spawn and subsequent owned effects."""
import asyncio
import struct
import tempfile
import unittest
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.m1 import SpeechRequest


class AudioStageDeadlineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.model = self.root / "fixture.bin"
        self.model.touch()
        self.voice = self.root / "fixture.onnx"
        self.voice.touch()
        self.voice.with_name("fixture.onnx.json").write_text('{"language":{"code":"en"}}')
        self.recording = self.root / "fixture.wav"
        self.wav(self.recording)

    def wav(self, path):
        with wave.open(str(path), "wb") as out:
            out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            out.writeframes(struct.pack("<hh", 1250, -1250) * 1600)

    async def exercise(self, mode, late=None):
        owner = self
        loop = asyncio.get_running_loop()
        original_time = loop.time
        offset = 0
        events, children, closed, effects = [], [], [], []

        def advance(seconds):
            nonlocal offset
            offset += seconds

        def publish(value):
            events.append(value)
            if late == "publish" and value in ("synthesizing", "transcribing"):
                advance(200)
            if late == "speaking_callback" and value == "speaking":
                advance(200)

        @asynccontextmanager
        async def child(name, *args, **kw):
            children.append(name)
            try:
                class Process:
                    returncode = 0
                    async def wait(self):
                        effects.append(name + ":wait")
                        return 0
                    async def communicate(self, payload):
                        effects.append(name + ":input")
                        owner.wav(args[-1])
                        if late == "synthesis_result":
                            advance(100)
                        return b"", b""
                if name == "whisper-cli":
                    args[-1].with_suffix(".txt").write_text("fixture transcript")
                    if late == "whisper_spawn":
                        advance(130)
                if name == "piper" and late == "piper_spawn":
                    advance(100)
                if name == "aplay" and late == "playback_spawn":
                    advance(190)
                yield Process()
            finally:
                closed.append(name)

        async def runner(*args):
            effects.append("runner")
            args[-1].with_suffix(".txt").write_text("fixture transcript")
            if late == "runner_result":
                advance(130)

        async def run():
            if mode == "speech":
                await audio.speak(SpeechRequest("Fixture", "en", str(self.voice)),
                                  publish, checkpoint=lambda: None)
            else:
                return await audio.transcribe(str(self.model), str(self.recording),
                                              runner if mode == "runner" else None,
                                              publish, checkpoint=lambda: None)

        with patch.object(loop, "time", side_effect=lambda: original_time() + offset), \
             patch.object(audio, "child", child), patch.object(audio, "tools_ready", return_value=True):
            if late:
                with self.assertRaises(asyncio.TimeoutError):
                    await run()
            else:
                result = await run()
                if mode != "speech":
                    self.assertEqual(result, "fixture transcript")
        self.assertEqual(children, closed)
        if mode == "speech":
            if late == "publish":
                self.assertEqual((children, events, effects), ([], ["synthesizing"], []))
            elif late == "piper_spawn":
                self.assertEqual((children, events, effects), (["piper"], ["synthesizing"], []))
            elif late == "synthesis_result":
                self.assertEqual((children, events, effects), (["piper"], ["synthesizing"], ["piper:input"]))
            elif late == "playback_spawn":
                self.assertEqual((children, events, effects), (["piper", "aplay"], ["synthesizing"], ["piper:input"]))
            elif late == "speaking_callback":
                self.assertEqual((children, events, effects), (["piper", "aplay"], ["synthesizing", "speaking"], ["piper:input"]))
            else:
                self.assertEqual((children, events, effects), (["piper", "aplay"], ["synthesizing", "speaking"], ["piper:input", "aplay:wait"]))
        else:
            self.assertEqual(events, ["transcribing"])
            self.assertEqual(children, ["whisper-cli"] if mode == "whisper" and late != "publish" else [])
            self.assertEqual(effects, [] if late in ("publish", "whisper_spawn") else ["runner" if mode == "runner" else "whisper-cli:wait"])

    async def test_late_synthesis_publication_refuses_spawn(self):
        await self.exercise("speech", "publish")

    async def test_late_piper_spawn_refuses_input(self):
        await self.exercise("speech", "piper_spawn")

    async def test_late_synthesis_result_refuses_playback(self):
        await self.exercise("speech", "synthesis_result")

    async def test_late_playback_spawn_refuses_publication(self):
        await self.exercise("speech", "playback_spawn")

    async def test_late_speaking_callback_refuses_wait(self):
        await self.exercise("speech", "speaking_callback")

    async def test_late_transcription_publication_refuses_whisper(self):
        await self.exercise("whisper", "publish")

    async def test_late_whisper_spawn_refuses_wait(self):
        await self.exercise("whisper", "whisper_spawn")

    async def test_late_transcription_publication_refuses_runner(self):
        await self.exercise("runner", "publish")

    async def test_late_runner_result_refused(self):
        await self.exercise("runner", "runner_result")

    async def test_normal_speech(self):
        await self.exercise("speech")

    async def test_normal_whisper(self):
        await self.exercise("whisper")

    async def test_normal_runner(self):
        await self.exercise("runner")

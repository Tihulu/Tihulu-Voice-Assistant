# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
import asyncio
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from tihulu_jarvis import settings
from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.audio import has_signal, voice_for
from tihulu_jarvis.voice.m1 import Hold
from tihulu_jarvis.voice.session import Session


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_uncancellable_backend_does_not_write_history(self):
        session=Session()
        began, release=asyncio.Event(), asyncio.Event()
        async def chat(messages):
            began.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
            return "Merhaba!"
        task=session.own(lambda e:session.reply("naber nasilsin",chat,e))
        await began.wait()
        session.invalidate(clear=True)
        release.set()
        with self.assertRaisesRegex(Hold,"turn_cancelled"):
            await task
        self.assertEqual(session.history,())
        self.assertIsNone(session.context.response_language)

    async def test_text_chat_does_not_require_a_voice(self):
        session=Session()
        responses=iter(["Hello how are you?", "Merhaba, nasılsın?"])
        async def chat(messages):
            return next(responses)
        display,speech=await session.own(lambda e:session.reply("naber nasilsin",chat,e))
        self.assertIsNone(speech)
        self.assertIn("Merhaba",display)
        self.assertEqual(len(session.history),2)
        self.assertEqual(session.context.response_language,"tr")


class LocalChoicesTests(unittest.TestCase):
    def test_silence_and_voiced_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"synthetic.wav"
            for sample, expected in [(0,False),(1250,True)]:
                with wave.open(str(p),"wb") as f:
                    f.setparams((1,2,16000,0,"NONE","not compressed"))
                    # An alternating synthetic waveform; no real audio is shipped.
                    f.writeframes(struct.pack("<hh",sample,-sample)*1600)
                self.assertEqual(has_signal(p),expected)

    def test_voice_locale_must_match_selected_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"fixture.onnx"
            p.touch()
            p.with_name(p.name+".json").write_text('{"language":{"code":"de_DE"}}',encoding="utf-8")
            self.assertEqual(voice_for(str(p),"de").language,"de")
            with self.assertRaisesRegex(Hold,"voice_locale_unverified"):
                voice_for(str(p),"tr")

    def test_independent_choices_survive_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"settings.json"
            cfg=settings.empty()
            cfg.update(llm="fixture-chat",stt="fixture-speech",voices={"tr":"fixture-tr"})
            settings.save(cfg,p)
            updated=settings.load(p)
            updated["voices"]["tr"]="next-tr"
            settings.save(updated,p)
            result=settings.load(p)
            self.assertEqual((result["llm"],result["stt"],result["voices"]),("fixture-chat","fixture-speech",{"tr":"next-tr"}))
            self.assertEqual(p.stat().st_mode & 0o077,0)


class TranscribeFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        model = self.tmp / "fixture-whisper.bin"
        model.write_bytes(b"synthetic weights fixture\n")
        self.model = str(model)

    def recording(self, payload=None, name="clip.wav"):
        path = self.tmp / name
        if payload is None:
            # An alternating synthetic waveform; no real audio is shipped.
            payload = struct.pack("<hh", 1250, -1250) * 1600
        with wave.open(str(path), "wb") as f:
            f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            f.writeframes(payload)
        return path

    async def fixture_runner(self, text):
        async def runner(model, wav, stem):
            stem.with_suffix(".txt").write_text(text, encoding="utf-8")
        return runner

    async def test_an_owner_supplied_recording_is_transcribed(self):
        clip = self.recording()
        seen = []
        result = await audio.transcribe(str(self.model), str(clip), await self.fixture_runner("Merhaba!"),
                                        publish=seen.append)
        self.assertEqual(result, "Merhaba!")
        self.assertEqual(seen, ["transcribing"])

    async def test_a_rejected_recording_never_reaches_the_engine(self):
        calls = []

        async def counting(model, wav, stem):
            calls.append(wav)
            stem.with_suffix(".txt").write_text("text", encoding="utf-8")

        silent = self.tmp / "silent.wav"
        with wave.open(str(silent), "wb") as f:
            f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            f.writeframes(b"\x00\x00" * 800)
        with self.assertRaisesRegex(Hold, "no_speech_signal"):
            await audio.transcribe(str(self.model), str(silent), counting)
        with self.assertRaisesRegex(Hold, "recording_not_found"):
            await audio.transcribe(str(self.model), str(self.tmp / "missing.wav"), counting)
        unsupported = self.tmp / "clip.mp3"
        unsupported.write_bytes(b"not audio")
        with self.assertRaisesRegex(Hold, "unsupported_recording"):
            await audio.transcribe(str(self.model), str(unsupported), counting)
        self.assertEqual(calls, [])

    async def test_a_failed_engine_is_bounded(self):
        async def broken(model, wav, stem):
            raise OSError("fixture")

        with self.assertRaises(OSError):
            await audio.transcribe(str(self.model), str(self.recording()), broken)
        # A whitespace-only transcript is empty, which is the precise reason.
        with self.assertRaisesRegex(Hold, "empty_transcript"):
            await audio.transcribe(str(self.model), str(self.recording()),
                                   await self.fixture_runner("   "))

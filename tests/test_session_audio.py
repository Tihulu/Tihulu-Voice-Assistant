# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
import asyncio
import struct
import tempfile
import shutil
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

    async def test_invalid_adapter_response_never_commits_history(self):
        session = Session()

        async def chat(messages):
            return "hello\x00world"

        with self.assertRaisesRegex(Hold, "response_rejected_after_one_retry"):
            await session.own(lambda e: session.reply("hello", chat, e))
        self.assertEqual(session.history, ())

    async def test_invalid_stream_delta_never_reaches_composer(self):
        session = Session()
        seen = []

        class Backend:
            async def chat(self, messages, on_delta):
                on_delta("hello\x00world")
                return "Hello"

        with self.assertRaisesRegex(Hold, "invalid_response_text"):
            await session.own(lambda e: session.reply("hello", Backend(), e, on_delta=seen.append))
        self.assertEqual(seen, [])
        self.assertEqual(session.history, ())

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
        self.addCleanup(shutil.rmtree, self.tmp)
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
                                        publish=seen.append, checkpoint=lambda: None)
        self.assertEqual(result, "Merhaba!")
        self.assertEqual(seen, ["transcribing"])

    async def test_speech_after_initial_31_seconds_is_transcribed(self):
        payload = bytes(32 * 16000 * 2) + struct.pack("<hh", 1250, -1250) * 1600
        clip = self.recording(payload)
        result = await audio.transcribe(self.model, str(clip),
                                        await self.fixture_runner("Merhaba!"),
                                        checkpoint=lambda: None)
        self.assertEqual(result, "Merhaba!")

    async def test_truncated_voiced_recording_never_reaches_inference(self):
        clip = self.recording(struct.pack("<hh", 1250, -1250) * 16000)
        clip.write_bytes(clip.read_bytes()[:-1000])
        calls = []

        async def runner(*args):
            calls.append(args)

        self.assertFalse(audio.has_signal(clip))
        with self.assertRaisesRegex(Hold, "no_speech_signal"):
            await audio.transcribe(self.model, str(clip), runner, checkpoint=lambda: None)
        self.assertEqual(calls, [])

    async def test_segmented_transcription_preserves_all_samples_and_order(self):
        speech = struct.pack("<hh", 1250, -1250) * 16000
        payload = speech + bytes(32000) + speech + bytes(32000) + speech
        clip = self.recording(payload)
        parts, seen = [], []

        async def runner(model, wav, stem):
            with wave.open(str(wav), "rb") as w:
                parts.append(w.readframes(w.getnframes()))
            stem.with_suffix(".txt").write_text(str(len(parts)), encoding="utf-8")

        result = await audio.transcribe(self.model, str(clip), runner, publish=seen.append,
                                        checkpoint=lambda: None, language="segmented")
        self.assertEqual(result, "1\n2\n3")
        self.assertEqual(b"".join(parts), payload)
        self.assertEqual(seen, ["transcribing"])

    async def test_segmented_mode_refuses_over_limit_without_decoding(self):
        clip = self.recording(struct.pack("<hh", 1250, -1250) * (31 * 16000))
        calls = []

        async def runner(*args):
            calls.append(args)

        with self.assertRaisesRegex(Hold, "segmented_recording_too_long"):
            await audio.transcribe(self.model, str(clip), runner,
                                   checkpoint=lambda: None, language="segmented")
        self.assertEqual(calls, [])

    async def test_segmented_mode_refuses_excess_segments_without_decoding(self):
        speech = struct.pack("<hh", 1250, -1250) * 16000
        clip = self.recording((speech + bytes(32000)) * 9)
        calls = []

        async def runner(*args):
            calls.append(args)

        with self.assertRaisesRegex(Hold, "too_many_speech_segments"):
            await audio.transcribe(self.model, str(clip), runner,
                                   checkpoint=lambda: None, language="segmented")
        self.assertEqual(calls, [])

    async def test_long_silent_recording_still_refused(self):
        clip = self.recording(bytes(32 * 16000 * 2))
        calls = []

        async def runner(*args):
            calls.append(args)

        with self.assertRaisesRegex(Hold, "no_speech_signal"):
            await audio.transcribe(self.model, str(clip), runner, checkpoint=lambda: None)
        self.assertEqual(calls, [])

    async def test_engine_output_preserves_universal_newline_semantics(self):
        clip = self.recording()

        async def runner(model, wav, stem):
            stem.with_suffix(".txt").write_bytes(b"first\r\nsecond\rthird\n")

        result = await audio.transcribe(self.model, str(clip), runner, checkpoint=lambda: None)
        self.assertEqual(result, "first\nsecond\nthird")

    async def test_invalid_engine_output_is_reported_precisely(self):
        clip = self.recording()
        for payload in (b"\xff", b"hello\x00world", b"a" * (audio.MAX_TRANSCRIPT + 1)):
            with self.subTest(size=len(payload)):
                async def runner(model, wav, stem):
                    stem.with_suffix(".txt").write_bytes(payload)

                with self.assertRaisesRegex(Hold, "invalid_transcript"):
                    await audio.transcribe(self.model, str(clip), runner, checkpoint=lambda: None)

    async def test_invalid_speech_payload_is_refused_before_model_access(self):
        from tihulu_jarvis.voice.m1 import SpeechRequest
        for text in ("hello\x00world", "hello\ud800", " ", "a" * 32001):
            with self.subTest(text_length=len(text)), self.assertRaisesRegex(Hold, "invalid_speech_text"):
                await audio.speak(SpeechRequest(text, "en", "not a model"),
                                  lambda _: self.fail("unexpected playback"),
                                  checkpoint=lambda: None)

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
            await audio.transcribe(str(self.model), str(silent), counting, checkpoint=lambda: None)
        with self.assertRaisesRegex(Hold, "recording_not_found"):
            await audio.transcribe(str(self.model), str(self.tmp / "missing.wav"), counting, checkpoint=lambda: None)
        unsupported = self.tmp / "clip.mp3"
        unsupported.write_bytes(b"not audio")
        with self.assertRaisesRegex(Hold, "unsupported_recording"):
            await audio.transcribe(str(self.model), str(unsupported), counting, checkpoint=lambda: None)
        self.assertEqual(calls, [])

    async def test_a_failed_engine_is_bounded(self):
        async def broken(model, wav, stem):
            raise OSError("fixture")

        with self.assertRaises(OSError):
            await audio.transcribe(str(self.model), str(self.recording()), broken, checkpoint=lambda: None)
        # A whitespace-only transcript is empty, which is the precise reason.
        with self.assertRaisesRegex(Hold, "empty_transcript"):
            await audio.transcribe(str(self.model), str(self.recording()),
                                   await self.fixture_runner("   "), checkpoint=lambda: None)

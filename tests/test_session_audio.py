# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
import asyncio
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from tihulu_jarvis import settings
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

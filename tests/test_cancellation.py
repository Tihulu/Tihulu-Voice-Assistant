# SPDX-License-Identifier: AGPL-3.0-only
"""Cancellation and locale regression tests; no audio device or model is used."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.m1 import Hold, LanguageContext, complete_turn
from tihulu_jarvis.voice.session import Session


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_invalidation_drains_cleanup_before_next_turn(self):
        session = Session()
        entered, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))

        async def operation(epoch):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()

        task = session.own(operation)
        await asyncio.wait_for(entered.wait(), 2)
        session.invalidate()
        await asyncio.wait_for(cleaning.wait(), 2)
        session.invalidate(clear=True)
        session.invalidate()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        with self.assertRaisesRegex(Hold, "operation_busy"):
            session.own(operation)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(session.history, ())
        # The cancellation guard must reset for a newly owned operation.
        entered.clear()
        next_task = session.own(operation)
        await asyncio.wait_for(entered.wait(), 2)
        session.invalidate()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(next_task, 2)

    async def test_cancel_reaps_owned_native_process(self):
        session, processes = Session(), []
        entered = asyncio.Event()

        async def operation(epoch):
            async with audio.child("aplay", "-c", "import sys; sys.stdin.buffer.read()", stdin=True) as process:
                processes.append(process)
                entered.set()
                await asyncio.Future()

        with patch.object(audio.shutil, "which", return_value=sys.executable):
            task = session.own(operation)
            try:
                await asyncio.wait_for(entered.wait(), 3)
                session.invalidate()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
                self.assertIsNotNone(processes[0].returncode)
            finally:
                for process in processes:
                    await audio.terminate(process)

    async def test_cancel_during_spawn_still_reaps_late_process(self):
        session, processes = Session(), []
        spawning, release = asyncio.Event(), asyncio.Event()
        spawn = asyncio.create_subprocess_exec

        async def delayed_spawn(*args, **kwargs):
            spawning.set()
            await release.wait()
            process = await spawn(*args, **kwargs)
            processes.append(process)
            return process

        async def operation(epoch):
            async with audio.child("aplay", "-c", "import sys; sys.stdin.buffer.read()", stdin=True):
                self.fail("Cancelled capture must not become usable")

        with patch.object(audio.shutil, "which", return_value=sys.executable), \
                patch.object(audio.asyncio, "create_subprocess_exec", side_effect=delayed_spawn):
            task = session.own(operation)
            try:
                await asyncio.wait_for(spawning.wait(), 3)
                session.invalidate()
                await asyncio.sleep(0)
                session.invalidate(clear=True)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].returncode)
            finally:
                release.set()
                for process in processes:
                    await audio.terminate(process)


class SelectedVoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.voice = Path(self.temp.name) / "synthetic.onnx"
        self.voice.touch()  # Metadata fixture, not usable model weights.
        self.voice.with_name(self.voice.name + ".json").write_text(
            json.dumps({"language": {"code": "tr_TR"}}), encoding="utf-8")

    async def test_unrelated_missing_voice_does_not_block_selected_locale(self):
        choices = {"tr": str(self.voice), "de": str(self.voice.parent / "missing.onnx")}
        voices = audio.SelectedVoices(choices)
        choices["tr"] = choices["de"]  # The turn owns a snapshot.
        chat = AsyncMock(return_value="Merhaba!")
        done = await complete_turn("naber nasilsin", LanguageContext(), (), chat, voices)
        self.assertEqual((done.speech.language, done.speech.voice_id), ("tr", str(self.voice)))
        chat.assert_awaited_once()

    async def test_missing_requested_locale_stops_before_chat(self):
        chat = AsyncMock()
        with self.assertRaisesRegex(Hold, "matching_voice_unavailable"):
            await complete_turn("Hello!", LanguageContext(), (), chat,
                                audio.SelectedVoices({"tr": str(self.voice)}))
        chat.assert_not_awaited()

    async def test_wrong_requested_locale_stops_before_chat(self):
        chat = AsyncMock()
        with self.assertRaisesRegex(Hold, "voice_locale_unverified"):
            await complete_turn("Hallo!", LanguageContext(), (), chat,
                                audio.SelectedVoices({"de": str(self.voice)}))
        chat.assert_not_awaited()

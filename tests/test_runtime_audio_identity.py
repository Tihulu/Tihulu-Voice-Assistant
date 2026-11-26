# SPDX-License-Identifier: AGPL-3.0-only
"""Runtime audio file identity checks are separate from import validation."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.m1 import Hold, SpeechRequest


class RuntimeAudioIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.model = self.root / "fixture.bin"
        self.model.write_bytes(b"weights")
        self.voice = self.root / "fixture.onnx"
        self.voice.write_bytes(b"voice")
        self.config = self.root / "fixture.onnx.json"
        self.config.write_text('{"language":{"code":"en"}}')

    def replace(self, target):
        new = target.with_suffix(".replacement")
        new.write_bytes(target.read_bytes())
        new.replace(target)

    def test_selected_model_rejects_symlink(self):
        link = self.root / "link.bin"
        link.symlink_to(self.model)
        with self.assertRaisesRegex(Hold, "incompatible_model_file"):
            audio.selected_file(str(link), ".bin")

    def test_runtime_guard_refuses_same_content_replacement(self):
        check = audio._bind_runtime_model(self.model, lambda: None)
        check()
        self.replace(self.model)
        with self.assertRaisesRegex(Hold, "model_file_changed"):
            check()

    async def test_synthesis_refuses_replaced_model_at_publication(self):
        await self.synthesis_replacement(self.voice, "model_file_changed")

    async def test_synthesis_refuses_replaced_config_at_publication(self):
        await self.synthesis_replacement(self.config, "voice_locale_unverified")

    async def synthesis_replacement(self, target, reason):
        def publish(stage):
            if stage == "synthesizing":
                self.replace(target)
        with patch.object(audio, "tools_ready", return_value=True), patch.object(audio, "child") as child:
            with self.assertRaisesRegex(Hold, reason):
                await audio.speak(SpeechRequest("Fixture", "en", str(self.voice)),
                                  publish, checkpoint=lambda: None)
            child.assert_not_called()

    async def test_transcription_refuses_model_replaced_before_stage(self):
        import asyncio
        async def capture(*args, **kwargs):
            self.replace(self.model)
        with patch.object(audio, "tools_ready", return_value=True), \
             patch.object(audio, "_capture", side_effect=capture), \
             patch.object(audio, "_whisper") as whisper:
            with self.assertRaisesRegex(Hold, "model_file_changed"):
                await audio.listen(str(self.model), asyncio.Event(), lambda _: None,
                                   checkpoint=lambda: None)
            whisper.assert_not_called()

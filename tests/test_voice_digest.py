# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import tempfile
import unittest
from pathlib import Path
from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.m1 import Hold


class VoiceDigestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.model = Path(tmp.name) / "fixture.onnx"
        self.model.write_bytes(b"weights")
        self.config = self.model.with_name(self.model.name + ".json")
        self.config.write_text('{"language":{"code":"en"}}')
        self.digest = hashlib.sha256(self.config.read_bytes()).hexdigest()

    def bind(self):
        return audio.bind_voice_digest(str(self.model), "en", self.digest, checkpoint=lambda: None)

    def test_same_locale_changed_bytes_refused(self):
        self.config.write_text('{"language":{"code":"en"},"speaker_id":1}')
        with self.assertRaisesRegex(Hold, "voice_digest_mismatch"):
            self.bind()

    def test_guard_refuses_sidecar_replaced_after_validation(self):
        guard = self.bind()
        guard()
        replacement = self.config.with_suffix(".new")
        replacement.write_bytes(self.config.read_bytes())
        replacement.replace(self.config)
        with self.assertRaisesRegex(Hold, "voice_locale_unverified"):
            guard()

    def test_import_proof_hashes_exact_validated_bytes(self):
        _, proof = audio.inspect_voice(str(self.model), "en")
        self.assertEqual(proof[2], self.digest)

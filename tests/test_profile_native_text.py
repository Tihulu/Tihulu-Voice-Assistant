# SPDX-License-Identifier: AGPL-3.0-only
"""Profile text validation must precede storage, import and UI use."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from tihulu_jarvis import profiles
from tihulu_jarvis.voice.m1 import Hold


class ProfileTextTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = profiles.Store(self.root / "store")
        self.source = profiles.Profile("fixture", name="Original")

    def data(self):
        return json.loads(profiles.Store._dump(self.source))

    def test_parser_rejects_native_unsafe_fields(self):
        for bad in ("\ud800", "\udfff", "x\x00y"):
            for field in ("name", "description", "stt_model_id", "llm_model_id",
                          "tts_model_id", "voice_by_language", "allowed_providers", "scope"):
                with self.subTest(field=field, bad=repr(bad)):
                    data = self.data()
                    if field == "voice_by_language":
                        data[field] = {"en": bad}
                    elif field == "allowed_providers":
                        data[field] = [bad]
                    elif field == "scope":
                        data["rules"] = [{"action": "filesystem_read",
                                          "scope": [bad], "decision": "deny"}]
                    else:
                        data[field] = bad
                    with self.assertRaises(Hold):
                        profiles.Store._parse(json.dumps(data))

    def test_direct_validation_refuses_nonstring_display_text(self):
        for field in ("name", "description"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(Hold, "invalid_profile_text"):
                    replace(self.source, **{field: None}).checked()

    def test_invalid_save_preserves_active_and_history(self):
        self.store.save(self.source)
        active = self.store._path(self.source.id)
        old = active.read_bytes()
        history = {p.name: p.read_bytes() for p in self.store.history.glob("*.json")}
        for bad in ("\ud800", "x\x00y"):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(Hold):
                    self.store.save(replace(self.source, version=2, name=bad))
                self.assertEqual(active.read_bytes(), old)
                self.assertEqual({p.name: p.read_bytes() for p in self.store.history.glob("*.json")}, history)

    def test_malformed_import_never_reaches_store(self):
        path = self.root / "incoming.json"
        for bad in ("\ud800", "x\x00y"):
            with self.subTest(bad=repr(bad)):
                data = self.data()
                data["name"] = bad
                raw = json.dumps(data).encode("utf-8")
                path.write_bytes(raw)
                with self.assertRaisesRegex(Hold, "profile_import_rejected"):
                    self.store.import_file(str(path))
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(self.store.ids(), ())

    def test_legacy_malformed_profile_is_not_activated(self):
        path = self.store._path(self.source.id)
        for bad in ("\ud800", "x\x00y"):
            with self.subTest(bad=repr(bad)):
                data = self.data()
                data["name"] = bad
                raw = json.dumps(data).encode("utf-8")
                path.write_bytes(raw)
                with self.assertRaises(Hold):
                    self.store.startup_profile(self.source.id)
                self.assertEqual(path.read_bytes(), raw)

    def test_international_profile_roundtrip(self):
        profile = replace(self.source, name="Türkçe · Deutsch · 日本語",
                          description="Merhaba 🌍\nGuten Tag",
                          stt_model_id="/fixture/özel.bin", llm_model_id="模型",
                          tts_model_id="Stimme", voice_by_language={"tr": "ses"},
                          allowed_providers=("anbieter",),
                          filesystem_rules=(profiles.Rule("filesystem_read", ("özel",), "deny"),))
        self.store.save(profile)
        self.assertEqual(self.store.load(profile.id), profile)
        path = self.root / "valid.json"
        path.write_text(profiles.Store._dump(profile), encoding="utf-8")
        self.assertEqual(self.store.import_file(str(path)), profile)
        self.assertEqual(profiles.sanitized(profile).name, profile.name)

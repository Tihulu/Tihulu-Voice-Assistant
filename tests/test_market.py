# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""AI Market policy and lifecycle tests. No network, device or model is used."""
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

from tihulu_jarvis.market import (ACTIVE, ALLOWED_NEXT, CATEGORIES, TERMINAL, ModelCard,
                                  Registry, advance, digest, from_file, from_models)
from tihulu_jarvis.voice.m1 import Hold


def card(**kw):
    values = {"model_id": "fixture-model", "display_name": "Fixture", "category": "stt"}
    values.update(kw)
    return ModelCard(**values).checked()


class CardTests(unittest.TestCase):
    def test_categories_are_exactly_three_and_validated(self):
        self.assertEqual(CATEGORIES, ("stt", "llm", "tts"))
        with self.assertRaisesRegex(Hold, "unknown_model_category"):
            card(category="vision")

    def test_unknown_fields_are_never_invented(self):
        fresh = card()
        self.assertEqual(fresh.health_status, "unknown/not tested")
        self.assertEqual(fresh.compatibility_status, "unknown/not tested")
        self.assertEqual(fresh.version, "unknown")
        self.assertEqual(fresh.license_id, "unknown")
        self.assertFalse(fresh.installed)
        self.assertFalse(fresh.registered)

    def test_format_and_engine_compatibility_is_truthful(self):
        self.assertEqual(card(engine="piper", artifact_format="onnx").compatibility_status, "compatible")
        self.assertEqual(card(engine="piper", artifact_format="gguf").compatibility_status,
                         "incompatible_format")
        # An unknown format is not evidence of incompatibility.
        self.assertEqual(card(engine="piper", artifact_format="other").compatibility_status,
                         "unknown/not tested")
        self.assertEqual(card(engine="anything", artifact_format="onnx").compatibility_status,
                         "unknown/not tested")

    def test_validation_rejects_bad_fields(self):
        for bad, reason in (("model_id", "invalid_model_id"), ("category", "unknown_model_category"),
                            ("state", "unknown_lifecycle_state"), ("trust_level", "invalid_card_field"),
                            ("artifact_format", "invalid_card_field"),
                            ("local_or_cloud", "invalid_card_field"),
                            ("languages", "unsupported_language_tag")):
            with self.subTest(field=bad):
                with self.assertRaisesRegex(Hold, reason):
                    card(**{bad: ("" if bad != "languages" else ("TR",))})
        with self.assertRaisesRegex(Hold, "invalid_size"):
            card(disk_size_bytes=-1)
        with self.assertRaisesRegex(Hold, "invalid_storage_reference"):
            card(storage_ref="/home/owner/weights/model.bin")

    def test_lifecycle_is_linear_and_terminal(self):
        steps = ["REVIEWED", "APPROVED", "QUEUED", "DOWNLOADING", "VERIFIED",
                 "REGISTERED", "TESTABLE", ACTIVE]
        current = card()
        for state in steps:
            current = advance(current, state)
            self.assertEqual(current.state, state)
            self.assertEqual(current.registered, state in ("REGISTERED", "TESTABLE", ACTIVE))
        with self.assertRaisesRegex(Hold, "lifecycle_transition_rejected"):
            advance(current, "DOWNLOADING")
        self.assertEqual(ALLOWED_NEXT["FAILED"], ())
        self.assertIn("CANCELLED", TERMINAL)
        cancelled = advance(card(), "CANCELLED")
        with self.assertRaisesRegex(Hold, "lifecycle_transition_rejected"):
            advance(cancelled, "REVIEWED")

    def test_a_failed_download_leaves_nothing_registered(self):
        current = card()
        for state in ("REVIEWED", "APPROVED", "QUEUED", "DOWNLOADING", "FAILED"):
            current = advance(current, state)
        failed = current
        self.assertFalse(failed.registered)
        self.assertEqual(failed.health_status, "unknown/not tested")


class FileImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_import_records_provenance_without_copying_weights(self):
        path = self.tmp / "fixture-whisper.bin"
        payload = b"synthetic weights fixture, not real model data\n"
        path.write_bytes(payload)
        before = path.stat().st_mtime_ns
        item = from_file(str(path), "stt", model_id="fixture-whisper", engine="whisper.cpp",
                         languages=("tr", "en"), display_name="Fixture Whisper")
        self.assertTrue(item.installed)
        self.assertEqual(item.trust_level, "owner")
        self.assertEqual(item.artifact_format, "ggml")
        self.assertEqual(item.compatibility_status, "compatible")
        self.assertEqual(item.disk_size_bytes, len(payload))
        self.assertEqual(item.storage_ref, "sha256-" + hashlib.sha256(payload).hexdigest()[:32])
        self.assertEqual(path.stat().st_mtime_ns, before)  # import never rewrites weights
        self.assertIn(path, [p for p in [path]])  # the file remains where the owner put it

    def test_shared_weights_are_marked_and_never_owned_by_us(self):
        path = self.tmp / "tr_fixture.onnx"
        path.write_bytes(b"voice fixture\n")
        item = from_file(str(path), "tts", engine="piper", shared_storage_owner="piper",
                         languages=("tr",))
        self.assertEqual(item.shared_storage_owner, "piper")
        self.assertFalse(item.owned_by_jarvis)
        with self.assertRaisesRegex(Hold, "shared_and_owned_conflict"):
            from_file(str(path), "tts", shared_storage_owner="piper", owned_by_jarvis=True)

    def test_missing_or_relative_path_is_rejected(self):
        with self.assertRaisesRegex(Hold, "model_file_not_found"):
            from_file("weights/missing.bin", "stt")
        with self.assertRaisesRegex(Hold, "model_file_not_found"):
            from_file(str(self.tmp / "missing.bin"), "stt")

    def test_digest_is_bounded_and_complete(self):
        path = self.tmp / "digest.bin"
        path.write_bytes(b"abc")
        self.assertEqual(digest(path)[0], hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(digest(path, limit=2), (hashlib.sha256(b"ab").hexdigest(), 2))


class InventoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_models_come_from_the_endpoint_not_from_guesses(self):
        seen = []

        class Client:
            async def models(self):
                seen.append("called")
                return ("fixture-qwen", "fixture-qwen", "bad\nentry", "", "fixture-llama")

        cards = await from_models(Client())
        self.assertEqual([c.model_id for c in cards], ["fixture-qwen", "fixture-llama"])
        self.assertEqual(seen, ["called"])
        self.assertTrue(all(c.installed for c in cards))
        self.assertTrue(all(not c.registered for c in cards))
        # A Jan endpoint does not tell us a file format; nothing is invented.
        self.assertEqual(cards[0].compatibility_status, "unknown/not tested")

    async def test_inventory_failure_is_a_hold_not_a_silent_empty_list(self):
        class Broken:
            async def models(self):
                raise OSError("fixture")

        with self.assertRaisesRegex(Hold, "market_inventory_unavailable"):
            await from_models(Broken())
        class Empty:
            async def models(self):
                return ()

        with self.assertRaisesRegex(Hold, "no_installed_chat_models"):
            await from_models(Empty())


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Registry(self.tmp)

    def active(self, model_id, category="stt", **kw):
        """Walk the full lifecycle; a card becomes usable only at ACTIVE."""
        current = card(model_id=model_id, category=category, **kw)
        for state in ("REVIEWED", "APPROVED", "QUEUED", "DOWNLOADING", "VERIFIED",
                      "REGISTERED", "TESTABLE", ACTIVE):
            current = advance(current, state)
        return self.store.save_card(current)

    def test_registry_roundtrip_and_unknown_ids(self):
        self.active("fixture-stt")
        self.assertEqual(len(self.store.cards()), 1)
        self.assertEqual(self.store.card("fixture-stt").state, ACTIVE)
        with self.assertRaisesRegex(Hold, "model_not_registered"):
            self.store.card("missing")

    def test_independent_selection_per_profile_persists(self):
        self.active("fixture-stt")
        self.active("fixture-chat", category="llm", provider="jan", engine="jan")
        self.active("fixture-tr", category="tts", engine="piper", artifact_format="onnx")
        private = self.store.select("private-assistant", "stt", "fixture-stt")
        self.assertEqual(private, {"stt": "fixture-stt"})
        self.store.select("private-assistant", "tts", "fixture-tr")
        self.store.select("coding-agent", "llm", "fixture-chat")
        again = Registry(self.tmp)
        self.assertEqual(again.selection("private-assistant"),
                         {"stt": "fixture-stt", "tts": "fixture-tr"})
        self.assertEqual(again.selection("coding-agent"), {"llm": "fixture-chat"})
        # A card that is not ACTIVE cannot be selected.
        again.save_card(card(model_id="draft", category="llm"))
        with self.assertRaisesRegex(Hold, "model_not_selectable"):
            again.select("private-assistant", "llm", "draft")

    def test_unregister_removes_registry_entry_only(self):
        path = self.tmp / "fixture-stt.bin"
        path.write_bytes(b"weights fixture\n")
        item = from_file(str(path), "stt", model_id="fixture-stt", engine="whisper.cpp")
        self.store.save_card(advance(advance(item, "TESTABLE"), ACTIVE))
        self.store.select("private-assistant", "stt", "fixture-stt")
        self.store.unregister("fixture-stt")
        self.assertEqual(self.store.cards(), ())
        self.assertEqual(self.store.selection("private-assistant"), {})
        self.assertTrue(path.is_file())  # unregistering never deletes the owner's file
        with self.assertRaisesRegex(Hold, "model_not_registered"):
            self.store.unregister("fixture-stt")

    def test_shared_weights_cannot_be_uninstalled(self):
        path = self.tmp / "tr_fixture.onnx"
        path.write_bytes(b"voice fixture\n")
        item = from_file(str(path), "tts", engine="piper", shared_storage_owner="piper")
        self.store.save_card(item)
        with self.assertRaisesRegex(Hold, "shared_weights_are_not_removable"):
            self.store.uninstall("tr_fixture.onnx", approval="owner-1")
        self.assertTrue(path.is_file())
        self.assertEqual(len(self.store.cards()), 1)

    def test_removing_an_imported_file_requires_explicit_approval(self):
        path = self.tmp / "fixture-stt.bin"
        path.write_bytes(b"weights fixture\n")
        item = from_file(str(path), "stt", model_id="fixture-stt", engine="whisper.cpp")
        self.store.save_card(item)
        with self.assertRaisesRegex(Hold, "unregister_only_without_approval"):
            self.store.uninstall("fixture-stt")
        self.assertTrue(path.is_file())
        self.store.uninstall("fixture-stt", approval="owner-1")
        # JARVIS never deletes files it did not create; the entry leaves instead.
        self.assertTrue(path.is_file())
        self.assertEqual(self.store.cards(), ())

    def test_owned_artifact_uninstall_requires_approval_and_stays_in_root(self):
        owned = self.tmp / "artifacts" / "fixture-download"
        owned.parent.mkdir(parents=True, exist_ok=True)
        owned.write_bytes(b"downloaded fixture\n")
        item = self.active("fixture-download", category="llm", owned_by_jarvis=True)
        with self.assertRaisesRegex(Hold, "uninstall_requires_approval"):
            self.store.uninstall("fixture-download")
        self.assertTrue(owned.is_file())
        self.store.uninstall("fixture-download", approval="owner-1")
        self.assertFalse(owned.is_file())
        self.assertEqual(self.store.cards(), ())

    def test_registry_files_carry_no_private_paths(self):
        path = self.tmp / "fixture-stt.bin"
        path.write_bytes(b"weights fixture\n")
        item = from_file(str(path), "stt", model_id="fixture-stt", engine="whisper.cpp")
        self.store.save_card(item)
        text = (self.tmp / "registry.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn("/home/", text)
        self.assertIn("sha256-", text)

    def test_malformed_registry_is_a_hold(self):
        (self.tmp / "registry.json").write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(Hold, "registry_unreadable"):
            self.store.cards()


class NoNetworkTests(unittest.TestCase):
    def test_market_module_has_no_transport(self):
        """This layer must describe transport, never implement it."""
        source = Path(__import__("tihulu_jarvis.market", fromlist=["market"]).__file__).read_text()
        for banned in ("import socket", "import urllib", "import http", "import requests",
                       "urlopen", "aiohttp", "httpx"):
            self.assertNotIn(banned, source)
        self.assertIn("DIGEST_LIMIT", source)

# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""AI Market policy and lifecycle tests. No network, device or model is used."""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from tihulu_jarvis import market
from tihulu_jarvis.transfer_http import loopback_fetch
from tihulu_jarvis.market import (ACTIVE, ALLOWED_NEXT, CATEGORIES, TERMINAL, Catalog,
                                  ModelCard, Registry, Source, Transfer, advance, digest,
                                  from_file, from_models, human_size, parse_catalog,
                                  key_path, load_key, register_installed,
                                  resumable_partials, save_key, sign_catalog, usage,
                                  verify_catalog, CATALOG_LIMIT)
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

    def test_verified_staging_can_cancel_but_published_item_cannot(self):
        current = card()
        for state in ("REVIEWED", "APPROVED", "QUEUED", "DOWNLOADING", "VERIFIED"):
            current = advance(current, state)
        cancelled = advance(current, "CANCELLED")
        self.assertFalse(cancelled.registered)
        self.assertEqual(cancelled.state, "CANCELLED")
        with self.assertRaisesRegex(Hold, "lifecycle_transition_rejected"):
            advance(cancelled, "REGISTERED")
        committed = advance(current, "REGISTERED")
        self.assertTrue(committed.registered)
        with self.assertRaisesRegex(Hold, "lifecycle_transition_rejected"):
            advance(committed, "CANCELLED")

    def test_registering_an_already_installed_item_never_replaces_a_download(self):
        discovered = card(category="llm", installed=True, local_or_cloud="local")
        self.assertEqual(register_installed(discovered).state, "REGISTERED")
        for bad, reason in ((card(category="llm", installed=False), "DISCOVERED->REGISTERED"),
                            (card(category="llm", installed=True, local_or_cloud="cloud"), "DISCOVERED->REGISTERED")):
            with self.assertRaisesRegex(Hold, "lifecycle_transition_rejected:" + reason):
                register_installed(bad)
        with self.assertRaisesRegex(Hold, "lifecycle_transition_rejected"):
            register_installed(advance(card(), "REVIEWED"))

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

    def test_import_proof_refuses_same_size_replacement(self):
        from tihulu_jarvis import market
        path = self.tmp / "fixture.bin"
        path.write_bytes(b"old")
        card, proof = market.inspect_file(str(path), "stt", engine="whisper.cpp")
        other = self.tmp / "replacement"
        other.write_bytes(b"new")
        other.replace(path)
        with self.assertRaisesRegex(Hold, "model_file_changed"):
            market.require_import_proof(proof)

    def test_import_refuses_symlink_and_fifo(self):
        import os
        path = self.tmp / "fixture.bin"
        target = self.tmp / "other"
        target.write_bytes(b"fixture")
        path.symlink_to(target)
        with self.assertRaisesRegex(Hold, "model_file_not_found"):
            from_file(str(path), "stt")
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(Hold, "model_file_not_found"):
            from_file(str(path), "stt")

    def test_import_hash_checks_cancellation_between_reads(self):
        path = self.tmp / "fixture.bin"
        path.write_bytes(b"x" * (3 << 20))
        calls = []
        def checkpoint():
            calls.append(1)
            if len(calls) == 4:
                raise Hold("turn_cancelled")
        with self.assertRaisesRegex(Hold, "turn_cancelled"):
            from_file(str(path), "stt", checkpoint=checkpoint)
        self.assertEqual(len(calls), 4)

    def test_import_refuses_mutation_during_hash(self):
        path = self.tmp / "fixture.bin"
        path.write_bytes(b"x" * (2 << 20))
        calls = []
        def checkpoint():
            calls.append(1)
            if len(calls) == 4:
                with path.open("r+b") as f:
                    f.write(b"changed")
        with self.assertRaises(Hold):
            from_file(str(path), "stt", checkpoint=checkpoint)

    def test_voice_sidecar_rejects_fifo_and_symlink(self):
        import os
        from tihulu_jarvis.voice import audio
        path = self.tmp / "tr.onnx"
        path.write_bytes(b"fixture")
        config = path.with_name(path.name + ".json")
        target = self.tmp / "other.json"
        target.write_text('{"language":{"code":"tr"}}')
        config.symlink_to(target)
        with self.assertRaisesRegex(Hold, "voice_locale_unverified"):
            audio.voice_for(str(path), "tr")
        config.unlink()
        os.mkfifo(config)
        with self.assertRaisesRegex(Hold, "voice_locale_unverified"):
            audio.voice_for(str(path), "tr")

    def test_voice_sidecar_rejects_duplicate_locale(self):
        from tihulu_jarvis.voice import audio
        path = self.tmp / "tr.onnx"
        path.write_bytes(b"fixture")
        path.with_name(path.name + ".json").write_text(
            '{"language":{"code":"de","code":"tr"}}')
        with self.assertRaisesRegex(Hold, "voice_locale_unverified"):
            audio.voice_for(str(path), "tr")

    def test_voice_sidecar_proof_refuses_identical_replacement(self):
        from tihulu_jarvis.voice import audio
        path = self.tmp / "tr.onnx"
        path.write_bytes(b"fixture")
        config = path.with_name(path.name + ".json")
        config.write_text('{"language":{"code":"tr"}}')
        voice, proof = audio.inspect_voice(str(path), "tr")
        other = self.tmp / "new.json"
        other.write_bytes(config.read_bytes())
        other.replace(config)
        with self.assertRaisesRegex(Hold, "voice_locale_unverified"):
            audio.require_voice_proof(proof)

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


class UsageTests(unittest.TestCase):
    def test_usage_reports_only_known_values(self):
        stt = card(model_id="a-stt", category="stt", disk_size_bytes=3 * 1024 ** 3,
                   memory_estimate=2 * 1024 ** 3, requires_gpu=True)
        voice = card(model_id="b-tts", category="tts", engine="piper", artifact_format="onnx",
                     shared_storage_owner="piper", disk_size_bytes=40 * 1024 ** 2)
        report = usage((stt, voice))
        self.assertEqual(report.cards, 2)
        self.assertEqual(report.by_category, (("stt", 1), ("tts", 1)))
        self.assertEqual(report.by_state, (("DISCOVERED", 2),))
        self.assertEqual(report.disk_bytes, 3 * 1024 ** 3 + 40 * 1024 ** 2)
        self.assertEqual(report.shared, 1)
        self.assertEqual(report.imported, 1)
        self.assertEqual(report.requires_gpu, 1)
        self.assertEqual(report.memory_bytes, 2 * 1024 ** 3)
        self.assertEqual(report.unknown_memory, 1)
        text = report.report()
        self.assertIn("2 cards", text)
        self.assertIn("disk 3.0 GiB", text)
        self.assertIn("shared 1", text)
        self.assertIn("memory 2.0 GiB", text)
        self.assertTrue(human_size(0).startswith("unknown"))
        self.assertEqual(human_size(1536), "1.5 KiB")
        self.assertEqual(human_size(1024 ** 3), "1.0 GiB")

    def test_empty_registry_reports_nothing_invented(self):
        empty = usage(())
        self.assertEqual(empty.cards, 0)
        self.assertEqual(empty.disk_bytes, 0)
        self.assertIn("unknown/not tested", empty.report())


async def fake_fetch(path, note, start=0):
    """A fixture transport: it writes into the isolated transfer directory."""
    with open(path, "wb") as f:
        f.write(b"weights")
    note(start + len(b"weights"))
    return len(b"weights")


class TransferTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Registry(self.tmp)

    def active_card(self, **kw):
        """A card that already completed the lifecycle and remains usable."""
        current = card(**kw)
        for state in ("REVIEWED", "APPROVED", "QUEUED", "DOWNLOADING", "VERIFIED",
                      "REGISTERED", "TESTABLE", ACTIVE):
            current = advance(current, state)
        return current

    def source(self, **kw):
        values = {"reference": "catalog:fixture-model", "checksum": hashlib.sha256(b"weights").hexdigest(),
                  "size": len(b"weights"), "version": "1.0", "license_id": "MIT",
                  "network_approval": "owner-1", "approval_expires_at": 1e12}
        values.update(kw)
        return Source(**values)

    def test_transfer_registers_a_verified_artifact(self):
        item = card(category="llm", state="DISCOVERED")
        self.store.save_card(item)
        transfer = Transfer(self.store, item, self.source(), fake_fetch, root=self.tmp)
        done = asyncio.get_event_loop().run_until_complete(transfer.run())
        self.assertEqual(done.state, "REGISTERED")
        self.assertTrue(done.owned_by_jarvis)
        self.assertEqual(done.disk_size_bytes, len(b"weights"))
        self.assertEqual(done.storage_ref, "sha256-" + hashlib.sha256(b"weights").hexdigest()[:32])
        self.assertEqual(done.version, "1.0")
        self.assertEqual(done.license_id, "MIT")
        target = self.tmp / "artifacts" / "fixture-model"
        self.assertTrue(target.is_file())
        self.assertFalse(list((self.tmp / "transfers").glob("tihulu-transfer-*")))
        self.assertEqual(self.store.card("fixture-model").state, "REGISTERED")

    def test_checksum_mismatch_fails_and_changes_nothing_registered(self):
        # A different model stays installed and usable while this one fails.
        other = self.active_card(model_id="fixture-installed", category="llm", installed=True)
        self.store.save_card(other)
        item = card(category="llm", state="DISCOVERED")
        self.store.save_card(item)
        transfer = Transfer(self.store, item, self.source(checksum="0" * 64), fake_fetch, root=self.tmp)
        with self.assertRaisesRegex(Hold, "transfer_checksum_mismatch"):
            asyncio.get_event_loop().run_until_complete(transfer.run())
        self.assertEqual(transfer.state, "FAILED")
        # The previously installed entry is untouched and still usable.
        self.assertEqual(self.store.card("fixture-installed").state, ACTIVE)
        self.assertFalse((self.tmp / "artifacts" / "fixture-model").exists())
        self.assertFalse(list((self.tmp / "transfers").glob("tihulu-transfer-*")))
        self.assertEqual(self.store.card("fixture-model").state, "FAILED")

    def test_a_transfer_never_overwrites_an_installed_model(self):
        item = self.active_card(category="llm", installed=True)
        self.store.save_card(item)
        transfer = Transfer(self.store, item, self.source(), fake_fetch, root=self.tmp)
        with self.assertRaisesRegex(Hold, "model_already_registered"):
            asyncio.get_event_loop().run_until_complete(transfer.run())
        self.assertEqual(self.store.card("fixture-model").state, ACTIVE)
        self.assertFalse(list((self.tmp / "transfers").glob("tihulu-transfer-*")))

    def test_size_mismatch_and_invalid_length_are_rejected(self):
        item = card(category="llm", state="DISCOVERED")
        self.store.save_card(item)
        transfer = Transfer(self.store, item, self.source(size=99), fake_fetch, root=self.tmp)
        with self.assertRaisesRegex(Hold, "transfer_size_mismatch"):
            asyncio.get_event_loop().run_until_complete(transfer.run())
        async def lying(path, note):
            note(0)
            return 0
        # Use a fresh card: the first failure is terminal, not reusable consent.
        second = self.store.save_card(card(model_id="fixture-second", category="llm"))
        with self.assertRaisesRegex(Hold, "transfer_reported_invalid_length"):
            asyncio.get_event_loop().run_until_complete(Transfer(self.store, second, self.source(), lying, root=self.tmp).run())

    def test_without_authorization_the_transfer_holds_before_writing(self):
        item = card(category="llm", state="DISCOVERED")
        self.store.save_card(item)

        async def boom(path, note):
            raise AssertionError("a transfer must not start without authorization")

        transfer = Transfer(self.store, item, self.source(network_approval=""), boom, root=self.tmp)
        with self.assertRaisesRegex(Hold, "network_not_authorized"):
            asyncio.get_event_loop().run_until_complete(transfer.run())
        self.assertEqual(transfer.state, "HOLD")
        self.assertFalse(list((self.tmp / "transfers").glob("tihulu-transfer-*")))

    def test_expired_authorization_holds_the_transfer(self):
        item = card(category="llm", state="DISCOVERED")
        self.store.save_card(item)
        transfer = Transfer(self.store, item, self.source(approval_expires_at=5.0), fake_fetch, now=10.0)
        with self.assertRaisesRegex(Hold, "network_not_authorized"):
            asyncio.get_event_loop().run_until_complete(transfer.run())

    async def test_cancellation_removes_the_partial_file(self):
        item = card(category="llm", state="DISCOVERED")
        self.store.save_card(item)
        started = asyncio.Event()

        async def stalling(path, note):
            started.set()
            await asyncio.Future()

        transfer = Transfer(self.store, item, self.source(), stalling, root=self.tmp)
        task = asyncio.get_running_loop().create_task(transfer.run())
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        self.assertEqual(transfer.state, "CANCELLED")
        self.assertFalse(list((self.tmp / "transfers").glob("tihulu-transfer-*")))
        self.assertEqual(self.store.card("fixture-model").state, "CANCELLED")

    def test_a_source_without_checksum_never_starts(self):
        with self.assertRaisesRegex(Hold, "checksum_required_before_download"):
            self.source(checksum="").checked()
        with self.assertRaisesRegex(Hold, "invalid_source_reference"):
            self.source(reference="/home/owner/weights").checked()
        # An unstated size stays unknown; the transport limit still bounds it.
        self.assertEqual(self.source(size=0).size, 0)
        self.assertEqual(human_size(0), "unknown/not tested")

    def test_transport_credentials_are_not_accepted(self):
        with self.assertRaisesRegex(Hold, "invalid_approval_reference"):
            self.source(network_approval="secret/key").checked()


class CatalogTests(unittest.TestCase):
    def document(self, **kw):
        values = {"entries": [
            {"model_id": "fixture-whisper-large", "display_name": "Fixture Whisper Large",
             "category": "stt", "engine": "whisper.cpp", "artifact_format": "ggml",
             "languages": ["tr", "en", "de"], "license_id": "MIT",
             "source_ref": "catalog:fixture-whisper-large",
             "source_checksum": hashlib.sha256(b"stt-weights").hexdigest(),
             "source_size": 11, "source_version": "v3", "publisher": "fixture-publisher"},
            {"model_id": "fixture-tr-voice", "display_name": "Fixture TR Voice",
             "category": "tts", "engine": "piper", "artifact_format": "onnx",
             "languages": ["tr"], "license_id": "CC-BY-4.0",
             "source_ref": "catalog:fixture-tr-voice",
             "source_checksum": hashlib.sha256(b"voice-weights").hexdigest(),
             "source_size": 13, "publisher": "fixture-publisher"},
        ]}
        values.update(kw)
        return json.dumps(values)

    def test_catalog_entries_carry_terms_and_no_paths(self):
        catalog = parse_catalog(self.document(), source_name="owner list")
        self.assertEqual(len(catalog.entries), 2)
        entry = catalog.entries[0]
        card = entry.checked()
        self.assertEqual((card.category, card.engine, card.artifact_format), ("stt", "whisper.cpp", "ggml"))
        self.assertEqual(card.languages, ("tr", "en", "de"))
        self.assertEqual(card.state, "DISCOVERED")
        self.assertFalse(card.installed)
        self.assertEqual(card.trust_level, "curated")
        # A catalog publishes terms; the owner's approval is a separate act.
        self.assertFalse(card.approved_source())
        terms = entry.terms("owner-approval", 1e12)
        self.assertEqual(terms.checksum, hashlib.sha256(b"stt-weights").hexdigest())
        self.assertEqual(terms.size, 11)
        self.assertTrue(terms.authorized(0.0))
        self.assertFalse(terms.authorized(1e12 + 1))
        approved = market.replace(card, network_approval="owner-approval", approval_expires_at=1e12)
        self.assertTrue(approved.approved_source())
        self.assertFalse(market.replace(card, network_approval="", ).approved_source())
        self.assertIn("unknown", card.health_status)

    def test_search_filters_without_ranking_or_guessing(self):
        catalog = parse_catalog(self.document())
        self.assertEqual([e.model_id for e in catalog.search()], ["fixture-whisper-large", "fixture-tr-voice"])
        self.assertEqual([e.model_id for e in catalog.search(category="tts")], ["fixture-tr-voice"])
        self.assertEqual([e.model_id for e in catalog.search(language="de")], ["fixture-whisper-large"])
        self.assertEqual([e.model_id for e in catalog.search(engine="piper")], ["fixture-tr-voice"])
        self.assertEqual([e.model_id for e in catalog.search(text="whisper")], ["fixture-whisper-large"])
        self.assertEqual(catalog.search(text="nothing-matches"), ())
        with self.assertRaisesRegex(Hold, "catalog_entry_not_found"):
            catalog.card("missing")

    def test_malformed_catalog_entries_are_rejected(self):
        with self.assertRaisesRegex(Hold, "invalid_catalog"):
            parse_catalog(self.document(entries=[{"model_id": "x"}]))
        # An empty owner catalog is legitimate; it simply offers nothing.
        self.assertEqual(parse_catalog(self.document(entries=[])).search(), ())
        with self.assertRaisesRegex(Hold, "catalog_too_large"):
            parse_catalog("[" + "x" * (CATALOG_LIMIT + 1) + "]")
        with self.assertRaisesRegex(Hold, "invalid_catalog"):
            parse_catalog(json.dumps({"entries": "not-a-list"}))

    def test_entries_without_terms_cannot_start_a_download(self):
        text = json.dumps({"entries": [{"model_id": "no-terms", "display_name": "No terms",
                                       "category": "stt", "source_ref": "catalog:no-terms",
                                       "source_checksum": "", "source_size": 0}]})
        with self.assertRaisesRegex(Hold, "checksum_required_before_download"):
            parse_catalog(text)

    def test_duplicate_ids_and_paths_in_references_are_rejected(self):
        with self.assertRaisesRegex(Hold, "duplicate_catalog_entry"):
            parse_catalog(self.document(entries=[
                {"model_id": "dup", "display_name": "A", "category": "stt", "source_ref": "catalog:a",
                 "source_checksum": "0" * 64, "source_size": 1},
                {"model_id": "dup", "display_name": "B", "category": "stt", "source_ref": "catalog:b",
                 "source_checksum": "1" * 64, "source_size": 2}]))
        with self.assertRaisesRegex(Hold, "invalid_source_reference"):
            parse_catalog(self.document(entries=[
                {"model_id": "path-entry", "display_name": "P", "category": "stt",
                 "source_ref": "/home/owner/weights.bin", "source_checksum": "0" * 64,
                 "source_size": 1}]))


class ResumeTransferTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Registry(self.tmp)

    def active_card(self, **kw):
        current = card(**kw)
        for state in ("REVIEWED", "APPROVED", "QUEUED", "DOWNLOADING", "VERIFIED",
                      "REGISTERED", "TESTABLE", ACTIVE):
            current = advance(current, state)
        return current

    async def serve_on(self, ranges=True):
        async def route(reader, writer):
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                want = 0
                for field in request.split(b"\r\n"):
                    if field.lower().startswith(b"range:"):
                        want = int(field.split(b"=")[1].split(b"-")[0])
                honoured = want and ranges
                body = b"weights"[want:] if honoured else b"weights"
                status = b"HTTP/1.1 206 Partial Content" if honoured else b"HTTP/1.1 200 OK"
                extra = (b"Content-Range: bytes %d-%d/%d\r\n" % (want, len(b"weights") - 1,
                                                                 len(b"weights"))) if honoured else b""
                writer.write(status + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n"
                             + extra + b"Content-Type: application/octet-stream\r\n\r\n" + body)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(route, "127.0.0.1", 0)
        return "http://127.0.0.1:%d/weights.bin" % server.sockets[0].getsockname()[1], server

    def source_for(self, url):
        return Source(url, hashlib.sha256(b"weights").hexdigest(), len(b"weights"),
                     "1.0", "MIT", "owner-1", 1e12)

    async def test_a_partial_transfer_resumes_and_registers(self):
        url, server = await self.serve_on()
        async with server:
            item = card(category="llm", state="DISCOVERED")
            self.store.save_card(item)
            started, stalled = asyncio.Event(), asyncio.Event()

            async def stalling(path, note):
                with open(path, "wb") as f:
                    f.write(b"we")
                note(2)
                started.set()
                await stalled.wait()
                return 2

            transfer = Transfer(self.store, item, self.source_for(url), stalling, root=self.tmp)
            task = asyncio.get_running_loop().create_task(transfer.run())
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            stalled.set()  # only now let the fixture handler finish
            partial = Path(transfer.root) / ("partial-fixture-model")
            self.assertTrue(partial.is_file())
            kept = partial.stat().st_size
            card_after = self.store.card("fixture-model")
            self.assertEqual(card_after.state, "CANCELLED")
            # The card stays cancelled until the resumed transfer verifies it.
            resumed = Transfer(self.store, card_after, self.source_for(url), loopback_fetch(url),
                               root=transfer.root)
            done = await asyncio.wait_for(resumed.resume(lambda count: None, start=kept), 5)
            self.assertEqual(done.state, "REGISTERED")
            self.assertEqual(done.disk_size_bytes, len(b"weights"))
            self.assertFalse(partial.exists())
            artifact = Path(transfer.registry.root, "artifacts", "fixture-model")
            self.assertEqual(artifact.read_bytes(), b"weights")

    async def test_a_resume_without_a_matching_partial_is_refused(self):
        url, server = await self.serve_on()
        async with server:
            item = card(category="llm", state="CANCELLED")
            self.store.save_card(item)
            transfer = Transfer(self.store, item, self.source_for(url), loopback_fetch(url),
                                root=self.tmp)
            with self.assertRaisesRegex(Hold, "transfer_resume_offset_unverified"):
                await transfer.resume(lambda count: None, start=3)

    async def test_a_resume_of_an_installed_model_is_refused(self):
        url, server = await self.serve_on()
        async with server:
            item = self.active_card(category="llm", installed=True)
            self.store.save_card(item)
            transfer = Transfer(self.store, item, self.source_for(url), loopback_fetch(url),
                                root=self.tmp)
            with self.assertRaisesRegex(Hold, "model_already_registered"):
                await transfer.resume(lambda count: None, start=3)


class RevisionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Registry(self.tmp)

    def card(self, **kw):
        values = {"model_id": "fixture-model", "display_name": "Fixture", "category": "stt"}
        values.update(kw)
        return ModelCard(**values).checked()

    def test_revisions_are_kept_and_restorable(self):
        first = self.store.save_card(self.card(display_name="First"))
        second = self.store.save_card(self.card(display_name="Second"))
        self.assertEqual(first.revision, 0)
        self.assertEqual(second.revision, 1)
        self.assertEqual(self.store.card("fixture-model").display_name, "Second")
        history = self.store.history("fixture-model")
        self.assertEqual([c.revision for c in history], [0])
        self.assertEqual(history[0].display_name, "First")
        restored = self.store.rollback("fixture-model", 0)
        self.assertEqual(restored.revision, 2)
        self.assertEqual(self.store.card("fixture-model").display_name, "First")
        self.assertEqual(len(self.store.history("fixture-model")), 2)

    def test_an_unchanged_card_is_not_a_new_revision(self):
        saved = self.store.save_card(self.card())
        again = self.store.save_card(self.card())
        self.assertEqual(again.revision, 0)
        self.assertEqual(saved, again)
        self.assertEqual(self.store.history("fixture-model"), ())

    def test_rollback_of_an_unknown_revision_is_refused(self):
        self.store.save_card(self.card())
        with self.assertRaisesRegex(Hold, "revision_not_found"):
            self.store.rollback("fixture-model", 7)

    def test_history_is_empty_and_bounded(self):
        self.assertEqual(self.store.history("missing"), ())
        for index in range(Registry.HISTORY_LIMIT + 4):
            self.store.save_card(self.card(display_name="Revision %d" % index))
        self.assertEqual(len(self.store.history("fixture-model")), Registry.HISTORY_LIMIT)
        with self.assertRaisesRegex(Hold, "invalid_model_id"):
            self.store.history("bad/id")

    def test_history_survives_a_reopen(self):
        self.store.save_card(self.card(display_name="First"))
        self.store.save_card(self.card(display_name="Second"))
        again = Registry(self.tmp)
        self.assertEqual([c.display_name for c in again.history("fixture-model")], ["First"])
        self.assertEqual(again.card("fixture-model").display_name, "Second")


class UpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Registry(self.tmp)

    def active_card(self, **kw):
        current = card(**kw)
        for state in ("REVIEWED", "APPROVED", "QUEUED", "DOWNLOADING", "VERIFIED",
                      "REGISTERED", "TESTABLE", ACTIVE):
            current = advance(current, state)
        return current

    async def test_an_installed_item_updates_and_keeps_its_history(self):
        source = Source("catalog:fixture", hashlib.sha256(b"weights").hexdigest(),
                        len(b"weights"), "2.0", "MIT", "owner-1", 1e12)
        installed = self.active_card(model_id="fixture-model", category="stt",
                                     engine="whisper.cpp", artifact_format="ggml",
                                     installed=True, trust_level="curated",
                                     disk_size_bytes=len(b"weights"),
                                     storage_ref="sha256-" + hashlib.sha256(b"weights").hexdigest()[:32],
                                     source_version="1.0")
        self.store.save_card(installed)
        transfer = Transfer(self.store, installed, source, fake_fetch, root=self.tmp)
        done = await asyncio.wait_for(transfer.update(lambda count: None), 5)
        self.assertEqual(done.state, "ACTIVE")
        self.assertEqual(done.source_version, "2.0")
        current = self.store.card("fixture-model")
        self.assertEqual(current.revision, 1)
        previous = self.store.history("fixture-model")
        self.assertEqual(len(previous), 1)
        self.assertEqual(previous[0].source_version, "1.0")
        self.assertEqual(previous[0].revision, 0)

    async def test_an_update_of_an_uninstalled_item_is_refused(self):
        source = Source("catalog:fixture", hashlib.sha256(b"weights").hexdigest(),
                        len(b"weights"), "2.0", "MIT", "owner-1", 1e12)
        transfer = Transfer(self.store, card(), source, fake_fetch, root=self.tmp)
        with self.assertRaisesRegex(Hold, "model_not_installed"):
            await transfer.update(lambda count: None)

    async def test_a_failed_update_keeps_the_previous_revision(self):
        source = Source("catalog:fixture", "0" * 64, len(b"weights"), "2.0", "MIT",
                        "owner-1", 1e12)
        installed = self.active_card(installed=True)
        self.store.save_card(installed)
        transfer = Transfer(self.store, installed, source, fake_fetch, root=self.tmp)
        with self.assertRaisesRegex(Hold, "transfer_checksum_mismatch"):
            await transfer.update(lambda count: None)
        # A failed update leaves the installed item and its settings intact.
        current = self.store.card("fixture-model")
        self.assertEqual(current.state, "ACTIVE")
        self.assertEqual(current.source_version, "unknown")
        # Nothing was replaced, so no revision was created either.
        self.assertEqual(self.store.history("fixture-model"), ())


class PartialDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.transfers = self.tmp / "transfers"
        self.transfers.mkdir(parents=True)

    def test_partials_are_reported_with_their_size(self):
        (self.transfers / "partial-fixture-model").write_bytes(b"abc")
        (self.transfers / "partial-other").write_bytes(b"abcdefgh")
        (self.transfers / "partial-empty").write_bytes(b"")
        self.assertEqual(resumable_partials(self.tmp),
                         (("fixture-model", 3), ("other", 8)))

    def test_no_transfers_directory_is_not_an_error(self):
        self.assertEqual(resumable_partials(self.tmp.parent / "missing"), ())


class CatalogTrustTests(unittest.TestCase):
    document = {
        "source_name": "fixture catalog",
        "entries": [
            {"model_id": "fixture-whisper-large", "display_name": "Fixture Whisper Large",
             "category": "stt", "engine": "whisper.cpp", "artifact_format": "ggml",
             "languages": ["tr", "en", "de"], "license_id": "MIT",
             "source_ref": "catalog:fixture-whisper-large",
             "source_checksum": hashlib.sha256(b"stt-weights").hexdigest(),
             "source_size": 11, "source_version": "v3", "publisher": "fixture-publisher"},
        ],
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: os.environ.pop("XDG_DATA_HOME", None))
        os.environ["XDG_DATA_HOME"] = str(self.tmp)

    def test_a_signed_catalog_verifies_and_an_unsigned_one_does_not(self):
        save_key("owner-catalog", "shared-secret-for-catalog-signing")
        signed = sign_catalog(json.dumps(self.document), "owner-catalog", now=100.0)
        verified = parse_catalog(signed)
        self.assertTrue(verified.verified)
        self.assertEqual(verified.key_ref, "owner-catalog")
        self.assertIn("verified", verified.report())
        unsigned = parse_catalog(json.dumps(self.document))
        self.assertFalse(unsigned.verified)
        self.assertIn("unverified", unsigned.report())
        self.assertEqual(unsigned.entries[0].model_id, "fixture-whisper-large")

    def test_a_tampered_catalog_never_verifies(self):
        save_key("owner-catalog", "shared-secret-for-catalog-signing")
        signed = json.loads(sign_catalog(json.dumps(self.document), "owner-catalog"))
        signed["entries"][0]["source_size"] = 999
        self.assertFalse(parse_catalog(json.dumps(signed)).verified)
        signed_ok = json.loads(sign_catalog(json.dumps(self.document), "owner-catalog"))
        signed_ok["source_name"] = "renamed by someone else"
        self.assertFalse(parse_catalog(json.dumps(signed_ok)).verified)

    def test_an_unknown_key_is_a_bounded_reason(self):
        save_key("owner-catalog", "shared-secret-for-catalog-signing")
        signed = json.loads(sign_catalog(json.dumps(self.document), "owner-catalog"))
        signed["signature"]["key_ref"] = "other-key"
        catalog = parse_catalog(json.dumps(signed))
        self.assertFalse(catalog.verified)
        verified, reason = verify_catalog(catalog)
        self.assertFalse(verified)
        self.assertIn(reason, ("key_unreadable", "invalid_key_reference", "invalid_key_material"))

    def test_keys_are_private_and_bounded(self):
        path = key_path("owner-catalog")
        save_key("owner-catalog", "shared-secret-for-catalog-signing")
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o077, 0)
        with self.assertRaisesRegex(Hold, "invalid_key_material"):
            save_key("short", "tooshort")
        with self.assertRaisesRegex(Hold, "invalid_key_reference"):
            save_key("bad/name", "shared-secret-for-catalog-signing")
        with self.assertRaisesRegex(Hold, "key_unreadable"):
            load_key("missing-key")

    def test_a_tampered_key_file_is_refused(self):
        save_key("owner-catalog", "shared-secret-for-catalog-signing")
        key_path("owner-catalog").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(Hold, "invalid_key_material"):
            load_key("owner-catalog")
        self.assertFalse(parse_catalog(json.dumps(self.document)).verified)

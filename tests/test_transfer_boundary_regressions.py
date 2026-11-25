# SPDX-License-Identifier: AGPL-3.0-only
"""Evidence-only #99 probes. No transport, model, device or owner data."""
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tihulu_jarvis import market
from tihulu_jarvis.voice.m1 import Hold

OLD=b"synthetic-old"
NEW=b"synthetic-new"

class TransferBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.store=market.Registry(self.root)
        self.calls=[]
        self.target=self.root/"artifacts"/"fixture-model"

    def card(self, installed=False):
        card=market.ModelCard(model_id="fixture-model",display_name="Fixture",category="stt").checked()
        if installed:
            for state in ("REVIEWED","APPROVED","QUEUED","DOWNLOADING","VERIFIED","REGISTERED"):
                card=market.advance(card,state)
            from dataclasses import replace
            card=replace(card,owned_by_jarvis=True,storage_ref="sha256-"+hashlib.sha256(OLD).hexdigest()[:32],disk_size_bytes=len(OLD),source_version="1.0")
            self.target.parent.mkdir(parents=True,exist_ok=True)
            self.target.write_bytes(OLD)
        return self.store.save_card(card)

    def source(self, **overrides):
        fields=dict(reference="catalog:fixture",checksum=hashlib.sha256(NEW).hexdigest(),
                    size=len(NEW),version="2.0",license_id="MIT",network_approval="fixture-grant",approval_expires_at=10)
        fields.update(overrides)
        return market.Source(**fields)

    async def fetch(self,path,note,start=0):
        self.calls.append(start)
        Path(path).write_bytes(NEW)
        note(len(NEW))
        return len(NEW)

    def transfer(self,card,source,fetch=None,now=1):
        return market.Transfer(self.store,card,source,fetch or self.fetch,now=now)

    async def test_resume_without_approval_does_not_call_fetch(self):
        t=self.transfer(self.card(),self.source(network_approval=""))
        try:
            await t.resume()
        except Hold:
            pass
        self.assertEqual(self.calls,[],"unapproved resume called synthetic fetch")
        self.assertFalse(self.target.exists())

    async def test_expired_update_preserves_old_artifact(self):
        t=self.transfer(self.card(True),self.source(),now=11)
        try:
            await t.update()
        except Hold:
            pass
        self.assertEqual(self.calls,[],"expired update called synthetic fetch")
        self.assertEqual(self.target.read_bytes(),OLD)

    async def test_update_refuses_manifest_size_mismatch(self):
        t=self.transfer(self.card(True),self.source(size=len(NEW)+1))
        with self.assertRaises(Hold):
            await t.update()
        self.assertEqual(self.target.read_bytes(),OLD)

    async def test_update_refuses_false_reported_byte_count(self):
        async def false_count(path,note,start=0):
            await self.fetch(path,note,start)
            return len(NEW)+1
        t=self.transfer(self.card(True),self.source(),false_count)
        with self.assertRaises(Hold):
            await t.update()
        self.assertEqual(self.target.read_bytes(),OLD)

    async def test_registry_failure_does_not_replace_old_artifact(self):
        t=self.transfer(self.card(True),self.source())
        with patch.object(self.store,"save_card",side_effect=OSError("synthetic registry write failure")):
            with self.assertRaises((OSError,Hold)):
                await t.update()
        self.assertEqual(self.target.read_bytes(),OLD,"registry failed but old bytes were overwritten")

    async def test_rollback_restores_actual_old_bytes_or_refuses(self):
        t=self.transfer(self.card(True),self.source())
        await t.update()
        current=self.store.card("fixture-model")
        try:
            restored=self.store.rollback("fixture-model",0)
        except Hold:
            self.assertEqual(self.store.card("fixture-model"),current)
            self.assertEqual(self.target.read_bytes(),NEW)
            return
        self.assertEqual(self.target.read_bytes(),OLD,"metadata rollback did not restore original bytes")
        self.assertEqual(restored.storage_ref,"sha256-"+hashlib.sha256(OLD).hexdigest()[:32])

    async def test_initial_run_without_approval_control(self):
        t=self.transfer(self.card(),self.source(network_approval=""))
        with self.assertRaises(Hold):
            await t.run()
        self.assertEqual(self.calls,[])
        self.assertFalse(self.target.exists())

    async def test_wrong_digest_update_control(self):
        t=self.transfer(self.card(True),self.source(checksum="0"*64))
        with self.assertRaises(Hold):
            await t.update()
        self.assertEqual(self.target.read_bytes(),OLD)

    async def test_live_refusal_during_fetch_drains_without_publication(self):
        import asyncio
        entered=asyncio.Event()
        drained=asyncio.Event()
        allowed=True
        def checkpoint():
            if not allowed:
                raise Hold("profile_changed")
        async def delayed(path,note,start=0):
            Path(path).write_bytes(NEW)
            entered.set()
            try:
                await asyncio.Future()
            finally:
                drained.set()
        t=market.Transfer(self.store,self.card(True),self.source(),delayed,now=1,
                          checkpoint=checkpoint)
        task=asyncio.create_task(t.update())
        await asyncio.wait_for(entered.wait(),2)
        allowed=False
        with self.assertRaisesRegex(Hold,"profile_changed"):
            await asyncio.wait_for(task,3)
        self.assertTrue(drained.is_set())
        self.assertEqual(self.target.read_bytes(),OLD)

    async def test_changed_registry_card_refuses_stale_update(self):
        from dataclasses import replace
        card=self.card(True)
        async def changed(path,note,start=0):
            await self.fetch(path,note,start)
            self.store.save_card(replace(card,display_name="Newer edit"))
            return len(NEW)
        with self.assertRaisesRegex(Hold,"transfer_card_changed"):
            await self.transfer(card,self.source(),changed).update()
        self.assertEqual(self.target.read_bytes(),OLD)
        self.assertEqual(self.store.card(card.model_id).display_name,"Newer edit")

    async def test_symlink_payload_refused(self):
        card=self.card(True)
        other=self.root/"outside"
        other.write_bytes(NEW)
        async def redirected(path,note,start=0):
            Path(path).unlink()
            Path(path).symlink_to(other)
            return len(NEW)
        with self.assertRaises(Hold):
            await self.transfer(card,self.source(),redirected).update()
        self.assertEqual(self.target.read_bytes(),OLD)
        self.assertEqual(other.read_bytes(),NEW)

    async def test_ordinary_update_still_succeeds(self):
        card=self.card(True)
        result=await self.transfer(card,self.source()).update()
        self.assertEqual(result.storage_ref,"sha256-"+hashlib.sha256(NEW).hexdigest()[:32])
        self.assertEqual(self.target.read_bytes(),NEW)

    async def test_stale_initial_card_refused_before_fetch(self):
        from dataclasses import replace
        card=self.card()
        newer=self.store.save_card(replace(card,display_name="Newer edit"))
        with self.assertRaisesRegex(Hold,"transfer_card_changed"):
            await self.transfer(card,self.source()).run()
        self.assertFalse(self.calls)
        self.assertEqual(self.store.card(card.model_id),newer)

    async def test_initial_fetch_cannot_overwrite_newer_card(self):
        from dataclasses import replace
        card=self.card()
        edits=[]
        async def changed(path,note,start=0):
            Path(path).write_bytes(NEW)
            edits.append(self.store.save_card(replace(self.store.card(card.model_id),
                                                      display_name="Newer edit")))
            return len(NEW)
        with self.assertRaisesRegex(Hold,"transfer_card_changed"):
            await self.transfer(card,self.source(),changed).run()
        self.assertEqual(self.store.card(card.model_id),edits[0])
        self.assertFalse(self.target.exists())

    async def test_cancelled_stale_transfer_discards_partial(self):
        from dataclasses import replace
        card=self.card()
        entered=asyncio.Event()
        async def delayed(path,note,start=0):
            Path(path).write_bytes(NEW[:3])
            entered.set()
            await asyncio.Future()
        task=asyncio.create_task(self.transfer(card,self.source(),delayed).run())
        await asyncio.wait_for(entered.wait(),2)
        newer=self.store.save_card(replace(self.store.card(card.model_id),display_name="Newer edit"))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.store.card(card.model_id),newer)
        self.assertFalse((self.root/"transfers"/"partial-fixture-model").exists())

    async def install_verified(self):
        card=self.card()
        async def old_fetch(path,note,start=0):
            Path(path).write_bytes(OLD)
            note(len(OLD))
            return len(OLD)
        return await self.transfer(card,self.source(checksum=hashlib.sha256(OLD).hexdigest(),
                                                    version="1.0"),old_fetch).run()

    async def test_reopened_registry_restores_verified_old_bytes_and_redoes(self):
        old=await self.install_verified()
        new=await self.transfer(old,self.source()).update()
        self.assertEqual(new.artifact_digest,hashlib.sha256(NEW).hexdigest())
        registry=market.Registry(self.root)
        restored=registry.rollback(old.model_id,old.revision)
        self.assertEqual(self.target.read_bytes(),OLD)
        self.assertEqual(restored.artifact_digest,old.artifact_digest)
        self.assertEqual(restored.network_approval,"")
        self.assertEqual(restored.approval_expires_at,0)
        registry.rollback(new.model_id,new.revision)
        self.assertEqual(self.target.read_bytes(),NEW)

    async def test_corrupt_archive_refuses_without_changing_current(self):
        old=await self.install_verified()
        new=await self.transfer(old,self.source()).update()
        archive=self.root/"artifact-revisions"/old.model_id/old.artifact_digest
        archive.write_bytes(b"x"*len(OLD))
        with self.assertRaises(Hold):
            self.store.rollback(old.model_id,old.revision)
        self.assertEqual(self.target.read_bytes(),NEW)
        self.assertEqual(self.store.card(old.model_id),new)

    async def test_archive_is_not_hardlinked_to_active_bytes(self):
        old=await self.install_verified()
        await self.transfer(old,self.source()).update()
        archive=self.root/"artifact-revisions"/old.model_id/old.artifact_digest
        self.store.rollback(old.model_id,old.revision)
        self.assertNotEqual(archive.stat().st_ino,self.target.stat().st_ino)
        self.target.write_bytes(b"x"*len(OLD))
        self.assertEqual(archive.read_bytes(),OLD)

    async def test_rollback_save_failure_preserves_current_bytes(self):
        old=await self.install_verified()
        new=await self.transfer(old,self.source()).update()
        with patch.object(self.store,"save_card",side_effect=OSError("fixture write failure")):
            with self.assertRaises(OSError):
                self.store.rollback(old.model_id,old.revision)
        self.assertEqual(self.target.read_bytes(),NEW)
        self.assertEqual(self.store.card(old.model_id),new)

    async def test_mutated_owned_artifact_refuses_update(self):
        old=await self.install_verified()
        self.target.write_bytes(b"x"*len(OLD))
        with self.assertRaises(Hold):
            await self.transfer(old,self.source()).update()
        self.assertEqual(self.target.read_bytes(),b"x"*len(OLD))
        self.assertEqual(self.store.card(old.model_id),old)

    async def test_registry_store_failure_keeps_history_and_active_bytes(self):
        old=await self.install_verified()
        history=self.store.history(old.model_id)
        with patch.object(self.store,"store",side_effect=OSError("fixture commit failure")):
            with self.assertRaises(OSError):
                await self.transfer(old,self.source()).update()
        self.assertEqual(self.store.history(old.model_id),history)
        self.assertEqual(self.store.card(old.model_id),old)
        self.assertEqual(self.target.read_bytes(),OLD)

    async def test_legacy_history_read_only_migration(self):
        import json
        from dataclasses import replace
        card=self.card()
        previous=replace(card,display_name="Older",revision=0)
        current=replace(card,display_name="Current",revision=1)
        legacy=self.store.revisions/(card.model_id+".jsonl")
        legacy.write_text(json.dumps(previous.__dict__)+"\n")
        self.store.store((current,),{})
        self.assertEqual(self.store.history(card.model_id),(previous,))
        saved=self.store.save_card(replace(current,display_name="Next"))
        self.assertEqual(saved.revision,2)
        self.assertEqual(self.store.history(card.model_id),(previous,current))
        self.assertEqual(legacy.read_text(),json.dumps(previous.__dict__)+"\n")
        reopened=market.Registry(self.root)
        self.assertEqual(reopened.history(card.model_id),(previous,current))

    async def test_history_with_foreign_model_refused(self):
        import json
        from dataclasses import replace
        card=self.card()
        legacy=self.store.revisions/(card.model_id+".jsonl")
        legacy.write_text(json.dumps(replace(card,model_id="different").__dict__)+"\n")
        with self.assertRaises(Hold):
            self.store.history(card.model_id)

class RegistryDecodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = market.Registry(self.root)
        self.card = market.ModelCard(model_id="fixture-model", display_name="Fixture", category="stt")

    def test_oversize_registry_rejected_before_full_read(self):
        with self.registry.path.open("wb") as stream:
            stream.truncate(self.registry.LIMIT + 1)
        with self.assertRaisesRegex(Hold, "registry_too_large"):
            self.registry.load()

    def test_utf8_byte_limit_consistent_on_read_and_write(self):
        import json
        text = json.dumps({"cards": [], "selection": {}, "padding": "é" * self.registry.LIMIT},
                          ensure_ascii=False)
        self.registry.path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(Hold, "registry_too_large"):
            self.registry.load()
        from dataclasses import replace
        card = replace(self.card, display_name="é" * 100)
        with patch.object(self.registry, "LIMIT", 1200):
            self.registry.path.unlink()
            with self.assertRaisesRegex(Hold, "registry_too_large"):
                self.registry.store((card,), {}, history={card.model_id: [card.__dict__]})
        self.assertFalse(self.registry.path.exists())

    def test_symlink_registry_refused(self):
        outside = self.root / "outside.json"
        outside.write_text('{"cards":[]}')
        self.registry.path.symlink_to(outside)
        with self.assertRaises(Hold):
            self.registry.load()
        self.assertEqual(outside.read_text(), '{"cards":[]}')

    def test_duplicate_json_keys_rejected(self):
        self.registry.path.write_text('{"cards":[],"cards":[]}')
        with self.assertRaises(Hold):
            self.registry.load()

    def test_malformed_shapes_and_duplicate_models_hold(self):
        import json
        for data in ([], {"cards": {}}, {"cards": [42]}, {"selection": []},
                     {"cards": [self.card.__dict__, self.card.__dict__]},
                     {"cards": [{**self.card.__dict__, "revision": True}]},
                     {"history": {self.card.model_id: [42]}}):
            with self.subTest(data=data):
                self.registry.path.write_text(json.dumps(data))
                with self.assertRaises(Hold):
                    self.registry.load()

    def test_legacy_history_dangling_symlink_rejected(self):
        path = self.registry.revisions / (self.card.model_id + ".jsonl")
        path.symlink_to(self.root / "missing")
        with self.assertRaises(Hold):
            self.registry.history(self.card.model_id)

    def test_nonfinite_json_rejected(self):
        self.registry.path.write_text('{"cards":[],"padding":NaN}')
        with self.assertRaises(Hold):
            self.registry.load()

class RegistryIsolationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = market.Registry(self.root)
        self.other = market.Registry(self.root)
        self.card = market.ModelCard(model_id="isolation-fixture",
                                     display_name="Fixture", category="stt")

    def test_competing_instance_cannot_read_or_write_during_transaction(self):
        saved = self.registry.save_card(self.card)
        with self.registry._write_guard():
            with self.assertRaisesRegex(Hold, "registry_busy"):
                self.other.load()
            with self.assertRaisesRegex(Hold, "registry_busy"):
                self.other.save_card(self.card)
            self.assertEqual(self.registry.card(saved.model_id), saved)
        self.assertEqual(self.other.card(saved.model_id), saved)

    def test_process_lock_contention_and_release(self):
        import subprocess
        import sys
        # A real independent process holds the same inode until stdin closes.
        code = ("import fcntl, sys; "
                "f = open(sys.argv[1], 'a+b'); "
                "fcntl.flock(f, fcntl.LOCK_EX); "
                "print('locked', flush=True); sys.stdin.read()")
        lock = self.root / ".registry.lock"
        self.registry.save_card(self.card)
        proc = subprocess.Popen([sys.executable, "-c", code, str(lock)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                text=True)
        try:
            import select
            ready, _, _ = select.select([proc.stdout], [], [], 10)
            self.assertTrue(ready, "child did not acquire lock")
            self.assertEqual(proc.stdout.readline().strip(), "locked")
            with self.assertRaisesRegex(Hold, "registry_busy"):
                self.registry.save_card(self.card)
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            proc.stdout.close()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.registry.save_card(self.card), self.card)

    def test_exception_releases_lock_and_preserves_registry(self):
        saved = self.registry.save_card(self.card)
        with self.assertRaises(OSError):
            with self.registry._write_guard():
                raise OSError("fixture fault")
        self.assertEqual(self.other.card(saved.model_id), saved)

    def test_symlink_lock_refused_without_touching_target(self):
        outside = self.root / "outside"
        outside.write_bytes(b"untouched")
        (self.root / ".registry.lock").symlink_to(outside)
        with self.assertRaisesRegex(Hold, "registry_lock_unavailable"):
            self.registry.save_card(self.card)
        self.assertEqual(outside.read_bytes(), b"untouched")
        self.assertFalse(self.registry.path.exists())

    def test_hardlinked_lock_refused(self):
        import os
        outside = self.root / "outside"
        outside.write_bytes(b"untouched")
        os.chmod(outside, 0o600)
        os.link(outside, self.root / ".registry.lock")
        with self.assertRaisesRegex(Hold, "registry_lock_unavailable"):
            self.registry.save_card(self.card)
        self.assertEqual(outside.read_bytes(), b"untouched")

    def test_transfer_persist_rejects_competing_writer(self):
        card = self.registry.save_card(self.card)
        source = market.Source(reference="catalog:fixture",
                               checksum=hashlib.sha256(NEW).hexdigest(),
                               size=len(NEW), license_id="MIT")
        transfer = market.Transfer(self.other, card, source, None, now=1)
        with self.registry._write_guard():
            with self.assertRaisesRegex(Hold, "registry_busy"):
                transfer._persist("REVIEWED")
        self.assertEqual(self.registry.card(card.model_id), card)

class RemovalPartialTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = market.Registry(self.root)
        from dataclasses import replace
        checksum = hashlib.sha256(OLD).hexdigest()
        self.card = self.registry.save_card(replace(
            market.ModelCard(model_id="fixture-model", display_name="Fixture", category="stt"),
            owned_by_jarvis=True, artifact_digest=checksum,
            storage_ref="sha256-" + checksum[:32], disk_size_bytes=len(OLD)))
        self.target = self.root / "artifacts" / self.card.model_id
        self.target.parent.mkdir()
        self.target.write_bytes(OLD)

    def transfer(self):
        source = market.Source(reference="catalog:fixture",
                               checksum=hashlib.sha256(NEW).hexdigest(),
                               size=len(NEW))
        return market.Transfer(self.registry, self.card, source, None, now=1)

    def test_uninstall_write_failure_restores_bytes_and_card(self):
        with patch.object(self.registry, "store", side_effect=OSError("disk fault")):
            with self.assertRaises(OSError):
                self.registry.uninstall(self.card.model_id, approval="fixture")
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertEqual(self.registry.card(self.card.model_id), self.card)
        self.assertEqual(list(self.target.parent.glob(".uninstall-*")), [])

    def test_uninstall_refuses_changed_verified_bytes(self):
        self.target.write_bytes(NEW)
        with self.assertRaises(Hold):
            self.registry.uninstall(self.card.model_id, approval="fixture")
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertEqual(self.registry.card(self.card.model_id), self.card)

    def test_uninstall_refuses_symlink(self):
        outside = self.root / "outside"
        outside.write_bytes(OLD)
        self.target.unlink()
        self.target.symlink_to(outside)
        with self.assertRaises(Hold):
            self.registry.uninstall(self.card.model_id, approval="fixture")
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(outside.read_bytes(), OLD)

    def test_successful_uninstall_removes_only_approved_owned_file(self):
        self.registry.uninstall(self.card.model_id, approval="fixture")
        self.assertFalse(self.target.exists())
        self.assertEqual(self.registry.cards(), ())
        self.assertEqual(list(self.target.parent.glob(".uninstall-*")), [])

    def test_cancelled_partial_does_not_clobber_other_partial(self):
        transfer = self.transfer()
        partial = transfer.root / ("partial-" + self.card.model_id)
        partial.write_bytes(OLD)
        transfer.temp = transfer.root / "fixture-temp"
        transfer.temp.write_bytes(NEW)
        transfer._finish_temp(keep=True)
        self.assertEqual(partial.read_bytes(), OLD)
        self.assertFalse((transfer.root / "fixture-temp").exists())

    def test_cancelled_partial_with_stale_card_is_discarded(self):
        from dataclasses import replace
        transfer = self.transfer()
        transfer.temp = transfer.root / "fixture-temp"
        transfer.temp.write_bytes(NEW)
        self.registry.save_card(replace(self.card, display_name="Changed"))
        transfer._finish_temp(keep=True)
        self.assertFalse((transfer.root / ("partial-" + self.card.model_id)).exists())
        self.assertFalse((transfer.root / "fixture-temp").exists())

    def test_partial_consume_preserves_replacement_inode(self):
        transfer = self.transfer()
        partial = transfer.root / ("partial-" + self.card.model_id)
        partial.write_bytes(OLD)
        from tihulu_jarvis.selected_csv import selection_identity
        identity = selection_identity(str(partial.absolute()), authorized=lambda: True,
                                      max_bytes=len(OLD))
        replacement = transfer.root / "replacement"
        replacement.write_bytes(NEW)
        replacement.replace(partial)
        transfer._consume_partial(identity)
        self.assertEqual(partial.read_bytes(), NEW)

    def test_partial_consume_removes_matching_inode(self):
        transfer = self.transfer()
        partial = transfer.root / ("partial-" + self.card.model_id)
        partial.write_bytes(OLD)
        from tihulu_jarvis.selected_csv import selection_identity
        identity = selection_identity(str(partial.absolute()), authorized=lambda: True,
                                      max_bytes=len(OLD))
        transfer._consume_partial(identity)
        self.assertFalse(partial.exists())

    def test_partial_listing_rejects_symlink_and_invalid_id(self):
        transfer = self.transfer()
        (transfer.root / "partial-link").symlink_to(self.target)
        (transfer.root / "partial-!invalid").write_bytes(OLD)
        (transfer.root / "partial-valid").write_bytes(NEW)
        self.assertEqual(market.resumable_partials(self.root), (("valid", len(NEW)),))

class RemovalRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = market.Registry(self.root)
        from dataclasses import replace
        checksum = hashlib.sha256(OLD).hexdigest()
        self.card = self.registry.save_card(replace(
            market.ModelCard(model_id="fixture-model", display_name="Fixture", category="stt"),
            owned_by_jarvis=True, artifact_digest=checksum,
            storage_ref="sha256-" + checksum[:32], disk_size_bytes=len(OLD)))
        self.target = self.root / "artifacts" / self.card.model_id
        self.target.parent.mkdir()
        self.target.write_bytes(OLD)
        self.backup = self.target.parent / ".uninstall-recovery"

    def interrupt(self, phase):
        import subprocess
        import sys
        # Real process death; no finally clauses or exception cleanup runs.
        code = """
import os, sys
sys.path.insert(0, sys.argv[2])
from tihulu_jarvis import market
r = market.Registry(sys.argv[1])
phase = sys.argv[3]
with r._write_guard():
    if phase == "journal":
        original = r._write_removal_journal
        def hook(record):
            original(record)
            os._exit(73)
        r._write_removal_journal = hook
    elif phase == "moved":
        r.store = lambda *a, **kw: os._exit(73)
    elif phase == "committed":
        original = r.store
        def hook(*a, **kw):
            original(*a, **kw)
            os._exit(73)
        r.store = hook
    elif phase == "cleaned":
        original = market._sync_directory
        def hook(path):
            original(path)
            if (path == r.root / "artifacts"
                    and not (path / "fixture-model").exists()
                    and not (path / ".uninstall-recovery").exists()):
                os._exit(73)
        market._sync_directory = hook
    r.uninstall("fixture-model", approval="fixture")
sys.exit(99)
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.root),
                                 str(Path(market.__file__).resolve().parent.parent), phase],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertTrue(self.registry.removal_journal.exists())

    def assert_old_restored(self):
        reopened = market.Registry(self.root)
        self.assertEqual(reopened.card(self.card.model_id), self.card)
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertFalse(self.backup.exists())
        self.assertFalse(reopened.removal_journal.exists())
        self.assertEqual(reopened.card(self.card.model_id), self.card)

    def assert_new_recovered(self):
        reopened = market.Registry(self.root)
        self.assertEqual(reopened.cards(), ())
        self.assertFalse(self.target.exists())
        self.assertFalse(self.backup.exists())
        self.assertFalse(reopened.removal_journal.exists())
        self.assertEqual(reopened.cards(), ())

    def test_process_death_after_journal_before_move(self):
        self.interrupt("journal")
        self.assert_old_restored()

    def test_process_death_after_move_before_commit(self):
        self.interrupt("moved")
        self.assertFalse(self.target.exists())
        self.assertEqual(self.backup.read_bytes(), OLD)
        self.assert_old_restored()

    def test_process_death_after_registry_commit(self):
        self.interrupt("committed")
        self.assertEqual(self.backup.read_bytes(), OLD)
        self.assert_new_recovered()

    def test_process_death_after_backup_cleanup(self):
        self.interrupt("cleaned")
        self.assertFalse(self.backup.exists())
        self.assert_new_recovered()

    def test_recovery_refuses_corrupt_backup(self):
        self.interrupt("moved")
        self.backup.write_bytes(NEW)
        with self.assertRaisesRegex(Hold, "uninstall_recovery_required"):
            market.Registry(self.root).load()
        self.assertEqual(self.backup.read_bytes(), NEW)
        self.assertTrue(self.registry.removal_journal.exists())
        self.assertFalse(self.target.exists())

    def test_recovery_refuses_unknown_registry_generation(self):
        self.interrupt("moved")
        self.registry.path.write_text('{"cards":[],"selection":{}}')
        with self.assertRaisesRegex(Hold, "uninstall_recovery_required"):
            market.Registry(self.root).load()
        self.assertEqual(self.backup.read_bytes(), OLD)
        self.assertTrue(self.registry.removal_journal.exists())

    def test_recovery_refuses_ambiguous_two_paths(self):
        self.interrupt("moved")
        self.target.write_bytes(NEW)
        with self.assertRaisesRegex(Hold, "uninstall_recovery_required"):
            market.Registry(self.root).load()
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertEqual(self.backup.read_bytes(), OLD)

    def test_bad_journal_never_removes_payload(self):
        self.registry.removal_journal.write_text('{"version":1,"model":"../outside"}')
        with self.assertRaisesRegex(Hold, "uninstall_recovery_required"):
            market.Registry(self.root).load()
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertTrue(self.registry.removal_journal.exists())

    def test_existing_untracked_backup_blocks_removal(self):
        self.backup.write_bytes(NEW)
        with self.assertRaisesRegex(Hold, "uninstall_recovery_required"):
            self.registry.uninstall(self.card.model_id, approval="fixture")
        self.assertEqual(self.backup.read_bytes(), NEW)
        self.assertEqual(self.target.read_bytes(), OLD)

class PublicationRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = market.Registry(self.root)
        self.target = self.root / "artifacts" / "fixture-model"
        self.backup = self.target.parent / ".publish-backup"

    def install_old(self):
        from dataclasses import replace
        digest = hashlib.sha256(OLD).hexdigest()
        card = market.ModelCard(model_id="fixture-model", display_name="Fixture",
                                category="stt")
        for state in ("REVIEWED", "APPROVED", "QUEUED", "DOWNLOADING",
                      "VERIFIED", "REGISTERED"):
            card = market.advance(card, state)
        card = replace(card, owned_by_jarvis=True, artifact_digest=digest,
                       disk_size_bytes=len(OLD), storage_ref="sha256-" + digest[:32])
        self.target.parent.mkdir()
        self.target.write_bytes(OLD)
        return self.registry.save_card(card)

    def child(self, operation, phase):
        import subprocess
        import sys
        code = """
import asyncio, hashlib, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from tihulu_jarvis import market
r = market.Registry(sys.argv[1])
operation, phase = sys.argv[3:5]
target = r.root / "artifacts" / "fixture-model"
backup = target.parent / ".publish-backup"
if phase == "journal":
    original = r._write_journal
    def hook(path, record):
        original(path, record)
        if path == r.publication_journal:
            os._exit(73)
    r._write_journal = hook
elif phase == "linked":
    original = market.os.link
    def hook(src, dst, **kw):
        original(src, dst, **kw)
        if Path(dst) == backup:
            os._exit(73)
    market.os.link = hook
elif phase == "moved":
    original = market.os.replace
    def hook(src, dst, **kw):
        original(src, dst, **kw)
        if Path(dst) == target and r.publication_journal.exists():
            os._exit(73)
    market.os.replace = hook
elif phase == "committed":
    original = r.save_card
    def hook(card):
        saved = original(card)
        if r.publication_journal.exists():
            os._exit(73)
        return saved
    r.save_card = hook
elif phase == "cleaned":
    original = market._sync_directory
    def hook(path):
        original(path)
        if (path == target.parent and r.publication_journal.exists()
                and target.exists() and not backup.exists()):
            os._exit(73)
    market._sync_directory = hook
if operation == "rollback":
    r.rollback("fixture-model", 0)
else:
    data = b"synthetic-new"
    source = market.Source(reference="catalog:fixture",
                           checksum=hashlib.sha256(data).hexdigest(),
                           size=len(data), network_approval="fixture",
                           approval_expires_at=100)
    async def fetch(path, note, start=0):
        Path(path).write_bytes(data)
        note(len(data))
        return len(data)
    t = market.Transfer(r, r.card("fixture-model"), source, fetch, now=1)
    asyncio.run(t.run() if operation == "initial" else t.update())
sys.exit(99)
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.root),
                                 str(Path(market.__file__).resolve().parent.parent),
                                 operation, phase], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertTrue(self.registry.publication_journal.exists())

    def assert_recovered(self, expected):
        reopened = market.Registry(self.root)
        card = reopened.card("fixture-model")
        self.assertEqual(self.target.read_bytes(), expected)
        self.assertEqual(card.artifact_digest, hashlib.sha256(expected).hexdigest())
        self.assertFalse(self.backup.exists())
        self.assertFalse(reopened.publication_journal.exists())
        self.assertEqual(reopened.card("fixture-model"), card)
        return card

    def test_update_death_after_journal(self):
        old = self.install_old()
        self.child("update", "journal")
        self.assertEqual(self.assert_recovered(OLD), old)

    def test_update_death_after_backup_link(self):
        old = self.install_old()
        self.child("update", "linked")
        self.assertEqual(self.assert_recovered(OLD), old)

    def test_update_death_after_payload_move(self):
        old = self.install_old()
        self.child("update", "moved")
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertEqual(self.assert_recovered(OLD), old)

    def test_update_death_after_registry_commit(self):
        old = self.install_old()
        self.child("update", "committed")
        self.assertGreater(self.assert_recovered(NEW).revision, old.revision)

    def test_update_death_after_cleanup(self):
        self.install_old()
        self.child("update", "cleaned")
        self.assert_recovered(NEW)

    def test_initial_publication_death_before_commit(self):
        self.registry.save_card(market.ModelCard(model_id="fixture-model",
                                                display_name="Fixture", category="stt"))
        self.child("initial", "moved")
        reopened = market.Registry(self.root)
        card = reopened.card("fixture-model")
        self.assertEqual(card.state, "VERIFIED")
        self.assertFalse(card.artifact_digest)
        self.assertFalse(self.target.exists())
        self.assertFalse(reopened.publication_journal.exists())

    def test_initial_publication_death_after_commit(self):
        self.registry.save_card(market.ModelCard(model_id="fixture-model",
                                                display_name="Fixture", category="stt"))
        self.child("initial", "committed")
        self.assertEqual(self.assert_recovered(NEW).state, "REGISTERED")

    def prepare_rollback(self):
        old = self.install_old()
        source = market.Source(reference="catalog:fixture",
                               checksum=hashlib.sha256(NEW).hexdigest(),
                               size=len(NEW), network_approval="fixture",
                               approval_expires_at=100)
        async def fetch(path, note, start=0):
            Path(path).write_bytes(NEW)
            note(len(NEW))
            return len(NEW)
        return asyncio.run(market.Transfer(self.registry, old, source, fetch, now=1).update())

    def test_rollback_death_before_commit(self):
        current = self.prepare_rollback()
        self.child("rollback", "moved")
        self.assertEqual(self.assert_recovered(NEW), current)

    def test_rollback_death_after_commit(self):
        current = self.prepare_rollback()
        self.child("rollback", "committed")
        restored = self.assert_recovered(OLD)
        self.assertGreater(restored.revision, current.revision)
        self.assertEqual(restored.network_approval, "")

    def test_corrupt_backup_blocks_recovery(self):
        self.install_old()
        self.child("update", "moved")
        self.backup.write_bytes(b"corrupt")
        with self.assertRaisesRegex(Hold, "publication_recovery_required"):
            market.Registry(self.root).load()
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertEqual(self.backup.read_bytes(), b"corrupt")
        self.assertTrue(self.registry.publication_journal.exists())

    def test_two_journals_fail_closed(self):
        self.install_old()
        self.child("update", "moved")
        self.registry.removal_journal.write_text("{}")
        with self.assertRaisesRegex(Hold, "registry_recovery_conflict"):
            market.Registry(self.root).load()
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertEqual(self.backup.read_bytes(), OLD)

class PersistentPartialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = market.Registry(self.root)
        self.card = self.registry.save_card(market.ModelCard(
            model_id="fixture-model", display_name="Fixture", category="stt", state="CANCELLED"))
        self.source = market.Source(reference="catalog:fixture",
                                    checksum=hashlib.sha256(NEW).hexdigest(), size=len(NEW),
                                    version="2.0", license_id="MIT",
                                    network_approval="fixture", approval_expires_at=100)
        self.calls = []

    async def fetch(self, path, note, start=0):
        self.calls.append(start)
        with open(path, "ab") as stream:
            stream.write(NEW[start:])
        note(len(NEW))
        return len(NEW)

    def transfer(self, card=None, source=None):
        return market.Transfer(market.Registry(self.root), card or self.card,
                               source or self.source, self.fetch, now=1)

    def partial(self):
        t = self.transfer()
        t.temp = t.root / "fixture-temp"
        t.temp.write_bytes(NEW[:3])
        t._finish_temp(keep=True)
        payload, record = t._partial_paths()
        self.assertTrue(payload.exists())
        self.assertTrue(record.exists())
        return payload, record

    async def test_restart_resume_valid_prefix_and_fresh_grant(self):
        from dataclasses import replace
        payload, record = self.partial()
        t = self.transfer(source=replace(self.source, network_approval="fresh", approval_expires_at=200))
        saved = await t.resume(start=3)
        self.assertEqual(self.calls, [3])
        self.assertEqual(saved.artifact_digest, self.source.checksum)
        self.assertFalse(payload.exists())
        self.assertFalse(record.exists())

    async def test_changed_prefix_never_reaches_transport(self):
        payload, record = self.partial()
        payload.write_bytes(b"bad")
        with self.assertRaisesRegex(Hold, "transfer_resume_identity_unverified"):
            await self.transfer().resume(start=3)
        self.assertEqual(self.calls, [])
        self.assertEqual(payload.read_bytes(), b"bad")
        self.assertTrue(record.exists())

    async def test_changed_source_terms_never_reach_transport(self):
        from dataclasses import replace
        self.partial()
        for source in (replace(self.source, reference="catalog:other"),
                       replace(self.source, checksum="0"*64),
                       replace(self.source, version="3.0"),
                       replace(self.source, license_id="other"),
                       replace(self.source, size=len(NEW)+1)):
            with self.subTest(source=source):
                with self.assertRaisesRegex(Hold, "transfer_resume_identity_unverified"):
                    await self.transfer(source=source).resume(start=3)
        self.assertEqual(self.calls, [])

    async def test_changed_card_revision_never_reaches_transport(self):
        from dataclasses import replace
        self.partial()
        newer = self.registry.save_card(replace(self.card, display_name="Changed"))
        with self.assertRaisesRegex(Hold, "transfer_resume_identity_unverified"):
            await self.transfer(card=newer).resume(start=3)
        self.assertEqual(self.calls, [])

    async def test_legacy_unbound_partial_never_reaches_transport(self):
        t = self.transfer()
        payload, metadata = t._partial_paths()
        payload.write_bytes(NEW[:3])
        with self.assertRaisesRegex(Hold, "transfer_resume_identity_unverified"):
            await t.resume(start=3)
        self.assertEqual(self.calls, [])
        self.assertTrue(payload.exists())
        self.assertFalse(metadata.exists())

    async def test_corrupt_or_symlink_metadata_never_reaches_transport(self):
        payload, record = self.partial()
        original = record.read_bytes()
        record.write_bytes(b'{"version":1,"version":1}')
        with self.assertRaisesRegex(Hold, "transfer_resume_identity_unverified"):
            await self.transfer().resume(start=3)
        record.unlink()
        outside = self.root / "outside"
        outside.write_bytes(original)
        record.symlink_to(outside)
        with self.assertRaisesRegex(Hold, "transfer_resume_identity_unverified"):
            await self.transfer().resume(start=3)
        self.assertEqual(self.calls, [])
        self.assertEqual(outside.read_bytes(), original)
        self.assertTrue(payload.exists())

    async def test_expired_grant_cannot_resume_valid_partial(self):
        from dataclasses import replace
        self.partial()
        with self.assertRaisesRegex(Hold, "network_not_authorized"):
            await self.transfer(source=replace(self.source, approval_expires_at=0.5)).resume(start=3)
        self.assertEqual(self.calls, [])

    async def test_metadata_does_not_contain_raw_source_or_grant(self):
        _, record = self.partial()
        raw = record.read_text()
        self.assertNotIn(self.source.reference, raw)
        self.assertNotIn('"network_approval"', raw)
        self.assertNotIn('"approval_expires_at"', raw)

class StrictRangeTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, response, start=3, prefix=b"abc", limit=32):
        from tihulu_jarvis.transfer_http import loopback_fetch
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "payload"
            target.write_bytes(prefix)
            async def serve(reader, writer):
                try:
                    await reader.readuntil(b"\r\n\r\n")
                    writer.write(response)
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
            server = await asyncio.start_server(serve, "127.0.0.1", 0)
            async with server:
                url = "http://127.0.0.1:%d/weights" % server.sockets[0].getsockname()[1]
                try:
                    size = await asyncio.wait_for(loopback_fetch(url, limit)(str(target), lambda n: None, start), 3)
                    return size, target.read_bytes(), None
                except Hold as exc:
                    return None, target.read_bytes(), str(exc)

    async def test_status_code_not_reason_phrase_controls_range(self):
        result = await self.request(b"HTTP/1.1 206 Custom\r\nContent-Range: bytes 3-5/6\r\nContent-Length: 3\r\n\r\ndef")
        self.assertEqual(result, (6, b"abcdef", None))

    async def test_200_with_custom_reason_restarts_from_zero(self):
        result = await self.request(b"HTTP/1.1 200 Custom\r\nContent-Length: 6\r\n\r\nabcdef")
        self.assertEqual(result, (6, b"abcdef", None))

    async def test_duplicate_length_rejected_before_write(self):
        result = await self.request(b"HTTP/1.1 206 Partial Content\r\nContent-Range: bytes 3-5/6\r\nContent-Length: 3\r\ncontent-length: 4\r\n\r\ndef")
        self.assertEqual(result, (None, b"abc", "transfer_invalid_response"))

    async def test_inconsistent_range_rejected_before_write(self):
        for bounds in (b"bytes 2-5/6", b"bytes 3-4/6", b"bytes 3-6/6", b"bytes 3-2/3"):
            with self.subTest(bounds=bounds):
                result = await self.request(b"HTTP/1.1 206 Partial Content\r\nContent-Range: " + bounds + b"\r\n\r\ndef")
                self.assertEqual(result, (None, b"abc", "transfer_range_not_honoured"))

    async def test_conflicting_range_length_rejected_before_write(self):
        result = await self.request(b"HTTP/1.1 206 Partial Content\r\nContent-Range: bytes 3-5/6\r\nContent-Length: 4\r\n\r\ndefg")
        self.assertEqual(result, (None, b"abc", "transfer_invalid_response"))

    async def test_truncated_range_rejected(self):
        result = await self.request(b"HTTP/1.1 206 Partial Content\r\nContent-Range: bytes 3-5/6\r\n\r\nd")
        self.assertEqual(result[0], None)
        self.assertEqual(result[2], "transfer_truncated")

    async def test_unsolicited_range_rejected(self):
        result = await self.request(b"HTTP/1.1 206 Custom\r\nContent-Range: bytes 0-2/3\r\n\r\nabc", start=0, prefix=b"")
        self.assertEqual(result, (None, b"", "transfer_range_not_honoured"))

    async def test_range_prefix_offset_mismatch_rejected(self):
        result = await self.request(b"HTTP/1.1 206 Custom\r\nContent-Range: bytes 3-5/6\r\n\r\ndef", prefix=b"ab")
        self.assertEqual(result, (None, b"ab", "transfer_resume_offset_unverified"))

class PartialQueueIdentityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = market.Registry(self.root)
        self.card = self.registry.save_card(market.ModelCard(
            model_id="fixture-model", display_name="Fixture", category="stt",
            state="CANCELLED", source_ref="catalog:fixture",
            source_checksum=hashlib.sha256(NEW).hexdigest(), source_size=len(NEW),
            source_version="2.0", license_id="MIT", network_approval="fixture",
            approval_expires_at=1e12))
        self.transfer = market.Transfer(self.registry, self.card,
                                        market.source_of(self.card), None)
        self.payload, self.metadata = self.transfer._partial_paths()

    def bound_partial(self):
        self.transfer.temp = self.transfer.root / "fixture-temp"
        self.transfer.temp.write_bytes(NEW[:3])
        self.transfer._finish_temp(keep=True)
        self.assertTrue(self.payload.exists())
        self.assertTrue(self.metadata.exists())
        return self.registry.partial_status(self.card.model_id)

    def test_bound_candidate_is_resumable_and_discard_removes_pair(self):
        status = self.bound_partial()
        self.assertEqual(status["state"], "RESUMABLE")
        self.registry.discard_partial(self.card.model_id, status["identity"])
        self.assertFalse(self.payload.exists())
        self.assertFalse(self.metadata.exists())
        self.assertIsNone(self.registry.partial_status(self.card.model_id))
        self.assertEqual(self.registry.card(self.card.model_id), self.card)

    def test_legacy_partial_is_not_resumable_but_explicitly_discardable(self):
        self.payload.write_bytes(NEW[:3])
        status = self.registry.partial_status(self.card.model_id)
        self.assertEqual(status["state"], "UNVERIFIED")
        self.registry.discard_partial(self.card.model_id, status["identity"])
        self.assertFalse(self.payload.exists())

    def test_changed_payload_blocks_stale_discard(self):
        status = self.bound_partial()
        replacement = self.transfer.root / "replacement"
        replacement.write_bytes(b"new")
        replacement.replace(self.payload)
        with self.assertRaisesRegex(Hold, "partial_changed"):
            self.registry.discard_partial(self.card.model_id, status["identity"])
        self.assertEqual(self.payload.read_bytes(), b"new")
        self.assertTrue(self.metadata.exists())

    def test_changed_metadata_blocks_stale_discard(self):
        status = self.bound_partial()
        self.metadata.write_text("{}")
        with self.assertRaisesRegex(Hold, "partial_changed"):
            self.registry.discard_partial(self.card.model_id, status["identity"])
        self.assertTrue(self.payload.exists())
        self.assertEqual(self.metadata.read_text(), "{}")

    def test_metadata_only_is_discardable(self):
        self.bound_partial()
        self.payload.unlink()
        status = self.registry.partial_status(self.card.model_id)
        self.assertEqual(status["state"], "UNVERIFIED")
        self.registry.discard_partial(self.card.model_id, status["identity"])
        self.assertFalse(self.metadata.exists())

    def test_symlink_and_hardlink_are_not_discardable(self):
        outside = self.root / "outside"
        outside.write_bytes(NEW[:3])
        self.payload.symlink_to(outside)
        with self.assertRaisesRegex(Hold, "partial_file_untrusted"):
            self.registry.partial_status(self.card.model_id)
        self.payload.unlink()
        import os
        os.link(outside, self.payload)
        with self.assertRaisesRegex(Hold, "partial_file_untrusted"):
            self.registry.partial_status(self.card.model_id)
        self.assertEqual(outside.read_bytes(), NEW[:3])

    def test_changed_card_marks_bound_candidate_unverified(self):
        from dataclasses import replace
        self.bound_partial()
        self.registry.save_card(replace(self.card, display_name="Changed"))
        status = self.registry.partial_status(self.card.model_id)
        self.assertEqual(status["state"], "UNVERIFIED")
        self.assertTrue(self.payload.exists())

class ConditionalRegistryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = market.Registry(self.root)
        self.other = market.Registry(self.root)
        self.entry = market.Entry(model_id="fixture-model", display_name="Fixture",
                                  category="stt", source_ref="catalog:fixture",
                                  source_checksum=hashlib.sha256(NEW).hexdigest(),
                                  source_size=len(NEW), source_version="2.0")
        self.card = self.entry.checked()

    def test_exact_card_compare_and_swap_rejects_stale_editor(self):
        from dataclasses import replace
        original = self.registry.save_card(self.card, expected=None)
        newer = self.other.save_card(replace(original, display_name="Newer"), expected=original)
        with self.assertRaisesRegex(Hold, "registry_card_changed"):
            self.registry.save_card(replace(original, display_name="Stale"), expected=original)
        self.assertEqual(self.registry.card(original.model_id), newer)

    def test_create_only_cannot_replace_existing_model(self):
        self.registry.save_card(self.card, expected=None)
        with self.assertRaisesRegex(Hold, "registry_card_changed"):
            self.other.save_card(self.card, expected=None)

    def test_generation_compare_and_swap_rejects_stale_registry(self):
        generation, cards, selection = self.registry.snapshot()
        self.other.save_card(self.card)
        with self.assertRaisesRegex(Hold, "registry_generation_changed"):
            self.registry.store_if_current(generation, cards, selection)
        self.assertEqual(self.registry.card(self.card.model_id), self.card)

    def test_matching_generation_can_commit(self):
        generation, _, selection = self.registry.snapshot()
        self.registry.store_if_current(generation, (self.card,), selection)
        self.assertEqual(self.registry.card(self.card.model_id), self.card)

    def test_catalog_approval_matches_exact_selected_terms(self):
        from dataclasses import replace
        original = self.registry.record_discovered(self.card)
        changed = self.other.save_card(replace(original, source_checksum="0"*64), expected=original)
        with self.assertRaisesRegex(Hold, "catalog_entry_changed"):
            self.registry.approve_catalog_entry(self.entry, "fixture", 1e12)
        self.assertEqual(self.registry.card(original.model_id), changed)
        self.assertFalse(changed.approved_source())

    def test_matching_catalog_approval_and_rediscovery_preserve_grant(self):
        approved = self.registry.approve_catalog_entry(self.entry, "fixture", 1e12)
        self.assertTrue(approved.approved_source())
        self.assertEqual(self.other.record_discovered(self.card), approved)
        self.assertEqual(self.registry.card(approved.model_id), approved)

    def test_changed_discovery_cannot_replace_approved_model(self):
        from dataclasses import replace
        approved = self.registry.approve_catalog_entry(self.entry, "fixture", 1e12)
        with self.assertRaisesRegex(Hold, "registry_discovery_conflict"):
            self.other.record_discovered(replace(self.card, source_checksum="0"*64))
        self.assertEqual(self.registry.card(approved.model_id), approved)

    def test_discovery_cannot_replace_installed_model(self):
        from dataclasses import replace
        installed = self.registry.save_card(replace(self.card, installed=True,
                                                     registered=True, state="REGISTERED"))
        with self.assertRaisesRegex(Hold, "registry_discovery_conflict"):
            self.other.record_discovered(self.card)
        self.assertEqual(self.registry.card(installed.model_id), installed)

class TransferLeaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = market.Registry(self.root)
        self.other = market.Registry(self.root)
        self.source = market.Source(reference="catalog:fixture",
                                    checksum=hashlib.sha256(NEW).hexdigest(),
                                    size=len(NEW), network_approval="fixture",
                                    approval_expires_at=1e12)
        self.card = self.registry.save_card(market.ModelCard(
            model_id="fixture-model", display_name="Fixture", category="stt"))
        self.calls = []

    async def fetch(self, path, note, start=0):
        self.calls.append(start)
        Path(path).write_bytes(NEW)
        note(len(NEW))
        return len(NEW)

    async def test_competing_transfer_cannot_call_transport(self):
        entered = asyncio.Event()
        async def stalled(path, note):
            entered.set()
            await asyncio.Future()
        first = market.Transfer(self.registry, self.card, self.source, stalled, now=1)
        task = asyncio.create_task(first.run())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            current = self.other.card(self.card.model_id)
            second = market.Transfer(self.other, current, self.source, self.fetch, now=1)
            with self.assertRaisesRegex(Hold, "transfer_busy"):
                await second.resume()
            self.assertEqual(self.calls, [])
            self.assertEqual(self.other.card(self.card.model_id), current)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        # Cancellation drains the first operation and releases the lease.
        current = self.other.card(self.card.model_id)
        second = market.Transfer(self.other, current, self.source, self.fetch, now=1)
        result = await second.resume()
        self.assertEqual(result.artifact_digest, self.source.checksum)
        self.assertEqual(self.calls, [0])

    async def test_different_models_can_transfer_while_first_awaits(self):
        entered = asyncio.Event()
        async def stalled(path, note):
            entered.set()
            await asyncio.Future()
        task = asyncio.create_task(market.Transfer(
            self.registry, self.card, self.source, stalled, now=1).run())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            other_card = self.other.save_card(market.ModelCard(
                model_id="other-model", display_name="Other", category="stt"))
            result = await market.Transfer(self.other, other_card, self.source,
                                           self.fetch, now=1).run()
            self.assertEqual(result.artifact_digest, self.source.checksum)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_explicit_discard_refuses_during_active_lease(self):
        transfer = market.Transfer(self.registry, self.card, self.source, self.fetch, now=1)
        payload, metadata = transfer._partial_paths()
        payload.write_bytes(NEW[:3])
        status = self.other.partial_status(self.card.model_id)
        with self.registry.transfer_lease(self.card.model_id):
            with self.assertRaisesRegex(Hold, "transfer_busy"):
                self.other.discard_partial(self.card.model_id, status["identity"])
            self.assertEqual(payload.read_bytes(), NEW[:3])
        self.other.discard_partial(self.card.model_id, status["identity"])
        self.assertFalse(payload.exists())
        self.assertFalse(metadata.exists())

    async def test_real_process_exit_releases_transfer_lease(self):
        import subprocess
        import sys
        import select
        code = """
import os, sys
sys.path.insert(0, sys.argv[2])
from tihulu_jarvis import market
r = market.Registry(sys.argv[1])
with r.transfer_lease("fixture-model"):
    print("locked", flush=True)
    sys.stdin.read()
    os._exit(73)
"""
        proc = subprocess.Popen([sys.executable, "-c", code, str(self.root),
                                 str(Path(market.__file__).resolve().parent.parent)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                text=True)
        try:
            ready, _, _ = select.select([proc.stdout], [], [], 10)
            self.assertTrue(ready)
            self.assertEqual(proc.stdout.readline().strip(), "locked")
            with self.assertRaisesRegex(Hold, "transfer_busy"):
                with self.registry.transfer_lease(self.card.model_id):
                    self.fail("competing process entered")
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            proc.stdout.close()
        self.assertEqual(proc.returncode, 73)
        with self.registry.transfer_lease(self.card.model_id):
            self.assertEqual(self.registry.card(self.card.model_id), self.card)

    async def test_symlink_lease_file_refused_before_transport(self):
        folder = self.root / ".transfer-locks"
        folder.mkdir(mode=0o700)
        outside = self.root / "outside"
        outside.write_bytes(b"untouched")
        (folder / (self.card.model_id + ".lock")).symlink_to(outside)
        with self.assertRaisesRegex(Hold, "transfer_lock_unavailable"):
            await market.Transfer(self.registry, self.card, self.source, self.fetch, now=1).run()
        self.assertEqual(self.calls, [])
        self.assertEqual(outside.read_bytes(), b"untouched")

    async def test_lease_released_after_transport_failure(self):
        async def failure(path, note):
            raise Hold("fixture_failure")
        with self.assertRaisesRegex(Hold, "fixture_failure"):
            await market.Transfer(self.registry, self.card, self.source, failure, now=1).run()
        with self.other.transfer_lease(self.card.model_id):
            self.assertEqual(self.other.card(self.card.model_id).state, "FAILED")

class LifecycleLeaseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = market.Registry(self.root)
        self.other = market.Registry(self.root)
        from dataclasses import replace
        digest = hashlib.sha256(OLD).hexdigest()
        self.card = self.registry.save_card(replace(
            market.ModelCard(model_id="fixture-model", display_name="Fixture", category="stt"),
            owned_by_jarvis=True, artifact_digest=digest,
            storage_ref="sha256-" + digest[:32], disk_size_bytes=len(OLD)))
        self.target = self.root / "artifacts" / self.card.model_id
        self.target.parent.mkdir()
        self.target.write_bytes(OLD)

    def test_active_transfer_blocks_removal_without_mutation(self):
        with self.registry.transfer_lease(self.card.model_id):
            with self.assertRaisesRegex(Hold, "transfer_busy"):
                self.other.uninstall(self.card.model_id, approval="fixture")
            self.assertEqual(self.target.read_bytes(), OLD)
            self.assertEqual(self.other.card(self.card.model_id), self.card)
            self.assertFalse(self.other.removal_journal.exists())
        self.other.uninstall(self.card.model_id, approval="fixture")
        self.assertFalse(self.target.exists())
        self.assertEqual(self.other.cards(), ())

    def test_active_transfer_blocks_unregister_and_rollback(self):
        from dataclasses import replace
        newer = self.registry.save_card(replace(self.card, display_name="Newer"))
        with self.registry.transfer_lease(self.card.model_id):
            with self.assertRaisesRegex(Hold, "transfer_busy"):
                self.other.unregister(self.card.model_id)
            with self.assertRaisesRegex(Hold, "transfer_busy"):
                self.other.rollback(self.card.model_id, self.card.revision)
            self.assertEqual(self.other.card(self.card.model_id), newer)
            self.assertEqual(self.target.read_bytes(), OLD)
        self.other.unregister(self.card.model_id)
        self.assertEqual(self.other.cards(), ())
        self.assertEqual(self.target.read_bytes(), OLD)

    def test_unrelated_model_removal_does_not_block_on_active_lease(self):
        other = self.registry.save_card(market.ModelCard(
            model_id="other-model", display_name="Other", category="stt"))
        with self.registry.transfer_lease(self.card.model_id):
            self.other.unregister(other.model_id)
            self.assertEqual(self.other.cards(), (self.card,))
            self.assertEqual(self.target.read_bytes(), OLD)

    def test_imported_uninstall_does_not_reenter_model_lease(self):
        other = self.registry.save_card(market.ModelCard(
            model_id="other-model", display_name="Other", category="stt"))
        self.other.uninstall(other.model_id, approval="fixture")
        self.assertEqual(self.other.cards(), (self.card,))

    def test_missing_owned_payload_uninstall_releases_lease(self):
        self.target.unlink()
        self.other.uninstall(self.card.model_id, approval="fixture")
        self.assertEqual(self.other.cards(), ())
        with self.registry.transfer_lease(self.card.model_id):
            self.assertFalse(self.target.exists())

if __name__=="__main__":
    unittest.main(verbosity=2)

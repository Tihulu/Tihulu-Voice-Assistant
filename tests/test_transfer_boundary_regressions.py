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

if __name__=="__main__":
    unittest.main(verbosity=2)

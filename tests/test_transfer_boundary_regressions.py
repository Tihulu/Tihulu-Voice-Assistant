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

if __name__=="__main__":
    unittest.main(verbosity=2)

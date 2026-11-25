# SPDX-License-Identifier: AGPL-3.0-only
"""Evidence-only #99 probes. No transport, model, device or owner data."""
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

if __name__=="__main__":
    unittest.main(verbosity=2)

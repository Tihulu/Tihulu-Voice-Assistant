import hashlib
import unittest
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused
from market_fixture import Manifest, FixtureRegistry, FixtureTransfer, registry_target

class MarketTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("r5", "session", "profile", 1, "route", "LOCAL_ONLY", False, True)
        self.data = b"synthetic"
        self.m = Manifest("model", "rev1", "file.bin", len(self.data),
                          hashlib.sha256(self.data).hexdigest(), "fixture-license")
        self.g = FixtureGateway(self.ctx, (profiles.Rule("external_network", (), "allow"), profiles.Rule("publish_artifact", (), "allow"),),
                                clock=profiles.PolicyClock())
        self.r = self.registry("registry")
        self.n=0

    def registry(self, identity):
        root=self.g.fixture_grant(identity,Request(self.ctx,"publish",registry_target(self.ctx),"local"),60)
        return FixtureRegistry(self.g,self.ctx,root)

    def publication(self,t,r):
        plan=r.plan(t.manifest)
        self.n+=1
        grant=self.g.fixture_grant("publish-"+str(self.n),Request(self.ctx,"publish",plan.target(),"local"),60)
        return plan,grant

    def finish(self,t,r=None):
        r=self.r if r is None else r
        return t.finish(r,*self.publication(t,r))

    def transfer(self, m, grant_id):
        grant = self.g.fixture_grant(grant_id, Request(self.ctx, "transfer", m.identity, "local", "public"), 60)
        return FixtureTransfer(self.g, grant, self.ctx, m)

    def test_chunked_exact_integrity_not_trust(self):
        t = self.transfer(self.m, "a")
        self.assertEqual(t.append(self.m.identity, 0, self.data[:3]), 3)
        t.append(self.m.identity, 3, self.data[3:])
        self.assertEqual(self.finish(t), "INTEGRITY_ONLY_NOT_TRUST")
        self.assertEqual(self.r.current("model"), self.m.identity)
        with self.assertRaises(Refused):
            self.finish(t)

    def test_invalid_offset_manifest_and_length(self):
        t = self.transfer(self.m, "a")
        for mid, off, chunk in (("other", 0, b"a"), (self.m.identity, True, b"a"),
                                (self.m.identity, 1, b"a"), (self.m.identity, 0, b"x"*4097)):
            with self.assertRaises(Refused):
                t.append(mid, off, chunk)
        with self.assertRaises(Refused):
            self.finish(t)
        self.assertIsNone(self.r.current("model"))

    def test_bad_digest_does_not_replace_existing(self):
        t = self.transfer(self.m, "a")
        t.append(self.m.identity, 0, self.data)
        self.finish(t)
        m2 = replace(self.m, revision="rev2")
        t2 = self.transfer(m2, "b")
        t2.append(m2.identity, 0, b"x" * m2.size)
        with self.assertRaises(Refused):
            self.finish(t2)
        self.assertEqual(self.r.current("model"), self.m.identity)

    def test_revoke_before_commit(self):
        t = self.transfer(self.m, "a")
        t.append(self.m.identity, 0, self.data)
        self.g.revoke("a")
        with self.assertRaises(Refused):
            self.finish(t)
        self.assertIsNone(self.r.current("model"))

    def test_invalid_manifest(self):
        for m in (replace(self.m, size=True), replace(self.m, size=65537),
                  replace(self.m, sha256="bad"), replace(self.m, file="../outside")):
            with self.assertRaises(Refused):
                m.checked()

    def test_transfer_grant_cannot_start_two_objects(self):
        transfer=self.transfer(self.m,"once")
        with self.assertRaisesRegex(Refused,"grant_already_claimed"):
            FixtureTransfer(self.g,transfer._grant,self.ctx,self.m)
        transfer.cancel()
        with self.assertRaises(Refused):
            FixtureTransfer(self.g,transfer._grant,self.ctx,self.m)
        self.assertEqual(bytes(transfer._data),b"")

    def test_cancel_clears_partial_and_blocks_finish(self):
        transfer=self.transfer(self.m,"cancel")
        transfer.append(self.m.identity,0,self.data[:3])
        transfer.cancel()
        self.assertEqual(bytes(transfer._data),b"")
        with self.assertRaises(Refused):
            transfer.append(self.m.identity,3,self.data[3:])
        with self.assertRaises(Refused):
            self.finish(transfer)
        self.assertIsNone(self.r.current("model"))

    def test_success_consumes_transfer_grant_and_rejects_direct_replay(self):
        t=self.transfer(self.m,"a")
        t.append(self.m.identity,0,self.data)
        self.finish(t)
        with self.assertRaises(Refused):
            FixtureTransfer(self.g,t._grant,self.ctx,self.m)
        self.assertEqual(self.r.current_bytes("model"),self.data)

    def test_failed_commit_discards_partial_and_retires_grant(self):
        t=self.transfer(self.m,"a")
        t.append(self.m.identity,0,b"x"*self.m.size)
        with self.assertRaises(Refused):
            self.finish(t)
        self.assertEqual(bytes(t._data),b"")
        self.assertTrue(t._done)
        with self.assertRaises(Refused):
            self.g.checkpoint(t._grant,t._req)
        self.assertIsNone(self.r.current("model"))

    def test_publication_grant_bound_to_exact_registry(self):
        t=self.transfer(self.m,"transfer")
        t.append(self.m.identity,0,self.data)
        plan,grant=self.publication(t,self.r)
        other=self.registry("other-root")
        with self.assertRaises(Refused):
            t.finish(other,other.plan(self.m),grant)
        self.assertIsNone(other.current("model"))
        self.assertIsNone(self.r.current("model"))
        self.g.checkpoint(grant,grant.request)

    def test_stale_publication_cannot_replace_newer_bytes(self):
        a=self.transfer(self.m,"a")
        a.append(self.m.identity,0,self.data)
        stale=self.publication(a,self.r)
        other=replace(self.m,revision="rev2")
        b=self.transfer(other,"b")
        b.append(other.identity,0,self.data)
        self.finish(b)
        with self.assertRaisesRegex(Refused,"stale_publication"):
            a.finish(self.r,*stale)
        self.assertEqual(self.r.current("model"),other.identity)

    def test_revoked_publication_after_claim_preserves_previous(self):
        from unittest.mock import patch
        t=self.transfer(self.m,"a")
        t.append(self.m.identity,0,self.data)
        self.finish(t)
        other=replace(self.m,revision="rev2")
        b=self.transfer(other,"b")
        b.append(other.identity,0,self.data)
        plan,grant=self.publication(b,self.r)
        original=self.g.claim
        def revoke(g,req):
            original(g,req)
            self.g.revoke(g.identity)
        with patch.object(self.g,"claim",side_effect=revoke):
            with self.assertRaises(Refused):
                b.finish(self.r,plan,grant)
        self.assertEqual(self.r.current("model"),self.m.identity)

    def test_registry_root_revoke_and_close(self):
        t=self.transfer(self.m,"a")
        t.append(self.m.identity,0,self.data)
        self.finish(t)
        self.g.revoke(self.r._root.identity)
        with self.assertRaises(Refused):
            self.r.current_bytes("model")
        self.r.close()
        self.assertEqual(self.r._objects,{})

# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway, Refused
from offline_browser import OfflineBrowser

class OfflineBrowserTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.rules=tuple(profiles.Rule(x,(),"allow") for x in ("browser_navigate","filesystem_read","host_shell"))
        self.g=FixtureGateway(self.ctx,self.rules,clock=profiles.PolicyClock())
        self.b=OfflineBrowser(self.g,self.ctx,"fixture","private-profile","tab","page","a"*64)
    def begin(self,op,name,element=None):
        call=self.b.prepare(op,element)
        grant=self.g.fixture_grant(name,call.request(self.ctx),60)
        self.b.begin(call,grant)
        return call,grant
    def opened(self):
        self.begin("offline_open","open")
        self.b.complete()
    def test_offline_open_read_click_and_new_read(self):
        self.opened()
        self.begin("offline_read","read","message")
        self.b.complete("before")
        self.assertEqual(self.b.result(),sha256(b"before").hexdigest())
        self.begin("offline_click","click","change")
        with self.assertRaises(Refused):
            self.b.result()
        self.b.complete()
        self.begin("offline_read","read2","message")
        self.b.complete("after")
        self.assertEqual(self.b.result(),sha256(b"after").hexdigest())
    def test_wrong_profile_digest_element_and_replay_denied(self):
        self.opened()
        call=self.b.prepare("offline_read","message")
        grant=self.g.fixture_grant("read",call.request(self.ctx),60)
        for index,value in ((0,"other"),(3,"b"*64),(4,"change")):
            args=list(call.args)
            args[index]=(args[index][0],value)
            with self.assertRaises(Refused):
                self.b.begin(replace(call,args=tuple(args)),grant)
        self.b.begin(call,grant)
        self.b.complete("before")
        with self.assertRaises(Refused):
            self.b.begin(call,grant)
    def test_parent_revocation_and_close(self):
        self.opened()
        self.begin("offline_read","read","message")
        self.b.complete("before")
        self.g.revoke("open")
        with self.assertRaises(Refused):
            self.b.result()
        with self.assertRaises(Refused):
            self.b.prepare("offline_click","change")
    def test_no_network_permission_needed_but_host_read_required(self):
        call=self.b.prepare("offline_open")
        for rules in (self.rules[:1],self.rules[:2]):
            g=FixtureGateway(self.ctx,rules,clock=profiles.PolicyClock())
            with self.assertRaises(Refused):
                g.fixture_grant("open",call.request(self.ctx),60)
        self.opened()
    def test_cloud_actor_and_cloud_chat_denied(self):
        call=self.b.prepare("offline_open")
        with self.assertRaises(Refused):
            self.g.fixture_grant("cloud",call.request(self.ctx,actor="cloud"),60)
        ctx=replace(self.ctx,mode="CLOUD_CHAT")
        g=FixtureGateway(ctx,self.rules,clock=profiles.PolicyClock())
        with self.assertRaises(Refused):
            g.fixture_grant("cloudchat",call.request(ctx),60)
    def test_closed_and_wrong_element_denied(self):
        self.opened()
        with self.assertRaises(Refused):
            self.b.prepare("offline_click","message")
        self.b.close()
        with self.assertRaises(Refused):
            self.b.prepare("offline_read","message")

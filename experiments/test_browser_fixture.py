import unittest
from hashlib import sha256
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from browser_fixture import Plan,BrowserFixture,action_target

class BrowserFixtureTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY",False,True)
        self.g=FixtureGateway(self.ctx,(profiles.Rule("external_network",(),"allow"),profiles.Rule("browser_navigate",(),"allow"),),clock=profiles.PolicyClock())
        self.root="https://example.com/docs"
        self.a=self.root+"/a"
        self.b=self.root+"/b"
        self.p=Plan("disposable-fixture",self.root,(self.a,self.b))
        self.launch=self.grant("launch",self.p.target())
        self.f=BrowserFixture(self.g,self.ctx,self.p,self.launch)
    def grant(self,i,target):
        return self.g.fixture_grant(i,Request(self.ctx,"browser",target,"local","public"),60)
    def navigate(self,url,i):
        g=self.grant(i,action_target(self.p,"navigate",url))
        self.f.navigate(url,g)
        return g
    def test_exact_separate_navigation_and_snapshot(self):
        self.navigate(self.a,"a")
        data=b"fixture text"
        digest=sha256(data).hexdigest()
        g=self.grant("snapshot",action_target(self.p,"snapshot",self.a,digest))
        self.assertEqual(self.f.snapshot(self.a,data,g),(digest,"fixture text"))
        with self.assertRaises(Refused):
            self.f.snapshot(self.a,data,g)
        self.navigate(self.b,"b")
        with self.assertRaises(Refused):
            self.f.snapshot(self.a,data,g)
    def test_automatic_traffic_denied_even_same_origin(self):
        self.navigate(self.a,"a")
        for kind in ("dns","script","image","fetch","websocket","webrtc","service-worker","download","external-handler","prefetch"):
            with self.assertRaises(Refused):
                self.f.request(kind,self.a)
    def test_wrong_profile_and_page_refused(self):
        with self.assertRaises(Refused):
            BrowserFixture(self.g,self.ctx,replace(self.p,profile="personal"),self.launch)
        with self.assertRaises(Refused):
            self.navigate(self.b,"b")
        with self.assertRaises(Refused):
            action_target(self.p,"navigate","https://other.com/docs/a")
    def test_revocation_and_close(self):
        self.navigate(self.a,"a")
        self.g.revoke("a")
        with self.assertRaises(Refused):
            self.navigate(self.b,"b")
        self.f.close()
        with self.assertRaises(Refused):
            self.f.request("image",self.a)
    def test_snapshot_content_bound(self):
        self.navigate(self.a,"a")
        g=self.grant("s",action_target(self.p,"snapshot",self.a,sha256(b"good").hexdigest()))
        with self.assertRaises(Refused):
            self.f.snapshot(self.a,b"bad",g)
        self.assertEqual(self.f.snapshot(self.a,b"good",g)[1],"good")

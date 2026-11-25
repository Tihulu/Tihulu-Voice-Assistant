import unittest
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Request,Refused
from session_controller import Template,SessionController
from voice_gate import Config,VoiceGate
from browser_fixture import Plan,BrowserFixture,action_target
from connector_protocol import Connector,JobSpec
from inference import Candidate,Envelope
from adapter_protocol import LocalAdapter,prepare,disclosure_target
from provider_session import Account,CallPlan,ProviderSession
from test_voice_gate import LOUD

class SessionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.controller=SessionController(profiles.PolicyClock())
        self.candidate=Candidate("jan","llm","local","jan",True,True,4,80)
        self.call=prepare(self.candidate,"model","llm",b"fixture")
        self.cfg=Config("fixture",600,3,2,100)
        self.spec=JobSpec("opencode-fixture","agent-session","job","plan",sha256(b"plan").hexdigest())
        self.browser=Plan("fixture-profile","https://example.com/docs",("https://example.com/docs/a",))
        self.page=self.browser.pages[0]
        self.account=Account("provider-fixture","account-ref","oauth-reference","origin-fixture",1)
        data=b'{"count":5}'
        self.envelope=Envelope(self.account.provider,data,sha256(data).hexdigest())
        self.provider_plan=CallPlan(self.account,"model",self.envelope.digest)
        self.rules=tuple(profiles.Rule(action,(target,),"allow") for action,target in (
            ("filesystem_read",self.call.target),("local_audio_device",self.cfg.target()),
            ("host_shell",self.spec.target()),("external_network",self.browser.target()),
            ("browser_navigate",self.browser.target()),
            ("external_network",action_target(self.browser,"navigate",self.page)),
            ("browser_navigate",action_target(self.browser,"navigate",self.page)),
            ("cloud_inference",self.provider_plan.target()),("cloud_disclosure",disclosure_target(self.envelope))))
        self.template=Template("profile","SECURE_HYBRID",self.rules)
        self.ctx=self.controller.activate(self.template,"task","session","route",offline=False)
        self.g=self.controller.current()
    def grant(self,identity,action,target,classification="none"):
        return self.controller.owner_grant(identity,Request(self.ctx,action,target,"local",classification),60,confirmed=True)
    def test_profile_switch_invalidates_all_inflight_adapters(self):
        v=VoiceGate(self.g,self.ctx,self.cfg)
        v.arm(self.grant("voice","capture",self.cfg.target(),"private"))
        v.feed(LOUD)
        a=LocalAdapter(self.g,self.ctx,self.candidate,"model","llm",b"fixture",self.grant("model","engine",self.call.target))
        a.event("model",1,"text","fixture")
        c=Connector(self.g,self.ctx,"opencode-fixture","agent-session")
        handle=c.begin(self.spec,self.grant("agent","agent",self.spec.target()),b"plan")
        browser=BrowserFixture(self.g,self.ctx,self.browser,self.grant("browser","browser",self.browser.target(),"public"))
        navigation=self.grant("nav","browser",action_target(self.browser,"navigate",self.page),"public")
        provider=ProviderSession(self.g,self.ctx,(self.account,))
        pg=self.grant("provider","provider",self.provider_plan.target(),"minimized")
        dg=self.grant("disclose","disclose",disclosure_target(self.envelope),"minimized")
        self.controller.activate(self.template,"new-task","new-session","new-route",offline=True)
        self.assertEqual(v.poll(),"held")
        self.assertEqual(v._lead,[])
        for fn in (lambda:a.event("model",2,"completed",None),
                   lambda:c.event(handle,"agent-session","job",1,"completed"),
                   lambda:browser.navigate(self.page,navigation),
                   lambda:provider.prepare(self.provider_plan,self.envelope,pg,dg)):
            with self.assertRaises(Refused):
                fn()
        self.assertEqual(a.state,"held")
        self.assertEqual(c.status,"held")
    def test_new_session_requires_fresh_exact_grants(self):
        old=self.grant("model","engine",self.call.target)
        self.ctx=self.controller.activate(self.template,"task","session","route",offline=False)
        new=self.controller.current()
        with self.assertRaises(Refused):
            LocalAdapter(new,self.ctx,self.candidate,"model","llm",b"fixture",old)
        fresh=self.grant("model","engine",self.call.target)
        a=LocalAdapter(new,self.ctx,self.candidate,"model","llm",b"fixture",fresh)
        a.event("model",1,"text","new")
        a.event("model",2,"completed",None)
        self.assertEqual(a.result(),b"new")
        self.controller.stop()
        with self.assertRaises(Refused):
            a.result()

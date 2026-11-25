import unittest
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Request,Refused
from session_controller import Template,SessionController

class SessionControllerTests(unittest.TestCase):
    def setUp(self):
        self.s=SessionController(profiles.PolicyClock())
        self.t=Template("profile","SECURE_HYBRID",(profiles.Rule("filesystem_read",("file",),"allow"),))
        self.ctx=self.s.activate(self.t,"task","session","route")
        self.req=Request(self.ctx,"engine","file","local")
    def grant(self):
        return self.s.owner_grant("grant",self.req,60,confirmed=True)
    def test_explicit_owner_confirmation_and_stop(self):
        with self.assertRaises(Refused):
            self.s.owner_grant("grant",self.req,60)
        g=self.grant()
        old=self.s.current()
        old.claim(g,self.req)
        self.s.stop()
        with self.assertRaises(Refused):
            old.checkpoint(g,self.req)
        with self.assertRaises(Refused):
            self.s.current()
    def test_route_profile_and_session_switch_invalidate_old_gateway(self):
        for i,changes in enumerate((("profile","session","new-route"),("other","session","new-route"),
                                    ("other","new-session","new-route"))):
            old=self.s.current()
            g=self.grant()
            self.ctx=self.s.activate(replace(self.t,identity=changes[0]),"task",changes[1],changes[2])
            self.assertEqual(self.ctx.generation,i+2)
            with self.assertRaises(Refused):
                old.checkpoint(g,self.req)
            with self.assertRaises(Refused):
                self.s.current().checkpoint(g,self.req)
            self.req=Request(self.ctx,"engine","file","local")
        self.grant()
    def test_import_clone_no_allow_inheritance(self):
        old=self.s.current()
        g=self.grant()
        ctx=self.s.activate(replace(self.t,identity="clone"),"task","clone-session","route",imported=True)
        with self.assertRaises(Refused):
            self.s.owner_grant("new",Request(ctx,"engine","file","local"),60,confirmed=True)
        with self.assertRaises(Refused):
            old.checkpoint(g,self.req)
    def test_invalid_transition_keeps_previous_context_and_close_terminal(self):
        g=self.grant()
        old=self.s.current()
        with self.assertRaises(Refused):
            self.s.activate(replace(self.t,mode="CUSTOM"),"task","session","route")
        self.assertIs(self.s.current(),old)
        old.checkpoint(g,self.req)
        self.s.close()
        with self.assertRaises(Refused):
            self.s.activate(self.t,"task","session","route")
        with self.assertRaises(Refused):
            old.checkpoint(g,self.req)

    def test_wildcard_allow_cannot_activate_trusted_controller(self):
        old=self.s.current()
        wildcard=replace(self.t,rules=(profiles.Rule("filesystem_read",(),"allow"),))
        with self.assertRaisesRegex(Refused,"exact_template_allow_required"):
            self.s.activate(wildcard,"task","session","route")
        self.assertIs(self.s.current(),old)
        ctx=self.s.activate(wildcard,"task","session","route",imported=True)
        with self.assertRaises(Refused):
            self.s.owner_grant("x",Request(ctx,"engine","file","local"),60,confirmed=True)

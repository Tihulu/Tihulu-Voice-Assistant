import unittest
import json
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from connector_protocol import Connector,JobSpec
from event_channel import EventChannel
class EventChannelTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","SECURE_HYBRID")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("host_shell",(),"allow"),),clock=profiles.PolicyClock())
        self.n=0
    def channel(self):
        self.n+=1
        spec=JobSpec("opencode-fixture","s","j"+str(self.n),"plan",sha256(b"plan").hexdigest())
        g=self.g.fixture_grant("g"+str(self.n),Request(self.ctx,"agent",spec.target(),"local"),60)
        c=Connector(self.g,self.ctx,"opencode-fixture","s")
        h=c.begin(spec,g,b"plan")
        return c,EventChannel(c,h,"s",spec.job),g
    def frame(self,sequence,kind,**extra):
        return json.dumps(dict(session="s",job="j"+str(self.n),sequence=sequence,kind=kind,**extra)).encode()
    def test_ordered_status_and_terminal_close(self):
        c,ch,_=self.channel()
        self.assertEqual(ch.receive(self.frame(1,"progress")),"running")
        self.assertEqual(ch.receive(self.frame(2,"completed")),"completed")
        with self.assertRaises(Refused):
            ch.receive(self.frame(3,"progress"))
        self.assertEqual(c.status,"completed")
    def test_unknown_fields_tool_requests_and_duplicate_keys_fail_closed(self):
        for extra in ({"command":"fixture"},{"tool_calls":[]},{"result":"private"}):
            c,ch,_=self.channel()
            with self.assertRaises(Refused):
                ch.receive(self.frame(1,"progress",**extra))
            self.assertEqual(c.status,"cancelled")
        c,ch,_=self.channel()
        with self.assertRaises(Refused):
            ch.receive(b'{"session":"s","session":"s","job":"j4","sequence":1,"kind":"progress"}')
        self.assertEqual(c.status,"cancelled")
    def test_revocation_and_close(self):
        c,ch,g=self.channel()
        self.g.revoke(g.identity)
        with self.assertRaises(Refused):
            ch.receive(self.frame(1,"completed"))
        self.assertEqual(c.status,"held")
        c,ch,_=self.channel()
        ch.close()
        with self.assertRaises(Refused):
            ch.receive(self.frame(1,"completed"))
        self.assertEqual(c.status,"cancelled")
    def test_malformed_numeric_and_frame_bounds(self):
        for seq in (True,2,1.0):
            c,ch,_=self.channel()
            with self.assertRaises(Refused):
                ch.receive(self.frame(seq,"progress"))
            self.assertIn(c.status,("held","cancelled"))
        c,ch,_=self.channel()
        with self.assertRaises(Refused):
            ch.receive(b"x"*4097)
        self.assertEqual(c.status,"cancelled")

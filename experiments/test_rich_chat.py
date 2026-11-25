import json
import unittest
from unittest.mock import patch
from tihulu_jarvis import profiles
from gateway import Context,Request,FixtureGateway,Refused
from rich_chat import RichChat,build,room_target,source_target,publish_target,export_target,digest

CSV=b"time_s,temperature_c\n0,20\n10,24\n20,28\n"

class RichChatTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"route","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),profiles.Rule("publish_artifact",(),"allow")),clock=profiles.PolicyClock())
        self.n=0
        self.room=self.grant("publish",room_target("room"),"none")
        self.s=RichChat(self.g,self.ctx,"room",self.room)
        self.addCleanup(self.s.close)
    def grant(self,action,target,classification="private"):
        self.n+=1
        return self.g.fixture_grant("g"+str(self.n),Request(self.ctx,action,target,"local",classification),60)
    def consents(self,i,kind,data,color="blue"):
        c=build(i,kind,data,color=color)
        return c,self.grant("engine",source_target(i,kind,data)),self.grant("publish",publish_target("room",c))
    def add(self,i="one",kind="temperature",data=CSV,color="blue"):
        c,s,p=self.consents(i,kind,data,color)
        self.assertEqual(self.s.add(i,kind,data,s,p,color=color),c)
        return c,s,p
    def test_real_engine_table_chart_csv_and_digests(self):
        c,_,_=self.add()
        self.assertEqual(self.s.cards(),(c,))
        table,chart,data=c.parts
        self.assertEqual(json.loads(table.data)["rows"][1],["mean",24.0,"degC"])
        self.assertIn(b"<polyline",chart.data)
        self.assertEqual(data.mime,"text/csv")
        self.assertTrue(all(p.digest==digest(p.data) for p in c.parts))
    def test_file_text_is_inert_and_invalid_inputs_refused(self):
        raw=b"<script>not code</script>"
        c,_,_=self.add("file","text",raw)
        self.assertEqual(c.parts[0].mime,"text/plain")
        self.assertEqual(c.parts[0].data,raw)
        for kind,data in (("svg",raw),("text",b"bad\x00"),("text",b"\xff"),("temperature",b"wrong")):
            with self.assertRaises(Refused):
                build("bad",kind,data)
    def test_color_change_requires_fresh_publication_consent(self):
        c,s,p=self.consents("one","temperature",CSV)
        with self.assertRaises(Refused):
            self.s.add("one","temperature",CSV,s,p,color="green")
        self.assertEqual(self.s.cards(),())
        green,_,_=self.add("green","temperature",CSV,color="green")
        self.assertIn(b"#15803d",green.parts[1].data)
    def test_changed_source_and_foreign_card_permission(self):
        c,s,p=self.consents("one","temperature",CSV)
        with self.assertRaises(Refused):
            self.s.add("one","temperature",CSV.replace(b"28",b"29"),s,p)
        self.assertEqual(self.s.cards(),())
        with self.assertRaises(Refused):
            self.s.add("other","temperature",CSV,s,p)
    def test_export_exact_part_destination_and_single_use(self):
        c,_,_=self.add()
        g=self.grant("publish",export_target("room",c,"chart","selected-slot"))
        for name,dest,confirm in (("chart","other",True),("table","selected-slot",True),("chart","selected-slot",False)):
            with self.assertRaises(Refused):
                self.s.export(c.identity,name,dest,g,confirmed=confirm)
        self.assertEqual(self.s.export(c.identity,"chart","selected-slot",g,confirmed=True),c.parts[1].data)
        with self.assertRaises(Refused):
            self.s.export(c.identity,"chart","selected-slot",g,confirmed=True)
    def test_revocation_prunes_only_affected_card(self):
        c,s,p=self.add()
        other,_,_=self.add("other","text",b"hello")
        self.g.revoke(s.identity)
        self.assertEqual(self.s.cards(),(other,))
        with self.assertRaises(Refused):
            self.s.read(c.identity)
    def test_clear_and_profile_close_invalidate_cards(self):
        c,s,p=self.add()
        self.s.clear(c.identity)
        with self.assertRaises(Refused):
            self.g.checkpoint(s,s.request)
        with self.assertRaises(Refused):
            self.s.add(c.identity,c.kind,CSV,s,p)
        self.add("other")
        self.g.close()
        with self.assertRaises(Refused):
            self.s.cards()
        self.assertEqual(self.s._cards,{})
    def test_source_revoked_at_publication_refuses_result(self):
        c,s,p=self.consents("one","temperature",CSV)
        original=self.g.claim
        def revoke(g,r):
            original(g,r)
            if g is p:
                self.g.revoke(s.identity)
        with patch.object(self.g,"claim",side_effect=revoke):
            with self.assertRaises(Refused):
                self.s.add("one","temperature",CSV,s,p)
        self.assertEqual(self.s.cards(),())
    def test_close_at_export_refuses_bytes(self):
        c,_,_=self.add()
        g=self.grant("publish",export_target("room",c,"chart","slot"))
        original=self.g.claim
        def close(grant,req):
            original(grant,req)
            self.s.close()
        with patch.object(self.g,"claim",side_effect=close):
            with self.assertRaises(Refused):
                self.s.export(c.identity,"chart","slot",g,confirmed=True)
        self.assertEqual(self.s._cards,{})
    def test_capacity_and_duplicate_identity(self):
        for i in range(16):
            self.add("card"+str(i),"text",b"hello")
        c,s,p=self.consents("extra","text",b"extra")
        with self.assertRaises(Refused):
            self.s.add("extra","text",b"extra",s,p)
        self.assertEqual(len(self.s.cards()),16)
        self.s.clear("card0")
        c,s,p=self.consents("card0","text",b"new")
        with self.assertRaises(Refused):
            self.s.add("card0","text",b"new",s,p)

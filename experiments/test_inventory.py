import unittest
from dataclasses import replace
from inventory import Entry, Inventory
from gateway import Refused
class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.i=Inventory()
        self.e=Entry("m","a"*64,"llm","jan",True,False)
        self.i.register(self.e)
    def test_shared_reference_and_lease_prevent_removal(self):
        self.i.register(replace(self.e,identity="alias"))
        self.i.select("profile","llm","alias")
        with self.assertRaises(Refused):
            self.i.removal_plan("m")
        self.i.unselect("profile","llm")
        h=self.i.lease("alias")
        with self.assertRaises(Refused):
            self.i.removal_plan("m")
        self.i.release(h)
        self.assertEqual(self.i.removal_plan("m"),"REFERENCE_ONLY")
    def test_compatibility_and_conflicting_ownership(self):
        self.i.register(Entry("bad","b"*64,"tts","piper",False,True))
        with self.assertRaises(Refused):
            self.i.select("profile","tts","bad")
        with self.assertRaises(Refused):
            self.i.select("profile","stt","m")
        with self.assertRaises(Refused):
            self.i.register(replace(self.e,identity="owned",owned=True))
    def test_owned_removal_is_only_plan(self):
        self.i.register(Entry("own","c"*64,"llm","native",True,True))
        self.assertEqual(self.i.removal_plan("own"),"REVIEW_OWNED_ARTIFACT")
        self.i.select("profile","llm","own")
        with self.assertRaises(Refused):
            self.i.removal_plan("own")

# SPDX-License-Identifier: AGPL-3.0-only
import unittest
import json
from dataclasses import replace,asdict
from gateway import Refused
from agent_custom_profiles import AgentProfile,import_profile

class CustomAgentProfileTests(unittest.TestCase):
    def setUp(self):
        self.p=AgentProfile("builder",1,"application","local",("typed_engine","local_cli"),"balanced","","",4096,1024)
    def test_exact_manifest_roundtrip(self):
        self.assertEqual(import_profile(json.dumps(asdict(self.p)).encode()),self.p)
    def test_import_cannot_restore_grant_or_command(self):
        for key,value in (("auto_approve",True),("shell_command","date"),("credentials","secret")):
            with self.assertRaises(Refused):
                import_profile(json.dumps(dict(asdict(self.p),**{key:value})).encode())
    def test_duplicate_and_malformed_manifest(self):
        for raw in (b'{"role":"planner","role":"terminal"}',b'[]',b'\xff',b'x'*8193):
            with self.assertRaises(Refused):
                import_profile(raw)
    def test_task_never_adds_channel(self):
        self.assertEqual(self.p.spec("worker",("typed_engine",)).needs,("typed_engine",))
        with self.assertRaises(Refused):
            self.p.spec("worker",("desktop_input",))
    def test_cloud_profiles_only_reasoning(self):
        with self.assertRaises(Refused):
            replace(self.p,locality="cloud",memory_mib=0).checked()
        p=replace(self.p,role="planner",locality="cloud",channels=("reasoning",),memory_mib=0)
        self.assertEqual(p.checked(),p)
    def test_bounds_and_exact_types(self):
        for p in (replace(self.p,revision=True),replace(self.p,output_tokens=0),replace(self.p,memory_mib=-1),
                  replace(self.p,channels=("local_cli","local_cli")),replace(self.p,model="bad path")):
            with self.assertRaises(Refused):
                p.checked()
    def test_digest_tracks_revision_and_model_but_not_channel_order(self):
        self.assertEqual(self.p.digest(),replace(self.p,channels=tuple(reversed(self.p.channels))).digest())
        self.assertNotEqual(self.p.digest(),replace(self.p,revision=2).digest())
        self.assertNotEqual(self.p.digest(),replace(self.p,model="qwen").digest())

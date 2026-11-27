# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from gateway import Context,Refused
from heterogeneous_agents import AgentSpec,ROLES,plan_route

class HeterogeneousAgentTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
    def route(self,role,channels,needs=None,available=None,locality="local",ctx=None):
        return plan_route(ctx or self.ctx,AgentSpec("agent",role,locality,channels,needs or channels),available or channels)
    def test_catalog_has_22_distinct_roles(self):
        self.assertEqual(len(ROLES),22)
        self.assertEqual(len(set(ROLES)),22)
    def test_reasoning_needs_no_screen_or_effect(self):
        r=self.route("reasoning",("reasoning",))
        self.assertFalse(r.requires_screen)
        self.assertFalse(r.effect_review)
    def test_application_prefers_typed_engine_to_cli(self):
        r=self.route("application",("local_cli","typed_engine","local_ipc"))
        self.assertEqual(r.channel,"typed_engine")
        self.assertFalse(r.requires_screen)
        self.assertTrue(r.effect_review)
    def test_desktop_prefers_accessibility_to_pixels_when_equivalent(self):
        r=self.route("desktop",("screen_observe","accessibility_read"))
        self.assertEqual(r.channel,"accessibility_read")
        self.assertFalse(r.requires_screen)
        r=self.route("desktop",("desktop_input",))
        self.assertTrue(r.requires_screen)
        self.assertTrue(r.effect_review)
    def test_observer_never_inherits_input(self):
        with self.assertRaises(Refused):
            self.route("vision",("screen_observe",),("desktop_input",))
    def test_cloud_advice_cannot_use_host_tools(self):
        ctx=replace(self.ctx,mode="SECURE_HYBRID",offline=False)
        self.assertEqual(self.route("planner",("reasoning",),locality="cloud",ctx=ctx).channel,"reasoning")
        for role,channel in (("terminal","local_cli"),("vision","image_review"),("desktop","desktop_input"),("connector","connector_read")):
            with self.assertRaises(Refused):
                self.route(role,(channel,),locality="cloud",ctx=ctx)
        with self.assertRaises(Refused):
            self.route("planner",("reasoning",),locality="cloud")
    def test_cloud_chat_does_not_enable_local_host_action(self):
        with self.assertRaises(Refused):
            self.route("terminal",("local_cli",),ctx=replace(self.ctx,mode="CLOUD_CHAT"))
    def test_unavailable_channel_holds_no_silent_gui_fallback(self):
        with self.assertRaises(Refused):
            self.route("desktop",("accessibility_read","desktop_input"),("accessibility_read",),("desktop_input",))
    def test_invalid_duplicate_and_role_channels_refused(self):
        for channels in (("local_cli","local_cli"),("shell",),("desktop_input",)):
            with self.assertRaises(Refused):
                self.route("terminal",channels)
    def test_offline_research_not_network(self):
        with self.assertRaises(Refused):
            self.route("research",("public_search",))

    def test_offline_denies_network_connector_and_browser_channels(self):
        for role,channel in (("connector","connector_read"),("connector","connector_write"),
                             ("browser","browser_read"),("browser","browser_action")):
            with self.assertRaises(Refused):
                self.route(role,(channel,))
        self.assertEqual(self.route("browser",("offline_browser_read",)).channel,"offline_browser_read")
        self.assertTrue(self.route("browser",("offline_browser_action",)).effect_review)
    def test_local_only_online_public_opt_in_is_explicit(self):
        ctx=replace(self.ctx,offline=False)
        with self.assertRaises(Refused):
            self.route("research",("public_search",),ctx=ctx)
        ctx=replace(ctx,public_opt_in=True)
        self.assertEqual(self.route("research",("public_search",),ctx=ctx).channel,"public_search")
        for role,channel in (("connector","connector_read"),("browser","browser_action")):
            with self.assertRaises(Refused):
                self.route(role,(channel,),ctx=ctx)
    def test_hybrid_online_connector_is_proposal_not_grant(self):
        ctx=replace(self.ctx,offline=False,mode="SECURE_HYBRID")
        r=self.route("connector",("connector_write",),ctx=ctx)
        self.assertTrue(r.effect_review)
        self.assertFalse(r.requires_screen)

# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Routing table tests: no engine, endpoint or model is used."""
import unittest

from tihulu_jarvis.profiles import Profile, Rule
from tihulu_jarvis.routing import TASKS, Route, Row, plan, permitted, summary, warnings
from dataclasses import replace as Replace

from tihulu_jarvis.voice.m1 import Hold


def profile(**kw):
    values = {"id": "local-only", "name": "Assistant", "based_on_mode": "LOCAL_ONLY", "version": 1}
    values.update(kw)
    return Profile(**values).checked()


LOCAL = Route("llm", "fixture-local-chat", "local", "jan", "http://127.0.0.1:9876/")
CLOUD = Route("llm", "fixture-provider-chat", "cloud", "fixture-provider")
LOCALS = tuple(Replace(LOCAL, task=task, model_id="fixture-" + task) for task in TASKS)
FULL = {row.task: row for row in LOCALS}


class PlanTests(unittest.TestCase):
    def test_every_task_states_its_effective_route(self):
        rows = plan(profile(), FULL)
        self.assertEqual(tuple(row.task for row in rows), ("stt", "llm", "tts"))
        for row in rows:
            self.assertEqual(row.route, FULL[row.task])
            self.assertEqual(row.reason, "local_route")
            self.assertEqual(row.warning, "")

    def test_a_missing_task_is_a_reason_not_a_guess(self):
        rows = plan(profile(), {"llm": LOCAL})
        self.assertEqual([row.reason for row in rows],
                         ["no_route_configured", "local_route", "no_route_configured"])
        self.assertIn("no route", summary(rows[0]))

    def test_a_refused_cloud_route_is_stated_as_such(self):
        rows = plan(profile(), {"llm": CLOUD})
        row = rows[1]
        self.assertIsNone(row.route)
        self.assertEqual(row.reason, "local_only_forbids_cloud_route")
        self.assertEqual(row.warning, "local_only_forbids_cloud_route")

    def test_a_selection_must_be_a_mapping_of_known_tasks(self):
        for bad in ([], (), "stt", None, 42):
            with self.subTest(selection=bad):
                with self.assertRaises(Hold):
                    plan(profile(), bad)
        # A route for another task inside a selection is refused by the row.
        with self.assertRaisesRegex(Hold, "route_task_mismatch"):
            plan(profile(), {"stt": LOCAL})

    def test_a_row_for_another_task_is_refused(self):
        with self.assertRaisesRegex(Hold, "route_task_mismatch"):
            Row("stt", LOCAL).checked()
        with self.assertRaisesRegex(Hold, "unknown_task"):
            Row("vision").checked()


class WarningTests(unittest.TestCase):
    def test_a_local_profile_warns_only_about_the_missing_fallback(self):
        self.assertEqual(warnings(profile()), ("no_fallback_configured",))

    def test_a_cloud_profile_states_the_real_limit(self):
        chat = profile(based_on_mode="CLOUD_CHAT")
        self.assertEqual(warnings(chat), ("cloud_route_without_provider",
                                          "no_fallback_configured"))
        approved = profile(based_on_mode="CLOUD_CHAT", allowed_providers=("fixture-provider",))
        self.assertEqual(warnings(approved), ("cloud_transport_not_available",
                                              "no_fallback_configured"))

    def test_a_denied_cloud_profile_has_nothing_to_warn_about(self):
        denied = profile(based_on_mode="CLOUD_CHAT",
                         tool_rules=(Rule("cloud_inference", ("x",), "deny"),
                                     Rule("cloud_disclosure", ("x",), "deny")))
        self.assertEqual(warnings(denied), ("no_fallback_configured",))


class SummaryTests(unittest.TestCase):
    def test_a_line_names_the_route_and_the_reason(self):
        row = plan(profile(), {"llm": LOCAL})[1]
        self.assertIn("this computer", summary(row))
        self.assertIn("local_route", summary(row))

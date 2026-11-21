# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Readiness tests: no endpoint is contacted and no device is opened."""
import tempfile
import unittest
from pathlib import Path

from tihulu_jarvis import readiness
from tihulu_jarvis.settings import empty
from tihulu_jarvis.voice.m1 import Hold


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = empty()
        self.cfg.update(endpoint="http://127.0.0.1:9876/", llm="fixture-chat",
                        stt=str(self.tmp / "synthetic.bin"))
        Path(self.cfg["stt"]).touch()
        for lang in ("tr", "en", "de"):
            model = self.tmp / (lang + ".onnx")
            model.touch()
            self.cfg["voices"][lang] = str(model)

    def test_everything_configured_is_ready(self):
        rows = readiness.report(self.cfg, ("fixture-chat",), True, "private-assistant")
        self.assertEqual([item.state for item in rows],
                         [readiness.READY] * 6)
        self.assertEqual([item.token for item in rows][0], "endpoint")

    def test_an_absent_choice_is_missing_not_unknown(self):
        self.cfg["stt"] = ""
        rows = readiness.report(self.cfg, ("fixture-chat",), True, "private-assistant")
        stt = [item for item in rows if item.token == "stt"][0]
        self.assertEqual((stt.state, stt.detail), (readiness.MISSING, "no_model_selected"))

    def test_a_selected_file_that_disappeared_is_missing(self):
        Path(self.cfg["stt"]).unlink()
        rows = readiness.report(self.cfg, ("fixture-chat",), True, "private-assistant")
        stt = [item for item in rows if item.token == "stt"][0]
        self.assertEqual(stt.detail, "selected_file_not_found")

    def test_a_non_loopback_endpoint_is_not_ready(self):
        self.cfg["endpoint"] = "https://provider.example/v1"
        rows = readiness.report(self.cfg, (), True, "private-assistant")
        endpoint = rows[0]
        self.assertEqual((endpoint.state, endpoint.detail),
                         (readiness.MISSING, "endpoint_not_loopback"))

    def test_nothing_discovered_is_unknown_not_missing(self):
        rows = readiness.report(self.cfg, (), True, "private-assistant")
        models = [item for item in rows if item.token == "local_models"][0]
        self.assertEqual((models.state, models.detail),
                        (readiness.UNKNOWN, "not_discovered_yet"))
        self.assertIn("1 not checked", readiness.summary(rows))

    def test_missing_audio_tools_and_profile_are_stated(self):
        rows = readiness.report(self.cfg, ("fixture-chat",), False, "")
        tools = [item for item in rows if item.token == "audio_tools"][0]
        self.assertEqual((tools.state, tools.detail),
                         (readiness.MISSING, "audio_backend_missing"))
        profile = [item for item in rows if item.token == "profile"][0]
        self.assertEqual(profile.detail, "no_active_profile")
        self.assertIn("2 missing", readiness.summary(rows))

    def test_the_summary_counts_each_state(self):
        rows = readiness.report(self.cfg, ("fixture-chat",), None, "private-assistant")
        text = readiness.summary(rows)
        self.assertIn("5 ready", text)
        self.assertIn("1 not checked: audio_tools", text)

    def test_a_bad_configuration_is_refused(self):
        with self.assertRaises(Hold):
            readiness.report("not a configuration", (), None, "")

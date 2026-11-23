# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Pre-flight tests: no program is run and no file is opened on the host."""
import unittest
from io import StringIO
from pathlib import Path

from tihulu_jarvis import settings

from tools import preflight
from tools.preflight import MISSING, READY, rows, verdict


def configuration():
    return {"endpoint": "http://127.0.0.1:9876/", "stt": "", "voices": {}}


class RowsTests(unittest.TestCase):
    def test_a_ready_machine_reports_every_question(self):
        items = rows(exe=lambda name: "/usr/bin/" + name, importer=lambda: (4, 14),
                     cfg={"endpoint": "http://127.0.0.1:9876/", "stt": "/none/x", "voices": {}})
        # The two device probes join after the audio tools, before the choices.
        self.assertEqual([token for token, _, _ in items][:7],
                         ["gtk", "arecord", "whisper-cli", "piper", "aplay",
                          "cuda_tools", "vulkan_tools"])
        # An unreadable or absent model is said to be missing, with the reason.
        by_token = dict((token, (state, detail)) for token, state, detail in items)
        self.assertEqual(by_token["gtk"], (READY, "GTK 4.14"))
        self.assertEqual(by_token["stt"][0], MISSING)
        self.assertEqual(by_token["voices"][0], MISSING)

    def test_a_missing_gtk_is_missing_not_skipped(self):
        def broken():
            raise ImportError("no gi")
        items = rows(exe=lambda name: None, importer=broken, cfg=configuration())
        by = dict((token, state) for token, state, _ in items)
        self.assertEqual(by["gtk"], MISSING)
        self.assertEqual(by["arecord"], MISSING)
        self.assertEqual(by["endpoint"], READY)

    def test_unreadable_settings_state_their_reason(self):
        def unreadable():
            raise OSError("permission denied")
        items = rows(exe=lambda name: None, importer=lambda: (4, 14),
                     cfg=None, loader=unreadable)
        by = dict((token, state) for token, state, detail in items for detail in [detail]
                  ) if False else {}
        for token, state, detail in items:
            by.setdefault(token, (state, detail))
        self.assertEqual(by["settings"][0], MISSING)
        self.assertIn("permission denied", by["settings"][1])
        self.assertNotIn("stt", by)

    def test_a_chosen_model_and_voice_are_reported(self):
        model = Path("/none/tihulu-preflight-fixture.bin")
        items = rows(exe=lambda name: None, importer=lambda: (4, 14),
                     cfg={"endpoint": "http://127.0.0.1:9876/", "stt": str(model),
                          "voices": {"tr": "/none/tr.onnx"}})
        by = dict((token, state) for token, state, _ in items)
        self.assertEqual(by["stt"], MISSING)   # the fixture path does not exist
        self.assertEqual(by["voices"], MISSING)


class VerdictTests(unittest.TestCase):
    def test_the_verdict_names_the_interpreter_it_checked(self):
        out = StringIO()
        verdict([("gtk", READY, "GTK 4.14")], out=out, interpreter="/usr/bin/python3")
        self.assertIn("preflight interpreter: /usr/bin/python3", out.getvalue())

    def test_the_verdict_states_the_missing_count_and_fails_on_it(self):
        out = StringIO()
        code = verdict([("gtk", READY, "GTK 4.14"), ("aplay", MISSING, "not found on PATH")],
                       out=out)
        self.assertEqual(code, 1)
        self.assertIn("aplay        missing  not found on PATH", out.getvalue())
        self.assertIn("2 questions, 1 missing", out.getvalue())
        out = StringIO()
        self.assertEqual(verdict([("gtk", READY, "GTK 4.14")], out=out), 0)


class DeviceRowTests(unittest.TestCase):
    def test_both_devices_are_asked_about(self):
        rows = {token: (state, detail) for token, state, detail
                in preflight.device_rows(exe=lambda name: "/usr/bin/" + name)}
        self.assertEqual(set(rows), {"cuda_tools", "vulkan_tools"})
        self.assertEqual(rows["cuda_tools"], (preflight.READY, "/usr/bin/nvidia-smi"))

    def test_a_missing_tool_is_missing_not_a_capability_claim(self):
        rows = {token: (state, detail) for token, state, detail
                in preflight.device_rows(exe=lambda name: None)}
        self.assertEqual(rows["cuda_tools"], (preflight.MISSING, "not found on PATH"))
        self.assertEqual(rows["vulkan_tools"], (preflight.MISSING, "not found on PATH"))

    def test_the_rows_join_the_whole_preflight(self):
        rows = preflight.rows(exe=lambda name: None,
                             importer=lambda: (4, 14),
                             cfg=settings.empty())
        tokens = [token for token, _, _ in rows]
        self.assertIn("cuda_tools", tokens)
        self.assertIn("vulkan_tools", tokens)
        self.assertEqual(tokens[:6], ["gtk", "arecord", "whisper-cli", "piper",
                                      "aplay", "cuda_tools"])

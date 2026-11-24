# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from tihulu_jarvis import analysis_controller as ac
from tihulu_jarvis.voice.m1 import Hold

DATA = b"time_s,temperature_c\n0,20\n10,22\n20,24\n30,26\n40,28\n"


class AnalysisControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name).resolve() / "fixture.csv")
        Path(self.path).write_bytes(DATA)
        self.context = ac.Context("session-a", "private-assistant", 1, "LOCAL_ONLY")
        self.grants = {("selected_csv_analysis", self.path)}
        self.controller = ac.AnalysisController(
            lambda context, action, target:
            context == self.context and (action, target) in self.grants)
        self.controller.activate(self.context)

    def analyze(self, **kw):
        ident = self.controller.analyze_selected(self.path, **kw)
        self.grants.add(("artifact_read", ident))
        return ident

    def test_exact_artifact_and_summary(self):
        ident = self.analyze()
        result = self.controller.get(ident)
        self.assertEqual(result.series.mean_c, 24)
        self.assertEqual(result.series.count, 5)
        self.assertEqual(result.series.input_sha256, hashlib.sha256(DATA).hexdigest())
        self.assertEqual(result.svg_sha256, hashlib.sha256(result.svg).hexdigest())
        self.assertIn("Mean 24 degrees Celsius", result.summary)
        self.assertNotIn(self.path, repr(result))
        self.assertIsNone(result.parent_id)
        self.assertEqual(Path(self.path).read_bytes(), DATA)

    def test_followups_use_snapshot_not_original_file(self):
        ident = self.analyze()
        root = self.controller.get(ident)
        Path(self.path).unlink()
        for color, lang, word in (("green", "tr", "Ortalama 24"),
                                  ("orange", "de", "Mittelwert 24"),
                                  ("blue", "en", "Mean 24")):
            self.grants.add(("artifact_recolor", ident))
            new = self.controller.recolor(ident, color=color, language=lang)
            self.grants.add(("artifact_read", new))
            result = self.controller.get(new)
            self.assertEqual(result.parent_id, ident)
            self.assertIs(result.series, root.series)
            self.assertIn(word, result.summary)
            self.assertEqual(result.color, color)
            ident = new

    def test_denies_unselected_file_before_io(self):
        with patch.object(ac, "read_selected_csv", side_effect=AssertionError("io")):
            with self.assertRaisesRegex(Hold, "analysis_not_authorized"):
                self.controller.analyze_selected(self.path + ".other")

    def test_other_modes_deny_even_with_grant(self):
        for mode in ("CLOUD_CHAT", "SECURE_HYBRID", "STANDARD_HYBRID", "CUSTOM"):
            self.context = ac.Context("session-a", "private-assistant", 1, mode)
            self.controller.activate(self.context)
            with self.assertRaisesRegex(Hold, "analysis_mode_denied"):
                self.controller.analyze_selected(self.path)

    def test_read_and_recolor_need_separate_grants(self):
        ident = self.controller.analyze_selected(self.path)
        with self.assertRaisesRegex(Hold, "analysis_not_authorized"):
            self.controller.get(ident)
        with self.assertRaisesRegex(Hold, "analysis_not_authorized"):
            self.controller.recolor(ident, color="green")
        self.grants.add(("artifact_recolor", ident))
        with self.assertRaisesRegex(Hold, "analysis_not_authorized"):
            self.controller.recolor(ident, color="green")
        self.assertFalse(self.controller.busy)

    def test_revocation_before_publication(self):
        real_render = ac.render_svg
        def render_then_revoke(*args, **kwargs):
            result = real_render(*args, **kwargs)
            self.grants.clear()
            return result
        with patch.object(ac, "render_svg", side_effect=render_then_revoke):
            with self.assertRaisesRegex(Hold, "analysis_not_authorized"):
                self.controller.analyze_selected(self.path)
        self.assertFalse(self.controller.busy)
        self.grants.add(("selected_csv_analysis", self.path))
        self.assertEqual(self.controller.get(self.analyze()).series.mean_c, 24)

    def test_cancel_before_publication_keeps_previous_result(self):
        ident = self.analyze()
        real_render = ac.render_svg
        def render_then_cancel(*args, **kwargs):
            result = real_render(*args, **kwargs)
            self.controller.cancel()
            return result
        with patch.object(ac, "render_svg", side_effect=render_then_cancel):
            with self.assertRaisesRegex(Hold, "analysis_cancelled"):
                self.controller.analyze_selected(self.path)
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.get(ident).series.count, 5)

    def test_switch_context_invalidates_old_artifacts(self):
        ident = self.analyze()
        self.context = ac.Context("session-b", "private-assistant", 2, "LOCAL_ONLY")
        self.controller.activate(self.context)
        with self.assertRaisesRegex(Hold, "analysis_artifact_not_found"):
            self.controller.get(ident)

    def test_hard_stop_requires_explicit_reactivation(self):
        ident = self.analyze()
        self.controller.hard_stop()
        with self.assertRaisesRegex(Hold, "analysis_mode_denied"):
            self.controller.get(ident)
        self.controller.activate(self.context)
        with self.assertRaisesRegex(Hold, "analysis_artifact_not_found"):
            self.controller.get(ident)

    def test_switch_during_worker_prevents_stale_commit(self):
        entered, release = Event(), Event()
        errors = []
        real_render = ac.render_svg
        def slow_render(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test timeout")
            return real_render(*args, **kwargs)
        def worker():
            try:
                self.controller.analyze_selected(self.path)
            except Exception as error:
                errors.append(str(error))
        with patch.object(ac, "render_svg", side_effect=slow_render):
            thread = Thread(target=worker, daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.controller.activate(self.context)
                with self.assertRaisesRegex(Hold, "analysis_busy"):
                    self.controller.analyze_selected(self.path)
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["analysis_cancelled"])
        self.assertFalse(self.controller.busy)

    def test_parent_read_revoked_before_recolor_publication(self):
        ident = self.analyze()
        self.grants.add(("artifact_recolor", ident))
        real_render = ac.render_svg
        def revoke_parent(*args, **kwargs):
            result = real_render(*args, **kwargs)
            self.grants.remove(("artifact_read", ident))
            return result
        with patch.object(ac, "render_svg", side_effect=revoke_parent):
            with self.assertRaisesRegex(Hold, "analysis_not_authorized"):
                self.controller.recolor(ident, color="green")
        self.assertFalse(self.controller.busy)

    def test_bounded_artifacts(self):
        with patch.object(ac, "MAX_ARTIFACTS", 1):
            self.analyze()
            with self.assertRaisesRegex(Hold, "analysis_artifact_limit"):
                self.analyze()

    def test_callback_failure_does_not_leak_diagnostics(self):
        def failure(*args):
            raise RuntimeError("PRIVATE-CANARY")
        controller = ac.AnalysisController(failure)
        controller.activate(self.context)
        with self.assertRaisesRegex(Hold, "^analysis_not_authorized$"):
            controller.analyze_selected(self.path)

    def test_invalid_arguments_and_failed_job_cleanup(self):
        with self.assertRaisesRegex(Hold, "analysis_invalid_language"):
            self.controller.analyze_selected(self.path, language="fr")
        with self.assertRaisesRegex(Hold, "chart_invalid_color"):
            self.controller.analyze_selected(self.path, color="<svg>")
        Path(self.path).write_bytes(b"wrong header\n")
        with self.assertRaisesRegex(Hold, "csv_invalid_header"):
            self.analyze()
        self.assertFalse(self.controller.busy)


if __name__ == "__main__":
    unittest.main()

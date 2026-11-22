# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Traceability-check tests: the real document is the fixture."""
import unittest
from pathlib import Path

from tools.trace_check import check

ROOT = Path(__file__).resolve().parent.parent


class RealDocumentTests(unittest.TestCase):
    def test_the_real_document_has_no_dangling_reference(self):
        rows, problems = check(ROOT)
        self.assertGreater(rows, 30)
        self.assertEqual(problems, [])


class SyntheticDocumentTests(unittest.TestCase):
    def write(self, tmp, text):
        path = Path(tmp) / "TRACE.md"
        (Path(tmp) / "docs").mkdir(exist_ok=True)
        (Path(tmp) / "src" / "tihulu_jarvis").mkdir(parents=True, exist_ok=True)
        (Path(tmp) / "src" / "tihulu_jarvis" / "real.py").write_text("# real\n")
        (Path(tmp) / "tests").mkdir(exist_ok=True)
        (Path(tmp) / "tests" / "test_real.py").write_text(
            "import unittest\n\n\nclass RealTests(unittest.TestCase):\n    pass\n")
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_row_pointing_at_a_missing_module_is_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, "| A stated requirement | `ghost.py` | `RealTests` |\n")
            rows, problems = check(Path(tmp), document="TRACE.md")
        self.assertEqual(rows, 1)
        # An unknown path is reported once as unknown and once as missing; both
        # name the code, so a reader is never left guessing which is which.
        self.assertEqual(len(problems), 2)
        self.assertTrue(all("ghost.py" in problem for problem in problems))

    def test_a_row_pointing_at_a_missing_test_class_is_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, "| A stated requirement | `real.py` | `GhostTests` |\n")
            _, problems = check(Path(tmp), document="TRACE.md")
        self.assertEqual(problems, ["unknown test class 'GhostTests' for 'A stated requirement'"])

    def test_a_well_formed_row_is_accepted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, "| A stated requirement | `real.py` | `RealTests` |\n")
            rows, problems = check(Path(tmp), document="TRACE.md")
        self.assertEqual((rows, problems), (1, []))

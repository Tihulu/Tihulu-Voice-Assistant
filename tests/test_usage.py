# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Usage-report tests: only /proc/self/status of this process is read."""
import unittest
from unittest.mock import patch

from tihulu_jarvis import usage


class MeasureTests(unittest.TestCase):
    def test_the_process_measures_its_own_resident_memory(self):
        # On Linux this is a real number; elsewhere it is honestly None.
        kib = usage.resident_kib()
        self.assertTrue(kib is None or kib > 0)
        if usage.resident_kib() is not None:
            self.assertGreater(usage.threads(), 0)
            self.assertIn("MiB resident", usage.summary())
            self.assertIn("threads", usage.summary())

    def test_an_unreadable_status_file_says_so_rather_guessing(self):
        def unreadable(*_args, **_kwargs):
            raise OSError("no procfs")
        with patch("builtins.open", side_effect=unreadable):
            self.assertIsNone(usage.resident_kib())
            self.assertIsNone(usage.threads())
            # Unmeasurable is stated, not replaced by a comfortable number.
            self.assertIn("not readable on this platform", usage.summary())

    def test_a_field_without_a_number_is_not_a_number(self):
        class Weird:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                return iter(["VmRSS:\tnot-a-number\n"])
        with patch("builtins.open", return_value=Weird()):
            self.assertIsNone(usage.resident_kib())

    def test_this_process_is_quiet(self):
        # One thread and a small resident set is the claim this file measures.
        if usage.threads() is not None:
            self.assertLessEqual(usage.threads(), 4)
            self.assertLess(usage.resident_kib(), 512 * 1024)

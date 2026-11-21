# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Receipt tests: no window, policy or engine is used."""
import unittest

from tihulu_jarvis.receipts import KINDS, is_receipt, line


class ReceiptTests(unittest.TestCase):
    def test_each_kind_names_its_outcome_and_subject(self):
        self.assertEqual(line("approved", "Switching to Cloud Chat"),
                         "Approved by you · Switching to Cloud Chat")
        self.assertEqual(line("refused", "read a file"),
                         "Refused by the active profile · read a file")
        self.assertEqual(line("performed", "transcribed clip.wav"),
                         "Performed on this computer · transcribed clip.wav")
        self.assertEqual(line("not_approved", "no privileges were added"),
                         "Not approved · no privileges were added")

    def test_a_receipt_is_recognisable_and_a_message_is_not(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                text = line(kind, "something")
                self.assertTrue(is_receipt(text))
        for text in ("You\nnaber nasilsin", "JARVIS\nMerhaba!", "Merhaba!",
                     "Refused · no subject", "Approved · no subject"):
            with self.subTest(text=text):
                self.assertFalse(is_receipt(text))

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            line("granted", "something")
        self.assertEqual(len(KINDS), 4)

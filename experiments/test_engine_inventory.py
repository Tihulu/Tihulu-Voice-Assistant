# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from capabilities import CapabilityCall
from gateway import Refused
from engine_inventory import Inventory, EngineEvidence


class EngineInventoryTests(unittest.TestCase):
    def setUp(self):
        self.call = CapabilityCall("tesseract", "5.5", "ocr_image",
                                   (("artifact","image1"), ("digest","a"*64), ("language","tr")))
        self.evidence = EngineEvidence("tesseract","5.5","ocr_image",True,True,True)

    def test_readiness_states_are_distinct_and_no_availability_claim(self):
        for evidence, expected in (
            (replace(self.evidence, detected=False, compatible=False, tested=False), "not_detected"),
            (replace(self.evidence, compatible=False, tested=False), "incompatible"),
            (replace(self.evidence, tested=False), "not_tested"),
            (self.evidence, "fixture_tested_activation_hold"),
        ):
            self.assertEqual(Inventory((evidence,)).readiness(self.call), expected)
        self.assertEqual(Inventory(()).readiness(self.call), "not_detected")

    def test_version_and_operation_cannot_inherit_test_evidence(self):
        inv = Inventory((self.evidence,))
        self.assertEqual(inv.readiness(replace(self.call,version="5.6")), "not_detected")
        pdf = CapabilityCall("poppler","5.5","pdf_extract",
                             (("artifact","doc"), ("digest","a"*64), ("page",1)))
        self.assertEqual(inv.readiness(pdf), "not_detected")

    def test_invalid_or_duplicate_evidence_refused(self):
        for bad in (replace(self.evidence, detected=False),
                    replace(self.evidence, compatible=False),
                    replace(self.evidence, tested=1),
                    replace(self.evidence, operation="run_shell")):
            with self.assertRaises(Refused):
                Inventory((bad,))
        with self.assertRaises(Refused):
            Inventory((self.evidence,self.evidence))
        with self.assertRaises(Refused):
            Inventory([self.evidence])

    def test_inventory_cannot_create_a_grant_or_runtime_operation(self):
        inv = Inventory((self.evidence,))
        self.assertFalse(hasattr(inv, "execute"))
        self.assertFalse(hasattr(inv, "grant"))
        self.assertEqual(inv.rows(), (self.evidence,))
        with self.assertRaises(TypeError):
            inv._items[("tesseract","5.5","ocr_image")] = None

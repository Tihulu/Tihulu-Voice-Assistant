# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway, Refused
from capabilities import CapabilityCall
from document_result import DocumentFixture, DocumentResult, TextBlock


class DocumentResultTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("task", "session", "profile", 1, "route", "LOCAL_ONLY")
        self.ocr = CapabilityCall("tesseract", "fixture", "ocr_image",
                                  (("artifact", "image1"), ("digest", "a"*64), ("language", "tr")))
        self.pdf = CapabilityCall("poppler", "fixture", "pdf_extract",
                                  (("artifact", "pdf1"), ("digest", "b"*64), ("page", 2)))

    def fixture(self, call):
        req = call.request(self.ctx)
        gateway = FixtureGateway(self.ctx, (profiles.Rule("filesystem_read", (req.target,), "allow"),
                                          profiles.Rule("host_shell", (req.target,), "allow")),
                                 clock=profiles.PolicyClock())
        grant = gateway.fixture_grant("doc", req, 60)
        return gateway, DocumentFixture(gateway, grant, self.ctx, call)

    def result_for(self, call):
        return DocumentResult(call.request(self.ctx).target, dict(call.args)["digest"],
                              dict(call.args).get("page", 1),
                              (TextBlock("Merhaba Dünya / Guten Morgen / Hello",
                                         (10, 20, 5000, 6000),
                                         93 if call.operation == "ocr_image" else None),))

    def test_multilingual_output_and_provenance(self):
        for call in (self.ocr, self.pdf):
            _, job = self.fixture(call)
            result = self.result_for(call)
            with self.assertRaises(Refused):
                job.result()
            job.complete(result)
            receipt = job.result()
            self.assertEqual(receipt[:4], ("SIMULATED_ONLY", call.adapter, call.version, result))
            self.assertEqual(len(receipt[4]), 64)
            with self.assertRaises(Refused):
                job.complete(result)

    def test_other_page_source_and_call_refused(self):
        result = self.result_for(self.pdf)
        for bad in (replace(result, page=1), replace(result, source_digest="f"*64),
                    replace(result, call_target="f"*64)):
            _, job = self.fixture(self.pdf)
            with self.assertRaisesRegex(Refused, "result_provenance_mismatch"):
                job.complete(bad)
            with self.assertRaises(Refused):
                job.result()
            with self.assertRaises(Refused):
                job.complete(result)

    def test_invalid_geometry_confidence_and_control_text(self):
        result = self.result_for(self.ocr)
        block = result.blocks[0]
        for bad in (replace(block, box=(0, 0, 0, 2)),
                    replace(block, box=(True, 0, 10, 20)),
                    replace(block, box=(0, 0, 10001, 20)),
                    replace(block, confidence=True), replace(block, confidence=101),
                    replace(block, text="x\x00y")):
            _, job = self.fixture(self.ocr)
            with self.assertRaises(Refused):
                job.complete(replace(result, blocks=(bad,)))
            with self.assertRaises(Refused):
                job.result()
        _, job = self.fixture(self.pdf)
        with self.assertRaisesRegex(Refused, "unexpected_confidence"):
            job.complete(replace(self.result_for(self.pdf), blocks=(block,)))

    def test_output_bounds_and_digest_include_geometry(self):
        result = self.result_for(self.ocr)
        block = result.blocks[0]
        for blocks in ((block,)*257, (replace(block, text="x"*16000),)*2):
            _, job = self.fixture(self.ocr)
            with self.assertRaises(Refused):
                job.complete(replace(result, blocks=blocks))
        _, a = self.fixture(self.ocr)
        _, b = self.fixture(self.ocr)
        a.complete(result)
        b.complete(replace(result, blocks=(replace(block, box=(11,20,5000,6000)),)))
        self.assertNotEqual(a.result()[4], b.result()[4])

    def test_revocation_and_cancel_prevent_publication(self):
        gateway, job = self.fixture(self.ocr)
        job.complete(self.result_for(self.ocr))
        gateway.revoke("doc")
        with self.assertRaises(Refused):
            job.result()
        _, job = self.fixture(self.pdf)
        job.cancel()
        with self.assertRaises(Refused):
            job.complete(self.result_for(self.pdf))
        with self.assertRaises(Refused):
            job.result()

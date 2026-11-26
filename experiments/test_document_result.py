# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway, Refused
from capabilities import CapabilityCall
from document_result import DocumentFixture, DocumentResult, TextBlock
from inference import Candidate


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


    def test_pdf_page_ocr_binds_original_and_raster_and_page(self):
        call = CapabilityCall("tesseract", "fixture", "pdf_page_ocr",
                              (("artifact","pdf1"), ("digest","b"*64), ("page",3),
                               ("render_digest","c"*64), ("language","tr")))
        result = DocumentResult(call.request(self.ctx).target, "b"*64, 3,
                                (TextBlock("Merhaba",(1,2,100,200),95),), "c"*64)
        for bad in (replace(result, render_digest=None), replace(result, render_digest="d"*64),
                    replace(result, page=1), replace(result, source_digest="c"*64)):
            _, job = self.fixture(call)
            with self.assertRaisesRegex(Refused, "result_provenance_mismatch"):
                job.complete(bad)
        _, job = self.fixture(call)
        job.complete(result)
        self.assertEqual(job.result()[3],result)

    def test_ordinary_pdf_extract_cannot_claim_ocr_render_provenance(self):
        _, job = self.fixture(self.pdf)
        with self.assertRaisesRegex(Refused,"result_provenance_mismatch"):
            job.complete(replace(self.result_for(self.pdf),render_digest="c"*64))


    def test_local_summary_requires_distinct_exact_result_and_language_grant(self):
        gateway, job = self.fixture(self.ocr)
        with self.assertRaises(Refused):
            job.prepare_summary("fixture","tr")
        result = self.result_for(self.ocr)
        job.complete(result)
        call = job.prepare_summary("fixture","tr")
        self.assertEqual(dict(call.args)["result_digest"],job.result()[4])
        req = call.request(self.ctx)
        # Exact scope is not a generic filesystem/host grant. A new owner
        # fixture policy must explicitly cover the summary request.
        with self.assertRaises(Refused):
            gateway.fixture_grant("summary",req,60)

    def test_local_summary_separate_grant_and_revocation(self):
        req = self.ocr.request(self.ctx)
        gateway = FixtureGateway(self.ctx, (profiles.Rule("filesystem_read",(),"allow"),
                                            profiles.Rule("host_shell",(),"allow")),
                                 clock=profiles.PolicyClock())
        grant = gateway.fixture_grant("doc",req,60)
        job = DocumentFixture(gateway,grant,self.ctx,self.ocr)
        job.complete(self.result_for(self.ocr))
        summary = job.prepare_summary("fixture","tr")
        approved = gateway.fixture_grant("summary",summary.request(self.ctx),60)
        for version,language in (("other","tr"),("fixture","de")):
            with self.assertRaisesRegex(Refused,"request_mismatch"):
                job.simulate_summary(version,language,approved)
        with self.assertRaisesRegex(Refused,"request_mismatch"):
            job.simulate_summary("fixture","tr",grant)
        self.assertEqual(job.simulate_summary("fixture","tr",approved)[0],"SIMULATED_ONLY")
        with self.assertRaisesRegex(Refused,"grant_already_claimed"):
            job.simulate_summary("fixture","tr",approved)
        fresh = gateway.fixture_grant("fresh",summary.request(self.ctx),60)
        gateway.revoke("doc")
        with self.assertRaises(Refused):
            job.simulate_summary("fixture","tr",fresh)

    def local_adapter_fixture(self):
        gateway = FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),
                                            profiles.Rule("host_shell",(),"allow")),
                                 clock=profiles.PolicyClock())
        grant = gateway.fixture_grant("doc",self.ocr.request(self.ctx),60)
        job = DocumentFixture(gateway,grant,self.ctx,self.ocr)
        job.complete(self.result_for(self.ocr))
        candidate = Candidate("qwen-fixture","llm","local","jan",True,True,4096,80)
        req = job.prepare_local_summary_adapter(candidate,"summary","fixture","tr")
        return gateway,job,candidate,req

    def test_local_adapter_exact_model_language_version_and_private_scope(self):
        g,job,candidate,req = self.local_adapter_fixture()
        self.assertEqual((req.action,req.classification,req.actor),("document_engine","private","local"))
        grant = g.fixture_grant("summary",req,60)
        for model,version,language in ((replace(candidate,identity="other"),"fixture","tr"),
                                       (candidate,"other","tr"),(candidate,"fixture","de")):
            with self.assertRaisesRegex(Refused,"request_mismatch"):
                job.start_local_summary_adapter(model,"summary",version,language,grant)
        adapter = job.start_local_summary_adapter(candidate,"summary","fixture","tr",grant)
        adapter.event("summary",1,"text","Sentetik özet")
        adapter.event("summary",2,"completed",None)
        self.assertEqual(adapter.result(),"Sentetik özet".encode("utf-8"))

    def test_local_adapter_rejects_cloud_and_ineligible_models(self):
        g,job,candidate,req = self.local_adapter_fixture()
        for bad in (replace(candidate,locality="cloud"),replace(candidate,provider="other"),
                    replace(candidate,task="tts"),replace(candidate,healthy=False)):
            with self.assertRaises(Refused):
                job.prepare_local_summary_adapter(bad,"summary","fixture","tr")

    def test_document_cancel_drops_partial_local_summary(self):
        g,job,candidate,req = self.local_adapter_fixture()
        adapter = job.start_local_summary_adapter(candidate,"summary","fixture","tr",
                                                  g.fixture_grant("summary",req,60))
        adapter.event("summary",1,"text","partial")
        job.cancel()
        with self.assertRaises(Refused):
            adapter.event("summary",2,"completed",None)
        self.assertEqual(adapter.state,"held")
        with self.assertRaises(Refused):
            adapter.result()

    def test_completed_local_summary_still_requires_live_document_grant(self):
        g,job,candidate,req = self.local_adapter_fixture()
        adapter = job.start_local_summary_adapter(candidate,"summary","fixture","tr",
                                                  g.fixture_grant("summary",req,60))
        adapter.event("summary",1,"text","synthetic")
        adapter.event("summary",2,"completed",None)
        g.revoke("doc")
        with self.assertRaises(Refused):
            adapter.result()
        self.assertEqual(adapter.state,"held")

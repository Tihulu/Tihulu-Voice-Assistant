# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway, Refused
from capabilities import CapabilityCall
from conversion_result import ConversionFixture, ConversionResult, MAX_OUTPUT

class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.call = CapabilityCall("ffmpeg","fixture","wav_to_flac",
                                   (("artifact","source"),("digest","a"*64),("output","new")))
        self.req = self.call.request(self.ctx)
        self.output = ConversionResult(self.req.target,"a"*64,"new","flac",b"fLaCfixture")

    def fixture(self,call=None):
        call = self.call if call is None else call
        g = FixtureGateway(self.ctx,tuple(profiles.Rule(a,(),"allow") for a in
                           ("host_shell","filesystem_read","filesystem_write")),
                           clock=profiles.PolicyClock())
        grant = g.fixture_grant("convert",call.request(self.ctx),60)
        return g,ConversionFixture(g,grant,self.ctx,call)

    def test_exact_output_and_digest(self):
        _,job = self.fixture()
        job.complete(self.output)
        self.assertEqual(job.result(),("SIMULATED_ONLY","ffmpeg","fixture",self.output,
                                       sha256(self.output.data).hexdigest()))
        with self.assertRaises(Refused):
            job.complete(self.output)

    def test_mismatched_source_target_format_destination_refused(self):
        for bad in (replace(self.output,request_target="b"*64),
                    replace(self.output,source_digest="b"*64),
                    replace(self.output,output="other"),
                    replace(self.output,format="pdf")):
            _,job = self.fixture()
            with self.assertRaisesRegex(Refused,"conversion_provenance_mismatch"):
                job.complete(bad)
            with self.assertRaises(Refused):
                job.result()
            with self.assertRaises(Refused):
                job.complete(self.output)

    def test_invalid_output_and_size(self):
        for data in (b"",b"fLaC",b"wrong",bytearray(b"fLaCfixture"),b"fLaC"+b"x"*MAX_OUTPUT):
            _,job = self.fixture()
            with self.assertRaises(Refused):
                job.complete(replace(self.output,data=data))

    def test_pdf_and_no_overwrite(self):
        call = replace(self.call,adapter="libreoffice",operation="text_to_pdf")
        _,job = self.fixture(call)
        out = replace(self.output,request_target=call.request(self.ctx).target,format="pdf",data=b"%PDF-fixture")
        job.complete(out)
        self.assertEqual(job.result()[3],out)
        with self.assertRaisesRegex(Refused,"source_overwrite_denied"):
            replace(call,args=call.args[:2]+(("output","source"),)).request(self.ctx)

    def test_revoked_or_cancelled_results_not_published(self):
        g,job = self.fixture()
        job.complete(self.output)
        g.revoke("convert")
        with self.assertRaises(Refused):
            job.result()
        _,job = self.fixture()
        job.cancel()
        with self.assertRaises(Refused):
            job.complete(self.output)


    def publication_fixture(self):
        g = FixtureGateway(self.ctx,tuple(profiles.Rule(a,(),"allow") for a in
                           ("host_shell","filesystem_read","filesystem_write","publish_artifact")),
                           clock=profiles.PolicyClock())
        conversion = g.fixture_grant("convert",self.req,60)
        job = ConversionFixture(g,conversion,self.ctx,self.call)
        job.complete(self.output)
        return g,conversion,job

    def test_publication_requires_exact_separate_grant(self):
        g,conversion,job = self.publication_fixture()
        call = job.prepare_publication("fixture","destination")
        self.assertEqual(dict(call.args)["result_digest"],sha256(self.output.data).hexdigest())
        approved = g.fixture_grant("publish",call.request(self.ctx),60)
        with self.assertRaisesRegex(Refused,"request_mismatch"):
            job.simulate_publication("fixture","other",approved)
        with self.assertRaisesRegex(Refused,"request_mismatch"):
            job.simulate_publication("fixture","destination",conversion)
        self.assertEqual(job.simulate_publication("fixture","destination",approved)[0],"SIMULATED_ONLY")
        with self.assertRaisesRegex(Refused,"conversion_not_publishable"):
            job.prepare_publication("fixture","destination")

    def test_publication_refuses_aliases_and_missing_policy(self):
        g,_,job = self.publication_fixture()
        for destination in ("source","new"):
            with self.assertRaisesRegex(Refused,"publication_destination_conflict"):
                job.prepare_publication("fixture",destination)
        limited,job = self.fixture()
        job.complete(self.output)
        call = job.prepare_publication("fixture","destination")
        with self.assertRaises(Refused):
            limited.fixture_grant("publish",call.request(self.ctx),60)

    def test_revocation_before_publication(self):
        g,_,job = self.publication_fixture()
        call = job.prepare_publication("fixture","destination")
        approved = g.fixture_grant("publish",call.request(self.ctx),60)
        g.revoke("convert")
        with self.assertRaises(Refused):
            job.simulate_publication("fixture","destination",approved)


    def test_publication_checks_exact_read_write_and_publish_ceilings(self):
        _,_,preview = self.publication_fixture()
        request = preview.prepare_publication("fixture","destination").request(self.ctx)
        for missing in ("filesystem_read","filesystem_write","publish_artifact"):
            rules = [profiles.Rule(a,(self.req.target,),"allow") for a in
                     ("host_shell","filesystem_read","filesystem_write")]
            rules += [profiles.Rule(a,(request.target,),"allow") for a in
                      ("filesystem_read","filesystem_write","publish_artifact") if a!=missing]
            g = FixtureGateway(self.ctx,tuple(rules),clock=profiles.PolicyClock())
            conversion = g.fixture_grant("convert",self.req,60)
            job = ConversionFixture(g,conversion,self.ctx,self.call)
            job.complete(self.output)
            with self.assertRaises(Refused):
                g.fixture_grant("publish",request,60)

    def test_publication_explicit_write_deny_cannot_be_overridden_by_allow(self):
        _,_,preview = self.publication_fixture()
        request = preview.prepare_publication("fixture","destination").request(self.ctx)
        rules = [profiles.Rule(a,(),"allow") for a in
                 ("host_shell","filesystem_read","filesystem_write","publish_artifact")]
        rules.append(profiles.Rule("filesystem_write",(request.target,),"deny"))
        g = FixtureGateway(self.ctx,tuple(rules),clock=profiles.PolicyClock())
        conversion = g.fixture_grant("convert",self.req,60)
        job = ConversionFixture(g,conversion,self.ctx,self.call)
        job.complete(self.output)
        with self.assertRaisesRegex(Refused,"explicit_deny"):
            g.fixture_grant("publish",request,60)

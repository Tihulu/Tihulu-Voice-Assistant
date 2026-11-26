# SPDX-License-Identifier: AGPL-3.0-only
"""Real filesystem primitives in a private synthetic fixture, never owner files."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway, Refused
from capabilities import CapabilityCall
from conversion_result import ConversionFixture, ConversionResult


def publish_fixture(directory, job, grant):
    """Fixed synthetic names, trusted directory, no production permissions.

    Pin the private fixture directory. Stage verified bytes with O_EXCL and
    link to the final name without replace. No arbitrary path is accepted.
    This does not serialize OS effects against real revocation or protect
    directories writable by other users/processes.
    """
    receipt = job.result()
    result, expected = receipt[3], receipt[4]
    if sha256(result.data).hexdigest() != expected:
        raise Refused("fixture_digest_mismatch")
    job.simulate_publication("fixture", "published", grant)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    created = False
    try:
        stage_fd = os.open(".fixture-stage", os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                           os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
        created = True
        with os.fdopen(stage_fd, "wb") as handle:
            handle.write(result.data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(".fixture-stage", "published", src_dir_fd=fd, dst_dir_fd=fd,
                follow_symlinks=False)
        os.fsync(fd)
    finally:
        try:
            if created:
                os.unlink(".fixture-stage", dir_fd=fd)
        finally:
            os.close(fd)


class PublicationFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="jarvis-publication-fixture-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.write_bytes(b"public synthetic source")
        self.data = b"fLaCsynthetic receipt only"
        self.before = sha256(self.source.read_bytes()).hexdigest()
        ctx = Context("fixture","session","profile",1,"local","LOCAL_ONLY")
        call = CapabilityCall("ffmpeg","fixture","wav_to_flac",
                              (("artifact","source"),("digest",self.before),("output","staging")))
        req = call.request(ctx)
        self.gateway = FixtureGateway(ctx,tuple(profiles.Rule(a,(),"allow") for a in
                      ("host_shell","filesystem_read","filesystem_write","publish_artifact")),
                      clock=profiles.PolicyClock())
        self.conversion = self.gateway.fixture_grant("conversion",req,60)
        self.job = ConversionFixture(self.gateway,self.conversion,ctx,call)
        self.job.complete(ConversionResult(req.target,self.before,"staging","flac",self.data))
        publish = self.job.prepare_publication("fixture","published")
        self.grant = self.gateway.fixture_grant("publication",publish.request(ctx),60)

    def unchanged(self):
        self.assertEqual(sha256(self.source.read_bytes()).hexdigest(),self.before)
        self.assertFalse((self.root / ".fixture-stage").exists())

    def test_publication_exact_bytes_and_no_replay(self):
        publish_fixture(self.root,self.job,self.grant)
        self.assertEqual((self.root/"published").read_bytes(),self.data)
        self.unchanged()
        with self.assertRaises(Refused):
            publish_fixture(self.root,self.job,self.grant)
        self.assertEqual((self.root/"published").read_bytes(),self.data)

    def test_existing_regular_destination_not_overwritten(self):
        target = self.root/"published"
        target.write_bytes(b"existing synthetic file")
        with self.assertRaises(FileExistsError):
            publish_fixture(self.root,self.job,self.grant)
        self.assertEqual(target.read_bytes(),b"existing synthetic file")
        self.unchanged()

    def test_symlink_destination_not_followed_or_replaced(self):
        target = self.root/"published"
        target.symlink_to(self.source)
        with self.assertRaises(FileExistsError):
            publish_fixture(self.root,self.job,self.grant)
        self.assertTrue(target.is_symlink())
        self.unchanged()

    def test_revoked_dependency_no_filesystem_effect(self):
        self.gateway.revoke("conversion")
        with self.assertRaises(Refused):
            publish_fixture(self.root,self.job,self.grant)
        self.assertFalse((self.root/"published").exists())
        self.unchanged()

    def test_existing_staging_file_is_not_removed_or_modified(self):
        stage = self.root/".fixture-stage"
        stage.write_bytes(b"preexisting stage")
        with self.assertRaises(FileExistsError):
            publish_fixture(self.root,self.job,self.grant)
        self.assertEqual(stage.read_bytes(),b"preexisting stage")
        self.assertFalse((self.root/"published").exists())
        self.assertEqual(sha256(self.source.read_bytes()).hexdigest(),self.before)

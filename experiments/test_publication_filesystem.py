# SPDX-License-Identifier: AGPL-3.0-only
"""Real filesystem primitives in a private synthetic fixture, never owner files."""
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from hashlib import sha256
from tihulu_jarvis import profiles
from gateway import Context, FixtureGateway, Refused
from capabilities import CapabilityCall
from conversion_result import ConversionFixture, ConversionResult, MAX_OUTPUT


def open_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(fd)
        raise Refused("fixture_directory_not_private")
    return fd


def destination_scope(fd):
    info = os.fstat(fd)
    return f"published.{info.st_dev:x}.{info.st_ino:x}"


def source_digest(fd):
    # This is a fixed fixture file, not a user-supplied path.
    source_fd = os.open("source", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                        os.O_CLOEXEC, dir_fd=fd)
    with os.fdopen(source_fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_OUTPUT:
            raise Refused("fixture_source_invalid")
        data = handle.read(MAX_OUTPUT + 1)
        if len(data) != info.st_size:
            raise Refused("fixture_source_invalid")
        return sha256(data).hexdigest()


def publish_fixture(directory, job, grant):
    """Fixed private CI directory; pin exact directory identity in fixture grant.

    No arbitrary paths or production permission. Same-UID malicious mutation,
    ancestor substitution and crash recovery remain outside this fixture.
    """
    receipt = job.result()
    result, expected = receipt[3], receipt[4]
    if sha256(result.data).hexdigest() != expected:
        raise Refused("fixture_digest_mismatch")
    fd = open_directory(directory)
    created = False
    try:
        if source_digest(fd) != result.source_digest:
            raise Refused("fixture_source_changed")
        job.simulate_publication("fixture", destination_scope(fd), grant)
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
                os.fsync(fd)
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
        fd = open_directory(self.root)
        try:
            publish = self.job.prepare_publication("fixture",destination_scope(fd))
        finally:
            os.close(fd)
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

    def test_replacement_directory_with_same_source_denied(self):
        with TemporaryDirectory(prefix="jarvis-replacement-") as other:
            replacement = Path(other)
            (replacement/"source").write_bytes(self.source.read_bytes())
            with self.assertRaisesRegex(Refused,"request_mismatch"):
                publish_fixture(replacement,self.job,self.grant)
            self.assertFalse((replacement/"published").exists())
            self.assertFalse((replacement/".fixture-stage").exists())
        publish_fixture(self.root,self.job,self.grant)
        self.assertEqual((self.root/"published").read_bytes(),self.data)

    def test_changed_source_refused_before_claim_or_write(self):
        original = self.source.read_bytes()
        self.source.write_bytes(b"changed")
        with self.assertRaisesRegex(Refused,"fixture_source_changed"):
            publish_fixture(self.root,self.job,self.grant)
        self.assertFalse((self.root/"published").exists())
        self.assertFalse((self.root/".fixture-stage").exists())
        self.source.write_bytes(original)
        publish_fixture(self.root,self.job,self.grant)
        self.unchanged()

    def test_symlink_directory_refused(self):
        with TemporaryDirectory(prefix="jarvis-alias-") as other:
            alias = Path(other)/"alias"
            alias.symlink_to(self.root,target_is_directory=True)
            with self.assertRaises(OSError):
                publish_fixture(alias,self.job,self.grant)
        self.assertFalse((self.root/"published").exists())
        self.unchanged()

    def test_nonprivate_directory_refused_before_claim(self):
        self.root.chmod(0o755)
        try:
            with self.assertRaisesRegex(Refused,"fixture_directory_not_private"):
                publish_fixture(self.root,self.job,self.grant)
            self.assertFalse((self.root/"published").exists())
        finally:
            self.root.chmod(0o700)
        publish_fixture(self.root,self.job,self.grant)
        self.unchanged()

    def test_source_symlink_refused_without_effect(self):
        with TemporaryDirectory(prefix="jarvis-source-alias-") as other:
            target = Path(other)/"source"
            target.write_bytes(self.source.read_bytes())
            self.source.unlink()
            self.source.symlink_to(target)
            with self.assertRaises(OSError):
                publish_fixture(self.root,self.job,self.grant)
            self.assertFalse((self.root/"published").exists())
            self.assertFalse((self.root/".fixture-stage").exists())
            self.assertTrue(self.source.is_symlink())

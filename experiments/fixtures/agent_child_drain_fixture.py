# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable CI only: fixed child, no model, shell, network or descendants."""
import subprocess
import sys
import unittest
from gateway import Refused
import test_local_agent_owned_drain as fixtures

CHILD="import sys; sys.stdin.buffer.read(1)"

class RealOwnedChildTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.ManagedWaveTests()
        f.setUp()
        self.f=f

    def child(self):
        p=subprocess.Popen([sys.executable,"-I","-S","-c",CHILD],
                           stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,close_fds=True,
                           env={"LC_ALL":"C"})
        self.addCleanup(self.cleanup_child,p)
        return p

    @staticmethod
    def cleanup_child(p):
        if p.poll() is None:
            p.kill()
        p.wait(timeout=3)
        if p.stdin is not None:
            p.stdin.close()

    def end(self,p):
        p.stdin.write(b"x")
        p.stdin.flush()
        return p.wait(timeout=3)

    def test_normal_exit_releases_only_after_wait(self):
        h=self.f.start("plan")
        p=self.child()
        self.assertIsNone(p.poll())
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        code=self.end(p)
        self.assertEqual(code,0)
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.assertEqual(self.f.exit(h,code),"COMPLETED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))

    def test_cancel_revokes_but_live_child_retains_reservation(self):
        h=self.f.start("plan")
        p=self.child()
        self.f.run.stop()
        self.assertIsNone(p.poll())
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        with self.assertRaises(Refused):
            self.f.g.checkpoint(self.f.grants["plan"],self.f.run.request("plan"))
        p.terminate()
        code=p.wait(timeout=3)
        self.assertNotEqual(code,0)
        self.assertEqual(self.f.exit(h,code),"DRAINED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))

    def test_timeout_is_not_exit_observation(self):
        h=self.f.start("plan")
        p=self.child()
        self.f.run.stop()
        with self.assertRaises(subprocess.TimeoutExpired):
            p.wait(timeout=.05)
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        p.kill()
        code=p.wait(timeout=3)
        self.assertEqual(self.f.exit(h,code),"DRAINED")

    def test_one_child_exit_does_not_release_other_child(self):
        self.f.exit(self.f.start("plan"))
        a,b=self.f.start("a"),self.f.start("b")
        pa,pb=self.child(),self.child()
        pa.terminate()
        self.assertEqual(self.f.exit(a,pa.wait(timeout=3)),"FAILED")
        self.assertIsNone(pb.poll())
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        pb.terminate()
        self.assertEqual(self.f.exit(b,pb.wait(timeout=3)),"DRAINED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))

if __name__=="__main__":
    unittest.main(verbosity=2)

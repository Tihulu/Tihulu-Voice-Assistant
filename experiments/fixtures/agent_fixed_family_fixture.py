# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable CI fixed parent + one child; not general process containment."""
import select
import subprocess
import sys
import unittest
from gateway import Refused
import test_local_agent_owned_drain as fixtures

PARENT = """
import subprocess,sys
child=subprocess.Popen([sys.executable,"-I","-S","-c","import sys; sys.stdin.buffer.read(1)"],
                       stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                       close_fds=True,env={"LC_ALL":"C"})
try:
    print("READY",flush=True)
    sys.stdin.buffer.read(1)
    child.stdin.close()
    child.wait(timeout=3)
    print("REAPED:"+str(child.returncode),flush=True)
    sys.stdin.buffer.read(1)
finally:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=3)
"""

class FixedFamilyTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.ManagedWaveTests()
        f.setUp()
        self.f=f

    def parent(self):
        p=subprocess.Popen([sys.executable,"-I","-S","-c",PARENT],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                           close_fds=True,env={"LC_ALL":"C"})
        self.addCleanup(self.cleanup,p)
        self.assertEqual(self.line(p),b"READY\n")
        return p

    def line(self,p):
        ready,_,_=select.select([p.stdout],[],[],3)
        self.assertTrue(ready,"fixed parent observation timed out")
        return p.stdout.readline(64)

    @staticmethod
    def cleanup(p):
        if p.stdin is not None and not p.stdin.closed:
            p.stdin.close()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=3)
        if p.stdout is not None:
            p.stdout.close()

    def reap_child(self,p):
        p.stdin.write(b"x")
        p.stdin.flush()
        self.assertEqual(self.line(p),b"REAPED:0\n")
        self.assertIsNone(p.poll())

    def finish_parent(self,p):
        p.stdin.write(b"x")
        p.stdin.flush()
        return p.wait(timeout=3)

    def test_reaped_descendant_alone_does_not_release_root(self):
        h=self.f.start("plan")
        m=self.f.run.register_member(h,"fixed-child",self.f.ctx,self.f.models)
        p=self.parent()
        self.reap_child(p)
        self.assertEqual(self.f.run.observe_member_exit(m,self.f.ctx,self.f.models),"PENDING")
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.assertEqual(self.f.exit(h,self.finish_parent(p)),"COMPLETED")
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))

    def test_cancel_waits_for_fixed_parent_and_child(self):
        h=self.f.start("plan")
        m=self.f.run.register_member(h,"fixed-child",self.f.ctx,self.f.models)
        p=self.parent()
        self.f.run.stop()
        with self.assertRaises(Refused):
            self.f.g.checkpoint(self.f.grants["plan"],self.f.run.request("plan"))
        self.assertEqual(self.f.ledger.snapshot(),(4096,2048,2048))
        self.reap_child(p)
        self.assertEqual(self.f.run.observe_member_exit(m,self.f.ctx,self.f.models),"PENDING")
        self.assertFalse(self.f.run.drained())
        self.assertEqual(self.f.exit(h,self.finish_parent(p)),"DRAINED")
        self.assertTrue(self.f.run.drained())
        self.assertEqual(self.f.ledger.snapshot(),(4096,0,4096))

if __name__=="__main__":
    unittest.main(verbosity=2)

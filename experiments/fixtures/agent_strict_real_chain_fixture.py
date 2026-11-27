# SPDX-License-Identifier: AGPL-3.0-only
"""Fixed public CI input/output chain, no general model or command executor."""
import subprocess
import sys
import unittest
from hashlib import sha256
from gateway import Refused
from local_agent_launch import LaunchBoundary
from local_agent_replan import AttemptCoordinator
from local_agent_resources import ResourceLedger
import test_local_agent_wave_run as base

# Fixed child consumes bounded public bytes, not instructions.
CHILD = """
import hashlib,sys
data=sys.stdin.buffer.read(1025)
if not 0<len(data)<=1024:
    sys.exit(2)
sys.stdout.buffer.write(hashlib.sha256(data).hexdigest().encode("ascii"))
"""

class StrictRealChainTests(unittest.TestCase):
    def setUp(self):
        f=base.WaveRunTests()
        f.setUp()
        self.f=f
        self.ledger=ResourceLedger(4096)
        c=AttemptCoordinator(f.g,self.ledger,"fixed-chain",f.workers,4096,2,
                             owned_drain=True,strict_results=True)
        self.run=c.confirm(c.prepare(f.ctx,f.models),f.ctx,f.models)
        self.boundary=LaunchBoundary(self.run)
        self.grants={w.identity:f.g.fixture_grant("chain-"+w.identity,self.run.request(w.identity),60)
                     for w in f.workers}
        self.children={}
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.boundary.stop()
        for t,p in tuple(self.children.items()):
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=3)
            self.boundary.observe_reaped(t,p.returncode,self.f.ctx,self.f.models)
            del self.children[t]

    def launch(self,w):
        t=self.boundary.prepare(w,self.grants[w],self.f.ctx,self.f.models)
        try:
            self.boundary.before_start(t,self.f.ctx,self.f.models)
        except Refused:
            self.boundary.not_started(t,self.f.ctx,self.f.models)
            raise
        try:
            p=subprocess.Popen([sys.executable,"-I","-S","-c",CHILD],
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL,close_fds=True,env={"LC_ALL":"C"})
        except BaseException:
            self.boundary.uncertain(t)
            raise
        self.children[t]=p
        self.boundary.started(t,self.f.ctx,self.f.models)
        return t

    @staticmethod
    def frame(worker,rows):
        return (b"public-chain-v1:"+worker.encode("ascii")+b"\0"+
                b"".join(len(data).to_bytes(4,"big")+data for _,data in rows))

    def finish(self,t,*,read=True):
        p=self.children[t]
        rows=self.boundary.inputs(t,self.f.ctx,self.f.models) if read else ()
        data=self.frame(t.worker,rows)
        self.assertLessEqual(len(data),1024)
        out,_=p.communicate(input=data,timeout=3)
        self.assertEqual(p.returncode,0)
        self.assertEqual(out,sha256(data).hexdigest().encode("ascii"))
        try:
            return self.boundary.observe_reaped(t,p.returncode,self.f.ctx,self.f.models,payload=out),out
        finally:
            del self.children[t]

    def test_full_real_four_worker_chain(self):
        _,root=self.finish(self.launch("plan"))
        a,b=self.launch("a"),self.launch("b")
        self.assertEqual(self.ledger.snapshot(),(4096,4096,0))
        _,pa=self.finish(a)
        self.assertEqual(self.ledger.snapshot(),(4096,2048,2048))
        _,pb=self.finish(b)
        self.assertNotEqual(pa,pb)
        self.assertNotEqual(root,pa)
        review=self.launch("review")
        status,final=self.finish(review)
        self.assertEqual(status,"COMPLETED")
        receipt,payload=self.run.output("review",self.f.ctx,self.f.models)
        self.assertEqual(payload,final)
        self.assertEqual(receipt.payload_digest,sha256(final).hexdigest())
        self.assertEqual(len(receipt.input_digest),64)
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))

    def test_real_exit_without_required_read_drains(self):
        self.finish(self.launch("plan"))
        a,b=self.launch("a"),self.launch("b")
        with self.assertRaises(Refused):
            self.finish(a,read=False)
        self.assertEqual(self.ledger.snapshot(),(4096,2048,2048))
        p=self.children[b]
        p.kill()
        p.communicate(timeout=3)
        self.assertEqual(self.boundary.observe_reaped(b,p.returncode,self.f.ctx,self.f.models),"DRAINED")
        del self.children[b]
        self.assertEqual(self.ledger.snapshot(),(4096,0,4096))

    def test_revoked_dependency_prevents_real_child_input(self):
        self.finish(self.launch("plan"))
        a=self.launch("a")
        self.f.g.revoke("chain-plan")
        with self.assertRaises(Refused):
            self.boundary.inputs(a,self.f.ctx,self.f.models)
        self.assertEqual(self.ledger.snapshot(),(4096,2048,2048))
        p=self.children[a]
        p.kill()
        p.communicate(timeout=3)
        self.assertEqual(self.boundary.observe_reaped(a,p.returncode,self.f.ctx,self.f.models),"DRAINED")
        del self.children[a]

if __name__=="__main__":
    unittest.main(verbosity=2)

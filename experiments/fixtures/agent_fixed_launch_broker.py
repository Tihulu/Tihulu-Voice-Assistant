# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable CI fixed single-child broker, not an agent runtime.

No user command, argument, environment, model output or executable accepted.
The fixed child only waits for stdin EOF. No descendants or network.
"""
import subprocess
import sys
from threading import current_thread
from gateway import Refused
from local_agent_launch import LaunchBoundary

_CHILD="import sys; sys.stdin.buffer.read(1); sys.stdout.buffer.write(b\"fixed-child-exited\"); sys.stdout.buffer.flush()"
_EXPECTED=b"fixed-child-exited"

class FixedChildBroker:
    def __init__(self,boundary):
        if type(boundary) is not LaunchBoundary:
            raise Refused("fixed_broker_invalid")
        self._boundary=boundary
        self._owner=current_thread()
        self._children={}
        self._closed=False

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("fixed_broker_thread")

    def launch(self,worker,grant,context,candidates):
        self._thread()
        if self._closed:
            raise Refused("fixed_broker_closed")
        ticket=self._boundary.prepare(worker,grant,context,candidates)
        try:
            self._boundary.before_start(ticket,context,candidates)
        except Refused:
            # No Popen call occurred, so this is definite no-start.
            self._boundary.not_started(ticket,context,candidates)
            raise
        try:
            child=subprocess.Popen([sys.executable,"-I","-S","-c",_CHILD],
                                   stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,close_fds=True,
                                   env={"LC_ALL":"C"})
        except BaseException:
            # Conservative: do not infer absence from arbitrary launch errors.
            self._boundary.uncertain(ticket)
            self._closed=True
            raise
        self._children[ticket]=child
        try:
            self._boundary.started(ticket,context,candidates)
        except BaseException:
            self._boundary.stop()
            self._closed=True
            # Exact child remains registered for explicit drain.
            raise
        return ticket

    def pending(self):
        self._thread()
        return tuple(self._children)

    def inputs(self,ticket,context,candidates):
        self._thread()
        if not any(t is ticket for t in self._children):
            raise Refused("fixed_child_unknown")
        return self._boundary.inputs(ticket,context,candidates)

    def stop(self):
        self._thread()
        self._closed=True
        return self._boundary.stop()

    def reap(self,ticket,context,candidates,*,finish=False,force=False):
        self._thread()
        if type(finish) is not bool or type(force) is not bool:
            raise Refused("fixed_reap_invalid")
        # Identity lookup only; no PID supplied by caller.
        child=next((p for t,p in self._children.items() if t is ticket),None)
        if child is None:
            raise Refused("fixed_child_unknown")
        if finish and child.stdin is not None and not child.stdin.closed:
            child.stdin.close()
        if force:
            self.stop()
            if child.poll() is None:
                child.kill()
        try:
            code=child.wait(timeout=0.1 if not finish and not force else 3)
        except subprocess.TimeoutExpired:
            return "PENDING"
        if child.stdin is not None and not child.stdin.closed:
            child.stdin.close()
        try:
            # Fixed child writes at most 18 bytes, no descendants. Waiting
            # before bounded pipe read is not safe for arbitrary programs.
            payload=child.stdout.read(len(_EXPECTED)+1)
            if code==0 and payload!=_EXPECTED:
                self.stop()
                self._boundary.observe_reaped(ticket,code,context,candidates)
                raise Refused("fixed_child_result_invalid")
            return self._boundary.observe_reaped(ticket,code,context,candidates,
                                                 payload=payload if code==0 else None)
        finally:
            child.stdout.close()
            # Under this fixed single-child contract, wait has reaped the
            # only process; never call this path for a general process tree.
            del self._children[ticket]

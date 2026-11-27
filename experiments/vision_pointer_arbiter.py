# SPDX-License-Identifier: AGPL-3.0-only
"""Single selected-frame controller; simulated effects only, no OS input."""
from dataclasses import dataclass
from threading import current_thread
from gateway import FixtureGateway,Request,Refused
from vision_private_frames import PrivateFrames,identity,target

PALETTE=("#2684ff","#a855f7","#10b981","#f59e0b","#ec4899",
         "#06b6d4","#ef4444","#84cc16","#6366f1","#f97316")

@dataclass(frozen=True,eq=False)
class Pointer:
    agent: str
    color: str
    x: int
    y: int
    epoch: int

@dataclass(frozen=True,eq=False)
class Proposal:
    agent: str
    operation: str
    x: int
    y: int
    epoch: int
    sequence: int
    frame_digest: str

class PointerArbiter:
    """One controller per gateway lifetime; selected-frame pixel coordinates.
    A stale frame or owner takeover closes the fixture. No actual portal session.
    """
    def __init__(self,gateway,frames,frame):
        if type(gateway) is not FixtureGateway or type(frames) is not PrivateFrames or frames._g is not gateway:
            raise Refused("pointer_scope")
        frames.window_current(frame)
        gateway.register_agent_attempt("vision-pointer-controller-v1")
        self._g=gateway
        self._frames=frames
        self._frame=frame
        self._owner=current_thread()
        self._marks={}
        self._colors={}
        self._pending=None
        self._claimed=None
        self._sequence=0
        self._closed=False

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("pointer_thread")

    def _check(self):
        self._thread()
        if self._closed:
            raise Refused("pointer_closed")
        try:
            self._frames.window_current(self._frame)
        except Refused:
            self.takeover()
            raise

    def _point(self,agent,x,y):
        self._check()
        identity(agent)
        if type(x) is not int or type(y) is not int or not 0<=x<self._frame.width or not 0<=y<self._frame.height:
            raise Refused("pointer_bounds")
        return self._frames.checkpoint(self._frame,agent)

    def mark(self,agent,x,y):
        self._point(agent,x,y)
        if agent not in self._colors:
            if len(self._colors)>=len(PALETTE):
                raise Refused("pointer_agent_limit")
            self._colors[agent]=PALETTE[len(self._colors)]
        marker=Pointer(agent,self._colors[agent],x,y,self._frame.epoch)
        self._marks[agent]=marker
        return marker

    def marks(self):
        self._check()
        for agent in tuple(self._marks):
            try:
                self._frames.checkpoint(self._frame,agent)
            except Refused:
                del self._marks[agent]
        return tuple(self._marks.values())

    def propose(self,agent,operation,x,y):
        self._point(agent,x,y)
        if type(operation) is not str or operation not in ("move","click"):
            raise Refused("pointer_operation")
        if self._pending is not None or self._claimed is not None:
            raise Refused("pointer_busy")
        if self._sequence>=64:
            raise Refused("pointer_limit")
        self._sequence+=1
        p=Proposal(agent,operation,x,y,self._frame.epoch,self._sequence,self._frame.pixel_digest)
        self._pending=p
        return p

    def request(self,p):
        self._check()
        if type(p) is not Proposal or (p is not self._pending and (self._claimed is None or p is not self._claimed[0])):
            raise Refused("pointer_proposal_stale")
        t=target("pointer-private-v1",self._frame.surface,self._frame.source_digest,
                 p.frame_digest,p.epoch,p.sequence,p.agent,p.operation,p.x,p.y)
        return Request(self._g.context,"application_ui",t,"local","private").checked()

    def confirm(self,p,grant):
        self._check()
        if p is None or p is not self._pending or self._claimed is not None:
            raise Refused("pointer_proposal_stale")
        req=self.request(p)
        review=self._point(p.agent,p.x,p.y)
        self._g.claim_with_dependencies(grant,req,(review,))
        self._pending=None
        self._claimed=(p,grant,req)

    def apply(self,p):
        self._check()
        if self._claimed is None or p is not self._claimed[0]:
            raise Refused("pointer_not_confirmed")
        _,grant,req=self._claimed
        self._claimed=None
        try:
            self._point(p.agent,p.x,p.y)
            self._g.checkpoint_claimed_group(((grant,req),))
            return ("SIMULATED_ONLY",p.agent,p.operation,p.x,p.y)
        finally:
            self._g.revoke(grant.identity)

    def takeover(self):
        self._thread()
        self._pending=None
        if self._claimed is not None:
            self._g.revoke(self._claimed[1].identity)
            self._claimed=None
        self._marks.clear()
        self._closed=True

# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit owner-reviewed source transitions. No DNS or network effect."""
from dataclasses import dataclass
from gateway import Request, Refused
from research import canonical, scoped

@dataclass(frozen=True)
class SourcePlan:
    root: str
    urls: tuple

class RedirectPlan:
    """A hop consumes an independently granted target, never inherits authority."""
    def __init__(self,gateway,context,plan):
        if type(plan) is not SourcePlan or type(plan.urls) is not tuple or not 1<=len(plan.urls)<=5:
            raise Refused("invalid_plan")
        root=canonical(plan.root)
        targets=tuple(canonical(url) for url in plan.urls)
        if len({t.identity for t in targets})!=len(targets) or any(not scoped(t,root) for t in targets):
            raise Refused("invalid_scope_or_loop")
        self._g,self._ctx,self._targets=gateway,context.checked(),targets
        self._index=0
        self._visited=[]
        self._state="ready"

    @property
    def state(self):
        return self._state

    def hop(self,url,grant):
        if self._state!="ready":
            raise Refused("plan_not_ready")
        try:
            target=canonical(url)
            if target!=self._targets[self._index]:
                raise Refused("unapproved_transition")
            req=Request(self._ctx,"research",target.identity,"local","public")
            if any(grant is g for g,_ in self._visited):
                raise Refused("grant_reuse")
            for g,r in self._visited:
                self._g.checkpoint(g,r)
            self._g.checkpoint(grant,req)
        except Refused:
            self._state="held"
            raise
        self._visited.append((grant,req))
        self._index+=1
        if self._index==len(self._targets):
            self._state="completed"
        return target.url

    def final_target(self):
        if self._state!="completed":
            raise Refused("plan_not_completed")
        try:
            for grant,req in self._visited:
                self._g.checkpoint(grant,req)
        except Refused:
            self._state="held"
            raise
        return self._targets[-1].url

    def close(self):
        self._visited.clear()
        self._state="closed"

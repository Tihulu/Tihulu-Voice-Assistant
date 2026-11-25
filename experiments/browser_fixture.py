# SPDX-License-Identifier: AGPL-3.0-only
"""Browser policy fixture, not a Firefox launcher or network sandbox.
All events are synthetic. Default deny; no actual browser execution.
"""
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from gateway import Request,Refused,token
from research import canonical,scoped

# No JS/subresource/download/websocket/external-handler exceptions in this slice.
@dataclass(frozen=True)
class Plan:
    profile: str
    root: str
    pages: tuple
    def checked(self):
        token(self.profile)
        root=canonical(self.root)
        if type(self.pages) is not tuple or not 1<=len(self.pages)<=5:
            raise Refused("invalid_browser_plan")
        pages=tuple(canonical(p) for p in self.pages)
        if len(set(pages))!=len(pages) or any(not scoped(p,root) for p in pages):
            raise Refused("invalid_browser_scope")
        return root,pages
    def target(self):
        root,pages=self.checked()
        return sha256(repr(("browser-fixture",self.profile,root.url,
                            tuple(p.url for p in pages))).encode("ascii")).hexdigest()

def action_target(plan,kind,url,document=""):
    if type(plan) is not Plan or kind not in ("navigate","snapshot"):
        raise Refused("invalid_browser_action")
    target=canonical(url)
    _,pages=plan.checked()
    if target not in pages:
        raise Refused("unapproved_page")
    if (kind=="snapshot" and (type(document) is not str or len(document)!=64
            or any(c not in "0123456789abcdef" for c in document))) or (kind=="navigate" and document!=""):
        raise Refused("invalid_document")
    return sha256(repr((plan.target(),kind,target.url,document)).encode("ascii")).hexdigest()

class BrowserFixture:
    def __init__(self,gateway,context,plan,grant):
        if type(plan) is not Plan:
            raise Refused("invalid_browser_plan")
        self._g,self._ctx,self._plan=gateway,context.checked(),plan
        self._req=Request(context,"browser",plan.target(),"local","public")
        self._g.claim(grant,self._req)
        self._grant=grant
        self._active=None
        self._visited=[]
        self._closed=False
        self._lock=Lock()
    def _check(self):
        if self._closed:
            raise Refused("browser_closed")
        self._g.checkpoint(self._grant,self._req)
        for g,r in self._visited:
            self._g.checkpoint(g,r)
    def navigate(self,url,grant):
        with self._lock:
            self._check()
            _,pages=self._plan.checked()
            target=canonical(url)
            if len(self._visited)>=len(pages) or target!=pages[len(self._visited)]:
                raise Refused("unexpected_navigation")
            req=Request(self._ctx,"browser",action_target(self._plan,"navigate",url),"local","public")
            self._g.claim(grant,req)
            self._visited.append((grant,req))
            self._active=target
            return "SIMULATED_NAVIGATION_ONLY"
    def request(self,kind,url):
        # Explicitly no inherited permission for any automatic browser traffic.
        with self._lock:
            self._check()
            raise Refused("browser_automatic_traffic_denied")
    def snapshot(self,url,data,grant):
        with self._lock:
            self._check()
            if self._active!=canonical(url) or type(data) is not bytes or not 0<len(data)<=65536:
                raise Refused("invalid_snapshot")
            try:
                text=data.decode("utf-8")
            except UnicodeError:
                raise Refused("invalid_snapshot") from None
            if any((ord(c)<32 and c not in "\n\t") or 127<=ord(c)<=159
                   or 0x202a<=ord(c)<=0x202e or 0x2066<=ord(c)<=0x2069 for c in text):
                raise Refused("invalid_snapshot")
            digest=sha256(data).hexdigest()
            req=Request(self._ctx,"browser",action_target(self._plan,"snapshot",url,digest),"local","public")
            self._g.checkpoint(grant,req)
            self._check()
            self._g.claim(grant,req)
            return digest,text
    def close(self):
        with self._lock:
            self._closed=True
            self._active=None
            self._visited.clear()
            self._g.revoke(self._grant.identity)

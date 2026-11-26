# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic browser task lifecycle using the shared gateway.

No actual browser, network, personal profile, or OS window is accessed.
"""
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from gateway import FixtureGateway, Refused, token
from capabilities import CapabilityCall, public_fixture_url

MAX_PAGES = 5
MAX_SNAPSHOT_BYTES = 16384


@dataclass(frozen=True)
class BrowserPlan:
    browser: str
    version: str
    profile: str
    tab: str
    pages: tuple

    def checked(self):
        for value in (self.browser, self.version, self.profile, self.tab):
            if type(value) is not str:
                raise Refused("invalid_browser_identity")
            token(value)
        if self.browser not in ("firefox", "brave") or type(self.pages) is not tuple or not 1 <= len(self.pages) <= MAX_PAGES:
            raise Refused("invalid_browser_plan")
        for page in self.pages:
            public_fixture_url(page)
        if len(set(self.pages)) != len(self.pages):
            raise Refused("duplicate_browser_page")
        return self

    def call(self, kind, url, digest=None):
        self.checked()
        public_fixture_url(url)
        if url not in self.pages:
            raise Refused("unapproved_page")
        args = (("profile",self.profile),("tab",self.tab),("url",url))
        if kind == "snapshot":
            args += (("digest",digest),)
        elif kind != "navigate" or digest is not None:
            raise Refused("invalid_browser_action")
        return CapabilityCall(self.browser,self.version,kind,args).checked()


class BrowserTask:
    """Single owned synthetic tab, exact navigation and content grants."""
    def __init__(self, gateway, context, plan):
        if type(gateway) is not FixtureGateway or type(plan) is not BrowserPlan:
            raise Refused("invalid_browser_fixture")
        self._g, self._ctx, self._plan = gateway, context, plan.checked()
        if gateway.context != context:
            raise Refused("stale_context")
        self._lock = Lock()
        self._closed = False
        self._index = 0
        self._active = None
        self._result = None
        self._claims = []

    def _check(self):
        if self._closed:
            raise Refused("browser_closed")
        if self._claims:
            self._g.checkpoint_claimed_group(tuple(self._claims))

    def navigate(self, url, grant):
        with self._lock:
            self._check()
            if self._index >= len(self._plan.pages) or url != self._plan.pages[self._index]:
                raise Refused("unexpected_navigation")
            request = self._plan.call("navigate",url).request(self._ctx)
            if self._claims:
                self._g.claim_with_dependencies(grant,request,tuple(self._claims))
            else:
                self._g.claim(grant,request)
            self._claims.append((grant,request))
            self._check()
            self._active = url
            self._index += 1
            self._result = None
            return "SIMULATED_NAVIGATION_ONLY"

    def snapshot(self, url, data, grant):
        with self._lock:
            self._check()
            if url != self._active or self._active is None or self._result is not None:
                raise Refused("snapshot_without_current_navigation")
            if type(data) is not bytes or not 0 < len(data) <= MAX_SNAPSHOT_BYTES:
                raise Refused("invalid_snapshot")
            try:
                text = data.decode("utf-8")
            except UnicodeError:
                raise Refused("invalid_snapshot") from None
            if any((ord(c)<32 and c not in "\n\t") or 127<=ord(c)<=159
                   or 0x202a<=ord(c)<=0x202e or 0x2066<=ord(c)<=0x2069 for c in text):
                raise Refused("invalid_snapshot")
            digest = sha256(data).hexdigest()
            request = self._plan.call("snapshot",url,digest).request(self._ctx)
            self._g.claim_with_dependencies(grant,request,tuple(self._claims))
            self._claims.append((grant,request))
            self._check()
            self._result = (url,digest,text)

    def result(self):
        with self._lock:
            self._check()
            if self._result is None:
                raise Refused("no_current_snapshot")
            return ("SIMULATED_ONLY",self._result)

    def automatic_request(self, kind, url):
        with self._lock:
            self._check()
            raise Refused("automatic_browser_traffic_denied")

    def close(self):
        with self._lock:
            self._closed = True
            self._active = None
            self._result = None
            for grant, _ in self._claims:
                self._g.revoke(grant.identity)
            self._claims.clear()

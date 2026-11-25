# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic research collection; no browser/network or model invocation."""
from dataclasses import dataclass
from threading import RLock
from gateway import Request, Refused
from research import canonical, citation, Source
from research_transport import fixture_response

@dataclass(frozen=True)
class Evidence:
    url: str
    digest: str
    start: int
    end: int
    quote: str

class ResearchSession:
    def __init__(self, gateway, context, root):
        self._g, self._ctx, self._root = gateway, context, canonical(root).url
        self._sources = {}
        self._bytes = 0
        self._closed = False
        self._lock = RLock()

    def collect(self, grant, url, resolved, peer, headers, body):
        with self._lock:
            return self._collect(grant,url,resolved,peer,headers,body)

    def _collect(self, grant, url, resolved, peer, headers, body):
        if self._closed:
            raise Refused("session_closed")
        target = canonical(url)
        if target.identity in self._sources or len(self._sources) >= 8:
            raise Refused("source_limit_or_duplicate")
        if type(body) is not bytes or self._bytes + len(body) > 262144:
            raise Refused("session_bytes_limit")
        source = fixture_response(self._g, grant, self._ctx, url, self._root,
                                  resolved, peer, headers, body)
        self._g.checkpoint(grant, Request(self._ctx, "research", target.identity, "local", "public"))
        if self._closed:
            raise Refused("session_closed")
        self._sources[target.identity] = (grant, source)
        self._bytes += len(body)
        return target.identity

    def cite(self, identity, digest, start, end):
        with self._lock:
            return self._cite(identity,digest,start,end)

    def _cite(self, identity, digest, start, end):
        if self._closed or type(identity) is not str or identity not in self._sources:
            raise Refused("unknown_source")
        grant, source = self._sources[identity]
        req = Request(self._ctx, "research", identity, "local", "public")
        self._g.checkpoint(grant, req)
        quote = citation(source, digest, start, end)
        self._g.checkpoint(grant, req)
        if self._closed:
            raise Refused("session_closed")
        return Evidence(source.target.url, digest, start, end, quote)

    def close(self):
        with self._lock:
            self._closed = True
            self._sources.clear()
            self._bytes = 0

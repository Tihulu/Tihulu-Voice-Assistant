# SPDX-License-Identifier: AGPL-3.0-only
"""Public search fixture: bind exact query bytes, provider and citations.
No DNS, HTTP, credentials or real search engine. No untrusted callbacks.
"""
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from gateway import FixtureGateway, Refused
from capabilities import CapabilityCall, public_fixture_url
from adapter_protocol import text_bytes

MAX_QUERY_BYTES = 512
MAX_RESULTS = 12
MAX_SNIPPET = 1000


def safe_display(value, limit):
    if type(value) is not str:
        raise Refused("invalid_search_text")
    data = text_bytes(value)
    if len(data) > limit or any(
            0x202a <= ord(c) <= 0x202e or 0x2066 <= ord(c) <= 0x2069
            for c in value):
        raise Refused("invalid_search_text")
    return data


@dataclass(frozen=True)
class SearchQuery:
    provider_version: str
    text: str

    def call(self):
        data = safe_display(self.text, MAX_QUERY_BYTES)
        if not self.text.strip():
            raise Refused("invalid_search_query")
        return CapabilityCall("search_provider", self.provider_version, "web_search",
                              (("query_digest", sha256(data).hexdigest()),)).checked()


@dataclass(frozen=True)
class Hit:
    url: str
    title: str
    snippet: str

    def checked(self):
        public_fixture_url(self.url)
        safe_display(self.title, 200)
        safe_display(self.snippet, MAX_SNIPPET)
        return self


@dataclass(frozen=True)
class SearchResult:
    request_target: str
    hits: tuple

    def checked(self, req):
        if type(self.request_target) is not str or self.request_target != req.target:
            raise Refused("search_query_mismatch")
        if type(self.hits) is not tuple or len(self.hits) > MAX_RESULTS:
            raise Refused("invalid_search_results")
        urls = set()
        for hit in self.hits:
            if type(hit) is not Hit:
                raise Refused("invalid_search_hit")
            hit.checked()
            if hit.url in urls:
                raise Refused("duplicate_search_url")
            urls.add(hit.url)
        return self


class SearchFixture:
    def __init__(self, gateway, grant, context, query):
        if type(gateway) is not FixtureGateway or type(query) is not SearchQuery:
            raise Refused("invalid_search_fixture")
        self._g, self._grant = gateway, grant
        self._req = query.call().request(context)
        gateway.claim(grant, self._req)
        self._state = "ready"
        self._result = None
        self._lock = Lock()

    def complete(self, result):
        with self._lock:
            if self._state != "ready":
                raise Refused("terminal_call")
            try:
                self._g.checkpoint(self._grant, self._req)
                if type(result) is not SearchResult:
                    raise Refused("invalid_search_result")
                result.checked(self._req)
                self._g.checkpoint(self._grant, self._req)
                self._result = result
                self._state = "completed"
            except Refused:
                self._result = None
                self._state = "held"
                raise

    def result(self):
        with self._lock:
            if self._state != "completed":
                raise Refused("no_completed_result")
            try:
                self._g.checkpoint(self._grant, self._req)
            except Refused:
                self._result = None
                self._state = "held"
                raise
            # A search hit is untrusted metadata, not verified page contents,
            # a navigation grant, or instructions for an agent.
            return ("SIMULATED_ONLY", self._result)

    def cancel(self):
        with self._lock:
            self._result = None
            self._state = "cancelled"
            self._g.revoke(self._grant.identity)

# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping typed capability experiment; no OS, browser or network I/O.

The existing FixtureGateway owns all grants. An immutable call binds exact
adapter, operation, target and arguments; only approved synthetic receipts
are returned. No executable paths, generic functions or script strings.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from types import MappingProxyType

from gateway import Context, Request, FixtureGateway, Refused, token

# Closed capability table: new operations require code review, not model input.
# Fields: gateway action, classification, input schema.
CAPABILITIES = MappingProxyType({
    ("tesseract", "ocr_image"): ("document_engine", "private", ("artifact", "digest", "language")),
    ("poppler", "pdf_extract"): ("document_engine", "private", ("artifact", "digest", "page")),
    ("poppler", "pdf_render"): ("document_engine", "private", ("artifact", "digest", "page")),
    ("pdf_engine", "pdf_export"): ("document_export", "private", ("artifact", "digest", "output")),
    ("search_provider", "web_search"): ("research", "public", ("query_digest",)),
    ("firefox", "browser_read"): ("browser_read", "public", ("origin", "tab")),
    ("brave", "browser_read"): ("browser_read", "public", ("origin", "tab")),
    ("libreoffice", "window_inspect"): ("application_ui", "private", ("window",)),
    ("gimp", "window_inspect"): ("application_ui", "private", ("window",)),
    ("ffmpeg", "audio_convert"): ("application_engine", "private", ("artifact", "digest", "output")),
})
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
# A fixture origin identity, not a URL or DNS proof. A real adapter must
# separately validate actual navigation, redirects and network isolation.
_ORIGIN = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,125}[a-z0-9])?\Z")


def checked_value(key, value):
    if key in ("digest", "query_digest"):
        if type(value) is not str or not _DIGEST.fullmatch(value):
            raise Refused("invalid_digest")
    elif key == "language":
        if value not in ("tr", "en", "de", "tr-en-de"):
            raise Refused("invalid_language")
    elif key == "page":
        if type(value) is not int or not 1 <= value <= 500:
            raise Refused("invalid_page")
    elif key == "origin":
        if (type(value) is not str or not _ORIGIN.fullmatch(value)
                or "." not in value or ".." in value
                or value.endswith((".local", ".internal", ".localhost"))):
            raise Refused("invalid_fixture_origin")
    else:
        token(value)
    return value


@dataclass(frozen=True)
class CapabilityCall:
    adapter: str
    version: str
    operation: str
    args: tuple

    def checked(self):
        token(self.adapter)
        token(self.version)
        token(self.operation)
        spec = CAPABILITIES.get((self.adapter, self.operation))
        if spec is None:
            raise Refused("unsupported_capability")
        if type(self.args) is not tuple or len(self.args) != len(spec[2]):
            raise Refused("invalid_arguments")
        for pair, key in zip(self.args, spec[2]):
            if type(pair) is not tuple or len(pair) != 2 or pair[0] != key:
                raise Refused("invalid_arguments")
            checked_value(key, pair[1])
        if "output" in spec[2] and dict(self.args)["output"] == dict(self.args)["artifact"]:
            raise Refused("source_overwrite_denied")
        return self

    def request(self, context, *, actor="local"):
        self.checked()
        if type(context) is not Context:
            raise Refused("invalid_context")
        action, classification, _ = CAPABILITIES[(self.adapter, self.operation)]
        encoded = json.dumps((self.adapter, self.version, self.operation, self.args),
                             ensure_ascii=True, separators=(",", ":")).encode("ascii")
        return Request(context, action, sha256(encoded).hexdigest(), actor, classification).checked()


def simulate(gateway, grant, context, call, *, actor="local"):
    """Single-use claim and checked publication of a synthetic receipt only."""
    if type(gateway) is not FixtureGateway or type(call) is not CapabilityCall:
        raise Refused("invalid_fixture")
    request = call.request(context, actor=actor)
    gateway.claim(grant, request)
    gateway.checkpoint(grant, request)
    return ("SIMULATED_ONLY", call.adapter, call.operation, request.target)

# SPDX-License-Identifier: AGPL-3.0-only
"""R2 fixture-only canonical public sources and immutable citation spans."""
from dataclasses import dataclass
import hashlib
import re
from urllib.parse import urlsplit
from gateway import Request, Refused

LABEL = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
SEGMENT = re.compile(r"[A-Za-z0-9_~.-]{1,80}\Z")
MAX_TEXT = 65536

def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

@dataclass(frozen=True)
class Target:
    host: str
    segments: tuple

    @property
    def url(self):
        return "https://" + self.host + "/" + "/".join(self.segments)

    @property
    def identity(self):
        return digest(self.url)

def canonical(raw):
    if not isinstance(raw, str) or not raw.isascii() or not 9 <= len(raw) <= 512:
        raise Refused("invalid_url")
    if any(ord(c) < 33 or ord(c) == 127 or c in "%\\?#@" for c in raw):
        raise Refused("invalid_url")
    try:
        p = urlsplit(raw)
    except ValueError:
        raise Refused("invalid_url") from None
    host = p.netloc.lower()
    labels = host.split(".")
    if (p.scheme != "https" or not raw.startswith("https://") or len(labels) < 2
            or len(host) > 253 or any(not LABEL.fullmatch(x) or x.endswith("-") or x.startswith("xn--") for x in labels)
            or labels[-1] in ("local", "localhost", "internal", "invalid", "test", "onion", "arpa")
            or labels[-1].isdigit()):
        raise Refused("invalid_host")
    if not p.path.startswith("/"):
        raise Refused("explicit_path_required")
    parts = tuple(p.path[1:].split("/")) if p.path != "/" else ()
    if any(x in (".", "..") or not SEGMENT.fullmatch(x) for x in parts):
        raise Refused("invalid_path")
    return Target(host, parts)

def scoped(target, root):
    return target.host == root.host and target.segments[:len(root.segments)] == root.segments

@dataclass(frozen=True)
class Source:
    target: Target
    title: str
    text: str
    digest: str

def snapshot(gateway, grant, context, raw_url, approved_root, title, text):
    target = canonical(raw_url)
    root = canonical(approved_root)
    if not scoped(target, root):
        raise Refused("outside_scope")
    req = Request(context, "research", target.identity, "local", "public")
    gateway.checkpoint(grant, req)
    if (not isinstance(title, str) or not 1 <= len(title) <= 200
            or not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT
            or any(ord(c) < 32 and c not in "\n\t" or 0x7f <= ord(c) <= 0x9f
                   or 0x202a <= ord(c) <= 0x202e or 0x2066 <= ord(c) <= 0x2069
                   or 0xd800 <= ord(c) <= 0xdfff for c in title + text)):
        raise Refused("invalid_source")
    gateway.checkpoint(grant, req)
    return Source(target, title, text, digest(text))

def citation(source, expected_digest, start, end):
    if (type(source) is not Source or source.digest != expected_digest
            or digest(source.text) != expected_digest
            or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(source.text) or end - start > 1000):
        raise Refused("invalid_evidence_span")
    return source.text[start:end]

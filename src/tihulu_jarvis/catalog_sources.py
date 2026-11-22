# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Approved catalog sources: metadata from a host the owner registered.

A source is registered by the owner — a host, a kind and the filters chosen for
it — and a search reads that source's public listing API over HTTPS, bounded,
without credentials, without following redirects and without a single byte of
model data. A card is built only from what the listing actually states: a
missing digest or size is a refused card, never a guess, because a card that
cannot be verified must not look like one that can.
"""
import json
import re
from dataclasses import dataclass, replace
from urllib.parse import quote, urlsplit

from .market import CATALOG_LIMIT, ModelCard
from .voice.m1 import Hold

MAX_SOURCES = 32
KINDS = ("hf", "http")
HOSTS = re.compile(r"[A-Za-z0-9.-]{1,253}")
# A filter is a search term: letters, digits, dot, dash, underscore and spaces
# (quoted when encoded). A character that would change the meaning of the URL is
# not a search term, so it is not accepted.
FILTER = re.compile(r"[A-Za-z0-9 ._-]{0,96}")

# A listing's task, mapped to the three categories this client installs. A
# listing this client cannot categorise yields no card, not a wrong one.
PIPELINE_CATEGORY = {
    "automatic-speech-recognition": "stt",
    "text-to-speech": "tts",
    "text-generation": "llm",
    "text2text-generation": "llm",
    "translation": "llm",
}


@dataclass(frozen=True)
class Source:
    host: str
    kind: str
    filter: str = ""

    def checked(self):
        if not isinstance(self.host, str) or not HOSTS.fullmatch(self.host) or ".." in self.host:
            raise Hold("invalid_local_settings")
        if "." not in self.host or self.host.lower().endswith((".localhost", ".local",
                                                              ".internal", ".home.arpa")):
            raise Hold("target_not_public")
        if self.kind not in KINDS:
            raise Hold("unknown_source_kind")
        if not isinstance(self.filter, str) or not FILTER.fullmatch(self.filter.strip()):
            raise Hold("invalid_local_settings")
        try:
            ip = __import__("ipaddress").ip_address(self.host)
        except ValueError:
            pass
        else:
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                raise Hold("target_not_public")
        return self

    @property
    def search_path(self):
        where = quote(self.filter.strip()) if self.filter.strip() else ""
        return "/api/models?search=%s&limit=20" % where


def checked(entry):
    """A stored source, or a bounded refusal naming what is wrong with it."""
    if not isinstance(entry, dict) or set(entry) - {"host", "kind", "filter"}:
        raise Hold("invalid_local_settings")
    return Source(entry.get("host", ""), entry.get("kind", ""),
                  entry.get("filter", "")).checked()


def entries(items):
    """Stored sources as objects; a missing optional field is its default."""
    if not isinstance(items, (list, tuple)):
        raise Hold("invalid_local_settings")
    for item in items:
        if not isinstance(item, dict):
            # A non-mapping entry is refused with the settings reason, not by
            # an attribute error from deep inside the mapping.
            raise Hold("invalid_local_settings")
    return tuple(Source(item.get("host", ""), item.get("kind", ""),
                        item.get("filter", "")).checked() for item in items)


def store(sources):
    return [{"host": source.host, "kind": source.kind, "filter": source.filter}
            for source in sources]


def register(known, entry):
    """Add or replace one source, bounded in count."""
    source = checked(entry)
    rest = [item for item in entries(known)
            if (item.host, item.filter) != (source.host, source.filter)]
    if len(rest) >= MAX_SOURCES:
        raise Hold("too_many_sources")
    return store(rest[:MAX_SOURCES - 1] + [source])


def forget(known, entry):
    """Remove one source; forgetting an absent one is not an error."""
    source = checked(entry)
    return store([item for item in entries(known)
                  if (item.host, item.filter) != (source.host, source.filter)])


def listing_url(source, path=None):
    """The listing URL for a source, as a full https address."""
    source = source.checked() if isinstance(source, Source) else checked(source)
    return "https://%s%s" % (source.host, path or source.search_path)


def slug(repo_id):
    """A registry id for a repository id, injectively escaped.

    The registry's ids are slugs (no slash), so a repository id is escaped by
    replacing the slash with a colon — a mapping that cannot collide, unlike a
    dash or a dropped character. The repository id itself stays on the card as
    its display name and inside the source reference.
    """
    if not isinstance(repo_id, str) or "/" not in repo_id:
        return repo_id if isinstance(repo_id, str) else ""
    head, _, tail = repo_id.partition("/")
    return (head + ":" + tail)[:128]


def cards(payload, source):
    """Truthful cards from a Hugging Face-shaped listing.

    A card is built only from fields the listing actually carries: id, name,
    the formats and languages in its tags, its license, its size and its digest.
    A row without a size or digest, or whose task this client cannot
    categorise, yields no card at all — the count of refused rows is returned
    with the cards so the interface can say so.
    """
    if not isinstance(payload, list):
        raise Hold("invalid_local_settings")
    source = source.checked() if isinstance(source, Source) else checked(source)
    out, refused = [], 0
    # A row beyond the bound is counted, so the interface can say the listing
    # was longer rather than implying it was complete.
    refused += max(0, len(payload) - 50)
    for item in payload[:50]:
        if not isinstance(item, dict):
            refused += 1
            continue
        repo_id = item.get("modelId") or item.get("id")
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        tags = [tag for tag in tags if isinstance(tag, str)]
        pipeline = str(item.get("pipeline_tag") or "").lower()
        category = PIPELINE_CATEGORY.get(pipeline)
        formats = [tag.lower() for tag in tags
                   if tag.lower() in ("ggml", "onnx", "safetensors", "bin")]
        languages = sorted({tag for tag in tags if re.fullmatch(r"[a-z]{2}", tag)})
        files = [file for file in (item.get("files") or []) if isinstance(file, dict)]
        size = sum(file.get("size") or 0 for file in files)
        digest = next((file["lfs"].get("sha256") or "" for file in files
                       if isinstance(file.get("lfs"), dict)), "")
        if not repo_id or not isinstance(repo_id, str) or not category \
                or not size or not isinstance(digest, str) \
                or not re.fullmatch(r"[0-9a-f]{64}", digest):
            # Unverifiable or uncategorisable is refused, not rounded up.
            refused += 1
            continue
        license_id = next((tag.split(":", 1)[1] for tag in tags
                           if tag.startswith("license:")), "unknown")
        card = ModelCard(
            model_id=slug(repo_id), display_name=repo_id[:256], category=category,
            provider=source.host, engine="unknown", version="unknown",
            artifact_format="ggml" if "ggml" in formats else (
                "onnx" if "onnx" in formats else "other"),
            languages=tuple(languages),
            license_id=license_id[:64], state="DISCOVERED",
            source_ref="https://%s/%s" % (source.host, repo_id),
            source_checksum=str(digest)[:128], source_size=int(size),
        ).checked()
        out.append(card)
    return tuple(out), refused


def safe_url(source, path=None):
    """The one URL a source may be read from, or a bounded refusal.

    A listing is fetched over HTTPS with the query the listing API defines —
    `search` and `limit` only. A fragment, credentials, a second query key or a
    non-ASCII address is refused, so the URL a source can produce is the URL
    this client built, never one the listing's own payload shaped.
    """
    url = listing_url(source, path)
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.fragment or parts.username or parts.password:
        raise Hold("invalid_source_reference")
    if not url.isascii() or any(ord(c) <= 32 for c in url):
        raise Hold("invalid_source_reference")
    keys = sorted(key for key, _ in (pair.split("=", 1) if "=" in pair else (pair, "")
                                     for pair in parts.query.split("&") if pair))
    if keys != ["limit", "search"]:
        raise Hold("invalid_source_reference")
    return url


async def fetch(source, opener, timeout=15.0):
    """Read a source's listing, bounded and without credentials.

    ``opener(url, timeout)`` returns the body as text; the default verifies TLS
    against the system trust store. A caller may inject one for tests; a
    production caller must not disable verification.
    """
    url = safe_url(source)
    if not callable(opener):
        raise Hold("transport_not_available")
    try:
        body = await opener(url, timeout)
    except Hold:
        raise
    except Exception:
        raise Hold("source_fetch_failed") from None
    if not isinstance(body, str) or len(body.encode("utf-8")) > CATALOG_LIMIT:
        raise Hold("source_too_large")
    try:
        json.loads(body)
    except ValueError:
        raise Hold("invalid_source_listing") from None
    return body

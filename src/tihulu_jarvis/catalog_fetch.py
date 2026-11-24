# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Bounded curated-catalog validation and injectable transport.

Production network transport is held until destination and redirect enforcement
are verified. Local import and approved synthetic adapter tests remain usable.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from .market import CATALOG_LIMIT
from .voice.m1 import Hold

MAX_BYTES = CATALOG_LIMIT
TIMEOUT = 15.0


def allowed(raw, allowlist):
    """Decide whether a catalog URL may be fetched at all."""
    if not isinstance(raw, str) or not isinstance(allowlist, (list, tuple, set)):
        raise Hold("invalid_local_settings")
    if not raw.startswith("https://"):
        raise Hold("catalog_source_not_https")
    try:
        parts = urlsplit(raw)
    except (ValueError, TypeError):
        raise Hold("invalid_local_settings") from None
    host = parts.hostname or ""
    if not host:
        raise Hold("invalid_local_settings")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass  # a hostname, checked against the allowlist below
    else:
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
            # A private address is not a catalog source; local files use the picker.
            raise Hold("catalog_source_not_public")
        host = str(ip)
    if host not in set(allowlist) and parts.hostname not in set(allowlist):
        raise Hold("catalog_host_not_allowed")
    if any(c in raw for c in "?#@\"'\\ ") or not raw.isascii() or any(ord(c) <= 32 for c in raw):
        raise Hold("invalid_local_settings")
    if (parts.path and (not re.fullmatch(r"/[A-Za-z0-9._~/-]{0,200}", parts.path)
                        or ".." in parts.path.split("/"))):
        # No traversal, no query, no credentials.
        raise Hold("invalid_local_settings")
    return host


def verify_body(text):
    """A catalog body is bounded and must parse as JSON before any use."""
    if not isinstance(text, str):
        raise Hold("invalid_catalog")
    if len(text.encode("utf-8")) > MAX_BYTES:
        # The bound is enforced before anything is parsed.
        raise Hold("catalog_too_large")
    try:
        import json
        data = json.loads(text)
    except ValueError:
        raise Hold("invalid_catalog") from None
    if not isinstance(data, dict):
        raise Hold("invalid_catalog")
    return text


async def fetch(raw, allowlist, opener, timeout=TIMEOUT):
    """Fetch an allowed catalog. ``opener(url, timeout)`` returns response text.

    The stock opener verifies TLS against the system trust store. A caller
    may inject one for tests; a production caller must not disable it.
    """
    host = allowed(raw, allowlist)
    if not callable(opener):
        raise Hold("transport_not_available")
    try:
        body = await opener(raw, timeout)
    except Hold:
        raise
    except Exception:
        raise Hold("catalog_fetch_failed") from None
    return verify_body(body)


def default_opener():
    """Return an opener that verifies certificates against the system store."""
    def open_url(url, timeout=TIMEOUT):
        raise Hold("transport_not_available")
    return open_url


def urlopen_opener():
    """Fail closed before network I/O until an audited transport is available.

    The historical urllib opener followed redirects before checking Location
    and performed blocking I/O before an await of its non-awaitable result.
    Do not enable a new public network adapter in the R1 remediation.
    """
    async def held(url, timeout=TIMEOUT):
        raise Hold("catalog_transport_not_verified")
    return held

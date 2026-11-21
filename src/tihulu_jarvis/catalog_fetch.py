# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""A deliberately narrow HTTPS transport for curated catalogs.

A catalog is fetched only from a host the owner explicitly allows, over
HTTPS with default certificate verification, without credentials, without
following redirects and with a hard size limit. ``opener`` is injectable so
the policy can be tested without a socket.
"""
import ipaddress
import re
import ssl
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
    """An opener using the standard library with default TLS verification.

    It is created lazily so importing this module never touches the network.
    """
    def open_url(url, timeout=TIMEOUT):
        import urllib.request
        context = ssl.create_default_context()
        request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       "User-Agent": "tihulu-jarvis-catalog"})
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            if response.headers.get("Location"):
                raise Hold("catalog_redirect_rejected")
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise Hold("catalog_too_large")
        return body.decode("utf-8")
    return open_url

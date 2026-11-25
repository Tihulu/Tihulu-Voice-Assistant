# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic response validation. No socket, DNS resolver or browser."""
import ipaddress
from gateway import Request, Refused
from research import canonical, scoped, snapshot

MAX_BODY = 65536
MAX_HEADERS = 32
MAX_HEADER_BYTES = 8192
# Explicitly deny transition, mapped and translation prefixes, plus non-global.
BLOCK6 = tuple(ipaddress.ip_network(x) for x in (
    "::/96", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48",
    "2001::/32", "2002::/16", "100::/64"))
BLOCK4 = tuple(ipaddress.ip_network(x) for x in (
    "0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24",
    "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "240.0.0.0/4"))

def addresses(values):
    if type(values) not in (tuple, list) or not 1 <= len(values) <= 16:
        raise Refused("invalid_address_set")
    out = []
    for raw in values:
        if not isinstance(raw, str) or "%" in raw or len(raw) > 45:
            raise Refused("invalid_address")
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise Refused("invalid_address") from None
        if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified
                or ip.is_loopback or ip.is_link_local or ip.is_private
                or any(ip in n for n in (BLOCK6 if ip.version == 6 else BLOCK4))):
            raise Refused("nonpublic_address")
        out.append(str(ip))
    return tuple(dict.fromkeys(out))

def fixture_response(gateway, grant, context, url, root, resolved, peer, headers, body):
    target = canonical(url)
    if not scoped(target, canonical(root)):
        raise Refused("outside_scope")
    req = Request(context, "research", target.identity, "local", "public")
    gateway.checkpoint(grant, req)
    allowed = addresses(resolved)
    if addresses((peer,))[0] not in allowed:
        raise Refused("peer_mismatch")
    if type(headers) not in (tuple, list) or len(headers) > MAX_HEADERS:
        raise Refused("invalid_headers")
    table = {}
    size = 0
    for pair in headers:
        if type(pair) is not tuple or len(pair) != 2:
            raise Refused("invalid_headers")
        name, value = pair
        if (not isinstance(name, str) or not isinstance(value, str)
                or not name.isascii() or not name.replace("-", "").isalnum()
                or not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise Refused("invalid_headers")
        size += len(name) + len(value)
        key = name.lower()
        if key in table or size > MAX_HEADER_BYTES:
            raise Refused("invalid_headers")
        table[key] = value
    if ("transfer-encoding" in table or "location" in table
            or table.get("content-encoding", "identity") != "identity"
            or table.get("content-type") != "text/plain; charset=utf-8"):
        raise Refused("unsupported_response")
    length = table.get("content-length", "")
    if (not length.isascii() or not length.isdecimal() or len(length) > 6
            or type(body) is not bytes or not 0 < len(body) <= MAX_BODY or int(length) != len(body)):
        raise Refused("invalid_body_length")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeError:
        raise Refused("invalid_utf8") from None
    return snapshot(gateway, grant, context, url, root, "Synthetic public source", text)

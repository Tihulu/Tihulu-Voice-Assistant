# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""A deliberately narrow download transport for approved catalog sources.

It exists so the market's download lifecycle can run end to end without
promising a general network capability. It connects only to an explicitly
approved, numeric loopback HTTP endpoint on this machine. It uses no
environment proxy, follows no redirect, sends no credentials, and streams
into the caller's isolated file with a hard size limit.

Anything beyond this posture is a separate, authorized gate.
"""
import asyncio
import re
import socket

from .voice.m1 import Hold

MAX_BODY = 64 * 1024 * 1024


def _url(raw):
    """Accept only an explicit numeric loopback http endpoint with a plain path."""
    if not isinstance(raw, str) or not raw.startswith("http://"):
        raise Hold("transport_endpoint_not_approved")
    if ("?" in raw or "#" in raw or "@" in raw or "\\" in raw or " " in raw
            or not raw.isascii() or any(ord(c) <= 32 or ord(c) >= 127 for c in raw)):
        raise Hold("transport_endpoint_not_approved")
    rest = raw[len("http://"):]
    if "/" not in rest:
        raise Hold("transport_endpoint_not_approved")
    authority, _, path = rest.partition("/")
    try:
        import ipaddress
        from urllib.parse import urlsplit
        parts = urlsplit(raw)
        ip = ipaddress.ip_address(parts.hostname or "")
        port = parts.port
    except (ValueError, TypeError):
        raise Hold("transport_endpoint_not_approved") from None
    segments = parts.path.split("/")[1:]
    if (not ip.is_loopback or "%" in str(ip) or not port or parts.username is not None
            or parts.password is not None or not parts.path.startswith("/")
            or not re.fullmatch(r"/[A-Za-z0-9._~/-]{0,200}", parts.path)
            or any(seg in (".", "..") for seg in segments)):
        # No traversal, no query, no credentials, no non-loopback host.
        raise Hold("transport_endpoint_not_approved")
    return ip, port, parts.path


def loopback_fetch(raw, limit=MAX_BODY):
    """Build an async `fetch(path, note, start=0)` callable for one source.

    A transfer can continue from an offset: the request carries a Range header
    and a 206 answer must confirm exactly that offset. A source that answers
    200 instead restarts from zero, which is always verified afterwards.
    """
    ip, port, path = _url(raw)

    async def fetch(path_name, note, start=0):
        if type(start) is not int or not 0 <= start < limit:
            raise Hold("transfer_resume_offset_unverified")
        family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
        try:
            reader, writer = await asyncio.open_connection(str(ip), port, family=family, limit=16384)
        except OSError:
            raise Hold("transfer_unavailable") from None
        written = 0
        mode, offset = "wb", 0
        try:
            head = ("GET %s HTTP/1.1\r\nHost: %s:%s\r\n"
                    "Connection: close\r\nAccept: application/octet-stream\r\n"
                    % (path, ip, port))
            if start:
                head += "Range: bytes=%d-\r\n" % start
            writer.write((head + "\r\n").encode("ascii"))
            await writer.drain()
            line = await reader.readline()
            if not line.endswith(b"\r\n"):
                raise Hold("transfer_invalid_response")
            status = line[:-2]
            match = re.fullmatch(rb"HTTP/1\.[01] (200|206)(?: [^\r\n]*)?", status)
            if not match:
                # A redirect or an error from the source is never followed.
                raise Hold("transfer_rejected_by_source")
            partial = match.group(1) == b"206"
            if partial and not start:
                raise Hold("transfer_range_not_honoured")
            headers, size = {}, 0
            while True:
                field = await reader.readline()
                if not field.endswith(b"\r\n") or len(field) > 4096:
                    raise Hold("transfer_invalid_response")
                if field == b"\r\n":
                    break
                size += len(field) + 2
                if size > 16384:
                    raise Hold("transfer_invalid_response")
                key, sep, value = field[:-2].partition(b":")
                key = key.lower()
                if not sep or not re.fullmatch(rb"[a-z0-9!#$%&'*+.^_`|~-]+", key) or key in headers:
                    raise Hold("transfer_invalid_response")
                headers[key] = value.strip()
            length = headers.get(b"content-length")
            if headers.get(b"transfer-encoding") is not None or headers.get(b"location") is not None:
                raise Hold("transfer_redirect_or_stream_rejected")
            if length is not None and not re.fullmatch(rb"[0-9]{1,10}", length):
                raise Hold("transfer_invalid_response")
            ranged = headers.get(b"content-range")
            if partial:
                # The open-ended range must cover exactly the remaining bytes.
                bounds = re.fullmatch(rb"bytes ([0-9]{1,10})-([0-9]{1,10})/([0-9]{1,10})",
                                      ranged or b"")
                if not bounds:
                    raise Hold("transfer_range_not_honoured")
                first, last, total = map(int, bounds.groups())
                if total > limit:
                    raise Hold("transfer_too_large")
                if first != start or not first <= last == total - 1:
                    raise Hold("transfer_range_not_honoured")
                expected = last - first + 1
                if length is not None and int(length) != expected:
                    raise Hold("transfer_invalid_response")
                length = str(expected).encode("ascii")
                mode, offset = "ab", start
            elif ranged is not None:
                raise Hold("transfer_invalid_response")
            elif start:
                mode, offset = "wb", 0  # no Range support: start over, verify later
            if length is not None and int(length) > limit:
                raise Hold("transfer_too_large")
            if headers.get(b"content-type") is not None and b"application/octet-stream" not in headers[b"content-type"]:
                raise Hold("transfer_unexpected_content_type")
            # The caller owns a verified isolated prefix. Never append at a
            # different offset, even when called without the market wrapper.
            if offset:
                import os
                if os.stat(path_name, follow_symlinks=False).st_size != offset:
                    raise Hold("transfer_resume_offset_unverified")
            with open(path_name, mode) as out:
                if length is None:
                    while written <= limit:
                        block = await reader.read(min(65536, limit + 1 - written))
                        if not block:
                            break
                        out.write(block)
                        written += len(block)
                        note(written + offset)
                    if written > limit:
                        raise Hold("transfer_too_large")
                else:
                    expected = int(length)
                    while written < expected:
                        block = await reader.read(min(65536, expected - written))
                        if not block:
                            raise Hold("transfer_truncated")
                        out.write(block)
                        written += len(block)
                        note(written + offset)
                    if written > limit:
                        raise Hold("transfer_too_large")
            if written <= 0 and not offset:
                raise Hold("transfer_empty_body")
            return written + offset
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    return fetch

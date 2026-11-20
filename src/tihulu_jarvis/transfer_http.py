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
            if not re.fullmatch(rb"HTTP/1\.[01] (?:200|206)(?: [^\r\n]*)?", status):
                # A redirect or an error from the source is never followed.
                raise Hold("transfer_rejected_by_source")
            if status.endswith(b" 206 Partial Content") and not start:
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
                headers[key.lower().strip()] = value.strip()
            length = headers.get(b"content-length")
            if headers.get(b"transfer-encoding") is not None or headers.get(b"location") is not None:
                raise Hold("transfer_redirect_or_stream_rejected")
            if length is not None and not re.fullmatch(rb"[0-9]{1,10}", length):
                raise Hold("transfer_invalid_response")
            ranged = headers.get(b"content-range")
            if status.endswith(b" 206 Partial Content"):
                # The source must confirm exactly the offset we asked for.
                if ranged is None or not re.fullmatch(rb"bytes %d-[0-9]+/[0-9]+" % start, ranged.strip()):
                    raise Hold("transfer_range_not_honoured")
                mode, offset = "ab", start
                length = None  # the range length is described by Content-Range
                total = int(ranged.strip().rsplit(b"/", 1)[1])
                if total > limit:
                    raise Hold("transfer_too_large")
            elif start and status.endswith(b" 200 OK"):
                mode, offset = "wb", 0  # no Range support: start over, verify later
            if length is not None and int(length) > limit:
                raise Hold("transfer_too_large")
            if headers.get(b"content-type") is not None and b"application/octet-stream" not in headers[b"content-type"]:
                raise Hold("transfer_unexpected_content_type")
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

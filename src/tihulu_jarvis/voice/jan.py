# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Asynchronous Jan discovery and chat on an explicitly chosen numeric loopback."""
import asyncio
import ipaddress
import json
import math
import re
import socket
from urllib.parse import urlsplit

from .m1 import Hold

LIMIT = 524288


def endpoint(raw):
    try:
        u = urlsplit(raw)
        ip = ipaddress.ip_address(u.hostname or "")
        port = u.port
        if (not ip.is_loopback or "%" in str(ip) or u.scheme != "http" or not port
                or u.username is not None or u.password is not None
                or u.path not in ("", "/", "/v1", "/v1/") or any(c in raw for c in "?#\\")
                or not raw.isascii() or any(ord(c) <= 32 or ord(c) >= 127 for c in raw)):
            raise ValueError
        return ip, port
    except (ValueError, TypeError, AttributeError):
        raise Hold("invalid_loopback_endpoint") from None


async def line(r, cap):
    val = await r.readline()
    if len(val) > cap or not val.endswith(b"\r\n"):
        raise Hold("jan_invalid_http")
    return val[:-2]


async def body(r):
    status = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?", await line(r, 1024))
    if not status:
        raise Hold("jan_invalid_http")
    if status[1] != b"200":
        raise Hold("jan_http_" + status[1].decode("ascii"))
    headers, size = {}, 0
    while True:
        field = await line(r, 4096)
        if not field:
            break
        size += len(field) + 2
        k, colon, v = field.partition(b":")
        k = k.lower()
        if size > 16384 or not colon or not re.fullmatch(rb"[a-z0-9-]+", k):
            raise Hold("jan_invalid_http")
        if k in (b"content-length", b"transfer-encoding") and k in headers:
            raise Hold("jan_invalid_http")
        headers[k] = v.strip()
    length, enc = headers.get(b"content-length"), headers.get(b"transfer-encoding")
    if enc is not None:
        if enc.lower() != b"chunked" or length is not None:
            raise Hold("jan_invalid_http")
        out = bytearray()
        for _ in range(8192):
            field = (await line(r, 128)).split(b";", 1)[0]
            if not re.fullmatch(rb"[0-9a-fA-F]{1,8}", field):
                break
            n = int(field, 16)
            if not n:
                # Discard trailers by closing this single-use connection.
                return bytes(out)
            if n > LIMIT - len(out):
                raise Hold("jan_response_too_large")
            out.extend(await r.readexactly(n))
            if await r.readexactly(2) != b"\r\n":
                break
        raise Hold("jan_invalid_http")
    if length is not None:
        if not re.fullmatch(rb"[0-9]{1,10}", length):
            raise Hold("jan_invalid_http")
        n = int(length)
        if n > LIMIT:
            raise Hold("jan_response_too_large")
        return await r.readexactly(n)
    out = bytearray()
    while len(out) <= LIMIT:
        part = await r.read(min(16384, LIMIT + 1 - len(out)))
        if not part:
            return bytes(out)
        out.extend(part)
    raise Hold("jan_response_too_large")


def model_id(v):
    return isinstance(v, str) and 0 < len(v) <= 256 and v.isprintable()


class Jan:
    def __init__(self, url, model="", timeout=60.0):
        self.ip, self.port = endpoint(url)
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise Hold("invalid_timeout")
        self.model, self.timeout = model, timeout
        self._slots = asyncio.Semaphore(2)

    async def _exchange(self, operation, data):
        async with self._slots:
            ip, port = self.ip, self.port
            r, w = await asyncio.open_connection(str(ip), port, family=socket.AF_INET if ip.version == 4 else socket.AF_INET6, limit=16384)
            try:
                host = str(ip) if ip.version == 4 else "[" + str(ip) + "]"
                method = "GET" if data is None else "POST"
                payload = data or b""
                hdr = (f"{method} /v1/{operation} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                       "Connection: close\r\nAccept: application/json\r\nContent-Type: application/json\r\n"
                       f"Content-Length: {len(payload)}\r\n\r\n")
                w.write(hdr.encode("ascii") + payload)
                await w.drain()
                return json.loads((await body(r)).decode("utf-8"))
            finally:
                w.close()

    async def _request(self, op, data=None):
        payload = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        if payload is not None and len(payload) > LIMIT:
            raise Hold("jan_request_too_large")
        try:
            return await asyncio.wait_for(self._exchange(op, payload), self.timeout)
        except asyncio.TimeoutError:
            raise Hold("jan_timeout") from None
        except OSError:
            raise Hold("jan_unavailable") from None
        except (ValueError, EOFError, UnicodeError, RecursionError):
            raise Hold("jan_invalid_response") from None

    async def models(self):
        obj = await self._request("models")
        items = obj.get("data") if isinstance(obj, dict) else None
        if not isinstance(items, list):
            raise Hold("jan_invalid_models")
        return tuple(dict.fromkeys(x["id"] for x in items if isinstance(x, dict) and model_id(x.get("id"))))

    async def __call__(self, messages):
        if not model_id(self.model):
            raise Hold("select_installed_model")
        if not messages or any(m.role not in ("system", "assistant", "user") or not isinstance(m.content, str) for m in messages):
            raise Hold("invalid_messages")
        obj = await self._request("chat/completions", {"model": self.model, "stream": False,
                                  "messages": [{"role": m.role, "content": m.content} for m in messages]})
        try:
            msg = obj["choices"][0]["message"]
            text = msg["content"]
            if msg.get("role") != "assistant" or not isinstance(text, str) or not text.strip() or len(text) > 32000:
                raise ValueError
            return text
        except (KeyError, IndexError, TypeError, ValueError):
            raise Hold("jan_invalid_response") from None

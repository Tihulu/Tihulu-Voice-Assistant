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
# A stream is bounded by events and characters, so a runaway server cannot
# hold a turn open or grow a reply without end.
MAX_EVENTS = 4096
MAX_TEXT = 32000


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
    """One bounded HTTP line, or a bounded reason for a line too long to read.

    A server that sends a line longer than the transport's buffer gets
    ``jan_invalid_http``, never a crash in the event loop.
    """
    try:
        val = await r.readline()
    except (asyncio.LimitOverrunError, ValueError):
        raise Hold("jan_invalid_http") from None
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


async def _events(r, on_delta):
    """Yield the text of a server-sent chat stream, bounded and validated.

    Only ``data:`` fields are read; comments and other fields are ignored. A
    stream ends on ``[DONE]`` or when the connection closes. Nothing is
    rendered that the transport did not actually send.
    """
    parts = []
    for _ in range(MAX_EVENTS + 1):
        field = await line(r, 4096)
        if not field:
            continue
        if not field.startswith(b"data:"):
            continue
        data = field[5:].strip()
        if data == b"[DONE]":
            return "".join(parts)
        try:
            obj = json.loads(data.decode("utf-8"))
            piece = obj["choices"][0]["delta"].get("content")
        except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
            raise Hold("jan_invalid_response") from None
        if not isinstance(piece, str) or not piece:
            continue
        text = "".join(parts) + piece
        if len(text) > MAX_TEXT:
            raise Hold("jan_response_too_large")
        parts.append(piece)
        if on_delta is not None:
            on_delta(piece)
    raise Hold("jan_stream_unbounded")


class Jan:
    def __init__(self, url, model="", timeout=60.0):
        self.ip, self.port = endpoint(url)
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise Hold("invalid_timeout")
        self.model, self.timeout = model, timeout
        self._slots = asyncio.Semaphore(2)

    async def _connect(self):
        ip, port = self.ip, self.port
        return await asyncio.open_connection(str(ip), port,
                                             family=socket.AF_INET if ip.version == 4 else socket.AF_INET6,
                                             limit=16384)

    async def _exchange(self, operation, data):
        async with self._slots:
            r, w = await self._connect()
            try:
                host = str(self.ip) if self.ip.version == 4 else "[" + str(self.ip) + "]"
                method = "GET" if data is None else "POST"
                payload = data or b""
                hdr = (f"{method} /v1/{operation} HTTP/1.1\r\nHost: {host}:{self.port}\r\n"
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

    async def chat(self, messages, on_delta=None):
        """The chat completion, with an optional progressive callback.

        ``on_delta`` receives the pieces the server actually sent, in order.
        The pieces are also returned joined, so a caller that wants the whole
        answer still gets one validated string.
        """
        if not model_id(self.model):
            raise Hold("select_installed_model")
        if not messages or any(m.role not in ("system", "assistant", "user") or not isinstance(m.content, str) for m in messages):
            raise Hold("invalid_messages")
        body = {"model": self.model, "stream": on_delta is not None,
                "messages": [{"role": m.role, "content": m.content} for m in messages]}
        if on_delta is None:
            obj = await self._request("chat/completions", body)
            try:
                msg = obj["choices"][0]["message"]
                text = msg["content"]
                if msg.get("role") != "assistant" or not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
                    raise ValueError
                return text
            except (KeyError, IndexError, TypeError, ValueError):
                raise Hold("jan_invalid_response") from None
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(payload) > LIMIT:
            raise Hold("jan_request_too_large")
        try:
            return await asyncio.wait_for(self._stream(payload, on_delta), self.timeout)
        except asyncio.TimeoutError:
            raise Hold("jan_timeout") from None
        except OSError:
            raise Hold("jan_unavailable") from None

    async def _stream(self, payload, on_delta):
        async with self._slots:
            r, w = await self._connect()
            try:
                host = str(self.ip) if self.ip.version == 4 else "[" + str(self.ip) + "]"
                hdr = (f"POST /v1/chat/completions HTTP/1.1\r\nHost: {host}:{self.port}\r\n"
                       "Connection: close\r\nAccept: text/event-stream\r\n"
                       "Content-Type: application/json\r\n"
                       f"Content-Length: {len(payload)}\r\n\r\n")
                w.write(hdr.encode("ascii") + payload)
                await w.drain()
                text = await self._stream_body(r, on_delta)
                # The joined text is validated exactly like a whole answer.
                if not text.strip() or len(text) > MAX_TEXT:
                    raise Hold("jan_invalid_response")
                return text
            finally:
                w.close()

    async def _stream_body(self, r, on_delta):
        status = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?", await line(r, 1024))
        if not status:
            raise Hold("jan_invalid_http")
        if status[1] != b"200":
            raise Hold("jan_http_" + status[1].decode("ascii"))
        while await line(r, 4096):
            # Headers are read and discarded; a stream carries no length.
            pass
        return await _events(r, on_delta)

    async def models(self):
        obj = await self._request("models")
        items = obj.get("data") if isinstance(obj, dict) else None
        if not isinstance(items, list):
            raise Hold("jan_invalid_models")
        return tuple(dict.fromkeys(x["id"] for x in items if isinstance(x, dict) and model_id(x.get("id"))))

    async def __call__(self, messages):
        return await self.chat(messages)

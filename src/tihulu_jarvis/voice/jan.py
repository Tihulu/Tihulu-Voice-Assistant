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
from ..owned_wait import await_authorized

LIMIT = 524288
# A stream is bounded by events and characters, so a runaway server cannot
# hold a turn open or grow a reply without end.
MAX_EVENTS = 4096
MAX_TEXT = 32000
AUTH_POLL = .25


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


async def response_head(r):
    """One HTTP parser for both reply modes; bounded framing and header bytes."""
    status = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?", await line(r, 1024))
    if not status:
        raise Hold("jan_invalid_http")
    if status[1] != b"200":
        raise Hold("jan_http_" + status[1].decode("ascii"))
    headers, size, count = {}, 0, 0
    while True:
        field = await line(r, 4096)
        if not field:
            break
        size += len(field) + 2
        count += 1
        k, colon, v = field.partition(b":")
        k = k.lower()
        if size > 16384 or count > 128 or not colon or not re.fullmatch(rb"[a-z0-9-]+", k):
            raise Hold("jan_invalid_http")
        if k in (b"content-length", b"transfer-encoding", b"content-encoding") and k in headers:
            raise Hold("jan_invalid_http")
        headers[k] = v.strip()
    length, enc = headers.get(b"content-length"), headers.get(b"transfer-encoding")
    if headers.get(b"content-encoding", b"identity").lower() != b"identity":
        raise Hold("jan_invalid_http")
    if enc is not None and (enc.lower() != b"chunked" or length is not None):
        raise Hold("jan_invalid_http")
    if length is not None:
        if not re.fullmatch(rb"[0-9]{1,10}", length):
            raise Hold("jan_invalid_http")
        length = int(length)
        if length > LIMIT:
            raise Hold("jan_response_too_large")
    return length, enc is not None


async def body_parts(r, length, chunked):
    """Decoded bounded body fragments, never raw chunk framing."""
    if chunked:
        total = 0
        for _ in range(8192):
            field = (await line(r, 128)).split(b";", 1)[0]
            if not re.fullmatch(rb"[0-9a-fA-F]{1,8}", field):
                raise Hold("jan_invalid_http")
            n = int(field, 16)
            if not n:
                # Bound trailers too, without using them as response metadata.
                size = 0
                for _ in range(129):
                    trailer = await line(r, 4096)
                    if not trailer:
                        return
                    size += len(trailer) + 2
                    if size > 16384 or not re.fullmatch(rb"[a-zA-Z0-9-]+:.*", trailer):
                        raise Hold("jan_invalid_http")
                raise Hold("jan_invalid_http")
            if n > LIMIT - total:
                raise Hold("jan_response_too_large")
            chunk = await r.readexactly(n)
            if await r.readexactly(2) != b"\r\n":
                raise Hold("jan_invalid_http")
            total += n
            yield chunk
        raise Hold("jan_invalid_http")
    total = 0
    while length is None or total < length:
        cap = min(16384, LIMIT + 1 - total)
        if length is not None:
            cap = min(cap, length - total)
        part = await r.read(cap)
        if not part:
            if length is not None and total != length:
                raise Hold("jan_invalid_http")
            return
        total += len(part)
        if total > LIMIT:
            raise Hold("jan_response_too_large")
        yield part


async def body(r):
    length, chunked = await response_head(r)
    return b"".join([part async for part in body_parts(r, length, chunked)])


def safe_text(value):
    # UTF-8/GTK cannot represent lone surrogates or embedded NUL as text.
    if not isinstance(value, str) or "\x00" in value:
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return True


def model_id(v):
    return isinstance(v, str) and 0 < len(v) <= 256 and v.isprintable()


async def event_lines(parts):
    """Assemble bounded SSE lines across arbitrary HTTP fragment boundaries."""
    pending = bytearray()
    async for part in parts:
        pending.extend(part)
        while True:
            end = pending.find(b"\n")
            if end < 0:
                if len(pending) > 4096:
                    raise Hold("jan_invalid_http")
                break
            if end > 4096:
                raise Hold("jan_invalid_http")
            field = bytes(pending[:end])
            del pending[:end + 1]
            yield field.removesuffix(b"\r")
    if pending:
        raise Hold("jan_invalid_http")


async def _events(lines, on_delta, *, framed=False):
    """Parse one JSON event per data line, requiring an explicit DONE marker.

    For a known HTTP frame consume the remainder through its valid end.
    Close-delimited streams can close the single-use socket at DONE.
    """
    parts, total, count, done = [], 0, 0, False
    async for field in lines:
        count += 1
        if count > MAX_EVENTS:
            raise Hold("jan_stream_unbounded")
        if not field or field.startswith(b":"):
            continue
        if done:
            raise Hold("jan_invalid_response")
        if not field.startswith(b"data:"):
            continue
        data = field[5:].strip()
        if data == b"[DONE]":
            done = True
            if not framed:
                return "".join(parts)
            continue
        try:
            obj = json.loads(data.decode("utf-8"))
            delta = obj["choices"][0]["delta"]
            if not isinstance(delta, dict):
                raise TypeError
            piece = delta.get("content")
            if piece is not None and not safe_text(piece):
                raise TypeError
        except (ValueError, KeyError, IndexError, TypeError, UnicodeError, RecursionError):
            raise Hold("jan_invalid_response") from None
        if not piece:
            continue
        total += len(piece)
        if total > MAX_TEXT:
            raise Hold("jan_response_too_large")
        parts.append(piece)
        if on_delta is not None:
            on_delta(piece)
    if not done:
        raise Hold("jan_invalid_response")
    return "".join(parts)


class Jan:
    def __init__(self, url, model="", timeout=60.0, *, checkpoint=lambda: None):
        self.ip, self.port = endpoint(url)
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise Hold("invalid_timeout")
        self.model, self.timeout = model, timeout
        if not callable(checkpoint):
            raise Hold("invalid_authorization_checkpoint")
        self._checkpoint = checkpoint
        self._slots = asyncio.Semaphore(2)

    async def _connect(self):
        ip, port = self.ip, self.port
        return await asyncio.open_connection(str(ip), port,
                                             family=socket.AF_INET if ip.version == 4 else socket.AF_INET6,
                                             limit=16384)

    async def _exchange(self, operation, data):
        self._checkpoint()
        async with self._slots:
            self._checkpoint()
            r, w = await self._connect()
            try:
                self._checkpoint()
                host = str(self.ip) if self.ip.version == 4 else "[" + str(self.ip) + "]"
                method = "GET" if data is None else "POST"
                payload = data or b""
                hdr = (f"{method} /v1/{operation} HTTP/1.1\r\nHost: {host}:{self.port}\r\n"
                       "Connection: close\r\nAccept: application/json\r\nContent-Type: application/json\r\n"
                       f"Content-Length: {len(payload)}\r\n\r\n")
                self._checkpoint()
                w.write(hdr.encode("ascii") + payload)
                await w.drain()
                self._checkpoint()
                raw = await body(r)
                self._checkpoint()
                return json.loads(raw.decode("utf-8"))
            finally:
                w.close()

    async def _request(self, op, data=None):
        self._checkpoint()
        payload = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        if payload is not None and len(payload) > LIMIT:
            raise Hold("jan_request_too_large")
        try:
            return await await_authorized(lambda: self._exchange(op, payload),
                                          self.timeout, self._checkpoint, interval=AUTH_POLL)
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
        self._checkpoint()
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
                if msg.get("role") != "assistant" or not safe_text(text) or not text.strip() or len(text) > MAX_TEXT:
                    raise ValueError
                return text
            except (KeyError, IndexError, TypeError, ValueError):
                raise Hold("jan_invalid_response") from None
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(payload) > LIMIT:
            raise Hold("jan_request_too_large")
        try:
            return await await_authorized(lambda: self._stream(payload, on_delta),
                                          self.timeout, self._checkpoint, interval=AUTH_POLL)
        except asyncio.TimeoutError:
            raise Hold("jan_timeout") from None
        except OSError:
            raise Hold("jan_unavailable") from None
        except (ValueError, EOFError, UnicodeError, RecursionError):
            raise Hold("jan_invalid_response") from None

    async def _stream(self, payload, on_delta):
        self._checkpoint()
        async with self._slots:
            self._checkpoint()
            r, w = await self._connect()
            try:
                self._checkpoint()
                host = str(self.ip) if self.ip.version == 4 else "[" + str(self.ip) + "]"
                hdr = (f"POST /v1/chat/completions HTTP/1.1\r\nHost: {host}:{self.port}\r\n"
                       "Connection: close\r\nAccept: text/event-stream\r\n"
                       "Content-Type: application/json\r\n"
                       f"Content-Length: {len(payload)}\r\n\r\n")
                self._checkpoint()
                w.write(hdr.encode("ascii") + payload)
                await w.drain()
                self._checkpoint()
                def guarded_delta(piece):
                    self._checkpoint()
                    on_delta(piece)
                text = await self._stream_body(r, guarded_delta)
                self._checkpoint()
                # The joined text is validated exactly like a whole answer.
                if not text.strip() or len(text) > MAX_TEXT:
                    raise Hold("jan_invalid_response")
                return text
            finally:
                w.close()

    async def _stream_body(self, r, on_delta):
        length, chunked = await response_head(r)
        parts = body_parts(r, length, chunked)
        lines = event_lines(parts)
        try:
            return await _events(lines, on_delta, framed=length is not None or chunked)
        finally:
            await lines.aclose()
            await parts.aclose()

    async def models(self):
        obj = await self._request("models")
        items = obj.get("data") if isinstance(obj, dict) else None
        if not isinstance(items, list):
            raise Hold("jan_invalid_models")
        return tuple(dict.fromkeys(x["id"] for x in items if isinstance(x, dict) and model_id(x.get("id"))))

    async def __call__(self, messages):
        return await self.chat(messages)

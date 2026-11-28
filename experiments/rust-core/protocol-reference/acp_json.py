# SPDX-License-Identifier: AGPL-3.0-only
"""Restricted ACP v1 JSON-RPC reference, not a shipping decoder or dispatcher."""
import json
import re
from dataclasses import dataclass

MAX_LINE = 65536
MAX_DEPTH = 24
MAX_NODES = 4096
MAX_TEXT = 16384
MAX_OPTIONS = 16
MAX_LABEL = 1024
OPTION_KINDS = {"allow_once", "allow_always", "reject_once", "reject_always"}
TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,256}", re.ASCII)
STOP_REASONS = {"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"}


class Refused(ValueError):
    """Bounded, constant error; never embeds untrusted message content."""


@dataclass(frozen=True)
class Message:
    kind: str
    rpc_id: int | None = None
    session: str | None = None
    version: int | None = None
    auth_offered: bool = False
    text_bytes: int = 0
    reason: str | None = None
    tool_call: str | None = None


def refuse(_=None):
    raise Refused("unsupported or malformed ACP fixture message")


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            refuse()
        result[key] = value
    return result


def integer(raw):
    if len(raw) > 20:
        refuse()
    value = int(raw)
    if not -(2**63) <= value < 2**64:
        refuse()
    return value


def depth_guard(text):
    # Only a resource guard: json.loads is the actual syntax parser.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                refuse()
        elif char in "]}":
            depth -= 1
            if depth < 0:
                refuse()
    if quoted or depth:
        refuse()


def bounded_tree(value):
    stack = [value]
    count = 0
    while stack:
        node = stack.pop()
        count += 1
        if count > MAX_NODES:
            refuse()
        if type(node) is dict:
            stack.extend(node.keys())
            stack.extend(node.values())
        elif type(node) is list:
            stack.extend(node)
        elif type(node) is str:
            node.encode("utf-8", "strict")


def obj(value, required, optional=()):
    if type(value) is not dict:
        refuse()
    keys = set(value)
    if not set(required) <= keys <= set(required) | set(optional):
        refuse()
    return value


def token(value):
    if type(value) is not str or TOKEN.fullmatch(value) is None:
        refuse()
    return value


def rpc_id(value):
    if type(value) is not int or not 0 <= value < 2**64:
        refuse()
    return value


def response(data):
    obj(data, ("jsonrpc", "id", "result"))
    ident = rpc_id(data["id"])
    result = data["result"]
    if ident == 0:
        obj(result, ("protocolVersion",), ("agentCapabilities", "agentInfo", "authMethods"))
        version = result["protocolVersion"]
        if type(version) is not int or not 0 <= version <= 65535:
            refuse()
        # These advertisements do not enable any features. Only their shape
        # is checked here; the reviewed manifest remains the authority.
        for field in ("agentCapabilities", "agentInfo"):
            if field in result and type(result[field]) is not dict:
                refuse()
        auth = result.get("authMethods", [])
        if type(auth) is not list:
            refuse()
        return Message("initialize", ident, version=version, auth_offered=bool(auth))
    if ident == 1:
        obj(result, ("sessionId",))
        return Message("session", ident, session=token(result["sessionId"]))
    if ident == 2:
        obj(result, ("stopReason",))
        reason = result["stopReason"]
        if type(reason) is not str:
            refuse()
        return Message("result", ident, reason=reason if reason in STOP_REASONS else "unknown")
    refuse()


def permission(data):
    """Recognize a narrow permission schema; never interpret option labels as grants."""
    obj(data, ("jsonrpc", "id", "method", "params"))
    ident = rpc_id(data["id"])
    if data["method"] != "session/request_permission":
        refuse()
    params = obj(data["params"], ("sessionId", "toolCall", "options"))
    session = token(params["sessionId"])
    # The restricted text-only client has no tool-call execution interface.
    # Recognize bare toolCallId or display-only metadata; hold richer payloads.
    call = obj(params["toolCall"], ("toolCallId",), ("title", "kind", "status", "name"))
    call_id = token(call["toolCallId"])
    for field in ("title", "kind", "status", "name"):
        if field in call and (type(call[field]) is not str
                              or len(call[field].encode("utf-8")) > MAX_LABEL):
            refuse()
    options = params["options"]
    if type(options) is not list or not 1 <= len(options) <= MAX_OPTIONS:
        refuse()
    seen = set()
    for option in options:
        obj(option, ("optionId", "name", "kind"))
        option_id = token(option["optionId"])
        name = option["name"]
        kind = option["kind"]
        if (option_id in seen or type(name) is not str or not name
                or len(name.encode("utf-8")) > MAX_LABEL
                or type(kind) is not str or kind not in OPTION_KINDS):
            refuse()
        seen.add(option_id)
    # Drop option IDs, labels and all operation descriptions. No selection.
    return Message("permission", ident, session=session, tool_call=call_id)


def notification(data):
    obj(data, ("jsonrpc", "method", "params"))
    if data["method"] != "session/update":
        refuse()
    params = obj(data["params"], ("sessionId", "update"))
    session = token(params["sessionId"])
    update = obj(params["update"], ("sessionUpdate", "content"), ("messageId",))
    if update["sessionUpdate"] != "agent_message_chunk":
        refuse()
    if "messageId" in update:
        token(update["messageId"])
    content = obj(update["content"], ("type", "text"))
    if content["type"] != "text" or type(content["text"]) is not str:
        refuse()
    size = len(content["text"].encode("utf-8"))
    if size > MAX_TEXT:
        refuse()
    return Message("text", session=session, text_bytes=size)


def decode(line):
    """Classify a complete line into metadata only, never content or effects.

    Deliberately narrow profile: response 0/1/2 and agent text update only.
    Permission requests yield metadata for a cancellation-only hold.
    Other inbound requests and unsupported updates are refused. No lifecycle state.
    """
    if type(line) is not bytes or not 0 < len(line) <= MAX_LINE:
        refuse()
    if any((byte < 32 and byte != 9) or byte == 127 for byte in line):
        refuse()
    try:
        text = line.decode("utf-8", "strict")
        depth_guard(text)
        data = json.loads(text, object_pairs_hook=pairs, parse_int=integer,
                          parse_float=refuse, parse_constant=refuse)
        bounded_tree(data)
        if type(data) is not dict or data.get("jsonrpc") != "2.0":
            refuse()
        if "method" in data:
            if "id" in data:
                return permission(data)
            return notification(data)
        return response(data)
    except (ValueError, UnicodeError, RecursionError):
        raise Refused("unsupported or malformed ACP fixture message") from None

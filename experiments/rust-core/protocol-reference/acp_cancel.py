# SPDX-License-Identifier: AGPL-3.0-only
"""Single-turn cancellation-only ACP reference. No IO, grants or process proof."""
import json
from dataclasses import dataclass

from acp_json import Refused, decode, refuse, token

MAX_PERMISSION_REPLIES = 32


@dataclass(frozen=True)
class Decision:
    event: str
    outbound: tuple[bytes, ...] = ()


def line(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"


class Conversation:
    """Reference state only; the Rust owner remains the runtime authority.

    One trusted connection object, one new session, one prompt. No implicit
    resume or retry. Outbound bytes are test artifacts, not transmitted.
    """

    def __init__(self, source):
        self._source = source
        self._phase = "initializing"
        self._session = None
        self._replied = set()

    @property
    def phase(self):
        return self._phase

    def _check_source(self, source):
        if source is not self._source:
            refuse()

    def _require(self, phase):
        if self._phase != phase:
            self._phase = "closed"
            refuse()

    def expect_new_session(self, source):
        self._check_source(source)
        self._require("initialized")
        self._phase = "creating"

    def prompt_sent(self, source):
        self._check_source(source)
        self._require("ready")
        self._phase = "prompting"

    def _cancel_message(self):
        return line({"jsonrpc": "2.0", "method": "session/cancel",
                     "params": {"sessionId": self._session}})

    def cancel(self, source):
        self._check_source(source)
        # A repeated owner stop must not break pending cancellation replies.
        # Never emit a second notification or resume the prompt.
        if self._phase == "cancelling":
            return Decision("hold")
        self._require("prompting")
        self._phase = "cancelling"
        return Decision("hold", (self._cancel_message(),))

    def receive(self, source, payload):
        self._check_source(source)
        if self._phase in ("closed", "terminal"):
            refuse()
        try:
            msg = decode(payload)
            if self._phase == "initializing" and msg.kind == "initialize":
                if msg.version != 1 or msg.auth_offered:
                    refuse()
                self._phase = "initialized"
                return Decision("initialized")
            if self._phase == "creating" and msg.kind == "session":
                self._session = token(msg.session)
                self._phase = "ready"
                return Decision("ready")
            if self._phase in ("prompting", "cancelling") and msg.kind in ("text", "permission"):
                if msg.session != self._session:
                    refuse()
                if msg.kind == "text":
                    return Decision("progress" if self._phase == "prompting" else "discard")
                # Each distinct pending request gets one cancelled response.
                # A duplicate or overflow closes without replaying a reply.
                if msg.rpc_id in self._replied or len(self._replied) >= MAX_PERMISSION_REPLIES:
                    refuse()
                self._replied.add(msg.rpc_id)
                first = self._phase == "prompting"
                self._phase = "cancelling"
                reply = line({"jsonrpc": "2.0", "id": msg.rpc_id,
                              "result": {"outcome": {"outcome": "cancelled"}}})
                return Decision("hold", (self._cancel_message(), reply) if first else (reply,))
            if self._phase in ("prompting", "cancelling") and msg.kind == "result":
                if self._phase == "cancelling" and msg.reason != "cancelled":
                    refuse()
                self._phase = "terminal"
                return Decision("reported_done" if msg.reason == "end_turn" else "reported_failure")
            refuse()
        except Refused:
            self._phase = "closed"
            raise

    def eof(self, source):
        self._check_source(source)
        self._phase = "closed"
        # No grant, completion publication, or process-exit observation.
        return Decision("transport_closed")

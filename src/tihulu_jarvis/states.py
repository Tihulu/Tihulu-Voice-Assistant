# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Semantic state as words, never as colour alone.

A colour is not a state: it cannot be read aloud, it cannot be told apart by
someone who does not see it, and it cannot be tested. This module turns the
client's live condition into a small vocabulary of tokens that a person reads
in words and a test can assert on.
"""
from .disclosure import STAYS_LOCAL

LOCAL = "local"
CLOUD = "cloud"
PRIVILEGED_TOOL = "privileged_tool"
MICROPHONE_ACTIVE = "microphone_active"
APPROVAL_PENDING = "approval_pending"
OFFLINE = "offline"
MUTED = "muted"

def privacy(data):
    """Whether the active configuration can reach a provider at all."""
    return CLOUD if data["boundary"] != STAYS_LOCAL else LOCAL


def microphone(stage):
    """The microphone state from the real stage, never from an animation."""
    if stage == "listening":
        return "listening"
    if stage == "transcribing":
        return "transcribing"
    return "idle"


def privileged(grants):
    """A local program may run under this profile."""
    return PRIVILEGED_TOOL if "host_shell" in grants else ""


def approval_pending(pending):
    return APPROVAL_PENDING if pending else ""


def muted(state):
    """Audio output is suppressed while a reply is still shown in text."""
    return MUTED if state else ""


def network(connected):
    return OFFLINE if not connected else ""


def tokens(data, stage, grants, pending, connected, muted_state=False):
    """The state as an ordered token list; empty means nothing to say."""
    out = [privacy(data)]
    if stage == "listening":
        out.append(MICROPHONE_ACTIVE)
    out.append(microphone(stage))
    out.append(privileged(grants))
    out.append(approval_pending(pending))
    out.append(muted(muted_state))
    out.append(network(connected))
    return tuple(t for t in out if t)

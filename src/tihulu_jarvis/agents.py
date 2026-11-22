# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""What this build does not do, stated as data.

A capability that is not implemented is listed with the reason it is closed, so
the interface can show an honest disabled item instead of a button that would
fail. Nothing here is a promise: each entry is a gate, not a plan.
"""
from dataclasses import dataclass

from .voice.m1 import Hold

NOT_IMPLEMENTED = "not_implemented"


@dataclass(frozen=True)
class Gate:
    token: str
    why: str

    def checked(self):
        if not self.token or len(self.token) > 64 or not self.why or len(self.why) > 160:
            raise Hold("invalid_local_settings")
        return self


# Each gate names a capability the specification describes and this build does
# not ship, with the reason it is closed rather than faked.
GATES = (
    Gate("opencode_session_list", "no OpenCode session is reachable from this build"),
    Gate("provider_login", "no provider credential is accepted, so none is stored"),
    Gate("agent_spawn", "this client starts no agent on any other host"),
    Gate("remote_tool_call", "a tool runs on this machine or nowhere"),
    Gate("task_cost_reporting", "no provider is reachable, so no cost is claimed"),
)

TOKENS = tuple(gate.token for gate in GATES)


def checked(item):
    """A gate the client can state: known, and with its reason."""
    if not isinstance(item, Gate) or item.token not in TOKENS:
        raise Hold("unknown_gate")
    return item.checked()


def closed():
    """Every gate, in the order the specification lists them."""
    return GATES

# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Where a message would go, decided before it is written.

The privacy boundary is derived from the active profile's granted privileges,
its approved providers and the kind of endpoint configured. No endpoint is
contacted here, and no message is inspected: the answer is a fact about the
configuration, never a guess about the future.

This build's only transport is a numeric loopback endpoint on this machine, so
the truthful default is that content does not leave the computer. A mode that
permits cloud reasoning states that permission without implying a road.
"""
from .profiles import granted
from .voice.m1 import Hold

STAYS_LOCAL = "stays_local"
CLOUD_WITH_APPROVAL = "cloud_with_approval"
CLOUD_WITHOUT_APPROVAL = "cloud_without_approval"

LOOPBACK_ONLY = "loopback_only"


def boundary(profile):
    """(level, reason) for what the active mode permits about leaving.

    The level comes from granted privileges, never from the mode name alone:
    a CLOUD_CHAT profile whose rules deny cloud reasoning stays local.
    """
    tokens = granted(profile)
    leaves = "cloud_disclosure" in tokens or "cloud_inference" in tokens
    if not leaves:
        return STAYS_LOCAL, "mode_grants_no_cloud"
    if profile.allowed_providers:
        return CLOUD_WITH_APPROVAL, "approved_providers_listed"
    return CLOUD_WITHOUT_APPROVAL, "no_approved_provider"


def endpoint_scope(endpoint):
    """'loopback' only for an explicit numeric loopback http endpoint."""
    try:
        from .voice.jan import endpoint as parse
        parse(endpoint)
    except (Hold, ValueError, TypeError):
        return "not_loopback"
    return "loopback"


def facts(profile, endpoint):
    """The disclosure as data: no address, no transcript, no provider secret."""
    level, reason = boundary(profile)
    scope = endpoint_scope(endpoint)
    return {
        "boundary": level,
        "boundary_reason": reason,
        "endpoint": scope,
        "transport": LOOPBACK_ONLY,
        "providers": tuple(profile.allowed_providers or ()),
        "grants": tuple(sorted(granted(profile))),
        # This build cannot send content off this machine, so this is always
        # False. It is stated anyway, so the answer never has to be inferred.
        "content_leaves": False,
        "leak_reason": "loopback_transport_only",
    }


def statement(data):
    """One short English line for the disclosure; the UI translates it."""
    if data["content_leaves"]:
        return "content may leave this computer"
    if data["boundary"] == CLOUD_WITH_APPROVAL:
        return ("this mode permits cloud reasoning with approved providers, "
                "but this build transports only to a loopback endpoint")
    if data["boundary"] == CLOUD_WITHOUT_APPROVAL:
        return ("this mode permits cloud reasoning without an approved provider, "
                "so every cloud route is refused; content stays on this computer")
    if data["endpoint"] != "loopback":
        return "the configured endpoint is not a loopback address; this build refuses it"
    return "content stays on this computer"

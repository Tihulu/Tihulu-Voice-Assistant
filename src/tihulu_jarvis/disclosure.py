# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Where a message would go, decided before it is written.

The privacy boundary is derived from the active profile's granted privileges,
its approved providers and the kind of endpoint configured. No endpoint is
contacted here, and no message is inspected: the answer is a fact about the
configuration, never a guess about the future.

This build's direct inference transport is numeric loopback only. That does
not prove the receiving service's model or upstream network behavior.
A profile's permitted capabilities are separate from transport availability.
"""
from .profiles import granted
from .voice.m1 import Hold

STAYS_LOCAL = "stays_local"
CLOUD_WITH_APPROVAL = "cloud_with_approval"
CLOUD_WITHOUT_APPROVAL = "cloud_without_approval"

LOOPBACK_ONLY = "loopback_only"


def boundary(profile, now=None):
    """(level, reason) for what the active mode permits about leaving.

    The level comes from granted privileges, never from the mode name alone:
    a CLOUD_CHAT profile whose rules deny cloud reasoning stays local.
    """
    tokens = granted(profile, now)
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


def facts(profile, endpoint, now=None):
    """The disclosure as data: no address, no transcript, no provider secret."""
    level, reason = boundary(profile, now)
    scope = endpoint_scope(endpoint)
    return {
        "boundary": level,
        "boundary_reason": reason,
        "endpoint": scope,
        "transport": LOOPBACK_ONLY,
        "providers": tuple(profile.allowed_providers or ()),
        "grants": tuple(sorted(granted(profile, now))),
        "direct_remote_inference": False,
        # None means unknown, not False. The receiver is a separate service.
        "content_leaves": None if scope == "loopback" else False,
        "upstream_verified": False,
        "leak_reason": "loopback_upstream_unverified" if scope == "loopback"
                       else "endpoint_refused",
    }


def statement(data):
    """Describe direct transport and uncertainty without promising locality."""
    if data["endpoint"] != "loopback":
        return "the configured endpoint is not a loopback address; this build refuses it"
    suffix = "loopback transport only; upstream processing is unverified"
    if data["boundary"] == CLOUD_WITH_APPROVAL:
        return "this mode permits cloud reasoning with approved providers, but " + suffix
    if data["boundary"] == CLOUD_WITHOUT_APPROVAL:
        return "this mode permits cloud reasoning; direct cloud routes are refused; " + suffix
    return suffix

# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Model routing under an operating mode.

A route is explicit: which engine serves a task, whether it is local or
cloud, and whether the active mode permits it. A route is never chosen
silently — an unavailable local route is a bounded reason, not a quiet
transition to a provider.
"""
from dataclasses import dataclass, replace

from .profiles import MODES, granted
from .voice.m1 import Hold

TASKS = ("stt", "llm", "tts")


@dataclass(frozen=True)
class Route:
    task: str
    model_id: str
    local_or_cloud: str
    provider: str = "unknown"
    endpoint: str = ""

    def checked(self):
        if self.task not in TASKS:
            raise Hold("unknown_task")
        if not isinstance(self.model_id, str) or not self.model_id:
            raise Hold("select_local_model")
        if self.local_or_cloud not in ("local", "cloud"):
            raise Hold("invalid_local_settings")
        if self.local_or_cloud == "cloud" and self.provider in ("", "unknown"):
            raise Hold("approved_provider_required")
        return self


@dataclass(frozen=True)
class Preference:
    """A profile's ordered preference for one task."""
    task: str
    order: tuple = ()

    def checked(self):
        if self.task not in TASKS:
            raise Hold("unknown_task")
        if any(not isinstance(r, Route) for r in self.order):
            raise Hold("invalid_route")
        for route in self.order:
            route.checked()
            if route.task != self.task:
                raise Hold("route_task_mismatch")
        return self


def permitted(profile, route):
    """Whether the active mode allows this route at all.

    LOCAL_ONLY never routes to a provider. A cloud route requires an approved
    provider name. A local route always stays local.
    """
    route.checked()
    if route.local_or_cloud == "local":
        return True, "local_route"
    if profile.based_on_mode == "LOCAL_ONLY":
        return False, "local_only_forbids_cloud_route"
    if profile.allowed_providers and route.provider not in profile.allowed_providers:
        return False, "approved_provider_required"
    return True, "mode_permits_cloud_route"


def select(profile, preference, now=0.0):
    """Choose the first permitted route, or report why none is available.

    There is no silent fallback: a cloud route is used only when the mode
    permits it and the owner ordered it.
    """
    preference.checked()
    for route in preference.order:
        ok, reason = permitted(profile, route)
        if ok:
            return route, reason
        last = reason
    raise Hold(last if preference.order else "no_route_configured")


def describe(route, reason):
    return "%s %s via %s (%s)" % (route.task, route.model_id,
                                  route.provider if route.local_or_cloud == "cloud" else "this computer",
                                  reason)

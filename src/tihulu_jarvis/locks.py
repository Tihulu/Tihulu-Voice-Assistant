# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Resource leases.

The microphone, audio playback, a GPU, a worktree and a browser profile are
exclusive resources. A turn that cannot get the lease it needs is refused
with a bounded reason rather than started anyway and failing later.
"""
import asyncio
from contextlib import asynccontextmanager

from .voice.m1 import Hold

RESOURCES = ("microphone", "audio_playback", "gpu", "worktree", "browser")


class Leases:
    """One lease per resource at a time, owned by a task identity."""

    def __init__(self):
        self._held = {}

    def held(self):
        return dict(self._held)

    def owner(self, resource):
        return self._held.get(resource)

    def acquire(self, resource, holder):
        if resource not in RESOURCES:
            raise Hold("unknown_resource")
        if not isinstance(holder, str) or not holder:
            raise Hold("invalid_lease_holder")
        current = self._held.get(resource)
        if current is not None and current != holder:
            raise Hold("resource_busy:" + resource)
        self._held[resource] = holder
        return holder

    def release(self, resource, holder):
        if resource not in RESOURCES:
            raise Hold("unknown_resource")
        current = self._held.get(resource)
        if current is None:
            return False
        if current != holder:
            raise Hold("lease_not_owned:" + resource)
        del self._held[resource]
        return True

    @asynccontextmanager
    async def hold(self, resource, holder):
        self.acquire(resource, holder)
        try:
            yield holder
        finally:
            if self._held.get(resource) == holder:
                del self._held[resource]

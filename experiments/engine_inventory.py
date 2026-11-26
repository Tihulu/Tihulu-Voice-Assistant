# SPDX-License-Identifier: AGPL-3.0-only
"""Fixture-only engine inventory/readiness; no scanning or process launch."""
from dataclasses import dataclass
from types import MappingProxyType
from gateway import Refused, token
from capabilities import CAPABILITIES, CapabilityCall


@dataclass(frozen=True)
class EngineEvidence:
    adapter: str
    version: str
    operation: str
    detected: bool
    compatible: bool
    tested: bool

    def checked(self):
        for value in (self.adapter, self.version, self.operation):
            if type(value) is not str:
                raise Refused("invalid_engine_identity")
            token(value)
        if (self.adapter, self.operation) not in CAPABILITIES:
            raise Refused("unsupported_capability")
        if any(type(v) is not bool for v in (self.detected, self.compatible, self.tested)):
            raise Refused("invalid_engine_evidence")
        if (self.compatible and not self.detected
                or self.tested and not self.compatible):
            raise Refused("contradictory_engine_evidence")
        return self


class Inventory:
    """Trusted synthetic probe observations, never model-reported readiness."""
    def __init__(self, observations):
        if type(observations) is not tuple or len(observations) > 64:
            raise Refused("invalid_engine_inventory")
        items = {}
        for item in observations:
            if type(item) is not EngineEvidence:
                raise Refused("invalid_engine_evidence")
            item.checked()
            key = (item.adapter, item.version, item.operation)
            if key in items:
                raise Refused("duplicate_engine_evidence")
            items[key] = item
        self._items = MappingProxyType(items)

    def readiness(self, call):
        if type(call) is not CapabilityCall:
            raise Refused("invalid_capability_call")
        call.checked()
        item = self._items.get((call.adapter, call.version, call.operation))
        if item is None or not item.detected:
            return "not_detected"
        if not item.compatible:
            return "incompatible"
        if not item.tested:
            return "not_tested"
        # Even verified test evidence cannot grant execution. This isolated
        # experiment has no runtime backend and never reports available.
        return "fixture_tested_activation_hold"

    def rows(self):
        return tuple(self._items.values())

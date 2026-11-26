# SPDX-License-Identifier: AGPL-3.0-only
"""Trusted disposable fixture gateway lifetime, not production authorization."""
from gateway import FixtureGateway,Refused

class FixtureLifetime:
    def __init__(self):
        self._gateways=[]
        self._closed=False

    def track(self,gateway):
        if self._closed or type(gateway) is not FixtureGateway:
            raise Refused("fixture_lifetime_closed_or_invalid")
        if any(existing is gateway for existing in self._gateways):
            raise Refused("fixture_lifetime_duplicate")
        self._gateways.append(gateway)
        return gateway

    def close(self):
        if not self._closed:
            self._closed=True
            # Closing the gateway invalidates every registered/claimed grant,
            # including those created immediately before an exception.
            for gateway in reversed(self._gateways):
                gateway.close()
            self._gateways.clear()

    def __enter__(self):
        if self._closed:
            raise Refused("fixture_lifetime_closed_or_invalid")
        return self

    def __exit__(self,exc_type,exc,tb):
        self.close()
        return False

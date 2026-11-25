# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping policy experiment. Decisions are simulations, never I/O grants."""
from dataclasses import dataclass
from threading import Lock
import math
import re
from tihulu_jarvis import profiles

MODES = ("LOCAL_ONLY", "CLOUD_CHAT", "STANDARD_HYBRID", "SECURE_HYBRID")
# Existing policy vocabulary is reused; exact scopes are additionally required.
ACTIONS = {
    "research": "external_network", "provider": "cloud_inference",
    "disclose": "cloud_disclosure", "local_plan": "publish_artifact",
    "engine": "filesystem_read", "agent": "host_shell",
    "catalog": "external_network", "transfer": "external_network",
    "publish": "publish_artifact",
}
HOST = frozenset(("engine", "agent", "local_plan", "publish"))
EXTERNAL = frozenset(("research", "provider", "disclose", "catalog", "transfer"))
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
MAX_GRANTS = 64


class Refused(ValueError):
    pass


def token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise Refused("invalid_identity")
    return value


@dataclass(frozen=True)
class Context:
    task: str
    session: str
    profile: str
    generation: int
    route: str
    mode: str
    offline: bool = True
    public_opt_in: bool = False

    def checked(self):
        for value in (self.task, self.session, self.profile, self.route):
            token(value)
        if (type(self.generation) is not int or not 0 < self.generation < 2**63
                or self.mode not in MODES or type(self.offline) is not bool
                or type(self.public_opt_in) is not bool):
            raise Refused("invalid_context")
        return self


@dataclass(frozen=True)
class Request:
    context: Context
    action: str
    target: str
    actor: str
    classification: str = "none"

    def checked(self):
        if type(self.context) is not Context:
            raise Refused("invalid_context")
        self.context.checked()
        token(self.target)
        if (not isinstance(self.action, str) or self.action not in ACTIONS or self.actor not in ("local", "cloud", "owner")
                or self.classification not in ("none", "public", "minimized", "private", "secret")):
            raise Refused("invalid_request")
        return self


@dataclass(frozen=True)
class Grant:
    identity: str
    request: Request
    deadline: float


class FixtureGateway:
    """One owner-created fixture context; no transport, callback or execution API.

    Reuses the existing PolicyClock and rule evaluator. Future real integration
    needs trusted generation/route binding, broker atomic initiation and OS isolation.
    This intentionally does not modify saved profiles or implement mode migration.
    """
    def __init__(self, context, rules, *, clock):
        if type(context) is not Context or type(clock) is not profiles.PolicyClock:
            raise Refused("invalid_fixture")
        self.context = context.checked()
        if type(rules) not in (tuple, list) or len(rules) > MAX_GRANTS:
            raise Refused("invalid_rules")
        self._rules = tuple(rules)
        for r in self._rules:
            if (type(r) is not profiles.Rule or not isinstance(r.action, str)
                    or r.action not in ACTIONS.values() or r.decision not in profiles.DECISIONS
                    or type(r.scope) is not tuple or len(r.scope) > 16
                    or type(r.expires_at) not in (int, float) or not 0 <= r.expires_at <= 10**12
                    or not isinstance(r.approval, str) or len(r.approval) > 64
                    or (r.approval and not r.expires_at)):
                raise Refused("invalid_rules")
            for scope in r.scope:
                token(scope)
            if len(set(r.scope)) != len(r.scope):
                raise Refused("invalid_rules")
        self._clock = clock
        self._grants = {}
        self._revoked = set()
        self._closed = False
        self._lock = Lock()

    def _check(self, req, now):
        if type(req) is not Request:
            raise Refused("invalid_request")
        req.checked()
        if self._closed or req.context != self.context:
            raise Refused("stale_context")
        if not math.isfinite(now):
            raise Refused("clock_unavailable")
        mode = self.context.mode
        if req.classification == "secret":
            raise Refused("secret_denied")
        if req.action in EXTERNAL and req.actor != "local":
            raise Refused("external_actor_denied")
        if req.action in EXTERNAL and self.context.offline:
            raise Refused("offline")
        if req.action in HOST and (mode == "CLOUD_CHAT" or req.actor != "local"):
            raise Refused("host_boundary")
        if req.action in ("provider", "disclose") and mode == "LOCAL_ONLY":
            raise Refused("local_only")
        if req.action in ("research", "catalog", "transfer") and mode == "LOCAL_ONLY" and not self.context.public_opt_in:
            raise Refused("public_opt_in_required")
        if req.action in EXTERNAL and req.classification == "private":
            raise Refused("private_egress_denied")
        if req.action in ("disclose", "provider") and req.classification not in (
                ("public", "minimized") if mode == "SECURE_HYBRID" else ("public",)):
            raise Refused("disclosure_classification_denied")
        if req.action in ("research", "catalog", "transfer") and req.classification != "public":
            raise Refused("public_data_required")
        # No silent selection override or grant inheritance.
        decision, reason = profiles.decide(self._rules, ACTIONS[req.action], (req.target,), now)
        if decision != profiles.ALLOW:
            raise Refused(reason)

    def fixture_grant(self, identity, req, seconds):
        """Trusted fixture setup only. Not callable from model output in runtime."""
        token(identity)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 3600:
            raise Refused("invalid_deadline")
        with self._lock:
            now = self._clock.now()
            self._check(req, now)
            if identity in self._grants or identity in self._revoked or len(self._grants) >= MAX_GRANTS:
                raise Refused("grant_identity_unavailable")
            grant = Grant(identity, req, now + seconds)
            self._grants[identity] = grant
            return grant

    def checkpoint(self, grant, req):
        """Return a fixture-only status; no permission token leaves this seam."""
        if type(grant) is not Grant:
            raise Refused("invalid_grant")
        token(grant.identity)
        with self._lock:
            now = self._clock.now()
            if self._grants.get(grant.identity) is not grant or grant.identity in self._revoked:
                raise Refused("unknown_or_revoked_grant")
            if now >= grant.deadline:
                self._revoked.add(grant.identity)
                raise Refused("expired")
            if grant.request != req:
                raise Refused("request_mismatch")
            try:
                self._check(req, now)
            except Refused:
                self._revoked.add(grant.identity)
                raise
            return "SIMULATED_ALLOW"

    def revoke(self, identity):
        token(identity)
        with self._lock:
            if identity in self._grants:
                self._revoked.add(identity)

    def close(self):
        with self._lock:
            self._closed = True

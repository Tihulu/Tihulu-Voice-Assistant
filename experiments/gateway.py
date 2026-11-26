# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping policy experiment. Decisions are simulations, never I/O grants."""
from dataclasses import dataclass
from threading import Lock
import math
from hashlib import sha256
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
    "voice_review": "filesystem_read",
    "capture": "local_audio_device", "browser": "browser_navigate",
    "document_engine": "filesystem_read", "document_export": "filesystem_write",
    "application_ui": "host_shell", "application_engine": "host_shell",
    "browser_read": "browser_navigate",
    "offline_browser": "browser_navigate",
    "conversion_publish": "publish_artifact",
}
HOST = frozenset(("engine", "agent", "local_plan", "publish", "capture",
                  "document_engine", "document_export", "application_ui",
                  "application_engine", "browser_read", "offline_browser", "conversion_publish"))
EXTERNAL = frozenset(("research", "provider", "disclose", "catalog", "transfer", "browser", "browser_read"))
EXTRA_CEILINGS = {
    "browser": ("external_network",),
    "browser_read": ("external_network", "host_shell"),
    "offline_browser": ("filesystem_read", "host_shell"),
    "document_engine": ("host_shell",),
    "document_export": ("filesystem_read", "host_shell"),
    "application_engine": ("filesystem_read", "filesystem_write"),
    "conversion_publish": ("filesystem_read", "filesystem_write"),
}
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


def voice_review_request(turn_req,parent_grant,parent_req):
    if (type(turn_req) is not Request or type(parent_grant) is not Grant or type(parent_req) is not Request
            or parent_grant.request!=parent_req or turn_req.context!=parent_req.context
            or turn_req.action!="document_engine" or turn_req.actor!="local" or turn_req.classification!="private"):
        raise Refused("invalid_voice_review")
    target=sha256(repr(("voice-review-v1",turn_req.target,parent_grant.identity,parent_req.target)).encode("ascii")).hexdigest()
    return Request(turn_req.context,"voice_review",target,"owner","private").checked()


class FixtureGateway:
    """One owner-created fixture context; no transport, callback or execution API.

    Reuses the existing PolicyClock and rule evaluator. Future real integration
    needs trusted generation/route binding, broker atomic initiation and OS isolation.
    This intentionally does not modify saved profiles or implement mode migration.
    """
    def __init__(self, context, rules, *, clock):
        if type(context) is not Context or type(clock) is not profiles.PolicyClock:
            raise Refused("invalid_fixture")
        self._context = context.checked()
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
        self._claimed = set()
        self._dependencies = {}
        self._transcript_receipts = {}
        self._detection_holds = {}
        self._closed = False
        self._lock = Lock()

    @property
    def context(self):
        return self._context

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
        if req.action in ("research", "catalog", "transfer", "browser", "browser_read") and mode == "LOCAL_ONLY" and not self.context.public_opt_in:
            raise Refused("public_opt_in_required")
        if req.action in EXTERNAL and req.classification == "private":
            raise Refused("private_egress_denied")
        if req.action in ("disclose", "provider") and req.classification not in (
                ("public", "minimized") if mode == "SECURE_HYBRID" else ("public",)):
            raise Refused("disclosure_classification_denied")
        if req.action in ("research", "catalog", "transfer", "browser", "browser_read") and req.classification != "public":
            raise Refused("public_data_required")
        if req.action == "offline_browser" and req.classification != "private":
            raise Refused("private_offline_browser_required")
        if req.action=="voice_review" and (req.actor!="owner" or req.classification!="private" or mode=="CLOUD_CHAT"):
            raise Refused("owner_voice_review_required")
        if req.action == "capture" and req.classification != "private":
            raise Refused("private_capture_required")
        # A browser operation requires both the browser-specific and the
        # external-network policy ceiling. No inherited research grant.
        actions = (ACTIONS[req.action], *EXTRA_CEILINGS.get(req.action, ()))
        for action in actions:
            if any(r.decision == profiles.DENY and r.matches(action, (req.target,)) for r in self._rules):
                raise Refused("explicit_deny")
            decision, reason = profiles.decide(self._rules, action, (req.target,), now)
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

    def _validate_one_locked(self, grant, req, now):
        if type(grant) is not Grant:
            raise Refused("invalid_grant")
        token(grant.identity)
        if self._grants.get(grant.identity) is not grant or grant.identity in self._revoked:
            raise Refused("unknown_or_revoked_grant")
        if not math.isfinite(now) or now >= grant.deadline:
            self._revoked.add(grant.identity)
            raise Refused("expired_or_unavailable" if not math.isfinite(now) else "expired")
        if grant.request != req:
            raise Refused("request_mismatch")
        try:
            self._check(req, now)
        except Refused:
            self._revoked.add(grant.identity)
            raise

    def _validate_locked(self, grant, req, now, validated=None):
        # One lock and one policy-clock observation: evaluate each exact grant
        # once even if a dense dependency DAG shares many ancestors. Repeated
        # references must still carry the identical registered grant and exact
        # request; never skip the dependency-claimed check.
        if validated is None:
            validated = {}
        pending = [(grant,req,False)]
        while pending:
            current,current_req,is_parent = pending.pop()
            if type(current) is not Grant or type(current_req) is not Request:
                raise Refused("invalid_grant")
            token(current.identity)
            if is_parent and current.identity not in self._claimed:
                raise Refused("dependency_not_claimed")
            previous=validated.get(current.identity)
            if previous is not None:
                if previous[0] is not current or previous[1]!=current_req:
                    raise Refused("request_mismatch")
                continue
            self._validate_one_locked(current,current_req,now)
            validated[current.identity]=(current,current_req)
            pending.extend((parent,parent_req,True)
                           for parent,parent_req in self._dependencies.get(current.identity,()))

    def _validate_grant(self, grant, req, *, claim=False):
        """Validate or atomically claim a fixture initiation, not an OS effect."""
        with self._lock:
            self._validate_locked(grant, req, self._clock.now())
            if claim:
                if grant.identity in self._claimed:
                    raise Refused("grant_already_claimed")
                self._claimed.add(grant.identity)
            return "SIMULATED_ALLOW"

    def _check_claimed_group_locked(self, dependencies, now):
        if type(dependencies) is not tuple or not 1 <= len(dependencies) <= MAX_GRANTS:
            raise Refused("invalid_dependency_group")
        identities = set()
        validated = {}
        for pair in dependencies:
            if type(pair) is not tuple or len(pair) != 2:
                raise Refused("invalid_dependency_group")
            grant, req = pair
            self._validate_locked(grant, req, now, validated)
            if grant.identity in identities or grant.identity not in self._claimed:
                raise Refused("dependency_not_claimed")
            identities.add(grant.identity)
        return identities

    def checkpoint_claimed_group(self, dependencies):
        """One simulated policy-time observation for an entire claimed chain."""
        with self._lock:
            self._check_claimed_group_locked(dependencies, self._clock.now())
            return "SIMULATED_ALLOW"

    def claim_with_dependencies(self, grant, req, dependencies):
        """Trusted fixture chain, no I/O callback or real effect authorization."""
        with self._lock:
            now = self._clock.now()
            self._validate_locked(grant, req, now)
            identities = self._check_claimed_group_locked(dependencies, now)
            if grant.identity in identities:
                raise Refused("dependency_not_claimed")
            if grant.identity in self._claimed:
                raise Refused("grant_already_claimed")
            self._dependencies[grant.identity] = dependencies
            self._claimed.add(grant.identity)
            return "SIMULATED_ALLOW"

    def record_transcript(self, grant, req, call, text_digest):
        """Trusted fixture completion registry, not an authentication API."""
        from capabilities import CapabilityCall
        if (type(call) is not CapabilityCall
                or (call.adapter,call.operation)!=("whisper_cpp","transcribe_wav")
                or call.request(self.context)!=req
                or type(text_digest) is not str or not re.fullmatch(r"[0-9a-f]{64}",text_digest)):
            raise Refused("invalid_transcript_receipt")
        args=dict(call.args)
        receipt=(args["output"],args["digest"],args["model_digest"],args["language"],text_digest)
        with self._lock:
            self._check_claimed_group_locked(((grant,req),),self._clock.now())
            if grant.identity in self._transcript_receipts:
                raise Refused("transcript_receipt_already_recorded")
            self._transcript_receipts[grant.identity]=receipt

    def record_detection_hold(self,grant,req,call,raw):
        from capabilities import CapabilityCall
        from language_detection import detection_observation,parse_detection
        if (type(call) is not CapabilityCall or (call.adapter,call.operation)!=("whisper_cpp","detect_language")
                or call.request(self.context)!=req):
            raise Refused("invalid_detection_hold")
        observed,score=detection_observation(raw)
        try:
            parse_detection(raw)
        except Refused:
            pass
        else:
            raise Refused("accepted_detection_cannot_be_hold")
        receipt=(call,observed,score,sha256(raw).hexdigest())
        with self._lock:
            self._check_claimed_group_locked(((grant,req),),self._clock.now())
            if grant.identity in self._detection_holds:
                raise Refused("detection_hold_already_recorded")
            self._detection_holds[grant.identity]=receipt
        return receipt[1:]

    def _language_override_review_locked(self,parent,parent_req,call,observation,now):
        from capabilities import CapabilityCall
        self._check_claimed_group_locked(((parent,parent_req),),now)
        hold=self._detection_holds.get(parent.identity)
        if hold is None or type(observation) is not tuple or hold[1:]!=observation:
            raise Refused("language_hold_receipt_mismatch")
        original=hold[0]
        if (type(call) is not CapabilityCall or (call.adapter,call.operation)!=("whisper_cpp","transcribe_wav")
                or original.version!=call.version):
            raise Refused("language_override_call")
        args=dict(call.checked().args)
        before=dict(original.args)
        if (any(args[k]!=before[k] for k in ("artifact","digest","model_digest"))
                or args["output"] in (before["artifact"],before["output"])):
            raise Refused("language_override_provenance")
        target=call.request(self.context).target
        digest=sha256(repr(("language-override-v1",parent.identity,parent_req.target,observation,target)).encode("ascii")).hexdigest()
        return Request(self.context,"voice_review",digest,"owner","private").checked()

    def prepare_language_override(self,parent,parent_req,call,observation):
        with self._lock:
            return self._language_override_review_locked(parent,parent_req,call,observation,self._clock.now())

    def claim_language_override(self,grant,req,call,dependencies,observation,review_grant):
        with self._lock:
            now=self._clock.now()
            self._validate_locked(grant,req,now)
            if type(dependencies) is not tuple or len(dependencies)!=1 or call.request(self.context)!=req:
                raise Refused("invalid_language_override_parent")
            parent,parent_req=dependencies[0]
            review_req=self._language_override_review_locked(parent,parent_req,call,observation,now)
            self._validate_locked(review_grant,review_req,now)
            if (grant.identity in self._claimed or review_grant.identity in self._claimed
                    or grant.identity==review_grant.identity):
                raise Refused("language_override_already_used")
            self._dependencies[review_grant.identity]=dependencies
            self._claimed.add(review_grant.identity)
            self._dependencies[grant.identity]=dependencies+((review_grant,review_req),)
            self._claimed.add(grant.identity)
            return "SIMULATED_ALLOW"

    def claim_voice_turn(self, grant, req, dependencies, receipt, review_grant=None):
        """Atomically validate exact registered transcript and claim child."""
        with self._lock:
            now=self._clock.now()
            self._validate_locked(grant,req,now)
            if type(dependencies) is not tuple or len(dependencies)!=1:
                raise Refused("invalid_voice_parent")
            identities=self._check_claimed_group_locked(dependencies,now)
            parent=dependencies[0][0]
            if self._transcript_receipts.get(parent.identity)!=receipt or type(receipt) is not tuple:
                raise Refused("voice_transcript_receipt_mismatch")
            if grant.identity in identities or grant.identity in self._claimed:
                raise Refused("grant_already_claimed")
            if type(review_grant) is not Grant:
                raise Refused("voice_review_required")
            review_req=voice_review_request(req,*dependencies[0])
            self._validate_locked(review_grant,review_req,now)
            if review_grant.identity in self._claimed or review_grant.identity==grant.identity:
                raise Refused("voice_review_already_used")
            # Exact owner review and turn claim are consumed together. Both
            # remain live ancestors; no separate race-prone approval lookup.
            self._dependencies[review_grant.identity]=dependencies
            self._claimed.add(review_grant.identity)
            self._dependencies[grant.identity]=dependencies+((review_grant,review_req),)
            self._claimed.add(grant.identity)
            return "SIMULATED_ALLOW"

    def claim_dependent(self, grant, req, dependency, dependency_req):
        return self.claim_with_dependencies(grant, req, ((dependency, dependency_req),))

    def checkpoint(self, grant, req):
        return self._validate_grant(grant, req)

    def claim(self, grant, req):
        """One initiation per grant; checkpoints remain valid until expiry/revoke."""
        return self._validate_grant(grant, req, claim=True)

    def revoke(self, identity):
        token(identity)
        with self._lock:
            if identity in self._grants:
                self._revoked.add(identity)

    def close(self):
        with self._lock:
            self._closed = True

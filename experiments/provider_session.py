# SPDX-License-Identifier: AGPL-3.0-only
"""Provider account-reference and payload consent fixture; no credential access.
Synthetic endpoint identities only, no vendor API compatibility claim.
"""
from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
from gateway import Context,FixtureGateway,Request,Refused,token
from inference import Envelope
from adapter_protocol import disclosure_target

@dataclass(frozen=True)
class Account:
    provider: str
    reference: str
    method: str
    origin: str
    generation: int
    def checked(self):
        for v in (self.provider,self.reference,self.origin):
            token(v)
        if (self.method not in ("api-key-reference","oauth-reference")
                or type(self.generation) is not int or not 0<self.generation<2**63):
            raise Refused("invalid_account_reference")
        return self

@dataclass(frozen=True)
class CallPlan:
    account: Account
    model: str
    payload_digest: str
    def target(self):
        if type(self.account) is not Account:
            raise Refused("invalid_account")
        a=self.account.checked()
        token(self.model)
        if (type(self.payload_digest) is not str or len(self.payload_digest)!=64
                or any(c not in "0123456789abcdef" for c in self.payload_digest)):
            raise Refused("invalid_payload")
        return sha256(repr(("provider-call",a.provider,a.reference,a.method,a.origin,
                            a.generation,self.model,self.payload_digest)).encode("ascii")).hexdigest()

def payload_target(plan,envelope):
    if type(plan) is not CallPlan or type(envelope) is not Envelope:
        raise Refused("invalid_plan")
    if plan.account.provider!=envelope.destination or plan.payload_digest!=envelope.digest:
        raise Refused("account_or_payload_mismatch")
    return sha256(repr(("provider-payload",plan.target(),disclosure_target(envelope))).encode("ascii")).hexdigest()

class ProviderSession:
    """Only synthetic minimized envelopes. No API key/token strings or transport."""
    def __init__(self,gateway,context,accounts):
        if (type(gateway) is not FixtureGateway or type(context) is not Context
                or gateway.context!=context or context.mode!="SECURE_HYBRID" or context.offline):
            raise Refused("secure_online_context_required")
        if type(accounts) is not tuple or not 1<=len(accounts)<=16:
            raise Refused("invalid_accounts")
        self._accounts={}
        for a in accounts:
            if type(a) is not Account:
                raise Refused("invalid_account")
            a.checked()
            if a.reference in self._accounts:
                raise Refused("duplicate_account")
            self._accounts[a.reference]=a
        self._g,self._ctx=gateway,context.checked()
        self._closed=False
        self._lock=RLock()
        self._used={}

    def prepare(self,plan,envelope,provider_grant,payload_grant):
        with self._lock:
            if self._closed or type(plan) is not CallPlan or type(envelope) is not Envelope:
                raise Refused("invalid_plan")
            target=plan.target()
            a=plan.account
            if (self._accounts.get(a.reference)!=a or envelope.destination!=a.provider
                    or envelope.digest!=plan.payload_digest):
                raise Refused("account_or_payload_mismatch")
            digest_target=payload_target(plan,envelope)
            pr=Request(self._ctx,"provider",target,"local","minimized")
            dr=Request(self._ctx,"disclose",digest_target,"local","minimized")
            self._g.checkpoint(provider_grant,pr)
            self._g.checkpoint(payload_grant,dr)
            # Two independent, single-use fixture permissions. Failure burns
            # any already-claimed permission; no retry or partial effect.
            claimed=[]
            def live():
                if self._closed or self._accounts.get(a.reference)!=a:
                    raise Refused("account_unavailable")
                self._g.checkpoint(provider_grant,pr)
                self._g.checkpoint(payload_grant,dr)
                if self._closed or self._accounts.get(a.reference)!=a:
                    raise Refused("account_unavailable")
            try:
                self._g.claim(provider_grant,pr)
                claimed.append(provider_grant)
                live()
                self._g.claim(payload_grant,dr)
                claimed.append(payload_grant)
                live()
                self._used.setdefault(a.reference,[]).extend(claimed)
                return "SIMULATED_PROVIDER_REQUEST_ONLY",envelope.payload
            except BaseException:
                for grant in claimed:
                    self._g.revoke(grant.identity)
                raise

    def forget(self,reference):
        token(reference)
        with self._lock:
            self._accounts.pop(reference,None)
            for grant in self._used.pop(reference,()):
                self._g.revoke(grant.identity)

    def close(self):
        with self._lock:
            self._closed=True
            self._accounts.clear()
            for grants in self._used.values():
                for grant in grants:
                    self._g.revoke(grant.identity)
            self._used.clear()

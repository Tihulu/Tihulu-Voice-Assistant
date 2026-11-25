# SPDX-License-Identifier: AGPL-3.0-only
"""Provider account-reference and payload consent fixture; no credential access.
Synthetic endpoint identities only, no vendor API compatibility claim.
"""
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from gateway import Request,Refused,token
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

class ProviderSession:
    """Only synthetic minimized envelopes. No API key/token strings or transport."""
    def __init__(self,gateway,context,accounts):
        if context.mode!="SECURE_HYBRID" or context.offline:
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
        self._lock=Lock()

    def prepare(self,plan,envelope,provider_grant,payload_grant):
        with self._lock:
            if self._closed or type(plan) is not CallPlan or type(envelope) is not Envelope:
                raise Refused("invalid_plan")
            target=plan.target()
            a=plan.account
            if (self._accounts.get(a.reference)!=a or envelope.destination!=a.provider
                    or envelope.digest!=plan.payload_digest):
                raise Refused("account_or_payload_mismatch")
            digest_target=disclosure_target(envelope)
            pr=Request(self._ctx,"provider",target,"local","minimized")
            dr=Request(self._ctx,"disclose",digest_target,"local","minimized")
            self._g.checkpoint(provider_grant,pr)
            self._g.checkpoint(payload_grant,dr)
            # Two independent, single-use fixture permissions. Failure burns
            # any already-claimed permission; no retry or partial effect.
            self._g.claim(provider_grant,pr)
            self._g.claim(payload_grant,dr)
            self._g.checkpoint(provider_grant,pr)
            self._g.checkpoint(payload_grant,dr)
            return "SIMULATED_PROVIDER_REQUEST_ONLY",envelope.payload

    def forget(self,reference):
        token(reference)
        with self._lock:
            self._accounts.pop(reference,None)

    def close(self):
        with self._lock:
            self._closed=True
            self._accounts.clear()

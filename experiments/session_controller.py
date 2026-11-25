# SPDX-License-Identifier: AGPL-3.0-only
"""Owner-controlled fixture context lifecycle. No settings or credential I/O."""
from dataclasses import dataclass,replace
from threading import RLock
from gateway import Context,Request,FixtureGateway,Refused,token
from tihulu_jarvis import profiles

@dataclass(frozen=True)
class Template:
    identity: str
    mode: str
    rules: tuple

class SessionController:
    """New context always closes the preceding gateway. No imported ALLOW."""
    def __init__(self,clock):
        if type(clock) is not profiles.PolicyClock:
            raise Refused("invalid_clock")
        self._clock=clock
        self._generation=0
        self._gateway=None
        self._lock=RLock()
        self._closed=False

    def activate(self,template,task,session,route,*,offline=True,public_opt_in=False,imported=False):
        if type(template) is not Template or type(imported) is not bool:
            raise Refused("invalid_template")
        if type(template.rules) is not tuple:
            raise Refused("invalid_rules")
        with self._lock:
            if self._closed or self._generation>=2**63-2:
                raise Refused("controller_closed")
            ctx=Context(task,session,template.identity,self._generation+1,route,template.mode,offline,public_opt_in)
            # Validate original template before downgrading any ALLOW.
            validated=FixtureGateway(ctx,template.rules,clock=self._clock)
            if imported:
                validated.close()
                rules=tuple(replace(r,decision=profiles.ASK,approval="",expires_at=0.0)
                            if r.decision==profiles.ALLOW else r for r in template.rules)
                validated=FixtureGateway(ctx,rules,clock=self._clock)
            if self._gateway is not None:
                self._gateway.close()
            self._generation=ctx.generation
            self._gateway=validated
            return ctx

    def current(self):
        with self._lock:
            if self._closed or self._gateway is None:
                raise Refused("no_active_context")
            return self._gateway

    def owner_grant(self,identity,request,seconds,*,confirmed=False):
        # This is a synthetic owner confirmation interface, not a real UI.
        if confirmed is not True or type(request) is not Request:
            raise Refused("explicit_confirmation_required")
        with self._lock:
            return self.current().fixture_grant(identity,request,seconds)

    def stop(self):
        with self._lock:
            if self._gateway is not None:
                self._gateway.close()
                self._gateway=None

    def close(self):
        with self._lock:
            self.stop()
            self._closed=True

# SPDX-License-Identifier: AGPL-3.0-only
"""Offline fixture browser ownership and exact one-shot operations, no I/O."""
from threading import Lock
from hashlib import sha256
from gateway import FixtureGateway, Refused, token
from capabilities import CapabilityCall
from adapter_protocol import text_bytes

class OfflineBrowser:
    def __init__(self,gateway,context,version,profile,tab,artifact,digest):
        if type(gateway) is not FixtureGateway or gateway.context!=context:
            raise Refused("invalid_fixture")
        self._g,self._ctx=gateway,context
        self._version=version
        self._args=(("profile",profile),("tab",tab),("artifact",artifact),("digest",digest))
        self._call("offline_open")
        self._lock=Lock()
        self._claims=[]
        self._state="new"
        self._active=None
        self._receipt=None

    def _call(self,operation,element=None):
        args=self._args if element is None else self._args+(("element",element),)
        return CapabilityCall("firefox",self._version,operation,args).checked()

    def prepare(self,operation,element=None):
        with self._lock:
            if self._state=="closed" or self._active is not None:
                raise Refused("browser_unavailable")
            if operation=="offline_open":
                if self._state!="new" or element is not None:
                    raise Refused("browser_open_denied")
            elif operation not in ("offline_read","offline_click") or self._state!="open" or element is None:
                raise Refused("browser_operation_denied")
            if operation=="offline_read" and element!="message" or operation=="offline_click" and element!="change":
                raise Refused("browser_element_denied")
            return self._call(operation,element)

    def begin(self,call,grant):
        with self._lock:
            if type(call) is not CapabilityCall or self._active is not None:
                raise Refused("browser_unavailable")
            op=call.operation
            if op=="offline_open":
                valid=self._state=="new" and call==self._call(op)
            elif op in ("offline_read","offline_click"):
                element="message" if op=="offline_read" else "change"
                valid=self._state=="open" and call==self._call(op,element)
            else:
                valid=False
            if not valid:
                raise Refused("browser_operation_mismatch")
            request=call.request(self._ctx)
            if self._claims:
                self._g.claim_with_dependencies(grant,request,tuple(self._claims))
            else:
                self._g.claim(grant,request)
            self._claims.append((grant,request))
            self._active=op
            self._receipt=None

    def checkpoint(self):
        with self._lock:
            if self._active is None or self._state=="closed":
                raise Refused("browser_unavailable")
            self._g.checkpoint_claimed_group(tuple(self._claims))

    def complete(self,text=None):
        with self._lock:
            if self._active is None or self._state=="closed":
                raise Refused("browser_unavailable")
            try:
                self._g.checkpoint_claimed_group(tuple(self._claims))
                if self._active=="offline_read":
                    self._receipt=sha256(text_bytes(text)).hexdigest()
                elif text is not None:
                    raise Refused("unexpected_browser_payload")
                self._g.checkpoint_claimed_group(tuple(self._claims))
                self._state="open"
                self._active=None
            except Refused:
                self._close()
                raise

    def result(self):
        with self._lock:
            if self._state!="open" or self._active is not None or self._receipt is None:
                raise Refused("no_browser_result")
            try:
                self._g.checkpoint_claimed_group(tuple(self._claims))
                return self._receipt
            except Refused:
                self._close()
                raise

    def _close(self):
        self._state="closed"
        self._active=None
        self._receipt=None
        for grant,_ in self._claims:
            self._g.revoke(grant.identity)

    def close(self):
        with self._lock:
            self._close()

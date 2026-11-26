# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded synthetic PDF split/merge receipts, not production filesystem I/O."""
from hashlib import sha256
from threading import Lock
from capabilities import CapabilityCall
from gateway import FixtureGateway, Refused
from conversion_result import MAX_OUTPUT

class PDFManipulation:
    def __init__(self,gateway,grant,context,call,*,parent=None):
        if (type(gateway) is not FixtureGateway or type(call) is not CapabilityCall
                or call.adapter!="poppler" or call.operation not in ("pdf_split","pdf_merge")):
            raise Refused("invalid_pdf_manipulation")
        self._g,self._grant,self._call=gateway,grant,call
        self._request=call.request(context)
        self._parent=parent
        if call.operation=="pdf_split":
            if parent is not None:
                raise Refused("unexpected_pdf_parent")
            gateway.claim(grant,self._request)
        else:
            if type(parent) is not PDFManipulation or parent._g is not gateway:
                raise Refused("pdf_split_parent_required")
            with parent._lock:
                if parent._call.operation!="pdf_split" or parent._state!="completed":
                    raise Refused("pdf_split_parent_required")
                parent._g.checkpoint(parent._grant,parent._request)
                args=dict(call.args)
                old=dict(parent._call.args)
                expected=dict(zip(old["page_order"],parent._digests))
                if (args["artifact"]!=old["output"] or args["digest"]!=old["digest"]
                        or any(p not in expected for p in args["page_order"])
                        or args["part_digests"]!=tuple(expected[p] for p in args["page_order"])):
                    raise Refused("pdf_merge_provenance_mismatch")
                gateway.claim_dependent(grant,self._request,parent._grant,parent._request)
        self._state="ready"
        self._digests=()
        self._lock=Lock()

    def complete(self,outputs):
        with self._lock:
            if self._state!="ready":
                raise Refused("terminal_pdf_manipulation")
            try:
                self._g.checkpoint(self._grant,self._request)
                count=len(dict(self._call.args)["page_order"]) if self._call.operation=="pdf_split" else 1
                if (type(outputs) is not tuple or len(outputs)!=count or
                        any(type(v) is not bytes or not 5<len(v)<=MAX_OUTPUT
                            or not v.startswith(b"%PDF-") for v in outputs)):
                    raise Refused("invalid_pdf_outputs")
                self._digests=tuple(sha256(v).hexdigest() for v in outputs)
                self._g.checkpoint(self._grant,self._request)
                self._state="completed"
            except Refused:
                self._digests=()
                self._state="held"
                raise

    def result(self):
        with self._lock:
            if self._state!="completed":
                raise Refused("no_pdf_result")
            self._g.checkpoint(self._grant,self._request)
            return self._digests

    def cancel(self):
        with self._lock:
            self._state="cancelled"
            self._digests=()
            self._g.revoke(self._grant.identity)

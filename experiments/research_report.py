# SPDX-License-Identifier: AGPL-3.0-only
"""Structured local research report, with revalidated exact source excerpts."""
from dataclasses import dataclass
from hashlib import sha256
import json
from gateway import Request, Refused
from research import canonical
from research_session import ResearchSession

@dataclass(frozen=True)
class Span:
    identity: str
    digest: str
    start: int
    end: int

@dataclass(frozen=True)
class Report:
    payload: bytes
    digest: str

class ReportBuilder:
    def __init__(self,gateway,context,session,spans):
        if type(session) is not ResearchSession or session._g is not gateway or session._ctx!=context:
            raise Refused("wrong_session")
        if type(spans) is not tuple or not 1<=len(spans)<=16 or any(type(s) is not Span for s in spans):
            raise Refused("invalid_spans")
        self._g,self._ctx,self._session,self._spans=gateway,context,session,spans
        self._closed=False
    def preview(self):
        if self._closed:
            raise Refused("report_closed")
        records=[]
        for n,s in enumerate(self._spans,1):
            e=self._session.cite(s.identity,s.digest,s.start,s.end)
            records.append(dict(number=n,url=e.url,digest=e.digest,start=e.start,end=e.end,quote=e.quote))
        # This is structured text, not executable markup or generated claims.
        payload=json.dumps({"sources":records},ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")
        if len(payload)>65536:
            raise Refused("report_limit")
        return Report(payload,sha256(payload).hexdigest())
    def export_bytes(self,grant,expected_digest):
        current=self.preview()
        if current.digest!=expected_digest:
            raise Refused("report_changed")
        req=Request(self._ctx,"publish",current.digest,"local")
        self._g.checkpoint(grant,req)
        if self.preview()!=current:
            raise Refused("report_changed")
        self._g.claim(grant,req)
        return current.payload
    def close(self):
        self._closed=True
        self._spans=()

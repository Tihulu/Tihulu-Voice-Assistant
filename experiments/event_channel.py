# SPDX-License-Identifier: AGPL-3.0-only
"""Strict normalized agent event framing, not OpenCode wire compatibility."""
import json
from threading import Lock
from gateway import Refused, token
from connector_protocol import Connector

MAX_FRAME=4096

def unique(pairs):
    out={}
    for k,v in pairs:
        if k in out:
            raise Refused("duplicate_field")
        out[k]=v
    return out

class EventChannel:
    """Only status events may cross the fixture boundary, no tool instructions."""
    def __init__(self,connector,handle,session,job):
        if type(connector) is not Connector:
            raise Refused("invalid_connector")
        self._connector,self._handle=connector,handle
        self._session,self._job=token(session),token(job)
        self._lock=Lock()
        self._closed=False

    def receive(self,raw):
        with self._lock:
            if self._closed:
                raise Refused("channel_closed")
            try:
                if type(raw) is not bytes or not 0<len(raw)<=MAX_FRAME:
                    raise Refused("invalid_frame")
                obj=json.loads(raw.decode("utf-8"),object_pairs_hook=unique)
                if (type(obj) is not dict or set(obj)!={"session","job","sequence","kind"}
                        or obj["session"]!=self._session or obj["job"]!=self._job
                        or type(obj["sequence"]) is not int
                        or obj["kind"] not in ("progress","completed","failed","cancelled")):
                    raise Refused("invalid_frame")
                state=self._connector.event(self._handle,self._session,self._job,obj["sequence"],obj["kind"])
                if state!="running":
                    self._closed=True
                return state
            except (ValueError,UnicodeError,RecursionError):
                self._closed=True
                try:
                    self._connector.cancel(self._handle)
                except Refused:
                    pass
                raise Refused("agent_event_refused") from None

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed=True
                try:
                    self._connector.cancel(self._handle)
                except Refused:
                    pass

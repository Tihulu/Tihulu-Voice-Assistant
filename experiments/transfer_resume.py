# SPDX-License-Identifier: AGPL-3.0-only
"""Owner-bound in-memory partial receipts; not durable storage or transport."""
from dataclasses import dataclass
import hashlib
from threading import Lock
from gateway import Request, Refused, Grant
from market_fixture import FixtureTransfer, MAX_CHUNK

@dataclass(frozen=True)
class Partial:
    manifest_id: str
    offset: int
    prefix_digest: str
    old_grant: str

class ResumeBook:
    """Bounded, single-session receipt authority. Never trusts copied metadata.

    The caller supplies synthetic bytes; the original receipt object is held
    by this owner-controlled book, consumed once on successful continuation.
    Restart invalidates receipts. Real authenticated durable storage remains HOLD.
    """
    def __init__(self, gateway, context):
        self._g,self._ctx=gateway,context.checked()
        self._issued=[]
        self._lock=Lock()
        self._closed=False

    def pause(self, transfer):
        with self._lock:
            if (self._closed or type(transfer) is not FixtureTransfer or transfer._done
                    or transfer._g is not self._g or transfer._ctx!=self._ctx
                    or len(self._issued)>=64):
                raise Refused("invalid_partial")
            self._g.checkpoint(transfer._grant,transfer._req)
            data=bytes(transfer._data)
            if not 0<len(data)<transfer.manifest.size:
                raise Refused("invalid_partial")
            receipt=Partial(transfer.manifest.identity,len(data),
                            hashlib.sha256(data).hexdigest(),transfer._grant.identity)
            self._g.checkpoint(transfer._grant,transfer._req)
            transfer._done=True
            transfer._data.clear()
            self._issued.append(receipt)
            self._g.revoke(transfer._grant.identity)
            return receipt,data

    def resume(self, grant, manifest, receipt, prefix):
        with self._lock:
            manifest.checked()
            if (self._closed or type(grant) is not Grant or type(receipt) is not Partial
                    or not any(r is receipt for r in self._issued)
                    or receipt.manifest_id!=manifest.identity
                    or type(receipt.offset) is not int or not 0<receipt.offset<manifest.size
                    or type(prefix) is not bytes or len(prefix)!=receipt.offset
                    or hashlib.sha256(prefix).hexdigest()!=receipt.prefix_digest
                    or grant.identity==receipt.old_grant):
                raise Refused("partial_mismatch")
            req=Request(self._ctx,"transfer",manifest.identity,"local","public")
            self._g.checkpoint(grant,req)
            t=FixtureTransfer(self._g,grant,self._ctx,manifest)
            for pos in range(0,len(prefix),MAX_CHUNK):
                t.append(manifest.identity,pos,prefix[pos:pos+MAX_CHUNK])
            self._g.checkpoint(grant,req)
            self._issued=[r for r in self._issued if r is not receipt]
            return t

    def close(self):
        with self._lock:
            self._closed=True
            self._issued.clear()

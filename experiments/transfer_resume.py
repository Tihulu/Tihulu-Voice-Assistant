# SPDX-License-Identifier: AGPL-3.0-only
"""In-memory resume receipt experiment. No filesystem, transport or download."""
from dataclasses import dataclass
import hashlib
from gateway import Request, Refused
from market_fixture import FixtureTransfer, MAX_CHUNK

@dataclass(frozen=True)
class Partial:
    manifest_id: str
    offset: int
    prefix_digest: str
    old_grant: str

def pause(transfer):
    if type(transfer) is not FixtureTransfer or transfer._done:
        raise Refused("invalid_partial")
    transfer._g.checkpoint(transfer._grant, transfer._req)
    data = bytes(transfer._data)
    if not 0 < len(data) < transfer.manifest.size:
        raise Refused("invalid_partial")
    partial = Partial(transfer.manifest.identity, len(data),
                      hashlib.sha256(data).hexdigest(), transfer._grant.identity)
    transfer._done = True
    transfer._data.clear()
    return partial, data

def resume(gateway, grant, context, manifest, receipt, prefix):
    manifest.checked()
    if (type(receipt) is not Partial or receipt.manifest_id != manifest.identity
            or type(receipt.offset) is not int or not 0 < receipt.offset < manifest.size
            or type(prefix) is not bytes or len(prefix) != receipt.offset
            or hashlib.sha256(prefix).hexdigest() != receipt.prefix_digest
            or grant.identity == receipt.old_grant):
        raise Refused("partial_mismatch")
    req = Request(context, "transfer", manifest.identity, "local", "public")
    gateway.checkpoint(grant, req)
    t = FixtureTransfer(gateway, grant, context, manifest)
    for pos in range(0, len(prefix), MAX_CHUNK):
        t.append(manifest.identity, pos, prefix[pos:pos+MAX_CHUNK])
    return t

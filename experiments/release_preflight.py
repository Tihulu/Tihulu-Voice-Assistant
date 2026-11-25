# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic evidence indexing, never certification or release authorization."""
from dataclasses import dataclass
from gateway import Refused, token
from release_rehearsal import hex_id, key

# These are evidence categories, not extra project gates or votes.
REQUIRED=(
    ("source-provenance","automation"),
    ("python-310","automation"),
    ("python-312","automation"),
    ("gtk","automation"),
    ("wheel","automation"),
    ("debian-extracted","automation"),
    ("independent-review","reviewer"),
    ("cosmic-picker","owner"),
    ("real-local-voice","owner"),
    ("real-csv-workflow","owner"),
    ("clean-install-rollback","owner"),
    ("release-signoff","owner"),
)
SCOPES=dict(REQUIRED)

@dataclass(frozen=True)
class Candidate:
    commit: str
    tree: str
    package: str
    def checked(self):
        hex_id(self.commit,40)
        hex_id(self.tree,40)
        hex_id(self.package,64)
        return self

@dataclass(frozen=True)
class Evidence:
    category: str
    candidate: Candidate
    actor: str
    outcome: str
    reference: str
    artifact_digest: str
    def checked(self):
        if type(self.category) is not str or self.category not in SCOPES or self.actor!=SCOPES[self.category]:
            raise Refused("wrong_evidence_scope")
        if type(self.candidate) is not Candidate:
            raise Refused("invalid_candidate")
        self.candidate.checked()
        if self.outcome not in ("PASS","FAIL","HOLD"):
            raise Refused("invalid_outcome")
        token(self.reference)
        hex_id(self.artifact_digest,64)
        return self

@dataclass(frozen=True)
class Preflight:
    candidate: Candidate
    missing: tuple
    failed: tuple
    held: tuple
    supplied_pass: tuple
    digest: str
    # This is deliberately not a PASS verdict even when every record says PASS.
    disposition: str

def preflight(candidate,records):
    if type(candidate) is not Candidate:
        raise Refused("invalid_candidate")
    candidate.checked()
    if type(records) is not tuple or len(records)>len(REQUIRED):
        raise Refused("invalid_evidence_set")
    seen={}
    for record in records:
        if type(record) is not Evidence:
            raise Refused("invalid_evidence")
        record.checked()
        if record.candidate!=candidate:
            raise Refused("stale_evidence")
        if record.category in seen:
            raise Refused("duplicate_evidence")
        seen[record.category]=record
    order=tuple(c for c,_ in REQUIRED)
    missing=tuple(c for c in order if c not in seen)
    failed=tuple(c for c in order if c in seen and seen[c].outcome=="FAIL")
    held=tuple(c for c in order if c in seen and seen[c].outcome=="HOLD")
    supplied=tuple(c for c in order if c in seen and seen[c].outcome=="PASS")
    fingerprint=key("evidence-index",candidate.commit,candidate.tree,candidate.package,
                    tuple((c,seen[c].actor,seen[c].outcome,seen[c].reference,seen[c].artifact_digest)
                          for c in order if c in seen))
    state="BLOCKED" if failed else "INCOMPLETE" if missing or held else "REVIEW_REQUIRED"
    return Preflight(candidate,missing,failed,held,supplied,fingerprint,state)

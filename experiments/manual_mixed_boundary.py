# SPDX-License-Identifier: AGPL-3.0-only
"""Experimental manual segmentation preview, not authorization or inference."""
from dataclasses import dataclass
from hashlib import sha256
import json
from gateway import Refused
from speech_result import pcm_info
from mixed_voice_review import split_pcm,MixedVoiceReview,EditedMixedPreview

@dataclass(frozen=True)
class BoundaryRow:
    start_frame: int
    end_frame: int
    audio_digest: str

@dataclass(frozen=True)
class BoundaryPreview:
    audio_digest: str
    sample_rate: int
    frames: int
    cuts: tuple
    rows: tuple
    preview_digest: str

def boundary_preview(data,cuts):
    """Prepare exact user-selected cuts; no guessed language or grant."""
    rate,frames=pcm_info(data)
    pieces=split_pcm(data,cuts)
    if any(p.end_frame-p.start_frame<rate for p in pieces):
        raise Refused("manual_boundary_short_segment")
    rows=tuple(BoundaryRow(p.start_frame,p.end_frame,p.digest) for p in pieces)
    audio=sha256(data).hexdigest()
    envelope=("manual-boundary-v1",audio,rate,frames,cuts,
              tuple((r.start_frame,r.end_frame,r.audio_digest) for r in rows))
    digest=sha256(json.dumps(envelope,separators=(",",":")).encode("ascii")).hexdigest()
    return BoundaryPreview(audio,rate,frames,cuts,rows,digest)

def verify_boundary_preview(data,cuts,shown):
    """Check current bytes and boundaries against exact prior display.
    Returned chunks are not authorized to execute or submit anything.
    """
    if type(shown) is not BoundaryPreview:
        raise Refused("manual_boundary_preview_required")
    fresh=boundary_preview(data,cuts)
    if fresh!=shown:
        raise Refused("manual_boundary_preview_stale")
    return split_pcm(data,fresh.cuts)


@dataclass(frozen=True)
class ManualReviewOutcome:
    status: str
    reason: str | None
    preview: EditedMixedPreview | None

def inspect_manual_review(data,cuts,shown,transcripts,model_digest,edits=()):
    """Fresh boundary and transcript checks for a UI; no submission authority."""
    try:
        verify_boundary_preview(data,cuts,shown)
        preview=MixedVoiceReview(data,cuts,transcripts,model_digest).edit_preview(edits)
    except Refused as exc:
        return ManualReviewOutcome("HOLD",str(exc),None)
    return ManualReviewOutcome("READY",None,preview)

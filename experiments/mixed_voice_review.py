# SPDX-License-Identifier: AGPL-3.0-only
"""Exact pre-segmented mixed-language voice review, no automatic segmentation/send."""
from dataclasses import dataclass
from hashlib import sha256
import struct
from gateway import Refused
from speech_result import pcm_info
from transcription_result import TranscriptFixture
from adapter_protocol import text_bytes

@dataclass(frozen=True)
class Segment:
    start_frame: int
    end_frame: int
    data: bytes
    digest: str

def split_pcm(data,cuts):
    """Caller supplies exact boundaries. Reject gaps, overlap, silent/short slices."""
    rate,frames=pcm_info(data)
    if (type(cuts) is not tuple or not 3<=len(cuts)<=9
            or any(type(n) is not int for n in cuts)
            or cuts[0]!=0 or cuts[-1]!=frames
            or any(b<=a for a,b in zip(cuts,cuts[1:]))):
        raise Refused("invalid_voice_segment_boundaries")
    pieces=[]
    for start,end in zip(cuts,cuts[1:]):
        body=data[44+start*2:44+end*2]
        header=struct.pack("<4sI4s4sIHHIIHH4sI",b"RIFF",len(body)+36,b"WAVE",b"fmt ",16,
                           1,1,rate,rate*2,2,16,b"data",len(body))
        chunk=header+body
        pcm_info(chunk)
        pieces.append(Segment(start,end,chunk,sha256(chunk).hexdigest()))
    if b"".join(s.data[44:] for s in pieces)!=data[44:]:
        raise Refused("voice_segment_coverage")
    return tuple(pieces)

@dataclass(frozen=True)
class SegmentReview:
    start_frame: int
    end_frame: int
    source_digest: str
    language: str
    text: str
    transcript_digest: str

@dataclass(frozen=True)
class MixedPreview:
    audio_digest: str
    sample_rate: int
    segments: tuple
    text: str
    text_digest: str

class MixedVoiceReview:
    """Build a fresh review from live typed transcript fixtures only.

    All segments share one fixture gateway/context. No grant, transport,
    automatic segmentation, language inference or automatic submit is provided.
    A displayed snapshot is not an authorization token.
    """
    def __init__(self,data,cuts,transcripts,model_digest):
        self._pieces=split_pcm(data,cuts)
        self._digest=sha256(data).hexdigest()
        self._rate=pcm_info(data)[0]
        if (type(transcripts) is not tuple or len(transcripts)!=len(self._pieces)
                or any(type(t) is not TranscriptFixture for t in transcripts)
                or len({id(t) for t in transcripts})!=len(transcripts)
                or type(model_digest) is not str or len(model_digest)!=64
                or any(c not in "0123456789abcdef" for c in model_digest)):
            raise Refused("invalid_mixed_voice_inputs")
        self._transcripts=transcripts
        self._model=model_digest
        self.preview()

    def preview(self):
        bindings=tuple(t.review_binding() for t in self._transcripts)
        gateway=bindings[0][0]
        if any(g is not gateway or req.context!=gateway.context for g,_,req,_,_ in bindings):
            raise Refused("mixed_voice_context_mismatch")
        if len({grant.identity for _,grant,_,_,_ in bindings})!=len(bindings):
            raise Refused("mixed_voice_duplicate_grant")
        rows=[]
        for piece,(_,_,_,value,digest) in zip(self._pieces,bindings):
            if (value.source_digest!=piece.digest or value.model_digest!=self._model
                    or value.language not in ("tr","en","de")
                    or sha256(text_bytes(value.text)).hexdigest()!=digest):
                raise Refused("mixed_voice_segment_mismatch")
            rows.append(SegmentReview(piece.start_frame,piece.end_frame,piece.digest,
                                      value.language,value.text,digest))
        if len({row.language for row in rows})<2:
            raise Refused("mixed_voice_requires_multiple_languages")
        text="\n".join(row.text for row in rows)
        digest=sha256(text_bytes(text)).hexdigest()
        gateway.checkpoint_claimed_group(tuple((grant,req) for _,grant,req,_,_ in bindings))
        return MixedPreview(self._digest,self._rate,tuple(rows),text,digest)


def silence_boundaries(data):
    """Conservative pause-based proposal for bounded PCM, not language identification.
    Fixed 20ms windows, peak <= 64 PCM units, at least 600ms quiet.
    Retain every frame, including silence. Ambiguous/no pause => HOLD.
    """
    rate,frames=pcm_info(data)
    window=rate//50
    if rate%50:
        raise Refused("unsupported_pause_sample_rate")
    quiet=[]
    start=None
    for pos in range(0,frames-window+1,window):
        body=data[44+pos*2:44+(pos+window)*2]
        silent=all(abs(value[0])<=64 for value in struct.iter_unpack("<h",body))
        if silent and start is None:
            start=pos
        elif not silent and start is not None:
            if pos-start>=30*window:
                quiet.append((start,pos))
            start=None
    # Leading/trailing silence is not an interior boundary.
    cuts=[0]
    for start,end in quiet:
        if start==0 or end>=frames:
            continue
        midpoint=(start+end)//2
        cuts.append(midpoint)
    cuts.append(frames)
    if len(cuts)<3 or len(cuts)>9 or any(b-a<rate for a,b in zip(cuts,cuts[1:])):
        raise Refused("pause_boundaries_ambiguous")
    pieces=split_pcm(data,tuple(cuts))
    # Require actual voiced samples on each side, not merely nonzero noise.
    if any(not any(abs(v[0])>64 for v in struct.iter_unpack("<h",piece.data[44:])) for piece in pieces):
        raise Refused("pause_segment_unvoiced")
    return tuple(cuts)

# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping handoff contract: reviewed text -> inert composer draft only."""
from dataclasses import dataclass
from hashlib import sha256
import json
from gateway import Refused
from manual_mixed_boundary import inspect_manual_review
from mixed_voice_review import EditedMixedPreview
from tihulu_jarvis.voice.session import Session
from tihulu_jarvis.voice.m1 import Hold

@dataclass(frozen=True)
class ComposerContext:
    session: Session
    epoch: int
    profile: str
    generation: str
    route: str
    model: str
    draft: str
    busy: bool
    closing: bool
    has_attachment: bool

    def checked(self):
        if (type(self.session) is not Session or type(self.epoch) is not int
                or any(type(v) is not str or not 0<len(v)<=256
                       for v in (self.profile,self.generation,self.route,self.model))
                or type(self.draft) is not str
                or any(type(v) is not bool for v in (self.busy,self.closing,self.has_attachment))):
            raise Refused("composer_context_invalid")
        try:
            self.session.check(self.epoch)
        except Hold:
            raise Refused("composer_session_changed") from None
        if self.busy or self.closing or self.has_attachment:
            raise Refused("composer_unavailable")
        if self.session.active is not None and not self.session.active.done():
            raise Refused("composer_session_busy")
        # Never overwrite a user's existing draft, including whitespace.
        if self.draft:
            raise Refused("composer_draft_occupied")
        return self

@dataclass(frozen=True)
class HandoffPreview:
    context: ComposerContext
    boundary_digest: str
    review_digest: str
    text_digest: str
    text: str
    confirmation_digest: str

def prepare_handoff(context,data,cuts,shown,transcripts,model_digest,edits=()):
    if type(context) is not ComposerContext:
        raise Refused("composer_context_invalid")
    context.checked()
    outcome=inspect_manual_review(data,cuts,shown,transcripts,model_digest,edits)
    if outcome.status!="READY" or type(outcome.preview) is not EditedMixedPreview:
        raise Refused(outcome.reason or "composer_review_unavailable")
    preview=outcome.preview
    text=preview.text
    # The shipped session has a 16000-character input limit. No truncation.
    if not 0<len(text)<=16000 or not any(c.isalnum() for c in text):
        raise Refused("composer_text_limit")
    envelope=("voice-draft-v1",context.epoch,context.profile,context.generation,
              context.route,context.model,shown.preview_digest,preview.review_digest,
              preview.text_digest)
    confirmation=sha256(json.dumps(envelope,separators=(",",":")).encode("ascii")).hexdigest()
    context.checked()
    return HandoffPreview(context,shown.preview_digest,preview.review_digest,
                          preview.text_digest,text,confirmation)

def confirmed_draft(current,prepared,confirmation,data,cuts,shown,transcripts,model_digest,edits=()):
    """Revalidate exact current state; return text, never mutate or send.
    Confirmation digest is a display binding, NOT an authentication token.
    Caller must obtain actual explicit user intent in the future native UI.
    """
    if type(prepared) is not HandoffPreview or type(current) is not ComposerContext:
        raise Refused("composer_preview_required")
    if type(confirmation) is not str or confirmation!=prepared.confirmation_digest:
        raise Refused("composer_confirmation_required")
    if current!=prepared.context:
        raise Refused("composer_context_changed")
    fresh=prepare_handoff(current,data,cuts,shown,transcripts,model_digest,edits)
    if fresh!=prepared:
        raise Refused("composer_preview_changed")
    return fresh.text

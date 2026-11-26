# SPDX-License-Identifier: AGPL-3.0-only
"""Trusted UI-thread lifecycle for a displayed, inert voice draft."""
from dataclasses import dataclass
from threading import get_ident
from gateway import Refused
from voice_composer_handoff import prepare_handoff,confirmed_draft,HandoffPreview

@dataclass(frozen=True,eq=False)
class Display:
    preview: HandoffPreview

class DraftDisplay:
    """One active display, one successful confirmation. No widget or send.
    Only the trusted UI thread may use this instance. Actual user gestures,
    fresh context capture and invalidation events belong to the UI caller.
    """
    def __init__(self):
        self._thread=get_ident()
        self._display=None

    def _check_thread(self):
        if get_ident()!=self._thread:
            raise Refused("draft_display_thread")

    def invalidate(self):
        self._check_thread()
        self._display=None

    def show(self,context,*args,edits=()):
        self.invalidate()
        preview=prepare_handoff(context,*args,edits)
        self._display=Display(preview)
        return self._display

    def accept(self,current,display,confirmation,*args,edits=()):
        self._check_thread()
        if self._display is None or display is not self._display:
            raise Refused("draft_display_stale")
        # Consume before validation: no retry of this display after a
        # failed confirmation, stale context, or expired source receipt.
        self._display=None
        return confirmed_draft(current,display.preview,confirmation,*args,edits)

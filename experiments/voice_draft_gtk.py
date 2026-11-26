# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable GTK4 widget experiment. Not imported by the shipped desktop."""
from dataclasses import replace
import gi
gi.require_version("Gtk","4.0")
from gi.repository import Gtk
from gateway import Refused
from voice_draft_display import DraftDisplay

class VoiceDraftWidget:
    """Trusted GTK thread only; context reader supplies current app snapshot.
    No submission signal, no model/tool call, no production policy adapter.
    """
    def __init__(self,context_reader):
        self._context_reader=context_reader
        self._control=DraftDisplay()
        self.root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
        self.preview=Gtk.TextView(editable=False,wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.composer=Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.status=Gtk.Label(label="No review",xalign=0)
        self.actions=Gtk.Box()
        for child in (self.preview,self.actions,self.composer,self.status):
            self.root.append(child)
        self._button=None
        self._handler=None
        self._review=None
        self.composer.get_buffer().connect("changed",self._changed)

    def text(self):
        b=self.composer.get_buffer()
        return b.get_text(b.get_start_iter(),b.get_end_iter(),True)

    def _current(self):
        return replace(self._context_reader(),draft=self.text())

    def _remove_button(self):
        button,handler=self._button,self._handler
        self._button=self._handler=None
        if button is not None:
            button.disconnect(handler)
            button.set_sensitive(False)
            self.actions.remove(button)

    def invalidate(self,reason="Review invalidated"):
        self._control.invalidate()
        self._review=None
        self._remove_button()
        self.preview.get_buffer().set_text("")
        self.status.set_text(reason)

    def _changed(self,*_):
        # Even typing and clearing again invalidates the displayed review.
        self.invalidate()

    def refresh_review(self):
        """Trusted UI event/tick hook; clear stale text and button on HOLD.
        Caller schedules ticks and profile/model/route change notifications.
        No background timer is owned by this isolated widget.
        """
        if self._review is None:
            return False
        display,args,edits=self._review
        try:
            self._control.refresh(self._current(),display,*args,edits=edits)
        except Refused as exc:
            self.invalidate("HOLD: "+str(exc))
            return False
        return True

    def present(self,*args,edits=()):
        self.invalidate()
        try:
            display=self._control.show(self._current(),*args,edits=edits)
        except Refused as exc:
            self.status.set_text("HOLD: "+str(exc))
            return None
        self._review=(display,args,edits)
        self.preview.get_buffer().set_text(display.preview.text)
        button=Gtk.Button(label="Use reviewed text")
        self._button=button
        def clicked(source):
            if source is not self._button:
                return
            try:
                text=self._control.accept(self._current(),display,
                    display.preview.confirmation_digest,*args,edits=edits)
            except Refused as exc:
                self.invalidate("HOLD: "+str(exc))
                return
            self.invalidate()
            self.composer.get_buffer().set_text(text)
            self.status.set_text("Draft ready; not sent")
        self._handler=button.connect("clicked",clicked)
        self.actions.append(button)
        self.status.set_text("Review ready")
        return button

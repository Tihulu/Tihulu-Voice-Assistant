# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable GTK4 widget experiment. Not imported by the shipped desktop."""
from dataclasses import replace
from threading import current_thread
import gi
gi.require_version("Gtk","4.0")
from gi.repository import Gtk,GLib
from gateway import Refused
from voice_draft_display import DraftDisplay
from voice_composer_handoff import ComposerContext

class VoiceDraftWidget:
    """Trusted GTK thread only; context reader supplies current app snapshot.
    No submission signal, no model/tool call, no production policy adapter.
    """
    def __init__(self,context_reader,*,refresh_ms=250):
        if type(refresh_ms) is not int or not 10<=refresh_ms<=1000:
            raise ValueError("invalid refresh interval")
        self._thread=current_thread()
        self._closed=False
        self._refresh_ms=refresh_ms
        self._tick=None
        self._tick_token=None
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
        self._changed_handler=self.composer.get_buffer().connect("changed",self._changed)

    def _check_thread(self):
        if current_thread() is not self._thread:
            raise Refused("draft_widget_thread")

    def text(self):
        self._check_thread()
        b=self.composer.get_buffer()
        return b.get_text(b.get_start_iter(),b.get_end_iter(),True)

    def _current(self):
        try:
            context=self._context_reader()
        except Exception:
            raise Refused("draft_context_unavailable") from None
        if type(context) is not ComposerContext:
            raise Refused("draft_context_unavailable")
        return replace(context,draft=self.text())

    def _stop_tick(self):
        source=self._tick
        self._tick=self._tick_token=None
        if source is not None:
            GLib.source_remove(source)

    def _schedule_tick(self):
        if self._closed or self._review is None or self._tick is not None:
            return
        token=object()
        self._tick_token=token
        def tick():
            if self._tick_token is not token:
                return GLib.SOURCE_REMOVE
            # Current source is removed by its return value, not source_remove.
            self._tick=self._tick_token=None
            if self.refresh_review():
                self._schedule_tick()
            return GLib.SOURCE_REMOVE
        self._tick=GLib.timeout_add(self._refresh_ms,tick)

    def close(self):
        """Explicit owner teardown; preserve any already-confirmed user draft."""
        self._check_thread()
        if self._closed:
            return
        self.invalidate("Closed")
        self._closed=True
        self.composer.get_buffer().disconnect(self._changed_handler)
        self.composer.set_editable(False)

    def _remove_button(self):
        button,handler=self._button,self._handler
        self._button=self._handler=None
        if button is not None:
            button.disconnect(handler)
            button.set_sensitive(False)
            self.actions.remove(button)

    def invalidate(self,reason="Review invalidated"):
        self._check_thread()
        self._stop_tick()
        self._control.invalidate()
        self._review=None
        self._remove_button()
        self.preview.get_buffer().set_text("")
        self.status.set_text(reason)

    def _changed(self,*_):
        # Even typing and clearing again invalidates the displayed review.
        self.invalidate()

    def refresh_review(self):
        """Clear stale text at an owned GTK tick or trusted UI event."""
        self._check_thread()
        if self._closed or self._review is None:
            return False
        display,args,edits=self._review
        try:
            self._control.refresh(self._current(),display,*args,edits=edits)
        except Refused as exc:
            self.invalidate("HOLD: "+str(exc))
            return False
        return True

    def present(self,*args,edits=()):
        self._check_thread()
        if self._closed:
            return None
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
        self._schedule_tick()
        return button

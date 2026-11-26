# SPDX-License-Identifier: AGPL-3.0-only
"""Real generated-engine receipts in disposable actual GTK widgets."""
from voice_draft_gtk import Gtk,VoiceDraftWidget

def exercise_review(record,composer,args,edits,expected):
    Gtk.init()
    widget=VoiceDraftWidget(lambda:composer)
    try:
        button=widget.present(*args,edits=edits)
        preview=widget.preview.get_buffer()
        shown=preview.get_text(preview.get_start_iter(),preview.get_end_iter(),True)
        record("mixed-gtk-exact-edited-preview",button is not None and shown==expected)
        record("mixed-gtk-no-draft-before-click",widget.text()=="" and composer.session.active is None)
        if button is None:
            return
        button.emit("clicked")
        record("mixed-gtk-exact-multiline-draft",widget.text()==expected and
               widget.status.get_text()=="Draft ready; not sent" and widget._tick is None)
        widget.composer.get_buffer().set_text("")
        button.emit("clicked")
        record("mixed-gtk-old-button-no-replay",widget.text()=="" and
               composer.session.history==() and composer.session.epoch==0 and composer.session.active is None)
    finally:
        widget.close()

def exercise_revoked(record,composer,args,edits):
    widget=VoiceDraftWidget(lambda:composer)
    try:
        button=widget.present(*args,edits=edits)
        preview=widget.preview.get_buffer()
        record("mixed-gtk-revoked-review-not-displayed",button is None and widget.text()=="" and
               preview.get_text(preview.get_start_iter(),preview.get_end_iter(),True)=="" and
               widget.status.get_text().startswith("HOLD:") and widget._tick is None)
    finally:
        widget.close()

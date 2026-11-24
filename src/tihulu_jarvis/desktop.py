# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Opt-in GTK 4 desktop client. No listener, background capture or provider login."""
import asyncio
import json
import locale
import os
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from . import (a11y, agents, attachments, catalog_sources, files, keys, queries,
                 readiness, receipts, resources, tasks, transcript, usage, web)
from .voice.jan import MAX_TEXT as STREAM_CAP
from . import catalog_fetch, disclosure, routing
from .analysis_ui import AnalysisView
from . import analysis_intent
from . import locks, search, states
from . import market, profiles, settings
from .voice import audio
from .voice.jan import Jan
from .voice.m1 import Hold, SpeechRequest
from .voice.session import Session


def _gtk():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio, GLib, Gtk
    except (ImportError, ValueError):
        raise SystemExit("GTK 4 with PyGObject is required for the desktop client.")

    return Gio, GLib, Gtk


def accessible_name(widget, text):
    """Give a control a screen-reader name when the platform offers one.

    A name is a property, not a promise: where the GTK build cannot set it the
    control is left exactly as it was, and the state row below carries the same
    words for a sighted reader.
    """
    try:
        from gi.repository import Gtk
        widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
    except (ImportError, ValueError, TypeError, AttributeError):
        return None
    return text


LOOK = (".workspace {padding:20px;} .hero {font-size:24px;font-weight:bold;}"
        " .receipt {padding:6px 10px;border-radius:8px;background:alpha(currentColor,.05);}"
        " .badge {color:#40ae96;font-weight:bold;} .message {padding:10px 14px;"
        "border-radius:12px;background:alpha(currentColor,.07);}"
        " .user {border-left:3px solid #39a68f;} .assistant {border-left:3px solid #787ee0;}")


# 83 wakeups a second while working; 25 while idle. The idle number is a
# measured compromise: a slower one saves more CPU but adds its own latency to
# every handoff between a GTK callback and a waiting coroutine, which the test
# suite showed as a four-fold slowdown.
BUSY_TICK_MS = 12
IDLE_TICK_MS = 40


class Bridge:
    """The asyncio slices on the GTK thread, and how often it wakes.

    A fixed 12 ms timer wakes the process more than eighty times a second even
    when nothing is happening, which is exactly the idle cost this replaces: a
    fast tick runs while work is pending, a slow one runs while the client is
    idle, and ``wake()`` remembers when work was last submitted so the interface
    can state how long it has been quiet.
    """

    def __init__(self):
        self.interval = IDLE_TICK_MS
        self.busy_at = None
        self._wake = None

    def attach(self, wake):
        """Give the bridge the re-arm callback the GLib loop provides."""
        self._wake = wake
        return self

    def wake(self):
        """Ask for the fast tick, and remember when work was submitted."""
        self.busy_at = time.monotonic()
        if self._wake is not None:
            self._wake()
        self.interval = BUSY_TICK_MS
        return self.interval

    def pause(self):
        """Called by the loop when it has no work left: the fast tick stops."""
        self.interval = IDLE_TICK_MS
        return self.interval

    def idle_for(self, now=None):
        """How long the client has been idle, in whole seconds."""
        if self.busy_at is None:
            return 0
        return max(0, int((now or time.monotonic()) - self.busy_at))


@contextmanager
def gtk_asyncio(loop):
    """Run small asyncio slices on the GTK thread until the window closes.

    The slow tick always runs; the fast tick is added when the client submits
    work and stops itself when the loop drains. A source is never removed from
    inside its own callback: that is undefined behaviour in GLib, and it is what
    made an earlier version of this bridge hang under load.
    """
    _, GLib, _ = _gtk()
    bridge = Bridge()
    fast = [None]

    def drain():
        loop.call_soon(loop.stop)
        loop.run_forever()
        if asyncio.all_tasks(loop):
            return True
        # Nothing pending: stop the fast tick until the next wake.
        fast[0] = None
        bridge.pause()
        return False

    def slow():
        loop.call_soon(loop.stop)
        loop.run_forever()
        if asyncio.all_tasks(loop):
            # Work appeared without a wake: go fast again rather than making
            # the loop wait out an idle tick that no longer describes it.
            wake()
        return True

    def wake():
        if fast[0] is None:
            fast[0] = GLib.timeout_add(BUSY_TICK_MS, drain)

    slow_source = GLib.timeout_add(IDLE_TICK_MS, slow)
    try:
        bridge.attach(wake)
        yield bridge
    finally:
        if slow_source is not None:
            GLib.source_remove(slow_source)
        if fast[0] is not None:
            GLib.source_remove(fast[0])
            fast[0] = None

def create_window(app, loop, bridge=None):
    """Construct the native window on the caller's GTK thread.

    ``bridge`` is the asyncio/GLib bridge the caller entered, when it has one:
    the window then wakes it promptly when work is submitted and states how long
    it has been idle. A window without a bridge still runs; it simply cannot
    state idleness.
    """
    _, GLib, Gtk = _gtk()
    from gi.repository import GObject
    lang = (locale.getlocale()[0] or "en").split("_")[0]
    def tr(en, tur, de):
        return {"tr":tur, "de":de}.get(lang,en)

    class Desktop:
        def __init__(self, app, loop=None, bridge=None):
            self.loop = loop
            self.bridge = bridge
            self.confirmations = []
            self.owned_picker = None
            self.owned_picker_handler = None
            self.attachment_context = None
            self.leases = locks.Leases()
            self.search_matches = ()
            self.growing = {}
            self.transcribe_runner = None
            self.win = Gtk.ApplicationWindow(application=app, title="TIHULU JARVIS")
            self.win.set_default_size(920, 650)
            self.session, self.inventory = Session(), ()
            self.stage, self.busy = "ready", False
            self.finish, self.playback, self.closing = None, None, False
            self.playback_stopping = False
            self.cfg = settings.empty()
            read_error = ""
            try:
                self.cfg = settings.load()
            except Hold as e:
                read_error = str(e)
            self.look = LOOK
            self.scale_provider = None
            self.apply_text_scale()
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
            root.add_css_class("workspace")
            self.win.set_child(root)
            top = Gtk.Box(spacing=14)
            root.append(top)
            title = Gtk.Label(label="TIHULU JARVIS",xalign=0)
            title.add_css_class("hero")
            top.append(title)
            local = Gtk.Label(label=tr("LOCAL CONNECTION","YEREL BAĞLANTI","LOKALE VERBINDUNG"))
            local.add_css_class("badge")
            top.append(local)
            # The topbar states the active mode, profile, model and privacy
            # boundary in words, so a glance never has to guess them.
            self.topbar_note = Gtk.Label(xalign=0, wrap=True, hexpand=True)
            top.append(self.topbar_note)
            # A hard stop in the topbar: the same cancellation path as the
            # composer's, reachable without looking for it.
            self.quick_stop = Gtk.Button(label=tr("Hard stop","Tümünü durdur","Alles stoppen"))
            self.quick_stop.add_css_class("badge")
            self.quick_stop.connect("clicked", lambda *_: self.hard_stop())
            self.quick_stop.set_sensitive(False)
            accessible_name(self.quick_stop, tr("Stop everything now",
                                                 "Her şeyi şimdi durdur",
                                                 "Alles sofort stoppen"))
            top.append(self.quick_stop)
            # The queue summary counts the tasks this client is actually
            # running; an idle window says idle rather than zero busy.
            self.queue_note = Gtk.Label(xalign=0, wrap=True)
            top.append(self.queue_note)
            self.status = Gtk.Label(xalign=0, wrap=True)
            root.append(self.status)
            # The state is stated in words, never in colour alone: a person who
            # cannot see the accent colour reads the same condition here.
            self.state_row = Gtk.Label(xalign=0, wrap=True)
            root.append(self.state_row)
            self.stack = Gtk.Stack(vexpand=True)
            tabs = Gtk.StackSwitcher(stack=self.stack)
            root.append(tabs)
            root.append(self.stack)
            chat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
            self.stack.add_titled(chat,"conversation",tr("Chat & Voice","Sohbet ve ses","Chat & Sprache"))
            self.log = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.scroll = Gtk.ScrolledWindow(vexpand=True)
            self.scroll.set_child(self.log)
            chat.append(self.scroll)
            searchrow = Gtk.Box(spacing=8)
            self.search_entry = Gtk.Entry(hexpand=True,placeholder_text=tr("Search this conversation…","Bu sohbette ara…","In diesem Gespräch suchen…"))
            searchrow.append(self.search_entry)
            self.search_clear = self.button(searchrow,tr("Clear","Temizle","Leeren"),self.clear_search)
            self.export_chat = self.button(searchrow, tr("Export conversation",
                                                         "Sohbeti dışa aktar",
                                                         "Gespräch exportieren"),
                                           self.export_conversation)
            chat.append(searchrow)
            self.search_results = Gtk.ListBox()
            chat.append(self.search_results)
            self.search_note = Gtk.Label(xalign=0, wrap=True)
            chat.append(self.search_note)
            self.search_entry.connect("changed", lambda *_: self.run_search())
            self.search_results.connect("row-selected", lambda *_: self.show_match())
            self.search_history = Gtk.ListBox()
            chat.append(self.search_history)
            historyrow = Gtk.Box(spacing=8)
            chat.append(historyrow)
            self.search_history_label = Gtk.Label(xalign=0, wrap=True)
            historyrow.append(self.search_history_label)
            self.search_history_label.set_text(tr("Recent searches",
                                                  "Son aramalar",
                                                  "Letzte Suchen"))
            self.search_forget = self.button(historyrow, tr("Forget searches",
                                                            "Aramaları unut",
                                                            "Suchen vergessen"),
                                             self.forget_searches)
            self.refresh_search_history()
            entryrow = Gtk.Box(spacing=8)
            self.entry = Gtk.Entry(hexpand=True,placeholder_text=tr("Write a message…","Bir mesaj yaz…","Nachricht schreiben…"))
            entryrow.append(self.entry)
            self.disclosure_note = Gtk.Label(xalign=0, wrap=True)
            chat.append(self.disclosure_note)
            self.send = self.button(entryrow,tr("Send","Gönder","Senden"),self.send_text)
            self.attach = self.button(entryrow,tr("Attach a file","Dosya ekle","Datei anhängen"),self.attach_file)
            self.entry.connect("activate",lambda *_:self.send_text())
            self.entry.connect("changed", lambda *_: self.refresh_send())
            self.attachment_note = Gtk.Label(xalign=0, wrap=True)
            chat.append(entryrow)
            chat.append(self.attachment_note)
            self.attachment = None
            controls = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6,row_spacing=4)
            chat.append(controls)
            self.talk = self.button(controls,tr("Talk","Konuş","Sprechen"),self.voice)
            self.finish_btn = self.button(controls,tr("Finish recording","Kaydı bitir","Aufnahme beenden"),lambda:self.finish.set() if self.finish else None)
            self.stop = self.button(controls,tr("Stop speaking","Sesi durdur","Wiedergabe stoppen"),self.stop_speaking)
            # Muting suppresses audio output, never the answer: the reply is
            # still shown, and the microphone still works.
            self.mute = Gtk.ToggleButton(label=tr("Mute","Sessiz","Stumm"))
            self.mute.connect("toggled", self.toggle_mute)
            controls.append(self.mute)
            accessible_name(self.mute, tr("Mute audio output", "Ses çıkışını sessize al",
                                          "Audioausgabe stummschalten"))
            self.cancel = self.button(controls,tr("Cancel turn","Turu iptal et","Antwort abbrechen"),lambda:self.interrupt(False))
            self.end = self.button(controls,tr("End conversation","Sohbeti bitir","Gespräch beenden"),lambda:self.interrupt(True))
            self.hard = self.button(controls,tr("Hard stop","Tümünü durdur","Alles stoppen"),self.hard_stop)
            for widget, caption in ((self.talk, tr("Start listening on the microphone",
                                                   "Mikrofonda dinlemeyi başlat",
                                                   "Zuhören mit dem Mikrofon starten")),
                                    (self.finish_btn, tr("Finish the recording",
                                                         "Kaydı bitir",
                                                         "Aufnahme beenden")),
                                    (self.stop, tr("Stop speaking the reply",
                                                   "Yanıtı okumayı durdur",
                                                   "Antwort nicht mehr vorlesen")),
                                    (self.cancel, tr("Cancel the current turn",
                                                     "Geçerli turu iptal et",
                                                     "Aktuelle Runde abbrechen")),
                                    (self.end, tr("End this conversation",
                                                  "Bu sohbeti bitir",
                                                  "Dieses Gespräch beenden")),
                                    (self.hard, tr("Stop everything now",
                                                   "Her şeyi şimdi durdur",
                                                   "Alles sofort stoppen"))):
                accessible_name(widget, caption)
            models = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
            view = Gtk.ScrolledWindow(vexpand=True)
            view.set_child(models)
            self.stack.add_titled(view,"models",tr("Installed models","Kurulu modeller","Installierte Modelle"))
            models.append(Gtk.Label(label=tr("Choose your local Jan connection and audio models. Nothing is downloaded.","Yerel Jan bağlantısını ve ses modellerini seç. İndirme yapılmaz.","Lokalen Jan-Zugang und Audiomodelle wählen. Es wird nichts heruntergeladen."),xalign=0,wrap=True))
            self.url = Gtk.Entry(text=self.cfg["endpoint"],placeholder_text="Jan · HTTP loopback URL")
            models.append(self.url)
            self.reload = self.button(models,tr("Discover Jan models","Jan modellerini bul","Jan-Modelle suchen"),self.discover)
            self.models = Gtk.ComboBoxText()
            models.append(self.models)
            self.models.connect("changed", self.select_model)
            self.url.connect("changed", lambda *_:self.forget_inventory())
            self.labels, self.pickers, self.previews = {}, [], {}
            self.preview_locales = {}
            for key, caption in [("stt","STT · Whisper (.bin)"),("tr","Piper · Türkçe"),("en","Piper · English"),("de","Piper · Deutsch")]:
                row = Gtk.Box(spacing=8)
                models.append(row)
                btn = self.button(row, caption, lambda k=key:self.choose(k))
                self.pickers.append(btn)
                self.labels[key] = Gtk.Label(xalign=0, wrap=True)
                row.append(self.labels[key])
                if key != "stt":
                    # A preview speaks a fixed sample in the selected locale only.
                    hear = self.button(row, tr("Preview","Dinle","Anhören"), lambda k=key:self.preview(k))
                    self.previews[hear] = key
            self.route_note = Gtk.Label(xalign=0, wrap=True)
            models.append(self.route_note)
            # What this machine can do, stated before a turn fails.
            self.readiness_note = Gtk.Label(xalign=0, wrap=True)
            models.append(self.readiness_note)
            # The per-task table: one row per task, with its effective route or
            # the bounded reason why none is used, never an implied road.
            self.route_rows = {}
            for task, caption in (("stt", tr("Speech to text", "Yazıya çevirme", "Sprache zu Text")),
                                  ("llm", tr("Reasoning", "Muhakeme", "Schlussfolgerung")),
                                  ("tts", tr("Speech", "Konuşma", "Sprache"))):
                row = Gtk.Box(spacing=8)
                models.append(row)
                row.append(Gtk.Label(label=caption, xalign=0))
                self.route_rows[task] = Gtk.Label(xalign=0, wrap=True, hexpand=True)
                row.append(self.route_rows[task])
            self.test_chat_button = self.button(models, tr("Test selected model","Seçilen modeli dene","Ausgewähltes Modell testen"), self.test_chat)
            self.transcribe_button = self.button(models, tr("Transcribe a recording","Kayıttan yazıya çevir","Aufnahme transkribieren"), self.transcribe_recording)
            self.test_note = Gtk.Label(xalign=0, wrap=True)
            models.append(self.test_note)
            self.update_labels()
            self.store = profiles.Store(profiles.profile_dir())
            self.policy_rows = {}
            self.market = market.Registry(market.market_dir())
            self.market_row_ids = {}
            # No download transport ships with this release; a future,
            # separately authorized transport is plugged in here.
            self.transport = None
            self.catalog = market.Catalog()
            self.catalog_rows = {}
            self.downloads = []
            self.download_entries = {}
            self.restore_download_queue()
            self.build_downloads_page()
            self.build_profiles_page()
            self.build_catalog_page()
            self.build_market_page()
            try:
                profiles.ensure_default(self.store)
                wanted = self.cfg.get("profile") or ""
                active = None
                if wanted:
                    candidate = self.store.load(wanted)
                    if candidate.state == "active":
                        active = candidate
                if active is None:
                    for pid in self.store.ids():
                        found = self.store.load(pid)
                        if found.state == "active":
                            active = found
                            break
                if active is None:
                    raise Hold("no_active_profile")
                self.activate_profile(active.id, quiet=True)
            except Hold as exc:
                read_error = (read_error + " " if read_error else "") + str(exc)
            self.win.connect("close-request",self.close)
            self.set_state("ready",read_error or tr("Open Installed models to connect.","Bağlantı için Kurulu modeller’i aç.","Zum Verbinden Installierte Modelle öffnen."))
            self.bind_keys()
            self.board = tasks.Board()
            self.apply_resource_profile()
            self.tools_available = None
            self.build_files_page()
            self.build_settings_page()
            self.build_resources_page()
            self.build_sources_page()
            self.build_agents_page()
            self.turn_note = ""
            self.build_activity_page()
            self.analysis = AnalysisView(self, chat, Gtk, GLib, tr, lang)
            self.refresh()
            self.win.present()

        def apply_text_scale(self):
            """Apply one stylesheet for the stored scale, and nothing else.

            Reduced motion is not a switch here: no scale writes a transition,
            an animation or a transform, so the interface has none at any size.
            """
            display = self.win.get_display()
            if self.scale_provider is not None:
                Gtk.StyleContext.remove_provider_for_display(display, self.scale_provider)
            provider = Gtk.CssProvider()
            self.scale_provider = provider
            try:
                provider.load_from_data(a11y.css(self.cfg.get("text_scale", "normal"),
                                                 self.look).encode("utf-8"))
                Gtk.StyleContext.add_provider_for_display(display, provider,
                                                          Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            except (Hold, ValueError):
                # A scale the client cannot honour is a bounded reason.
                self.scale_provider = None
                self.status.set_text("invalid_text_scale")

        def bind_keys(self):
            """Bind the stated shortcuts, and record what was bound.

            A binding is never silent: the table lives in ``keys`` and is
            exposed here so a test can assert it, and a control that does not
            exist yet is skipped rather than bound to nothing.
            """
            self.shortcuts = []
            targets = {"entry": self.entry, "search": self.search_entry,
                       "window": self.win}
            for name, caption, target, trigger, mods in keys.BINDINGS:
                widget = targets.get(target)
                if widget is None:
                    continue
                controller = Gtk.ShortcutController()
                controller.set_scope(Gtk.ShortcutScope.GLOBAL)
                controller.add_shortcut(Gtk.Shortcut(
                    trigger=Gtk.ShortcutTrigger.parse_string(trigger),
                    action=Gtk.CallbackAction.new(getattr(self, "run_" + name))))
                widget.add_controller(controller)
                self.shortcuts.append(name)

        def run_send(self, *_):
            """Send, but never while a turn is running or the field is empty."""
            if self.busy or not self.entry.get_text().strip():
                return True
            self.send_text()
            return True

        def run_focus_search(self, *_):
            self.search_entry.grab_focus()
            return True

        def run_cancel_turn(self, *_):
            if self.busy or self.owned_picker is not None or (hasattr(self, "analysis") and self.analysis.dialog is not None):
                self.interrupt(False)
            elif self.search_entry.get_text().strip():
                self.clear_search()
            return True

        def run_hard_stop(self, *_):
            if self.busy:
                self.interrupt(True)
            elif hasattr(self, "analysis"):
                self.hard_stop()
            return True

        def run_attach(self, *_):
            if not self.busy:
                self.attach_file()
            return True

        @staticmethod
        def button(parent, label, callback):
            b = Gtk.Button(label=label)
            b.connect("clicked",lambda *_: callback())
            parent.append(b)
            return b

        def set_state(self, state, detail=""):
            self.stage = state
            self.status.set_text(detail or state.replace("_"," ").capitalize())
            self.refresh()

        def build_profiles_page(self):
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
            view = Gtk.ScrolledWindow(vexpand=True)
            view.set_child(page)
            self.stack.add_titled(view,"profiles",tr("Profiles","Profiller","Profile"))
            self.profile_note = Gtk.Label(xalign=0,wrap=True)
            page.append(self.profile_note)
            self.profile_list = Gtk.ListBox()
            self.profile_list.connect("row-selected", lambda *_: self.refresh_profile_buttons())
            page.append(self.profile_list)
            controls = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
            page.append(controls)
            self.profile_activate = self.button(controls,tr("Activate","Etkinleştir","Aktivieren"),self.activate_selected)
            self.profile_switch = self.button(controls,tr("Switch mode","Mod değiştir","Modus wechseln"),self.switch_selected)
            self.profile_clone = self.button(controls,tr("Clone to CUSTOM","CUSTOM kopyala","Als CUSTOM klonen"),self.clone_selected)
            self.profile_delete = self.button(controls,tr("Delete","Sil","Löschen"),self.delete_selected)
            self.audit_list = Gtk.Label(xalign=0, wrap=True)
            page.append(self.audit_list)
            # Filters narrow what the log already holds; they never invent a row.
            filters = Gtk.Box(spacing=8)
            page.append(filters)
            self.audit_action = Gtk.ComboBoxText()
            self.audit_decision = Gtk.ComboBoxText()
            filters.append(self.audit_action)
            filters.append(self.audit_decision)
            self.audit_building = False
            self.audit_action.connect("changed", lambda *_: self.refresh_audit())
            self.audit_decision.connect("changed", lambda *_: self.refresh_audit())
            self.refresh_audit()
            picker = Gtk.ComboBoxText()
            for key in profiles.TEMPLATES:
                picker.append_text(key)
            picker.set_active(0)
            page.append(picker)
            self.profile_template = picker
            self.profile_new = self.button(page,tr("Create from template","Şablondan oluştur","Aus Vorlage erstellen"),self.create_template)
            self.profile_import = self.button(page,tr("Import file","Dosyadan al","Datei importieren"),self.import_profile)
            self.profile_export = self.button(page,tr("Export copy","Kopya ver","Kopie exportieren"),self.export_selected)
            self.refresh_profiles()

        def selected_profile(self):
            row = self.profile_list.get_selected_row()
            return self.policy_rows.get(row) if row is not None else None

        def audit_filters(self):
            """The filters actually chosen, or None when a box says "every".

            An empty log offers no actions: a filter is a name the log can
            show, never a free-text guess.
            """
            action = self.audit_action.get_active_text() if self.audit_action else None
            decision = self.audit_decision.get_active_text() if self.audit_decision else None
            if action in (None, "", "every", "all"):
                action = None
            if decision in (None, "", "every", "all"):
                decision = None
            return action, decision

        def refresh_audit(self):
            """Rebuild the filter choices from the log, then show the rows.

            Rebuilding a box changes its selection, which would call this again;
            the guard keeps one pass.
            """
            if self.audit_building:
                return
            self.audit_building = True
            try:
                self.rebuild_audit_filters()
                self.show_audit_rows()
            finally:
                self.audit_building = False

        def rebuild_audit_filters(self):
            chosen, decision = self.audit_filters()
            for box, values, keep in ((self.audit_action,
                                        ("all",) + profiles.audit_actions(self.store.root),
                                        chosen),
                                      (self.audit_decision, ("all", "allow", "deny", "shown"),
                                       decision)):
                box.remove_all()
                for value in values:
                    box.append_text(value)
                box.set_active(max(0, values.index(keep) if keep in values else 0))

        def show_audit_rows(self):
            chosen, decision = self.audit_filters()
            try:
                rows = profiles.read_audit(self.store.root, 40, action=chosen,
                                           decision=decision)
            except Hold:
                rows = ()
            if not rows:
                # An empty log and a filter that matches nothing are different
                # facts; the reader is told which one holds.
                if chosen is None and decision is None:
                    self.audit_list.set_text(tr("No policy decisions recorded yet.",
                                                "Henüz politika kararı kaydedilmedi.",
                                                "Noch keine Richtlinienentscheidungen."))
                else:
                    self.audit_list.set_text(tr("No decision matches this filter.",
                                                "Bu filtreyle eşleşen karar yok.",
                                                "Keine Entscheidung passt zu diesem Filter."))
                return
            self.audit_list.set_text("\n".join(
                "%s %s %s %s %s" % (time.strftime("%H:%M:%S", time.localtime(r["at"])),
                                    r["decision"], r["action"], "/".join(r["scope"]) or "-",
                                    r["reason"]) for r in rows))

        def refresh_profile_buttons(self):
            chosen = self.selected_profile()
            for w in (self.profile_activate, self.profile_clone, self.profile_delete,
                      self.profile_export, self.profile_switch):
                w.set_sensitive(not self.busy and chosen is not None)
            self.profile_list.set_sensitive(not self.busy)

        def refresh_profiles(self):
            for row in list(self.policy_rows):
                self.profile_list.remove(row)
            self.policy_rows = {}
            for pid in self.store.ids():
                entry = self.store.load(pid)
                label = Gtk.Label(label="%s · %s · %s" % (entry.name, entry.based_on_mode, entry.state),xalign=0)
                row = Gtk.ListBoxRow()
                row.set_child(label)
                self.profile_list.append(row)
                self.policy_rows[row] = entry
            active = self.cfg.get("profile") or ""
            for row, entry in self.policy_rows.items():
                if entry.id == active:
                    self.profile_list.select_row(row)
                    break
            self.refresh_profile_buttons()

        def picker_context(self):
            profile = self.active_profile()
            if profile.state != "active":
                raise Hold("profile_not_active")
            return (profile.id, profile.version, profile.based_on_mode, self.session.epoch)

        def dismiss_picker(self):
            dialog, handler = self.owned_picker, self.owned_picker_handler
            self.owned_picker = self.owned_picker_handler = None
            if dialog is not None:
                if handler is not None:
                    dialog.disconnect(handler)
                dialog.destroy()

        def show_owned_picker(self, dialog, response):
            """Keep a strong reference and reject late or cross-context callbacks."""
            if (self.busy or self.closing or self.owned_picker is not None
                    or (hasattr(self, "analysis") and self.analysis.dialog is not None)):
                dialog.destroy()
                return
            try:
                context = self.picker_context()
            except Hold as exc:
                dialog.destroy()
                self.set_state("ready", str(exc))
                return
            self.owned_picker = dialog
            dialog.set_modal(True)

            def dispatch(d, res):
                if d is not self.owned_picker:
                    return
                handler = self.owned_picker_handler
                self.owned_picker = self.owned_picker_handler = None
                if handler is not None:
                    d.disconnect(handler)
                try:
                    valid = (not self.busy and not self.closing
                             and self.picker_context() == context)
                except Hold:
                    valid = False
                if not valid:
                    d.destroy()
                    self.set_state("ready", "picker_context_changed")
                    return
                # Each original response owns destruction in its finally block.
                try:
                    response(d, res)
                except Exception:
                    self.set_state("ready", "local_operation_failed")
                self.refresh()

            self.owned_picker_handler = dialog.connect("response", dispatch)
            dialog.show()
            self.refresh()

        def clear_attachment(self):
            self.attachment = self.attachment_context = None
            self.attachment_note.set_text("")

        def attachment_route(self):
            profile = self.active_profile()
            return (profile.id, profile.version, profile.based_on_mode,
                    self.url.get_text().strip(), self.cfg["llm"])

        def activate_profile(self, profile_id, quiet=False):
            entry = self.store.load(profile_id).checked()
            if entry.state != "active":
                raise Hold("profile_not_active")
            self.dismiss_picker()
            self.clear_attachment()
            if hasattr(self, "analysis"):
                self.analysis.reset()
            self.cfg["profile"] = entry.id
            self.policy = profiles.effective(entry)
            self.save()
            self.profile_note.set_text("%s · %s · %s" % (entry.name, entry.based_on_mode,
                                                          profiles.MODE_TEXT[entry.based_on_mode]))
            if not quiet:
                self.set_state("ready", tr("Profile active.","Profil etkin.","Profil aktiv."))
            self.refresh()
            self.refresh_profiles()
            return entry

        def switch_selected(self):
            """Switch to a profile, with explicit consent for added privileges.

            A spoken command is never a privileged approval; only this trusted
            UI can grant an escalation.
            """
            chosen = self.selected_profile()
            if chosen is None or self.busy:
                return
            try:
                self.switch_profile(chosen.id)
            except Hold as exc:
                self.set_state("ready", str(exc))

        def switch_profile(self, profile_id):
            target = self.store.load(profile_id).checked()
            try:
                current = self.store.load(self.cfg["profile"]).checked()
            except (Hold, KeyError):
                current = None
            if current is None:
                extra = profiles.granted(target)
            else:
                extra = profiles.escalation(current, target)
            if not extra:
                self.activate_profile(profile_id)
                return None
            return self.confirm_escalation(target, extra)

        def confirm_escalation(self, target, extra):
            """Show exactly which privileges the switch would add."""
            lines = profiles.describe(extra)
            dialog = Gtk.Window(title=tr("Confirm added privileges",
                                         "Eklenen yetkileri onayla",
                                         "Hinzugefügte Rechte bestätigen"),
                                modal=True, transient_for=self.win)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            box.set_margin_top(16); box.set_margin_bottom(16)
            box.set_margin_start(16); box.set_margin_end(16)
            dialog.set_child(box)
            box.append(Gtk.Label(label=tr("Switching to %s would add:"
                                          % target.name,
                                          "%s profiline geçiş şunları ekler:" % target.name,
                                          "Wechsel zu %s würde hinzufügen:" % target.name),
                                 xalign=0, wrap=True))
            for line in lines:
                box.append(Gtk.Label(label="• " + line, xalign=0, wrap=True))
            row = Gtk.Box(spacing=8)
            box.append(row)
            self.button(row, tr("Cancel", "Vazgeç", "Abbrechen"),
                        lambda: self.cancel_escalation(target, dialog))
            self.button(row, tr("Confirm switch", "Geçişi onayla", "Wechsel bestätigen"),
                        lambda: self.apply_escalation(target, dialog, lines))
            dialog.connect("close-request", lambda *_: self.forget_confirmation(dialog) or False)
            self.confirmations.append(dialog)
            self.refresh_state_row()
            dialog.present()
            return dialog

        def forget_confirmation(self, dialog):
            if dialog in self.confirmations:
                self.confirmations.remove(dialog)
            self.refresh_state_row()
            return False

        def apply_escalation(self, target, dialog, lines):
            dialog.close()
            try:
                self.activate_profile(target.id)
            except Hold as exc:
                self.set_state("ready", str(exc))
                return
            what = "switching to %s adds %s" % (target.name, ", ".join(lines))
            self.receipt("approved", what)
            self.set_state("ready", tr("Mode switched with consent.",
                                       "Onayla mod değiştirildi.",
                                       "Modus mit Bestätigung gewechselt."))

        def cancel_escalation(self, target, dialog):
            dialog.close()
            # A cancelled approval changes nothing and says exactly that.
            self.receipt("not_approved", "switching to %s; no privileges were added"
                         % target.name)
            self.set_state("ready", tr("Switch cancelled.",
                                       "Geçiş vazgeçildi.",
                                       "Wechsel abgebrochen."))

        def activate_selected(self):
            chosen = self.selected_profile()
            if chosen is None or self.busy:
                return
            try:
                self.activate_profile(chosen.id)
            except Hold as exc:
                self.set_state("ready", str(exc))

        def clone_selected(self):
            chosen = self.selected_profile()
            if chosen is None or self.busy:
                return
            try:
                clone = profiles.clone_to_custom(chosen, chosen.name + " copy")
                self.store.save(clone)
                self.activate_profile(clone.id)
            except Hold as exc:
                self.set_state("ready", str(exc))

        def delete_selected(self):
            chosen = self.selected_profile()
            if chosen is None or self.busy or chosen.id == self.cfg.get("profile"):
                return
            try:
                (self.store.root / (chosen.id + ".json")).unlink(missing_ok=True)
                self.refresh_profiles()
            except OSError:
                self.set_state("ready", "profile_not_deletable")

        def create_template(self):
            if self.busy:
                return
            key = self.profile_template.get_active_text()
            try:
                entry = profiles.template(key)
                if (self.store.root / (entry.id + ".json")).is_file():
                    raise Hold("profile_already_exists")
                self.store.save(entry)
                self.activate_profile(entry.id)
            except Hold as exc:
                self.set_state("ready", str(exc))

        def import_profile_text(self, raw):
            """An imported copy arrives pending review, never activated."""
            entry = profiles.Store._parse(raw)
            clean = profiles.sanitized(entry)
            if (self.store.root / (clean.id + ".json")).is_file():
                raise Hold("profile_already_exists")
            self.store.save(clean)
            self.refresh_profiles()
            return clean

        def import_profile(self):
            if self.busy:
                return
            dialog = Gtk.FileChooserNative.new("Import a profile copy", self.win,
                                               Gtk.FileChooserAction.OPEN, "Select", "Cancel")
            filt = Gtk.FileFilter()
            filt.add_pattern("*.json")
            dialog.add_filter(filt)
            def response(d, res):
                try:
                    if res != Gtk.ResponseType.ACCEPT:
                        return
                    file = d.get_file()
                    path = file.get_path() if file else None
                    raw = Path(path).read_text(encoding="utf-8") if path else ""
                    self.import_profile_text(raw)
                    self.set_state("ready", "profile_imported_pending_review")
                except (Hold, OSError, ValueError):
                    self.set_state("ready", "profile_import_rejected")
                finally:
                    d.destroy()
            self.show_owned_picker(dialog, response)

        def export_selected(self):
            chosen = self.selected_profile()
            if chosen is None or self.busy:
                return None
            try:
                entry = self.store.load(chosen.id)
                # An export copy carries the envelope, never credentials or grants.
                return profiles.Store._dump(replace(profiles.sanitized(entry), state=entry.state))
            except (Hold, OSError, ValueError):
                self.set_state("ready", "profile_export_rejected")
                return None

        def route_selection(self):
            """The local route this client actually has for each task."""
            out = {}
            if self.cfg["stt"] and Path(self.cfg["stt"]).is_file():
                out["stt"] = routing.Route("stt", Path(self.cfg["stt"]).name, "local",
                                           "jan", self.cfg["endpoint"])
            if self.cfg["llm"] and self.cfg["llm"] in self.inventory:
                out["llm"] = routing.Route("llm", self.cfg["llm"], "local",
                                           "jan", self.cfg["endpoint"])
            speaking = [lang for lang in ("tr", "en", "de") if self.cfg["voices"].get(lang)]
            if speaking:
                out["tts"] = routing.Route("tts", ",".join(speaking), "local",
                                           "jan", self.cfg["endpoint"])
            return out

        def refresh_route_note(self):
            """State the effective route for the selected model, and the mode's limits."""
            try:
                profile = self.active_profile()
            except Hold:
                self.route_note.set_text("")
                return
            model = self.cfg["llm"]
            if model and model in self.inventory:
                route, reason = routing.select(profile, routing.Preference(
                    "llm", (routing.Route("llm", model, "local", "jan", self.cfg["endpoint"]),)))
                head = tr("Route: %s via this computer (%s)." % (route.model_id, reason),
                          "Rota: %s bu bilgisayar üzerinden (%s)." % (route.model_id, reason),
                          "Route: %s über diesen Computer (%s)." % (route.model_id, reason))
            else:
                head = tr("Route: no local model selected.", "Rota: yerel model seçili değil.",
                          "Route: kein lokales Modell ausgewählt.")
            if profile.based_on_mode == "LOCAL_ONLY":
                head += " " + tr("This mode never routes to a provider.",
                                 "Bu mod asla sağlayıcıya yönlendirmez.",
                                 "Dieser Modus leitet nie an einen Anbieter um.")
            elif profile.allowed_providers:
                head += " " + tr("Cloud routes need an approved provider.",
                                 "Bulut rotaları onaylı sağlayıcı gerektirir.",
                                 "Cloud-Routen erfordern einen genehmigten Anbieter.")
            self.route_note.set_text(head)

        def refresh_route_rows(self):
            """State every task's route, and what the mode cannot promise."""
            try:
                profile = self.active_profile()
                selection = self.route_selection()
            except Hold:
                for label in self.route_rows.values():
                    label.set_text("")
                return
            caption = {"local_route": tr("local", "yerel", "lokal"),
                       "local_only_forbids_cloud_route": tr("local mode refuses a cloud route",
                                                            "yerel mod bulut rotasını reddeder",
                                                            "lokaler Modus lehnt eine Cloud-Route ab"),
                       "approved_provider_required": tr("approved provider required",
                                                        "onaylı sağlayıcı gerekiyor",
                                                        "genehmigter Anbieter erforderlich"),
                       "no_route_configured": tr("no route configured",
                                                 "rota yapılandırılmamış",
                                                 "keine Route konfiguriert")}
            for row in routing.plan(profile, selection):
                self.route_rows[row.task].set_text(
                    caption.get(row.reason, row.reason) if row.route is None
                    else tr("%s on this computer" % row.route.model_id,
                            "%s bu bilgisayarda" % row.route.model_id,
                            "%s auf diesem Computer" % row.route.model_id))

        def run_search(self):
            """Search the local conversation; results are snippets, bounded.

            Nothing leaves this machine, and the field is disabled while a turn
            runs so a search can never race a reply.
            """
            query = self.search_entry.get_text().strip()
            rows, item = [], self.search_results.get_first_child()
            while item is not None:
                nxt = item.get_next_sibling()
                self.search_results.remove(item)
                item = nxt
            if not query:
                self.search_note.set_text("")
                return
            try:
                self.search_matches = search.find(self.session.history, query)
            except Hold as exc:
                self.search_matches = ()
                self.search_note.set_text(str(exc))
                return
            # Only a query that ran is remembered, and only once it ran.
            self.remember_search(query)
            for index, role, text in self.search_matches:
                row = Gtk.ListBoxRow()
                label = Gtk.Label(label="%s · %s" % (role, text), xalign=0, wrap=True)
                label.set_size_request(420, -1)
                row.set_child(label)
                row.match = (index, role, text)
                row.set_activatable(True)
                row.connect("activate", lambda *_: self.show_match())
                self.search_results.append(row)
            self.search_note.set_text(tr("%d matches for \"%s\"" % (len(self.search_matches), query),
                                         "\"%s\" için %d eşleşme" % (query, len(self.search_matches)),
                                         "%d Treffer für \"%s\"" % (len(self.search_matches), query)))

        def show_match(self):
            """Show the selected match in the detail line, never inline."""
            row = self.search_results.get_selected_row()
            if row is None or not hasattr(row, "match"):
                return
            index, role, text = row.match
            self.search_note.set_text(tr("Message %d (%s): %s" % (index + 1, role, text),
                                         "Mesaj %d (%s): %s" % (index + 1, role, text),
                                         "Nachricht %d (%s): %s" % (index + 1, role, text)))

        def refresh_search_history(self):
            """Show the remembered queries, newest first, re-runnable.

            The history is local and bounded; forgetting it clears the store,
            not just the row.
            """
            rows, item = [], self.search_history.get_first_child()
            while item is not None:
                nxt = item.get_next_sibling()
                self.search_history.remove(item)
                item = nxt
            for query in self.cfg.get("search_history", []):
                row = Gtk.ListBoxRow()
                row.query = query
                row.set_child(Gtk.Label(label=query, xalign=0, wrap=True))
                row.set_activatable(True)
                row.connect("activate", lambda *_: self.search_entry.set_text(row.query))
                self.search_history.append(row)
            self.search_history_label.set_visible(bool(self.cfg.get("search_history")))
            self.search_forget.set_sensitive(bool(self.cfg.get("search_history")))

        def remember_search(self, query):
            """Remember a query that was actually searched for."""
            try:
                self.cfg["search_history"] = list(queries.remember(
                    tuple(self.cfg.get("search_history", [])), query))
            except Hold:
                return
            self.save()
            self.refresh_search_history()

        def forget_searches(self):
            self.cfg["search_history"] = list(queries.clear())
            self.save()
            self.refresh_search_history()

        def export_conversation(self):
            """Write the conversation to a file the owner chooses.

            The file holds messages with their speaker and nothing else — no
            endpoint, no model path, no profile rule, no decision.
            """
            if self.busy or not self.session.history:
                self.set_state("ready", tr("Nothing to export yet.",
                                           "Henüz aktarılacak bir şey yok.",
                                           "Noch nichts zu exportieren."))
                return None
            dialog = Gtk.FileChooserNative.new("Export this conversation", self.win,
                                               Gtk.FileChooserAction.SAVE, "Export", "Cancel")
            dialog.set_current_name("tihulu-jarvis-conversation.txt")

            def response(d, res):
                try:
                    if res != Gtk.ResponseType.ACCEPT or self.busy:
                        return
                    file = d.get_file()
                    path = file.get_path() if file else None
                    self.write_export(path)
                finally:
                    d.destroy()
            self.show_owned_picker(dialog, response)
            return None

        def write_export(self, path):
            try:
                text = transcript.render(self.session.history, started=1.0)
                target = transcript.write(path, text)
            except Hold as exc:
                self.set_state("ready", str(exc))
                return None
            self.set_state("ready", tr("Conversation exported to a local file.",
                                       "Sohbet yerel bir dosyaya aktarıldı.",
                                       "Gespräch in eine lokale Datei exportiert."))
            self.receipt("performed", "exported the conversation to a local file")
            return target

        def clear_search(self):
            self.search_entry.set_text("")
            self.search_entry.grab_focus()

        def topbar_text(self):
            """Mode, profile, model and boundary, as one line of words.

            The line names what is active right now; an absent choice is said
            to be absent rather than left blank.
            """
            try:
                profile = self.active_profile()
            except Hold:
                mode, name = "", tr("no active profile", "etkin profil yok",
                                    "kein aktives Profil")
            else:
                mode, name = profile.based_on_mode, profile.name or profile.id
            model = self.cfg["llm"] or tr("no model selected", "model seçili değil",
                                          "kein Modell ausgewählt")
            boundary = tr("content stays on this computer",
                          "içerik bu bilgisayarda kalır",
                          "Inhalt bleibt auf diesem Computer")
            try:
                data = disclosure.facts(profile, self.url.get_text().strip())
            except (Hold, UnboundLocalError):
                data = None
            if data is not None and data["boundary"] != disclosure.STAYS_LOCAL:
                boundary = tr("cloud reasoning permitted",
                              "bulut çıkarımına izinli",
                              "Cloud-Schlussfolgerung erlaubt")
            parts = [mode or tr("no mode", "mod yok", "kein Modus"), name, model, boundary]
            if data is not None and data["endpoint"] != "loopback":
                # The boundary is about the profile; the endpoint is its own
                # fact, and a build that cannot use it says so rather than
                # leaving a reader to assume it will work.
                parts.append(tr("endpoint not a loopback address",
                                "uç nokta geri döngü adresi değil",
                                "Endpunkt ist keine Loopback-Adresse"))
            return " · ".join(parts)

        def refresh_state_row(self):
            """State the live condition in words: local or cloud, microphone,
            privileged tools, a pending approval, connection and idle state."""
            try:
                profile = self.active_profile()
                data = disclosure.facts(profile, self.url.get_text().strip())
                grants = profiles.granted(profile)
            except Hold:
                self.state_row.set_text("")
                self.topbar_note.set_text(self.topbar_text())
                return
            words = states.tokens(data, self.stage, grants,
                                  bool(self.confirmations),
                                  self.cfg["llm"] in self.inventory,
                                  bool(getattr(self, "mute", None)
                                       and self.mute.get_active()))
            caption = {"local": tr("local", "yerel", "lokal"),
                       "cloud": tr("cloud permitted", "buluta izinli", "Cloud erlaubt"),
                       "listening": tr("microphone listening", "mikrofon dinliyor", "Mikrofon hört zu"),
                       "transcribing": tr("microphone transcribing", "mikrofon yazıya çeviriyor", "Mikrofon transkribiert"),
                       "idle": tr("microphone idle", "mikrofon boşta", "Mikrofon untätig"),
                       "privileged_tool": tr("privileged tools may run", "ayrıcalıklı araçlar çalabilir", "Privilegierte Werkzeuge können laufen"),
                       "approval_pending": tr("approval pending", "onay bekliyor", "Bestätigung ausstehend"),
                       "muted": tr("audio output muted", "ses çıkışı sessiz", "Audioausgabe stumm"),
                       "offline": tr("no local model", "yerel model yok", "kein lokales Modell")}
            # A threshold-free statement stays true whichever refresh shows it,
            # so the row never has to be repainted by a timer to be honest.
            idle = self.bridge.idle_for() if self.bridge is not None else 0
            if not self.busy:
                words = words + (("waiting",))
            caption["waiting"] = tr("no work pending", "bekleyen iş yok",
                                    "keine Arbeit ausstehend")
            text = tr("State: ", "Durum: ", "Zustand: ") + " · ".join(
                caption.get(token, token) for token in words)
            self.state_row.set_text(text)
            self.topbar_note.set_text(self.topbar_text())

        def refresh_disclosure(self):
            """State the privacy boundary of a message before it is written.

            The facts come from the active profile and the configured endpoint;
            nothing is contacted and no message is inspected here.
            """
            try:
                profile = self.active_profile()
                endpoint = self.url.get_text().strip()
            except Hold:
                self.disclosure_note.set_text("")
                return
            data = disclosure.facts(profile, endpoint)
            if data["boundary"] == disclosure.CLOUD_WITH_APPROVAL:
                body = tr("Content you write may go to: %s." % ", ".join(data["providers"]),
                          "Yazdığın içerik şuraya gidebilir: %s." % ", ".join(data["providers"]),
                          "Inhalt, den du schreibst, kann an %s gehen." % ", ".join(data["providers"]))
                body += " " + tr("This build only transports to a loopback endpoint, so nothing leaves this computer yet.",
                                 "Bu sürüm yalnızca geri döngü uç noktasına taşır, bu yüzden şimdilik hiçbir şey bilgisayardan çıkmaz.",
                                 "Dieser Build transportiert nur zu einem Loopback-Endpunkt, deshalb verlässt nichts diesen Computer.")
            elif data["boundary"] == disclosure.CLOUD_WITHOUT_APPROVAL:
                body = tr("This mode permits cloud reasoning but no provider is approved, so cloud routes are refused.",
                          "Bu mod bulut çıkarımına izin verir ama onaylı sağlayıcı yok, bulut rotaları reddedilir.",
                          "Dieser Modus erlaubt Cloud-Schlussfolgerung, aber kein Anbieter ist genehmigt, daher werden Cloud-Routen abgelehnt.")
            elif data["endpoint"] != "loopback":
                body = tr("The configured endpoint is not a loopback address; this build refuses it.",
                          "Yapılandırılan uç nokta geri döngü adresi değil; bu sürüm onu reddeder.",
                          "Der konfigurierte Endpunkt ist keine Loopback-Adresse; dieser Build lehnt ihn ab.")
            else:
                body = tr("Content stays on this computer.",
                          "İçerik bu bilgisayarda kalır.",
                          "Inhalt bleibt auf diesem Computer.")
            self.disclosure_note.set_text(tr("Privacy: ", "Gizlilik: ", "Privatsphäre: ") + body)

        def refresh_send(self):
            text = self.entry.get_text()
            connected = self.cfg["llm"] in self.inventory
            direct = analysis_intent.direct(text) is not None
            local = getattr(self, "policy", None) is not None and self.policy.mode == "LOCAL_ONLY"
            self.send.set_sensitive(not self.busy and (connected or (local and direct)))

        def refresh(self):
            connected = self.cfg["llm"] in self.inventory
            # Whether the audio tools exist is asked once per refresh, and the
            # answer is what the readiness row states.
            self.tools_available = audio.tools_ready("arecord", "whisper-cli",
                                                     "piper", "aplay")
            if self.search_entry.get_text().strip():
                # A turn's reply can match a saved query; keep it honest.
                self.run_search()
            # The stated condition must be true while a turn runs, not only
            # while the window is idle: "microphone listening" is exactly the
            # state a reader cannot infer from a disabled control.
            self.refresh_route_note()
            self.refresh_readiness()
            self.refresh_route_rows()
            self.refresh_disclosure()
            self.refresh_state_row()
            self.search_entry.set_sensitive(not self.busy)
            self.search_clear.set_sensitive(not self.busy)
            # An export needs something to export; an empty log offers nothing.
            self.export_chat.set_sensitive(not self.busy and bool(self.session.history))
            self.attach.set_sensitive(not self.busy and self.cfg["llm"] in self.inventory)
            self.entry.set_sensitive(not self.busy)
            self.refresh_send()
            allowed = getattr(self, "policy", None) is not None and \
                self.policy.evaluate("local_audio_device", ("push_to_talk",))[0] == profiles.ALLOW
            self.talk.set_sensitive(not self.busy and connected and allowed \
                and bool(self.cfg["stt"]) and bool(self.cfg["voices"]) \
                and audio.tools_ready("arecord","whisper-cli","piper","aplay"))
            self.stop.set_sensitive(self.stage in ("synthesizing","speaking") and self.playback is not None)
            # Muting is available while a turn is idle or while output plays.
            self.mute.set_sensitive(not self.busy or self.playback is not None)
            self.finish_btn.set_sensitive(self.stage=="listening" and self.finish is not None)
            self.cancel.set_sensitive(self.busy)
            self.hard.set_sensitive(self.busy)
            # The topbar stop is the composer stop, so its state matches.
            self.quick_stop.set_sensitive(self.busy or self.owned_picker is not None)
            if hasattr(self, "analysis"):
                self.analysis.refresh()
                analysis_live = bool(self.analysis.dialog is not None or self.analysis.last or self.owned_picker is not None)
                self.hard.set_sensitive(self.busy or analysis_live)
                self.quick_stop.set_sensitive(self.busy or analysis_live)
            for w in (self.reload,self.url,self.models,*self.pickers,self.test_chat_button,
                      self.transcribe_button,*self.previews):
                w.set_sensitive(not self.busy)
            if not self.busy:
                self.test_chat_button.set_sensitive(connected)
                self.transcribe_button.set_sensitive(connected and bool(self.cfg["stt"]))
                for widget, lang in self.previews.items():
                    # A preview speaks only the locale that is actually selected.
                    widget.set_sensitive(bool(self.cfg["voices"].get(lang)) and allowed
                                         and audio.tools_ready("piper","aplay"))

        def build_resources_page(self):
            """Resource profiles: what the client may use, and what it lets go.

            A profile is stated in words before it is chosen, including what it
            costs: performance keeps a model warm and pays for it in memory,
            resource-optimized releases quickly and pays for it in cold starts.
            Nothing here starts a process or touches a device.
            """
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            view = Gtk.ScrolledWindow(vexpand=True)
            view.set_child(page)
            self.stack.add_titled(view, "resources", tr("Resources", "Kaynaklar", "Ressourcen"))
            row = Gtk.Box(spacing=8)
            page.append(row)
            row.append(Gtk.Label(label=tr("Profile", "Profil", "Profil"), xalign=0))
            self.resource_profile = Gtk.ComboBoxText()
            for value, caption in (("performance", tr("Performance", "Performans", "Leistung")),
                                   ("balance", tr("Balance", "Denge", "Ausgewogen")),
                                   ("resource_optimized", tr("Resource-optimized",
                                                             "Kaynak odaklı",
                                                             "Ressourcenoptimiert")),
                                   ("custom", tr("Custom", "Özel", "Benutzerdefiniert"))):
                self.resource_profile.append_text(caption)
            row.append(self.resource_profile)
            stored = self.cfg.get("resources", {})
            chosen = stored.get("profile", "balance")
            self.resource_profile.set_active(resources.PROFILE_IDS.index(chosen))
            self.resource_profile.connect("changed", self.choose_resource_profile)
            page.append(Gtk.Label(label=tr(
                "Performance keeps a model warm for the session and uses all cores; "
                "Balance releases a warm model after two idle minutes; "
                "Resource-optimized releases after fifteen seconds.",
                "Performans modeli oturum boyunca sıcak tutar ve tüm çekirdekleri kullanır; "
                "Denge iki boşta dakikadan sonra bırakır; "
                "Kaynak odaklı on beş saniyede bırakır.",
                "Leistung hält ein Modell die Sitzung über warm und nutzt alle Kerne; "
                "Ausgewogen gibt nach zwei untätigen Minuten frei; "
                "Ressourcenoptimiert nach fünfzehn Sekunden."),
                xalign=0, wrap=True))
            page.append(Gtk.Label(label=tr("Custom profile", "Özel profil",
                                           "Benutzerdefiniertes Profil"), xalign=0))
            self.resource_threads = Gtk.SpinButton.new_with_range(1, 256, 1)
            self.resource_threads.set_value(self.effective_resource().threads_for(resources.cores()))
            self.resource_threads.connect("value-changed", self.choose_resource_profile)
            page.append(Gtk.Label(label=tr("Threads for a local engine",
                                           "Yerel motor için iş parçacığı",
                                           "Threads für eine lokale Engine"), xalign=0))
            page.append(self.resource_threads)
            self.resource_gpu = Gtk.ComboBoxText()
            for value, caption in (("auto", tr("Automatic", "Otomatik", "Automatisch")),
                                   ("cuda", tr("CUDA (NVIDIA)", "CUDA (NVIDIA)",
                                               "CUDA (NVIDIA)")),
                                   ("vulkan", tr("Vulkan (AMD / other)",
                                                 "Vulkan (AMD / diğer)",
                                                 "Vulkan (AMD / andere)")),
                                   ("cpu", tr("Processor only", "Yalnızca işlemci",
                                              "Nur Prozessor"))):
                self.resource_gpu.append_text(caption)
            stored_gpu = self.effective_resource().gpu
            self.resource_gpu.set_active(resources.GPUS.index(stored_gpu))
            self.resource_gpu.connect("changed", self.choose_resource_profile)
            page.append(Gtk.Label(label=tr("Preferred device",
                                           "Tercih edilen aygıt",
                                           "Bevorzugtes Gerät"), xalign=0))
            page.append(self.resource_gpu)
            self.resource_warm = Gtk.CheckButton(
                label=tr("Keep models warm in the background (costs memory until released)",
                         "Modelleri arka planda sıcak tut (bırakılana kadar bellek harcar)",
                         "Modelle im Hintergrund warm halten (kostet Speicher bis zur Freigabe)"))
            self.resource_warm.set_active(self.effective_resource().keep_warm)
            self.resource_warm.connect("toggled", self.choose_resource_profile)
            page.append(self.resource_warm)
            self.resource_idle = Gtk.SpinButton.new_with_range(0, 86400, 5)
            self.resource_idle.set_value(self.effective_resource().idle_release)
            self.resource_idle.connect("value-changed", self.choose_resource_profile)
            page.append(Gtk.Label(label=tr("Release after this many idle seconds (0 = never)",
                                           "Bu kadar boşta saniyeden sonra bırak (0 = hiç)",
                                           "Nach so vielen untätigen Sekunden freigeben (0 = nie)"),
                                  xalign=0))
            page.append(self.resource_idle)
            self.resource_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.resource_note)
            self.refresh_resources_note()

        def effective_resource(self):
            return resources.effective(self.cfg.get("resources", {}))

        def refresh_resources_note(self):
            profile = self.effective_resource()
            count = resources.cores() or 4
            self.resource_note.set_text(tr(
                "Now: %s — %d threads, %s, %s."
                % ({"performance": "Performance", "balance": "Balance",
                    "resource_optimized": "Resource-optimized", "custom": "Custom"}[profile.id],
                   profile.threads_for(count),
                   {"auto": "automatic device", "cuda": "CUDA", "vulkan": "Vulkan",
                    "cpu": "processor only"}[profile.gpu],
                   "kept warm" if profile.keep_warm else
                   ("never released" if not profile.releases_after()
                    else "released after %d idle seconds" % profile.idle_release)),
                "Şimdi: %s — %d iş parçacığı, %s, %s."
                % (resources.LABELS[profile.id], profile.threads_for(count),
                   {"auto": "otomatik aygıt", "cuda": "CUDA", "vulkan": "Vulkan",
                    "cpu": "yalnızca işlemci"}[profile.gpu],
                   "sıcak tutuluyor" if profile.keep_warm else
                   ("hiç bırakılmıyor" if not profile.releases_after()
                    else "%d boşta saniyeden sonra bırakılıyor" % profile.idle_release)),
                "Jetzt: %s — %d Threads, %s, %s."
                % (resources.LABELS[profile.id], profile.threads_for(count),
                   {"auto": "automatisches Gerät", "cuda": "CUDA", "vulkan": "Vulkan",
                    "cpu": "nur Prozessor"}[profile.gpu],
                   "warm gehalten" if profile.keep_warm else
                   ("nie freigegeben" if not profile.releases_after()
                    else "nach %d untätigen Sekunden freigegeben" % profile.idle_release))))
            idle = self.bridge.idle_for() if self.bridge is not None else 0
            if not self.busy:
                # The measured time since the last submitted work; 0 is a true
                # answer immediately after a turn, not a placeholder.
                self.resource_note.set_text(self.resource_note.get_text() + " " + tr(
                    "Last work %ds ago: no engine is running." % idle,
                    "Son iş %d s önce: hiçbir motor çalışmıyor." % idle,
                    "Letzte Arbeit vor %d s: keine Engine läuft." % idle))
            # What the process itself uses, measured on demand rather than
            # polled: the row is refreshed when the owner opens the page.
            mib = usage.resident_mib()
            count = usage.threads()
            if mib is None:
                measured = tr("this process: resident memory is not readable here",
                              "bu süreç: yerleşik bellek burada okunamıyor",
                              "dieser Prozess: residenter Speicher hier nicht lesbar")
            elif count is None:
                measured = tr("this process: %d MiB resident" % mib,
                              "bu süreç: %d MiB yerleşik bellek" % mib,
                              "dieser Prozess: %d MiB resident" % mib)
            else:
                measured = tr("this process: %d MiB resident, %d threads" % (mib, count),
                              "bu süreç: %d MiB yerleşik bellek, %d iş parçacığı"
                              % (mib, count),
                              "dieser Prozess: %d MiB resident, %d Threads" % (mib, count))
            self.resource_note.set_text(self.resource_note.get_text() + " " + measured + ".")

        def choose_resource_profile(self, *_):
            """Apply the chosen profile now; a restart is never required."""
            chosen = resources.PROFILE_IDS[self.resource_profile.get_active()]
            stored = {"profile": chosen}
            if chosen == "custom":
                stored = {"profile": chosen,
                          "threads": int(self.resource_threads.get_value()),
                          "gpu": resources.GPUS[self.resource_gpu.get_active()],
                          "keep_warm": bool(self.resource_warm.get_active()),
                          "idle_release": int(self.resource_idle.get_value())}
            try:
                resources.effective(stored)
                self.cfg["resources"] = stored
            except Hold as exc:
                self.set_state("ready", str(exc))
                return
            self.save()
            self.apply_resource_profile()
            self.refresh_resources_note()
            self.refresh()

        def apply_resource_profile(self):
            """Hand the chosen profile to the voice layer, which starts the
            local engines. A profile chooses threads and the device only — never
            the executable, the flags or the model."""
            try:
                profile = self.effective_resource()
            except Hold:
                audio.configure(None)
                return None
            audio.configure(profile)
            return profile

        def keys_help(self):
            """The bound shortcuts, stated where a reader can find them.

            A binding is never silent: the same table the client binds is what
            the page lists, so a shortcut that is not here is not bound.
            """
            caption = {"send": tr("Send the message", "Mesajı gönder", "Nachricht senden"),
                       "focus_search": tr("Focus the conversation search",
                                          "Sohbet aramasına odaklan", "Gesprächsuche fokussieren"),
                       "cancel_turn": tr("Cancel the current turn", "Geçerli turu iptal et",
                                         "Aktuelle Runde abbrechen"),
                       "hard_stop": tr("Stop everything", "Her şeyi durdur", "Alles stoppen"),
                       "attach": tr("Attach a file", "Dosya ekle", "Datei anhängen")}
            target = {"entry": tr("in the message field", "mesaj alanında", "im Nachrichtenfeld"),
                      "search": tr("in the search field", "arama alanında", "im Suchfeld"),
                      "window": tr("anywhere in the window", "pencerenin her yerinde",
                                   "überall im Fenster")}
            lines = []
            for name, _, where, trigger, _ in keys.BINDINGS:
                if name not in self.shortcuts:
                    # A binding the client could not apply is not listed as if
                    # it were available.
                    continue
                lines.append("%s — %s (%s)" % (trigger, caption[name], target[where]))
            return "\n".join(lines) or tr("No shortcuts are bound in this build.",
                                         "Bu sürümde bağlı kısayol yok.",
                                         "In diesem Build sind keine Tastenkürzel gebunden.")

        def choose_spoken_text(self, *_):
            """Store the choice immediately; a restart is never required."""
            self.cfg["show_spoken_text"] = bool(self.settings_spoken.get_active())
            self.save()

        def choose_text_scale(self, *_):
            """Store and apply the chosen scale immediately, never at restart."""
            try:
                self.cfg["text_scale"] = a11y.checked(
                    "large" if self.settings_scale.get_active() == 1 else "normal")
            except Hold as exc:
                self.set_state("ready", str(exc))
                return
            self.apply_text_scale()
            self.save()

        def save(self):
            try:
                settings.save(self.cfg)
            except (Hold,OSError):
                self.status.set_text("Could not save local choices")

        def forget_inventory(self):
            self.inventory = ()
            self.models.remove_all()
            self.refresh()

        def select_model(self, widget):
            value = widget.get_active_text()
            if value and value in self.inventory:
                self.cfg["llm"] = value
                self.save()
                self.refresh()

        def update_labels(self):
            for key,label in self.labels.items():
                p = self.cfg["stt"] if key=="stt" else self.cfg["voices"].get(key)
                label.set_text(Path(p).name if p else "—")

        def register_choice(self, key, path):
            """Register a truthful card, then remember the local reference."""
            category = "stt" if key == "stt" else "tts"
            engine = "whisper.cpp" if key == "stt" else "piper"
            card = market.from_file(str(audio.selected_file(path, ".bin" if key == "stt" else ".onnx")),
                                    category, engine=engine,
                                    languages=(() if key == "stt" else (key,)))
            if key != "stt":
                # A voice must match the locale it is offered for.
                audio.voice_for(path, key)
            self.market.save_card(card)
            self.cfg.setdefault("market_refs", {})[card.storage_ref] = str(path)
            self.save()
            self.build_market_page()
            return card

        def remember(self, category, value):
            """Record a choice in this profile and in the local settings."""
            profile = self.store.load(self.cfg["profile"]).checked()
            fields = {"llm": "llm_model_id", "stt": "stt_model_id", "tts": "tts_model_id"}
            updated = replace(profile, version=profile.version + 1, **{fields[category]: value})
            self.store.save(updated)
            self.activate_profile(updated.id, quiet=True)

        def use_card(self, model_id):
            """Apply a registered card. Nothing is downloaded or started."""
            card = self.market.card(model_id)
            if card.compatibility_status == "incompatible_format":
                raise Hold("incompatible_model_format")
            if card.category == "llm":
                # A cloud-visible model must be one the endpoint actually serves.
                if model_id not in self.inventory:
                    raise Hold("model_not_in_inventory")
                # The endpoint itself verifies that this model is installed.
                card = self.market.save_card(market.register_installed(card))
                self.cfg["llm"] = model_id
                self.remember("llm", model_id)
                self.save()
                self.refresh()
                return
            # Using a verified import is an explicit activation, not a silent one.
            if card.state == "REGISTERED":
                card = market.advance(card, "TESTABLE")
            if card.state == "TESTABLE":
                card = market.advance(card, market.ACTIVE)
            card = self.market.save_card(card)
            if card.category == "stt":
                path = self.cfg.get("market_refs", {}).get(card.storage_ref)
                if not path:
                    raise Hold("select_local_model")
                self.cfg["stt"] = path
            elif card.category == "tts":
                if len(card.languages) != 1:
                    raise Hold("voice_locale_unverified")
                lang = card.languages[0]
                path = self.cfg.get("market_refs", {}).get(card.storage_ref)
                if not path:
                    raise Hold("select_local_model")
                self.cfg["voices"][lang] = path
            else:
                raise Hold("model_category_not_selectable_here")
            self.save()
            self.update_labels()
            self.refresh()

        def build_market_page(self):
            if getattr(self, "market_list", None) is not None:
                self.market_row_ids = {}
                child = self.market_list.get_first_child()
                while child is not None:
                    nxt = child.get_next_sibling()
                    self.market_list.remove(child)
                    child = nxt
            else:
                page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                view = Gtk.ScrolledWindow(vexpand=True)
                view.set_child(page)
                self.stack.add_titled(view, "market", tr("AI Market","Yapay Zeka Pazarı","KI-Markt"))
                self.market_note = Gtk.Label(xalign=0, wrap=True)
                page.append(self.market_note)
                self.storage_note = Gtk.Label(xalign=0, wrap=True)
                page.append(self.storage_note)
                self.market_filter = Gtk.ComboBoxText()
                for category in market.CATEGORIES:
                    self.market_filter.append_text(category)
                self.market_filter.set_active(0)
                self.market_filter.connect("changed", lambda *_: self.build_market_page())
                page.append(self.market_filter)
                self.market_list = Gtk.ListBox()
                self.market_list.connect("row-selected", lambda *_: self.refresh_market_buttons())
                page.append(self.market_list)
                row = Gtk.Box(spacing=8)
                page.append(row)
                self.market_use = self.button(row, tr("Use selected","Seçileni kullan","Auswahl verwenden"), self.use_selected_card)
                self.market_forget = self.button(row, tr("Unregister","Kaydı sil","Registrierung entfernen"), self.forget_selected_card)
                self.market_fetch = self.button(row, tr("Download","İndir","Herunterladen"), self.download_selected_card)
                self.market_find = self.button(row, tr("Discover installed Jan models","Kurulu Jan modellerini bul","Installierte Jan-Modelle suchen"), self.discover_market)
                self.market_rollback = self.button(row, tr("Roll back","Geri al","Zurücksetzen"), self.rollback_selected_card)
                self.market_update = self.button(row, tr("Update","Güncelle","Aktualisieren"), self.update_selected_card)

            wanted = self.market_filter.get_active_text() or "stt"
            rows = [c for c in self.market.cards() if c.category == wanted]
            for card in sorted(rows, key=lambda c: (c.state, c.model_id)):
                extra = ""
                if card.approved_source() and market.transport_for(card.source_ref) is None:
                    # The card is verifiable but there is no approved transport
                    # to fetch it with; the reason is stated, not left implied.
                    extra = tr(" · no approved transport",
                               " · onaylı taşıma yok",
                               " · kein genehmigter Transport")
                label = Gtk.Label(label="%s · %s · %s · %s · %s%s" % (
                    card.display_name, card.engine, card.artifact_format,
                    card.compatibility_status, card.state, extra), xalign=0, wrap=True)
                item = Gtk.ListBoxRow()
                item.set_child(label)
                item.set_activatable(True)
                item.connect("activate", lambda *_: self.use_selected_card())
                self.market_list.append(item)
                self.market_row_ids[item] = card.model_id
                # The row carries its card's id, so a keyboard activation and a
                # test both reach the same model rather than the label text.
                item.card_id = card.model_id
            self.market_note.set_text(tr(
                "Installed models and voices known to this computer. Unknown values stay unknown/not tested. Import happens in Installed models.",
                "Bu bilgisayarda bilinen kurulu modeller ve sesler. Bilinmeyen değerler bilinmiyor/denenmedi olarak kalır. İçe alma Kurulu modeller bölümünde yapılır.",
                "Bekannte installierte Modelle und Stimmen. Unbekannte Werte bleiben unbekannt/nicht getestet. Import unter Installierte Modelle."))
            self.storage_note.set_text(self.storage_report())
            self.refresh_market_buttons()

        def storage_report(self):
            """Aggregate only what the registry knows; nothing is estimated."""
            summary = market.usage(self.market.cards())
            if not summary.cards:
                return tr("No registered models yet.","Henüz kayıtlı model yok.",
                          "Noch keine registrierten Modelle.")
            unknown = summary.unknown_memory
            tail = tr("memory not estimated for %d items" % unknown,
                      "%d öğe için bellek tahmini yok" % unknown,
                      "für %d Einträge kein Speicherschätzwert" % unknown)
            return "%s · %s" % (summary.report(), tail if unknown else "")

        def build_catalog_page(self):
            if getattr(self, "catalog_list", None) is not None:
                self.refresh_catalog()
                return
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            view = Gtk.ScrolledWindow(vexpand=True)
            view.set_child(page)
            self.stack.add_titled(view, "catalog", tr("Catalog","Katalog","Katalog"))
            self.catalog_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.catalog_note)
            self.catalog_search = Gtk.SearchEntry()
            self.catalog_search.connect("search-changed", lambda *_: self.refresh_catalog())
            page.append(self.catalog_search)
            self.catalog_filter = Gtk.ComboBoxText()
            self.catalog_filter.append_text("")
            for category in market.CATEGORIES:
                self.catalog_filter.append_text(category)
            self.catalog_filter.set_active(0)
            self.catalog_filter.connect("changed", lambda *_: self.refresh_catalog())
            page.append(self.catalog_filter)
            self.catalog_list = Gtk.ListBox()
            self.catalog_list.connect("row-selected", lambda *_: self.refresh_catalog_buttons())
            page.append(self.catalog_list)
            row = Gtk.Box(spacing=8)
            page.append(row)
            self.catalog_add = self.button(row, tr("Add to registry","Kayda ekle","Registrieren"), self.add_catalog_entry)
            self.catalog_approve = self.button(row, tr("Approve download","İndirmeyi onayla","Download genehmigen"), self.approve_download)
            self.catalog_load = self.button(row, tr("Import catalog","Katalog al","Katalog importieren"), self.import_catalog)
            self.catalog_fetch_btn = self.button(page, tr("Fetch catalog","Katalog getir","Katalog abrufen"), self.fetch_catalog)
            self.catalog_url = Gtk.Entry(placeholder_text="https://catalog.example.test/list.json")
            self.catalog_url.connect("activate", lambda *_: self.fetch_catalog())
            page.append(self.catalog_url)
            self.catalog_hosts = Gtk.Entry(text=" ".join(self.cfg.get("allowed_hosts", [])),
                                            placeholder_text="allowed hosts, separated by spaces")
            self.catalog_hosts.connect("changed", lambda *_: self.remember_hosts())
            page.append(self.catalog_hosts)
            self.load_catalog()

        def load_catalog_text(self, text):
            catalog = market.parse_catalog(text)
            self.catalog = catalog
            path = market.catalog_path()
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            os.chmod(path, 0o600)
            self.refresh_catalog()
            return catalog

        def load_catalog(self):
            path = market.catalog_path()
            try:
                self.load_catalog_text(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self.catalog_note.set_text(self.catalog_report())
            except (Hold, OSError, ValueError):
                self.catalog_note.set_text(tr("Catalog unreadable.","Katalog okunamadı.",
                                              "Katalog nicht lesbar."))
            self.refresh_catalog()

        def refresh_catalog(self):
            for item in list(self.catalog_rows):
                self.catalog_list.remove(item)
            self.catalog_rows = {}
            wanted = self.catalog_filter.get_active_text() or ""
            text = self.catalog_search.get_text().strip()
            self.catalog_note.set_text(self.catalog_report())
            for entry in self.catalog.search(category=wanted, text=text):
                label = Gtk.Label(label="%s · %s · %s · %s · %s" % (
                    entry.display_name, entry.category, entry.engine, entry.artifact_format,
                    entry.license_id), xalign=0, wrap=True)
                item = Gtk.ListBoxRow()
                item.set_child(label)
                self.catalog_list.append(item)
                self.catalog_rows[item] = entry
            self.refresh_catalog_buttons()

        def selected_catalog_entry(self):
            row = self.catalog_list.get_selected_row()
            return self.catalog_rows.get(row) if row is not None else None

        def catalog_report(self):
            if not self.catalog.entries:
                return tr("No catalog yet. Import a curated catalog to discover models and voices.",
                          "Henüz katalog yok. Model ve ses keşfetmek için seçilmiş bir katalog al.",
                          "Noch kein Katalog. Kuratierten Katalog importieren, um Modelle zu finden.")
            return tr("%s. An unverified catalog may be inspected, never installed from."
                      % self.catalog.report(),
                      "%s. Doğrulanmamış katalog incelenebilir, kurulum yapılamaz."
                      % self.catalog.report(),
                      "%s. Ein ungeprüfter Katalog darf angesehen, nicht installiert werden."
                      % self.catalog.report())

        def refresh_catalog_buttons(self):
            chosen = self.selected_catalog_entry() is not None
            # An unverified catalog may be inspected, never installed from.
            for w in (self.catalog_add, self.catalog_approve):
                w.set_sensitive(not self.busy and chosen and self.catalog.verified)
            self.catalog_list.set_sensitive(not self.busy)
            self.catalog_load.set_sensitive(not self.busy)

        def add_catalog_entry(self):
            entry = self.selected_catalog_entry()
            if entry is None or self.busy:
                return
            try:
                if not self.catalog.verified:
                    # An unverified catalog may be inspected, never installed from.
                    raise Hold("catalog_not_verified")
                self.market.save_card(entry.checked())
                self.build_market_page()
                self.set_state("ready", tr("Added to registry.","Kayda eklendi.","Registriert."))
            except Hold as exc:
                self.set_state("ready", str(exc))

        def approve_download(self):
            entry = self.selected_catalog_entry()
            if entry is None or self.busy:
                return
            try:
                known = None
                try:
                    known = self.market.card(entry.model_id)
                except Hold:
                    pass
                if known is None:
                    known = self.market.save_card(entry.checked())
                if known.state != "DISCOVERED":
                    raise Hold("lifecycle_transition_rejected:" + known.state + "->REVIEWED")
                approved = replace(known, network_approval="owner-approved-download",
                                   approval_expires_at=1e12)
                self.market.save_card(approved)
                self.build_market_page()
                self.set_state("ready", tr("Download approved for this entry.",
                                           "Bu kayıt için indirme onaylandı.",
                                           "Download für diesen Eintrag genehmigt."))
            except Hold as exc:
                self.set_state("ready", str(exc))

        def build_web_page(self):
            """Approved web targets: reading is one right, submitting another.

            Nothing here fetches at startup, and a page is shown as bounded
            text: no script runs, nothing is stored beyond the preview.
            """
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            view = Gtk.ScrolledWindow(vexpand=True)
            view.set_child(page)
            self.stack.add_titled(view, "web", tr("Approved sites","Onaylı siteler","Genehmigte Seiten"))
            page.append(Gtk.Label(label=tr(
                "A site must be registered before it can be read. Reading is a GET preview; submitting needs a separate right.",
                "Bir site okunmadan önce kaydedilmelidir. Okuma GET önizlemesidir; göndermek ayrı bir izin ister.",
                "Eine Seite muss registriert sein, bevor sie gelesen wird. Lesen ist eine GET-Vorschau; Senden braucht ein eigenes Recht."),
                xalign=0, wrap=True))
            row = Gtk.Box(spacing=8)
            page.append(row)
            self.web_target = Gtk.Entry(hexpand=True,
                                        placeholder_text="https://site.test/path")
            row.append(self.web_target)
            self.web_submit = Gtk.CheckButton(label=tr("allow submitting", "göndermeye izin ver",
                                                       "Senden erlauben"))
            row.append(self.web_submit)
            self.web_add = self.button(row, tr("Register site", "Siteyi kaydet", "Seite registrieren"),
                                       self.register_target)
            self.web_targets = Gtk.ListBox()
            self.web_targets.connect("row-selected", lambda *_: self.refresh_web_buttons())
            page.append(self.web_targets)
            self.web_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.web_note)
            buttons = Gtk.Box(spacing=8)
            page.append(buttons)
            self.web_read = self.button(buttons, tr("Read page (GET)", "Sayfayı oku (GET)",
                                                    "Seite lesen (GET)"), self.read_target)
            self.web_submit_btn = self.button(buttons, tr("Submit (POST)", "Gönder (POST)",
                                                          "Senden (POST)"), self.submit_target)
            self.web_forget = self.button(buttons, tr("Forget site", "Siteyi unut", "Seite vergessen"),
                                          self.forget_target)
            self.refresh_web_page()

        def target_entries(self):
            return web.entries(self.cfg.get("web_targets", []))

        def selected_target(self):
            row = self.web_targets.get_selected_row()
            return row.target if row is not None else None

        def register_target(self):
            raw = self.web_target.get_text().strip()
            try:
                target = web.parse(raw)
                if self.web_submit.get_active():
                    target = web.Target(target.host, target.path, True)
                self.cfg["web_targets"] = web.store(web.register(
                    self.target_entries(), target))
            except Hold as exc:
                self.set_state("ready", str(exc))
                return
            self.save()
            self.web_target.set_text("")
            self.web_submit.set_active(False)
            self.refresh_web_page()

        def forget_target(self):
            target = self.selected_target()
            if target is None:
                return
            self.cfg["web_targets"] = web.store(web.forget(self.target_entries(), target))
            self.save()
            self.refresh_web_page()

        def refresh_web_page(self):
            rows, item = [], self.web_targets.get_first_child()
            while item is not None:
                nxt = item.get_next_sibling()
                self.web_targets.remove(item)
                item = nxt
            for target in self.target_entries():
                row = Gtk.ListBoxRow()
                row.target = target
                row.set_activatable(True)
                label = Gtk.Label(label=target.url + (" · submits allowed" if target.submit else ""),
                                  xalign=0, wrap=True)
                row.set_child(label)
                self.web_targets.append(row)
            self.refresh_web_buttons()

        def refresh_web_buttons(self):
            chosen = self.selected_target()
            self.web_read.set_sensitive(not self.busy and chosen is not None)
            self.web_submit_btn.set_sensitive(not self.busy and chosen is not None)
            self.web_forget.set_sensitive(chosen is not None)

        def read_target(self):
            target = self.selected_target()
            if target is None or self.busy:
                return
            try:
                method, url, reason = web.plan(target)
            except Hold as exc:
                self.web_note.set_text(str(exc))
                return
            self.require_tools(("browser_navigate", ("approved_target",)))
            self.web_note.set_text(tr("Reading %s (%s)…" % (url, method),
                                      "%s okunuyor (%s)…" % (url, method),
                                      "%s wird gelesen (%s)…" % (url, method)))

        def submit_target(self):
            target = self.selected_target()
            if target is None or self.busy:
                return
            try:
                web.plan(target, submit=True)
            except Hold as exc:
                # Submitting without the separate right is refused, and said.
                self.web_note.set_text(str(exc))
                self.receipt("refused", "submitting to %s (submission_not_approved)" % target.url)
                return
            self.require_tools(("browser_navigate", ("submission",)))
            self.web_note.set_text(tr("Submitting to %s (POST)…" % target.url,
                                      "%s adresine gönderiliyor (POST)…" % target.url,
                                      "Senden an %s (POST)…" % target.url))

        def build_activity_page(self):
            """The tasks this client runs, stated without a guessed percentage.

            A task names its kind, label, state, elapsed time and the leases it
            holds. Progress is bytes or nothing: a turn has no bytes to count.
            """
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            view = Gtk.ScrolledWindow(vexpand=True)
            view.set_child(page)
            self.stack.add_titled(view, "activity", tr("Activity","Etkinlik","Aktivität"))
            self.activity_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.activity_note)
            self.activity_list = Gtk.ListBox()
            page.append(self.activity_list)
            controls = Gtk.Box(spacing=8)
            page.append(controls)
            self.activity_stop = self.button(controls, tr("Stop the selected task",
                                                          "Seçili görevi durdur",
                                                          "Ausgewählte Aufgabe stoppen"),
                                             self.stop_task)
            self.refresh_activity()

        def turn_label(self):
            """What the running turn is about, for the board and for a reader.

            The label is captured when the turn is written, because the
            composer is emptied the moment it is sent.
            """
            return getattr(self, "turn_note", "") or tr("a local turn", "yerel tur",
                                                        "eine lokale Runde")

        def finish_task(self, board_id, t, message):
            """Record how a turn ended: done, cancelled or failed, with the reason."""
            try:
                state = "failed" if message else "done"
                if t.cancelled():
                    state = "cancelled"
                    message = message or "Stopped"
                self.board.finish(board_id, state, message or "")
            except Hold:
                pass

        def activity_rows(self):
            return tuple(reversed(self.board.rows))

        def refresh_queue_note(self):
            """State the running tasks as a count, from the board itself."""
            busy = self.board.busy()
            if not busy:
                self.queue_note.set_text(tr("Queue: idle", "Kuyruk: boşta",
                                            "Warteschlange: untätig"))
                return
            leases = sorted({resource for _, resource in self.board.locks()})
            detail = " (%s)" % ", ".join(leases) if leases else ""
            self.queue_note.set_text(tr("Queue: %d running%s" % (len(busy), detail),
                                        "Kuyruk: %d çalışıyor%s" % (len(busy), detail),
                                        "Warteschlange: %d läuft%s" % (len(busy), detail)))

        def note_leases(self):
            """Record which devices the running task holds, as it holds them.

            The board is the reason a busy device is explained, so it follows
            the leases rather than being told about them separately.
            """
            task_id = getattr(self, "active_task", None)
            if task_id is None:
                return
            try:
                row = self.board.get(task_id)
            except Hold:
                return
            if not row.busy:
                return
            try:
                self.board.update(task_id, locks=tuple(sorted(self.leases.held())))
                self.refresh_activity()
            except Hold:
                pass

        def refresh_activity(self):
            rows, item = [], self.activity_list.get_first_child()
            while item is not None:
                nxt = item.get_next_sibling()
                self.activity_list.remove(item)
                item = nxt
            states = {"queued": tr("queued", "sırada", "wartend"),
                      "running": tr("running", "çalışıyor", "läuft"),
                      "done": tr("done", "bitti", "fertig"),
                      "cancelled": tr("cancelled", "iptal", "abgebrochen"),
                      "failed": tr("failed", "başarısız", "fehlgeschlagen")}
            for row in self.activity_rows():
                line = Gtk.ListBoxRow()
                line.task = row
                detail = "%s · %s · %s · %.0fs" % (row.kind, row.label,
                                                   states[row.state], row.elapsed())
                if row.reason:
                    detail += " · %s" % row.reason
                if row.locks:
                    detail += " · " + ",".join(row.locks)
                if row.counted_total:
                    detail += " · %d/%d bytes" % (row.counted, row.counted_total)
                label = Gtk.Label(label=detail, xalign=0, wrap=True)
                line.set_child(label)
                line.set_activatable(True)
                self.activity_list.append(line)
            self.activity_stop.set_sensitive(bool(self.board.busy()))
            self.refresh_queue_note()
            self.activity_note.set_text(tr(
                "Progress is never guessed: a turn has no percentage, a download counts bytes.",
                "İlerleme asla tahmin edilmez: bir turun yüzdesi yoktur, indirme bayt sayar.",
                "Fortschritt wird nie geschätzt: eine Runde hat keinen Prozentsatz, ein Download zählt Bytes."))

        def stop_task(self):
            """Stop the selected busy task, and say which one was stopped."""
            row = self.activity_list.get_selected_row()
            chosen = row.task if row is not None else None
            if chosen is None or not chosen.busy:
                return
            self.board.cancel(chosen.id, "stopped_by_owner")
            # A running turn is cancelled through the real cancellation path.
            if chosen.kind == "turn" and self.busy:
                self.interrupt(False)
            self.refresh_activity()
            self.set_state("ready", tr("Task stopped.", "Görev durduruldu.",
                                       "Aufgabe gestoppt."))

        def build_files_page(self):
            """A folder the owner chose, listed and previewed behind the policy.

            Nothing is written here, and a preview is bounded text: a binary
            file is refused before it is decoded.
            """
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            view = Gtk.ScrolledWindow(vexpand=True)
            view.set_child(page)
            self.stack.add_titled(view, "files", tr("Files & Research",
                                                    "Dosyalar ve Araştırma",
                                                    "Dateien & Recherche"))
            row = Gtk.Box(spacing=8)
            page.append(row)
            self.folder_root = Gtk.Entry(hexpand=True,
                                         placeholder_text=tr("A folder on this computer",
                                                             "Bu bilgisayardaki bir klasör",
                                                             "Ein Ordner auf diesem Computer"))
            row.append(self.folder_root)
            self.folder_root.set_text(self.cfg.get("research_root", ""))
            self.folder_choose = self.button(row, tr("Choose folder", "Klasör seç",
                                                     "Ordner wählen"), self.choose_folder)
            self.folder_list = self.button(row, tr("List folder", "Klasörü listele",
                                                   "Ordner listen"), self.list_folder)
            self.folder_entries = Gtk.ListBox()
            self.folder_entries.connect("row-selected", lambda *_: self.refresh_files_buttons())
            page.append(self.folder_entries)
            self.file_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.file_note)
            self.file_preview = Gtk.Label(xalign=0, wrap=True, selectable=True)
            page.append(self.file_preview)
            self.file_read = self.button(page, tr("Read the selected file", "Seçili dosyayı oku",
                                                  "Ausgewählte Datei lesen"), self.read_selected_file)
            self.refresh_files_buttons()

        def choose_folder(self):
            if self.busy:
                return
            dialog = Gtk.FileChooserNative.new("Choose a folder to read from", self.win,
                                               Gtk.FileChooserAction.SELECT_FOLDER,
                                               "Choose", "Cancel")

            def response(d, res):
                try:
                    if res != Gtk.ResponseType.ACCEPT or self.busy:
                        return
                    file = d.get_file()
                    path = file.get_path() if file else None
                    self.read_folder(path)
                finally:
                    d.destroy()
            self.show_owned_picker(dialog, response)

        def read_folder(self, path):
            """Store a chosen folder and list it; nothing is read yet."""
            try:
                root = files.checked_root(path)
                self.cfg["research_root"] = root
                self.save()
                self.folder_root.set_text(root)
                self.list_folder(root)
            except Hold as exc:
                self.set_state("ready", str(exc))
                return None
            return root

        def list_folder(self, root=None):
            if self.busy:
                return
            root = root or self.folder_root.get_text().strip()
            try:
                self.require_tools(("filesystem_read", ("folder",)))
                folder = files.list_dir(root)
            except Hold as exc:
                # A refused or unreadable folder is a bounded reason.
                if "filesystem_read" in str(exc) or "profile_forbids" in str(exc):
                    self.receipt("refused", "list a folder (filesystem_read)")
                self.file_note.set_text(str(exc))
                return None
            self.cfg["research_root"] = folder.root
            self.save()
            self.folder_root.set_text(folder.root)
            self.show_folder(folder)
            self.file_note.set_text(tr("%d entries, folders first." % len(folder.entries),
                                       "%d girdi, önce klasörler." % len(folder.entries),
                                       "%d Einträge, Ordner zuerst." % len(folder.entries)))
            return folder

        def show_folder(self, folder):
            rows, item = [], self.folder_entries.get_first_child()
            while item is not None:
                nxt = item.get_next_sibling()
                self.folder_entries.remove(item)
                item = nxt
            for entry in folder.entries:
                row = Gtk.ListBoxRow()
                row.entry = entry
                caption = entry.name + ("/" if entry.directory else
                                        " · %d bytes" % entry.size)
                row.set_child(Gtk.Label(label=caption, xalign=0, wrap=True))
                row.set_activatable(True)
                # Enter and Space read the file, exactly like the button.
                row.connect("activate", lambda *_: self.read_selected_file())
                self.folder_entries.append(row)

        def refresh_files_buttons(self):
            row = self.folder_entries.get_selected_row()
            self.file_preview.set_text("")

        def read_selected_file(self):
            row = self.folder_entries.get_selected_row()
            if row is None or self.busy:
                return None
            entry = row.entry
            if entry.directory:
                return None
            try:
                self.require_tools(("filesystem_read", ("file",)))
                text = files.preview(self.cfg.get("research_root", ""), entry.name)
            except Hold as exc:
                if "profile_forbids" in str(exc):
                    self.receipt("refused", "read a file (filesystem_read)")
                self.file_note.set_text(str(exc))
                return None
            self.file_preview.set_text(text)
            self.file_note.set_text(tr("Preview of %s" % entry.name,
                                       "%s önizlemesi" % entry.name,
                                       "Vorschau von %s" % entry.name))
            return text

        def refresh_readiness(self):
            """State what this machine can do, before a turn fails.

            Nothing is probed that would touch the network or a device: the
            items come from the configuration and from what a discovery has
            actually returned, so an un-asked question stays unknown.
            """
            profile = self.cfg.get("profile", "")
            # The endpoint the owner is looking at is the one that counts, even
            # before it is saved: a readiness row about a stale value would be
            # a lie about what the next turn would use.
            live = dict(self.cfg, endpoint=self.url.get_text().strip())
            items = readiness.report(live, self.inventory,
                                     self.tools_available, profile)
            caption = {readiness.READY: tr("ready", "hazır", "bereit"),
                       readiness.MISSING: tr("missing", "eksik", "fehlt"),
                       readiness.UNKNOWN: tr("not checked yet", "henüz bakılmadı",
                                             "noch nicht geprüft")}
            labels = {"endpoint": tr("Jan endpoint", "Jan uç noktası", "Jan-Endpunkt"),
                      "local_models": tr("Local models", "Yerel modeller", "Lokale Modelle"),
                      "stt": tr("Speech to text", "Yazıya çevirme", "Sprache zu Text"),
                      "voices": tr("Voices", "Sesler", "Stimmen"),
                      "audio_tools": tr("Audio tools", "Ses araçları", "Audio-Werkzeuge"),
                      "profile": tr("Active profile", "Etkin profil", "Aktives Profil")}
            lines = []
            for item in items:
                detail = item.detail if item.detail and item.state != readiness.READY else ""
                lines.append("%s: %s%s" % (labels.get(item.token, item.token),
                                           caption[item.state],
                                           " (%s)" % detail if detail else ""))
            self.readiness_note.set_text("\n".join(lines))

        def build_settings_page(self):
            """Preferences in one place, with what they touch stated plainly.

            Display choices live here; models, sites and profiles have their
            own pages, and nothing on this page reaches a device or a provider.
            """
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            view = Gtk.ScrolledWindow(vexpand=True)
            view.set_child(page)
            self.stack.add_titled(view, "settings", tr("Settings", "Ayarlar", "Einstellungen"))
            row = Gtk.Box(spacing=8)
            page.append(row)
            row.append(Gtk.Label(label=tr("Text size", "Yazı boyutu", "Textgröße"), xalign=0))
            self.settings_scale = Gtk.ComboBoxText()
            for value, caption in (("normal", tr("Normal", "Normal", "Normal")),
                                   ("large", tr("Large", "Büyük", "Groß"))):
                self.settings_scale.append_text(caption)
            row.append(self.settings_scale)
            self.settings_scale.set_active(0 if self.cfg.get("text_scale") != "large" else 1)
            self.settings_scale.connect("changed", self.choose_text_scale)
            self.settings_spoken = Gtk.CheckButton(
                label=tr("Show the text that will be spoken",
                         "Okunacak metni göster",
                         "Den vorzulesenden Text zeigen"))
            page.append(self.settings_spoken)
            self.settings_spoken.set_active(bool(self.cfg.get("show_spoken_text")))
            self.settings_spoken.connect("toggled", self.choose_spoken_text)
            page.append(Gtk.Label(label=tr(
                "Models, approved sites and profiles have their own pages; nothing here reaches a device or a provider.",
                "Modeller, onaylı siteler ve profillerin kendi sayfaları var; buradaki hiçbir şey aygıta veya sağlayıcıya erişmez.",
                "Modelle, genehmigte Seiten und Profile haben eigene Seiten; nichts hier erreicht ein Gerät oder keinen Anbieter."),
                xalign=0, wrap=True))
            page.append(Gtk.Label(label=tr("Keyboard", "Klavye", "Tastatur"), xalign=0))
            self.keys_help_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.keys_help_note)
            self.keys_help_note.set_text(self.keys_help())
            forget = Gtk.Box(spacing=8)
            page.append(forget)
            self.settings_forget = self.button(forget, tr("Forget local choices",
                                                          "Yerel seçimleri unut",
                                                          "Lokale Auswahl vergessen"),
                                               self.forget_choices)
            self.settings_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.settings_note)
            self.settings_note.set_text(tr(
                "Forgetting removes the stored choices only. Profiles, the audit trail and the market registry are never touched.",
                "Unutma yalnızca saklı seçimleri kaldırır. Profiller, denetim kaydı ve pazar kaydı asla dokunulmaz.",
                "Vergessen entfernt nur die gespeicherte Auswahl. Profile, das Audit-Protokoll und die Markt-Registry werden nie berührt."))

        def forget_choices(self):
            """Erase the stored choices, and state what it did not erase."""
            try:
                removed = settings.forget()
            except Hold as exc:
                self.settings_note.set_text(str(exc))
                return
            # The active profile is kept: it is a policy, not a preference.
            profile = self.store_active_profile_id()
            self.cfg = settings.empty()
            self.cfg["profile"] = profile
            self.save()
            self.settings_scale.set_active(0)
            self.settings_spoken.set_active(False)
            self.apply_text_scale()
            self.refresh()
            outcome = tr("Local choices forgotten." if removed else "No stored choices to forget.",
                         "Yerel seçimler unutuldu." if removed else "Unutulacak saklı seçim yok.",
                         "Lokale Auswahl vergessen." if removed else "Keine gespeicherte Auswahl zu vergessen.")
            self.settings_note.set_text(outcome)
            self.set_state("ready", outcome)

        def store_active_profile_id(self):
            try:
                return self.active_profile().id
            except Hold:
                return self.cfg.get("profile", "")

        def build_agents_page(self):
            """The capabilities this build does not ship, stated and disabled.

            An item that would only fail is not offered as a control. Each gate
            is named with the reason it is closed, and the tasks the client does
            run live on the Activity page rather than here.
            """
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            view = Gtk.ScrolledWindow(vexpand=True)
            view.set_child(page)
            self.stack.add_titled(view, "agents", tr("Agents", "Ajanlar", "Agenten"))
            self.agents_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.agents_note)
            self.agents_list = Gtk.ListBox()
            page.append(self.agents_list)
            self.refresh_agents_page()

        def refresh_agents_page(self):
            rows, item = [], self.agents_list.get_first_child()
            while item is not None:
                nxt = item.get_next_sibling()
                self.agents_list.remove(item)
                item = nxt
            caption = {"opencode_session_list": tr("OpenCode project and session list",
                                                   "OpenCode proje ve oturum listesi",
                                                   "OpenCode-Projekt- und Sitzungsliste"),
                       "provider_login": tr("Provider sign-in",
                                            "Sağlayıcı oturumu",
                                            "Anbieter-Anmeldung"),
                       "agent_spawn": tr("Starting an agent",
                                         "Ajan başlatma",
                                         "Einen Agenten starten"),
                       "remote_tool_call": tr("Calling a tool on another host",
                                              "Başka bir konakta araç çağırma",
                                              "Ein Werkzeug auf anderem Host aufrufen"),
                       "task_cost_reporting": tr("Task cost and token reporting",
                                                 "Görev maliyeti ve jeton raporu",
                                                 "Aufgabenkosten- und Token-Bericht")}
            for gate in agents.closed():
                row = Gtk.ListBoxRow()
                row.gate = gate
                label = Gtk.Label(label="%s — %s (%s)" % (caption[gate.token], gate.why,
                                                          tr("not implemented in this build",
                                                             "bu sürümde uygulanmadı",
                                                             "in diesem Build nicht implementiert")),
                                  xalign=0, wrap=True)
                row.set_child(label)
                row.set_sensitive(False)
                self.agents_list.append(row)
            self.agents_note.set_text(tr(
                "This client runs its own turns, downloads and transcriptions; those are on the Activity page. Nothing here can start an agent, sign in to a provider, or send work to another host.",
                "Bu istemci kendi turlarını, indirmelerini ve yazıya çevirmelerini çalıştırır; onlar Etkinlik sayfasındadır. Buradaki hiçbir şey bir ajan başlatamaz, sağlayıcıda oturum açamaz veya işi başka bir konakla gönderemez.",
                "Dieser Client führt eigene Runden, Downloads und Transkriptionen aus; die stehen auf der Aktivitätsseite. Nichts hier kann einen Agenten starten, sich bei einem Anbieter anmelden oder Arbeit an einen anderen Host senden."))

        def build_sources_page(self):
            """Approved sources: metadata only, and what that means.

            A source is registered by the owner, listed and searched; a card
            from a listing carries only what the listing states. Installing is
            another matter: this build's approved transport is a numeric
            loopback endpoint, so a remote listing yields cards to inspect, not
            weights to fetch. The page says that instead of offering a button
            that would fail.
            """
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            view = Gtk.ScrolledWindow(vexpand=True)
            view.set_child(page)
            self.stack.add_titled(view, "sources", tr("Approved sources",
                                                      "Onaylı kaynaklar",
                                                      "Genehmigte Quellen"))
            page.append(Gtk.Label(label=tr(
                "A source is a host you register, with the filters you choose. Searching reads its public listing over HTTPS, without credentials and without downloading a single byte of a model.",
                "Kaynak, seçtiğin süzgeçlerle kaydettiğin bir konaktır. Arama, kimlik bilgisi olmadan ve modelin tek bir baytını indirmeden herkese açık listelesini HTTPS üzerinden okur.",
                "Eine Quelle ist ein von dir registrierter Host mit den von dir gewählten Filtern. Eine Suche liest ihr öffentliches Listing über HTTPS, ohne Anmeldedaten und ohne ein einziges Byte eines Modells herunterzuladen."),
                xalign=0, wrap=True))
            row = Gtk.Box(spacing=8)
            page.append(row)
            self.source_host = Gtk.Entry(hexpand=True,
                                         placeholder_text="huggingface.co")
            row.append(self.source_host)
            self.source_kind = Gtk.ComboBoxText()
            for value, caption in (("hf", "Hugging Face Hub"), ("http", tr("other", "diğer", "andere"))):
                self.source_kind.append_text(caption)
            self.source_kind.set_active(0)
            row.append(self.source_kind)
            self.source_filter = Gtk.Entry(hexpand=True,
                                           placeholder_text=tr("filter, e.g. whisper ggml",
                                                               "süzgeç, ör. whisper ggml",
                                                               "Filter, z. B. whisper ggml"))
            row.append(self.source_filter)
            self.source_register = self.button(row, tr("Register source", "Kaynağı kaydet",
                                                        "Quelle registrieren"),
                                               self.register_source)
            self.source_list = Gtk.ListBox()
            self.source_list.connect("row-selected", lambda *_: self.refresh_source_buttons())
            page.append(self.source_list)
            controls = Gtk.Box(spacing=8)
            page.append(controls)
            self.source_search = self.button(controls, tr("Search the selected source",
                                                          "Seçili kaynağı ara",
                                                          "Ausgewählte Quelle suchen"),
                                             self.search_source)
            self.source_forget = self.button(controls, tr("Forget source", "Kaynağı unut",
                                                          "Quelle vergessen"), self.forget_source)
            self.source_record = self.button(controls, tr("Record selected card",
                                                       "Seçili kartı kaydet",
                                                       "Ausgewählte Karte erfassen"),
                                              self.record_card)
            self.source_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.source_note)
            self.source_cards = Gtk.ListBox()
            self.source_cards.connect("row-selected", lambda *_: self.refresh_source_buttons())
            page.append(self.source_cards)
            self.refresh_source_list()

        def source_entries(self):
            return catalog_sources.entries(self.cfg.get("sources", []))

        def selected_source(self):
            row = self.source_list.get_selected_row()
            return row.source if row is not None else None

        def register_source(self):
            host = self.source_host.get_text().strip()
            entry = {"host": host, "kind": "hf" if self.source_kind.get_active() == 0 else "http",
                     "filter": self.source_filter.get_text().strip()}
            try:
                self.cfg["sources"] = catalog_sources.register(self.cfg.get("sources", []),
                                                              entry)
            except Hold as exc:
                self.set_state("ready", str(exc))
                return None
            self.save()
            self.source_host.set_text("")
            self.source_filter.set_text("")
            self.refresh_source_list()
            self.set_state("ready", tr("Source registered.", "Kaynak kaydedildi.",
                                       "Quelle registriert."))
            return entry

        def forget_source(self):
            source = self.selected_source()
            if source is None:
                return None
            try:
                self.cfg["sources"] = catalog_sources.forget(self.cfg.get("sources", []),
                                                            {"host": source.host,
                                                             "kind": source.kind,
                                                             "filter": source.filter})
            except Hold as exc:
                self.set_state("ready", str(exc))
                return None
            self.save()
            self.refresh_source_list()
            return source

        def refresh_source_list(self):
            rows, item = [], self.source_list.get_first_child()
            while item is not None:
                nxt = item.get_next_sibling()
                self.source_list.remove(item)
                item = nxt
            for source in self.source_entries():
                row = Gtk.ListBoxRow()
                row.source = source
                row.set_child(Gtk.Label(label="%s · %s · %s"
                                        % (source.host, source.kind, source.filter or "-"),
                                        xalign=0, wrap=True))
                row.set_activatable(True)
                self.source_list.append(row)
            self.refresh_source_buttons()

        def refresh_source_buttons(self):
            chosen = self.selected_source()
            card = self.source_cards.get_selected_row()
            self.source_search.set_sensitive(not self.busy and chosen is not None)
            self.source_forget.set_sensitive(chosen is not None)
            self.source_record.set_sensitive(not self.busy and card is not None)

        def search_source(self):
            """List a registered source's public metadata, bounded and verified.

            The same HTTPS policy as a catalog fetch applies: the host must be
            allowed, TLS is verified against the system store, no redirect is
            followed and no credential is sent. The opener is injectable so the
            policy is tested without a socket.
            """
            if self.busy:
                return None
            source = self.selected_source()
            if source is None:
                return None
            url = catalog_sources.safe_url(source)
            try:
                profiles.require_tool(self.active_profile(), "external_network", ("catalog",))
                raw = self.loop.run_until_complete(
                    catalog_sources.fetch(source, self.source_opener()))
                cards, refused = catalog_sources.cards(
                    json.loads(raw), source)
            except Hold as exc:
                self.set_state("ready", str(exc))
                return None
            self.show_source_cards(cards, refused)
            return cards

        def source_opener(self):
            """The listing opener; the default verifies TLS like a catalog."""
            return catalog_fetch.urlopen_opener()

        def show_source_cards(self, cards, refused):
            rows, item = [], self.source_cards.get_first_child()
            while item is not None:
                nxt = item.get_next_sibling()
                self.source_cards.remove(item)
                item = nxt
            for card in cards:
                row = Gtk.ListBoxRow()
                row.card = card
                caption = "%s · %s · %s · %s · %d MiB" % (
                    card.display_name, card.category, card.artifact_format,
                    ",".join(card.languages) or "-",
                    card.source_size // (1024 * 1024))
                row.set_child(Gtk.Label(label=caption, xalign=0, wrap=True))
                self.source_cards.append(row)
            stated = tr("%d cards, %d rows refused as unverifiable."
                        % (len(cards), refused),
                        "%d kart, %d satır doğrulanamaz diye reddedildi."
                        % (len(cards), refused),
                        "%d Karten, %d Zeilen als unverifizierbar abgelehnt."
                        % (len(cards), refused))
            self.source_note.set_text(stated + " " + tr(
                "Nothing here is installed: this build's approved transport is a loopback endpoint, so a remote listing is inspected, not fetched.",
                "Buradaki hiçbir şey kurulmaz: bu sürümün onaylı taşıması geri döngü uç noktasıdır, uzak listeleme incelenir, çekilmez.",
                "Hier wird nichts installiert: der genehmigte Transport dieses Builds ist ein Loopback-Endpunkt, ein entferntes Listing wird geprüft, nicht geladen."))

        def record_card(self):
            """Record a listed card as provenance, with nothing installed.

            The card keeps what the source's listing stated — digest, size,
            category — and is stored as DISCOVERED so the market can show it and
            a later transport can verify it. Nothing is downloaded here, and the
            button says so.
            """
            if self.busy:
                return None
            source = self.selected_source()
            row = self.source_cards.get_selected_row()
            card = row.card if row is not None else None
            if source is None or card is None:
                return None
            try:
                recorded = catalog_sources.registry_card(card, source)
                self.market.save_card(recorded)
            except Hold as exc:
                self.set_state("ready", str(exc))
                return None
            self.build_market_page()
            self.set_state("ready", tr("Recorded in the registry; nothing installed.",
                                       "Kayda alındı; hiçbir şey kurulmadı.",
                                       "In der Registry erfasst; nichts installiert."))
            return recorded

        def remember_hosts(self):
            hosts = [h for h in self.catalog_hosts.get_text().split() if h]
            if hosts != self.cfg.get("allowed_hosts", []):
                self.cfg["allowed_hosts"] = hosts
                self.save()

        def fetch_catalog(self):
            if self.busy:
                return
            self.remember_hosts()
            url = self.catalog_url.get_text().strip()
            hosts = self.cfg.get("allowed_hosts", [])
            try:
                self.require_catalog_fetch(url, hosts)
                raw = self.loop.run_until_complete(
                    catalog_fetch.fetch(url, hosts, catalog_fetch.urlopen_opener()))
                self.load_catalog_text(raw)
                self.set_state("ready", tr("Catalog fetched and verified.",
                                           "Katalog getirildi ve dogrulandi.",
                                           "Katalog abgerufen und geprueft."))
            except Hold as exc:
                self.set_state("ready", str(exc))

        def require_catalog_fetch(self, url, hosts):
            if not hosts:
                raise Hold("catalog_host_not_allowed")
            profiles.require_tool(self.active_profile(), "external_network", ("catalog",))
            catalog_fetch.allowed(url, hosts)

        def import_catalog(self):
            if self.busy:
                return
            dialog = Gtk.FileChooserNative.new("Import a curated catalog", self.win,
                                               Gtk.FileChooserAction.OPEN, "Select", "Cancel")
            filt = Gtk.FileFilter()
            filt.add_pattern("*.json")
            dialog.add_filter(filt)
            def response(d, res):
                try:
                    if res != Gtk.ResponseType.ACCEPT:
                        return
                    file = d.get_file()
                    path = file.get_path() if file else None
                    raw = Path(path).read_text(encoding="utf-8") if path else ""
                    self.load_catalog_text(raw)
                    self.set_state("ready", tr("Catalog imported.","Katalog alındı.",
                                               "Katalog importiert."))
                except (Hold, OSError, ValueError):
                    self.set_state("ready", "catalog_import_rejected")
                finally:
                    d.destroy()
            self.show_owned_picker(dialog, response)

        def selected_card(self):
            row = self.market_list.get_selected_row()
            return self.market_row_ids.get(row) if row is not None else None

        def refresh_market_buttons(self):
            chosen = self.selected_card()
            for w in (self.market_use, self.market_forget):
                w.set_sensitive(not self.busy and chosen is not None)
            self.market_list.set_sensitive(not self.busy)
            if not self.busy:
                ready = False
                if chosen is not None:
                    try:
                        card = self.market.card(chosen)
                        ready = (card.state == "DISCOVERED" and card.approved_source()
                                 and (self.transport is not None
                                      or market.transport_for(card.source_ref) is not None))
                    except Hold:
                        ready = False
                self.market_fetch.set_sensitive(ready)
                revisions = []
                if chosen is not None:
                    try:
                        revisions = self.market.history(chosen)
                    except Hold:
                        revisions = []
                self.market_rollback.set_sensitive(not self.busy and bool(revisions))
                updatable = False
                if chosen is not None:
                    try:
                        card_now = self.market.card(chosen)
                        updatable = (card_now.state in ("REGISTERED", "TESTABLE", "ACTIVE")
                                     and card_now.approved_source())
                    except Hold:
                        updatable = False
                self.market_update.set_sensitive(not self.busy and updatable)

        def use_selected_card(self):
            model_id = self.selected_card()
            if model_id is None or self.busy:
                return
            try:
                self.use_card(model_id)
                self.set_state("ready", tr("Model selected.","Model seçildi.","Modell ausgewählt."))
            except Hold as exc:
                self.set_state("ready", str(exc))

        def discover_market(self):
            async def run(epoch):
                raw = self.require_endpoint()
                client = Jan(raw, timeout=10)
                cards = await market.from_models(client, languages=(), now=0.0)
                self.session.check(epoch)
                self.cfg["endpoint"] = raw
                self.inventory = tuple(card.model_id for card in cards)
                self.models.remove_all()
                for model_id in self.inventory:
                    self.models.append_text(model_id)
                for card in cards:
                    known = None
                    try:
                        known = self.market.card(card.model_id)
                    except Hold:
                        pass
                    if known is None or known.state == "DISCOVERED":
                        # Re-discovery refreshes a card, never silently upgrades it.
                        self.market.save_card(card)
                self.save()
                self.build_market_page()
                self.refresh()
            self.submit(run)

        def download_selected_card(self):
            """Queue a discovered item for download; nothing installed is replaced."""
            model_id = self.selected_card()
            if model_id is None or self.busy:
                return
            self.submit(lambda epoch: self.download(epoch, model_id))

        async def download(self, epoch, model_id):
            card = self.market.card(model_id)
            source = market.source_of(card)
            transport = self.transport or market.transport_for(source.reference)
            if transport is None:
                raise Hold("download_transport_not_available")
            transfer = market.Transfer(self.market, card, source, transport)
            entry = {"id": model_id, "state": "QUEUED", "received": 0, "total": source.size,
                     "note": "", "resume": 0}
            self.downloads.append(entry)

            def started():
                entry["state"] = "DOWNLOADING"
                self.build_downloads_page()

            def progress(count):
                entry["received"] = count
                entry["state"] = "DOWNLOADING"
                self.build_downloads_page()
            try:
                self.session.check(epoch)
                started()
                result = await transfer.run(progress)
                entry["state"] = result.state
                entry["note"] = tr("Registered.","Kaydedildi.","Registriert.")
            finally:
                if transfer.state == "CANCELLED":
                    partial = Path(self.market.root, "transfers", "partial-" + model_id)
                    if partial.is_file() and partial.stat().st_size > 0:
                        entry["state"], entry["resume"] = "RESUMABLE", partial.stat().st_size
                        entry["note"] = tr("Partial kept; resume continues from here.",
                                           "Kısmi tutuldu; sürdürme buradan devam eder.",
                                           "Teil erhalten; Fortsetzung ab hier.")
                    else:
                        entry["state"], entry["note"] = "CANCELLED", tr(
                            "Cancelled.", "İptal edildi.", "Abgebrochen.")
                elif transfer.state in market.TERMINAL:
                    # A failed transfer is reported by the reason in the status line.
                    entry["state"], entry["note"] = transfer.state, ""
                elif transfer.state in ("REGISTERED", "TESTABLE", "ACTIVE"):
                    entry["state"], entry["note"] = transfer.state, tr(
                        "Registered.", "Kaydedildi.", "Registriert.")
                try:
                    # A cancelled turn has nothing to check; cleanup still runs.
                    self.session.check(epoch)
                except Hold:
                    pass
                self.build_downloads_page()
                self.build_market_page()

        def build_downloads_page(self):
            self.download_entries = {}
            if getattr(self, "download_list", None) is not None:
                child = self.download_list.get_first_child()
                while child is not None:
                    nxt = child.get_next_sibling()
                    self.download_list.remove(child)
                    child = nxt
            else:
                page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                view = Gtk.ScrolledWindow(vexpand=True)
                view.set_child(page)
                self.stack.add_titled(view, "downloads", tr("Downloads","İndirmeler","Downloads"))
                self.build_web_page()
                self.download_note = Gtk.Label(xalign=0, wrap=True)
                page.append(self.download_note)
                self.download_list = Gtk.ListBox()
                self.download_list.connect("row-selected", lambda *_: self.refresh_download_buttons())
                page.append(self.download_list)
                row = Gtk.Box(spacing=8)
                page.append(row)
                self.download_cancel = self.button(row, tr("Cancel","İptal et","Abbrechen"), self.cancel_download)
                self.download_resume = self.button(row, tr("Resume","Sürdür","Fortsetzen"), self.resume_download)
                self.download_discard = self.button(row, tr("Discard partial","Kısmını at","Teil verwerfen"), self.discard_download)
            for entry in list(self.downloads):
                label = Gtk.Label(label=self.download_line(entry), xalign=0, wrap=True)
                item = Gtk.ListBoxRow()
                item.set_child(label)
                self.download_list.append(item)
                self.download_entries[item] = entry
            self.download_note.set_text(tr(
                "Only approved, verified transfers. Nothing is downloaded without an approved source and checksum.",
                "Yalnızca onaylı ve doğrulanmış aktarımlar. Onaylı kaynak ve sağlama toplamı olmadan hiçbir şey indirilmez.",
                "Nur genehmigte, geprüfte Übertragungen. Ohne genehmigte Quelle und Prüfsumme wird nichts geladen."))
            self.refresh_download_buttons()

        def download_line(self, entry):
            total = market.human_size(entry["total"]) if entry["total"] else "unknown/not tested"
            part = "%s · %s · %d of %s bytes" % (entry["id"], entry["state"], entry["received"], total)
            return part + (" · " + entry["note"] if entry["note"] else "")

        def in_flight(self):
            return any(entry["state"] in ("QUEUED", "DOWNLOADING") for entry in self.downloads)

        def resumable(self):
            return any(entry["state"] == "RESUMABLE" for entry in self.downloads)

        def resumable_entry(self):
            for entry in self.downloads:
                if entry["state"] == "RESUMABLE":
                    return entry
            return None

        def refresh_download_buttons(self):
            # Cancelling is available while a transfer runs; it is not new work.
            self.download_cancel.set_sensitive(self.in_flight())
            self.download_resume.set_sensitive(self.resumable())
            self.download_discard.set_sensitive(self.resumable())

        def cancel_download(self):
            if not self.in_flight():
                return
            for entry in self.downloads:
                if entry["state"] in ("QUEUED", "DOWNLOADING"):
                    entry["note"] = tr("Cancelling…","İptal ediliyor…","Wird abgebrochen…")
            self.interrupt(False)
            self.build_downloads_page()

        def discard_download(self):
            entry = self.resumable_entry()
            if entry is None or self.busy:
                return
            Path(self.market.root, "transfers", "partial-" + entry["id"]).unlink(missing_ok=True)
            entry["state"], entry["resume"], entry["note"] = "CANCELLED", 0, tr(
                "Partial discarded.", "Kısmi atıldı.", "Teil verworfen.")
            self.build_downloads_page()

        def resume_download(self):
            entry = self.resumable_entry()
            if entry is None or self.busy:
                return
            start = entry["resume"]

            async def run(epoch):
                card = self.market.card(entry["id"])
                source = market.source_of(card)
                transport = self.transport or market.transport_for(source.reference)
                if transport is None:
                    raise Hold("download_transport_not_available")
                entry["state"], entry["note"] = "DOWNLOADING", tr("Resuming…","Sürdürülüyor…",
                                                                 "Fortsetzung…")
                transfer = market.Transfer(self.market, card, source, transport)
                try:
                    result = await transfer.resume(self.download_progress(entry, start), start)
                    entry["state"], entry["note"] = result.state, tr(
                        "Registered.", "Kaydedildi.", "Registriert.")
                finally:
                    if transfer.state == "CANCELLED":
                        partial = Path(self.market.root, "transfers",
                                       "partial-" + entry["id"])
                        if partial.is_file():
                            entry["state"], entry["resume"] = "RESUMABLE", partial.stat().st_size
                    self.session.check(epoch)
                    self.build_downloads_page()
                    self.build_market_page()
            self.submit(run)

        def restore_download_queue(self):
            """Rebuild the queue from disk: a partial transfer is resumable."""
            for model_id, size in market.resumable_partials(self.market.root):
                try:
                    card = self.market.card(model_id)
                except Hold:
                    continue  # an orphan partial is not this client's business
                if card.state in ("REGISTERED", "TESTABLE", "ACTIVE"):
                    continue  # an installed item has no pending transfer
                self.downloads.append({
                    "id": model_id, "state": "RESUMABLE", "received": size,
                    "total": card.source_size,
                    "note": tr("Restored from disk.","Diskten geri yüklendi.",
                               "Von der Festplatte wiederhergestellt."),
                    "resume": size})

        def download_progress(self, entry, offset):
            def progress(count):
                entry["received"] = count
                entry["state"] = "DOWNLOADING"
                self.build_downloads_page()
            return progress

        def rollback_selected_card(self):
            """Restore the previous revision of a registered item, as a new revision."""
            model_id = self.selected_card()
            if model_id is None or self.busy:
                return
            try:
                revisions = self.market.history(model_id)
                if not revisions:
                    raise Hold("revision_not_found")
                self.market.rollback(model_id, revisions[-1].revision)
                self.build_market_page()
                self.set_state("ready", tr("Previous revision restored.",
                                           "Önceki sürüm geri yüklendi.",
                                           "Vorherige Revision wiederhergestellt."))
            except Hold as exc:
                self.set_state("ready", str(exc))

        def update_selected_card(self):
            """Re-fetch the approved source of an installed item, keeping history."""
            model_id = self.selected_card()
            if model_id is None or self.busy:
                return

            async def run(epoch):
                card = self.market.card(model_id)
                source = market.source_of(card)
                transport = self.transport or market.transport_for(source.reference)
                if transport is None:
                    raise Hold("download_transport_not_available")
                entry = {"id": model_id, "state": "DOWNLOADING", "received": 0,
                         "total": source.size, "note": "", "resume": 0}
                self.downloads.append(entry)
                transfer = market.Transfer(self.market, card, source, transport)
                try:
                    result = await transfer.update(self.download_progress(entry, 0))
                    entry["state"], entry["note"] = result.state, tr(
                        "Updated.", "Güncellendi.", "Aktualisiert.")
                finally:
                    if transfer.state == "CANCELLED":
                        partial = Path(self.market.root, "transfers",
                                       "partial-" + model_id)
                        if partial.is_file():
                            entry["state"], entry["resume"] = "RESUMABLE", partial.stat().st_size
                    try:
                        self.session.check(epoch)
                    except Hold:
                        pass
                    self.build_downloads_page()
                    self.build_market_page()
            self.submit(run)

        def forget_selected_card(self):
            model_id = self.selected_card()
            if model_id is None or self.busy:
                return
            try:
                self.market.unregister(model_id)
                self.cfg["market_refs"] = {k: v for k, v in self.cfg.get("market_refs", {}).items()
                                           if self.ref_still_registered(k)}
                self.save()
                self.build_market_page()
            except Hold as exc:
                self.set_state("ready", str(exc))

        def ref_still_registered(self, ref):
            return any(card.storage_ref == ref for card in self.market.cards())

        def choose(self, key):
            dialog=Gtk.FileChooserNative.new("Select a trusted installed local model", self.win,Gtk.FileChooserAction.OPEN,"Select","Cancel")
            filt=Gtk.FileFilter()
            filt.add_pattern("*.bin" if key=="stt" else "*.onnx")
            dialog.add_filter(filt)
            def response(d, res):
                try:
                    if res!=Gtk.ResponseType.ACCEPT or self.busy:
                        return
                    file=d.get_file()
                    p=file.get_path() if file else None
                    # A card is registered before the choice is applied, so an
                    # unsupported or unreadable file never becomes the active model.
                    self.register_choice(key, p)
                    if key=="stt":
                        self.cfg["stt"]=str(audio.selected_file(p,".bin"))
                    else:
                        self.cfg["voices"][key]=audio.voice_for(p,key).id
                    self.save()
                    self.update_labels()
                    self.refresh()
                except Hold as exc:
                    self.status.set_text(str(exc))
                finally:
                    d.destroy()
            self.show_owned_picker(dialog, response)

        def preview(self, lang):
            if self.busy or self.cfg["llm"] not in self.inventory:
                return
            sample = {"tr": "Bu bir ses önizlemesidir.",
                      "en": "This is a voice preview.",
                      "de": "Das ist eine Stimmprobe."}[lang]
            self.require_tools(("local_audio_device", ("push_to_talk",)))
            speech = SpeechRequest(sample, lang, audio.voice_for(self.cfg["voices"][lang], lang).id)
            async def run(epoch):
                def progress(stage):
                    if epoch == self.session.epoch:
                        self.set_state(stage)
                # A preview shares the playback lease with a turn, never the
                # device: a refused lease is a bounded reason.
                async with self.leases.hold("audio_playback", "preview"):
                    await audio.speak(speech, progress)
            self.submit(run)

        def test_chat(self):
            if self.busy or self.cfg["llm"] not in self.inventory:
                return
            self.test_note.set_text(tr("Testing…","Sınanıyor…","Test läuft…"))
            async def run(epoch):
                # A disposable session: a test never enters the conversation.
                probe = Session()
                # A disposable session shares this turn's epoch, so cancellation
                # and staleness checks still apply; its history is never merged.
                probe.epoch = epoch
                self.turn_note = tr("testing the selected model",
                                    "seçilen model sınanıyor",
                                    "ausgewähltes Modell wird getestet")
                # A short, anchor-bearing probe so the reply language is provable.
                text = tr("Hello there! In one short sentence, what can you do?",
                          "Merhaba! Tek cümleyle cevapla: neler yapabilirsin?",
                          "Hallo! Antworte in einem kurzen Satz: was kannst du?")
                reply, _ = await probe.reply(text, self.backend_for_test(), epoch)
                self.session.check(epoch)
                self.test_note.set_text("%s · %s" % (reply.strip()[:240], probe.context.response_language or "unknown"))
            self.submit(run)

        def active_profile(self):
            """The profile whose policy is in force for this turn."""
            try:
                return self.store.load(self.cfg["profile"]).checked()
            except (Hold, KeyError):
                raise Hold("no_active_profile") from None

        def transcribe_recording(self):
            if self.busy or not self.cfg["stt"]:
                self.set_state("ready", "select_local_model")
                return
            dialog=Gtk.FileChooserNative.new("Select a recording to transcribe", self.win,Gtk.FileChooserAction.OPEN,"Select","Cancel")
            filt=Gtk.FileFilter()
            filt.add_pattern("*.wav")
            dialog.add_filter(filt)
            def response(d, res):
                try:
                    if res!=Gtk.ResponseType.ACCEPT or self.busy:
                        return
                    file=d.get_file()
                    p=file.get_path() if file else None
                    self.run_transcribe(p)
                finally:
                    d.destroy()
            self.show_owned_picker(dialog, response)

        def run_transcribe(self, path):
            """Transcribe an owner-supplied recording; nothing is captured here."""
            try:
                self.set_state("ready", "transcribing")
                raw = self.loop.run_until_complete(
                    audio.transcribe(self.cfg["stt"], path, runner=self.transcribe_runner))
            except Hold as exc:
                self.set_state("ready", str(exc))
                return None
            except Exception:
                self.set_state("ready", "local_operation_failed")
                return None
            self.bubble("Recording", raw)
            self.set_state("ready", "transcribed")
            return raw

        def require_tools(self, *requests):
            """Refuse an action the active profile denies, at the moment it runs.

            Every decision is appended to the local audit trail.
            """
            profile = self.active_profile()
            for action, scope in requests:
                try:
                    profiles.require_tool(profile, action, scope)
                except Hold as exc:
                    self.record_policy(action, scope, "deny", str(exc))
                    raise
                self.record_policy(action, scope, "allow", "rule_or_absent")

        def record_disclosure(self):
            """Record that the boundary was stated before the message was sent."""
            try:
                data = disclosure.facts(self.active_profile(), self.url.get_text().strip())
            except Hold:
                return
            self.record_policy("disclosure", (data["boundary"], data["endpoint"]),
                               "shown", data["leak_reason"])

        def record_policy(self, action, scope, decision, reason):
            try:
                profiles.record_decision(self.store.root, self.cfg["profile"], action,
                                         scope, decision, reason)
            except (Hold, OSError):
                self.status.set_text("audit_unavailable")
                return
            self.refresh_audit()

        def require_endpoint(self, raw=None):
            """The active mode decides what kind of endpoint may be used."""
            raw = self.url.get_text().strip() if raw is None else raw
            try:
                result = profiles.enforce_endpoint(self.store.load(self.cfg["profile"]).checked(), raw)
            except KeyError:
                raise Hold("no_active_profile") from None
            except Hold as exc:
                self.record_policy("endpoint", (raw[:64],), "deny", str(exc))
                raise
            self.record_policy("endpoint", (raw[:64],), "allow", "mode_policy")
            return result

        def backend_for_test(self):
            return Jan(self.require_endpoint(), self.cfg["llm"])

        def submit(self, work):
            if self.busy:
                return
            self.dismiss_picker()
            self.busy=True
            # Work is being submitted: ask for the fast tick instead of the
            # idle one, so a turn is answered promptly and an idle window is not.
            if self.bridge is not None:
                self.bridge.wake()
            task=self.session.own(work, loop=loop)
            epoch=self.session.epoch
            self.set_state("starting")
            # Every turn the client runs is a task on the board, with no
            # percentage: its state is the truth until it ends.
            board_id = self.board.start("turn", self.turn_label(), now=time.time())
            self.active_task = board_id
            self.turn_outcome = ""
            # A running task is visible while it runs, not only after it ends.
            self.refresh_activity()
            def finished(t):
                message=""
                try:
                    t.result()
                except asyncio.CancelledError:
                    message="Stopped"
                except Hold as e:
                    message=str(e)
                except asyncio.TimeoutError:
                    message="Operation timed out"
                except Exception:
                    message="Local operation failed"
                self.busy=False
                self.finish=self.playback=None
                self.finish_task(board_id, t, message)
                self.refresh_activity()
                # A turn that stated its own outcome keeps it: a generic
                # "ready" must not overwrite "reply muted; shown as text".
                if not message and self.turn_outcome:
                    message = self.turn_outcome
                if epoch==self.session.epoch:
                    self.set_state("ready",message)
                else:
                    self.set_state("ready","Stopped")
                if hasattr(self, "analysis"):
                    self.analysis.open_pending(epoch)
            task.add_done_callback(finished)

        def discover(self):
            async def run(epoch):
                raw=self.require_endpoint()
                client=Jan(raw,timeout=10)
                models=await client.models()
                self.session.check(epoch)
                self.cfg["endpoint"]=raw
                previous=self.cfg["llm"]
                self.cfg["llm"]=""
                self.inventory=models
                self.models.remove_all()
                for m in models:
                    self.models.append_text(m)
                if previous in models:
                    self.models.set_active(models.index(previous))
                self.save()
                if not models:
                    raise Hold("no_installed_chat_models")
            self.submit(run)

        def receipt(self, kind, what):
            """Record one decision in the conversation, as a receipt.

            A receipt is not a bubble: it carries no sender and no time that
            could be mistaken for an approval, and it names the outcome first.
            """
            text = receipts.line(kind, what)
            label = Gtk.Label(label=text, xalign=0, selectable=True, wrap=True)
            label.add_css_class("message")
            label.add_css_class("receipt")
            label.receipt = True
            self.log.append(label)
            GLib.idle_add(self.bottom)
            return label

        def bottom(self):
            adj = self.scroll.get_vadjustment()
            adj.set_value(max(0, adj.get_upper() - adj.get_page_size()))
            return False

        def spoken_line(self, speech, epoch):
            """Show the exact text that will be spoken, when asked for it.

            This is the normalised text the voice layer produces, not a second
            answer: a display text that cannot be spoken is refused, never
            silently shortened.
            """
            if not self.cfg.get("show_spoken_text") or speech is None:
                return
            if epoch != self.session.epoch:
                return
            label = Gtk.Label(label=tr("Spoken: ", "Okunan: ", "Gesprochen: ") + speech.text,
                              xalign=0, selectable=True, wrap=True)
            label.add_css_class("message")
            label.add_css_class("receipt")
            label.spoken = True
            self.log.append(label)
            GLib.idle_add(self.bottom)

        def bubble(self,speaker,text):
            label=Gtk.Label(label=speaker+"\n"+text,xalign=0,selectable=True,wrap=True)
            # A message that grows keeps its own bounded text here.
            self.growing[label]=text
            label.add_css_class("message")
            label.add_css_class("user" if speaker=="You" else "assistant")
            self.log.append(label)
            def bottom():
                adj=self.scroll.get_vadjustment()
                adj.set_value(max(0,adj.get_upper()-adj.get_page_size()))
                return False
            GLib.idle_add(bottom)
            # The caller keeps the label so a reply can grow into it.
            return label

        def extend(self,label,speaker,piece,replace=False):
            """Grow one message label with the text actually received."""
            if not isinstance(piece,str) or not piece:
                return
            grown = piece if replace else self.growing.get(label,"")+piece
            if len(grown) > STREAM_CAP:
                # A cap is a reason, never a silent truncation of a reply.
                self.growing[label]=grown[:STREAM_CAP]
                self.set_state("ready","stream_too_large")
                return
            self.growing[label]=grown
            label.set_text(speaker+"\n"+grown)

        def toggle_mute(self, *_):
            """Suppress audio output while every reply is still shown.

            Mute is state, not a preference: it is never stored, so a restart
            never begins muted, and a muted reply still appears as text.
            """
            if self.mute.get_active():
                if self.playback is not None:
                    # A turn already speaking stops its output now.
                    self.stop_speaking()
                self.set_state("ready", tr("Audio output muted.",
                                           "Ses çıkışı sessize alındı.",
                                           "Audioausgabe stummgeschaltet."))
            else:
                self.set_state("ready", tr("Audio output on.", "Ses çıkışı açık.",
                                           "Audioausgabe an."))
            self.refresh()

        def stop_speaking(self):
            task = self.playback
            if task is None or task.done() or self.playback_stopping:
                return
            # Cancellation includes asynchronous device cleanup. Queued clicks
            # must not cancel that cleanup again or unlock a new turn early.
            self.playback_stopping = True
            task.cancel()
            if self.stage != "stopping":
                self.set_state("stopping_playback")

        def hard_stop(self):
            self.clear_attachment()
            if hasattr(self, "analysis"):
                self.analysis.reset()
            self.interrupt(False)

        def interrupt(self, clear):
            self.dismiss_picker()
            if clear:
                self.clear_attachment()
            if hasattr(self, "analysis"):
                if clear:
                    self.analysis.reset()
                else:
                    self.analysis.cancel()
            self.session.invalidate(clear=clear)
            if clear:
                child=self.log.get_first_child()
                while child is not None:
                    nxt=child.get_next_sibling()
                    self.log.remove(child)
                    child=nxt
            self.set_state("stopping" if self.busy else "ready")

        def require_attachment(self):
            """The policy gate for an attachment, decided before any dialog.

            Returning None means the active profile denies the read, and the
            audit trail already carries the decision. A caller must not open a
            chooser in that case: a denied read never reaches a file picker.
            """
            if self.busy or self.cfg["llm"] not in self.inventory:
                return None
            try:
                profile = self.active_profile()
                if profile.state != "active" or profile.based_on_mode == "CLOUD_CHAT":
                    raise Hold("attachment_mode_denied")
                self.require_tools((attachments.ACTION, attachments.SCOPE))
            except Hold as exc:
                # A refused read is a receipt, not a silent menu item.
                self.receipt("refused", "read a file (filesystem_read)")
                self.set_state("ready", str(exc))
                return None
            return True

        def attach_file(self):
            """Attach a local text file, behind the active profile's policy."""
            if self.require_attachment() is None:
                return
            dialog=Gtk.FileChooserNative.new("Attach a text file", self.win,Gtk.FileChooserAction.OPEN,"Attach","Cancel")
            def response(d, res):
                try:
                    if res != Gtk.ResponseType.ACCEPT:
                        return
                    file = d.get_file()
                    self.read_attachment(file.get_path() if file else None)
                finally:
                    d.destroy()
            self.show_owned_picker(dialog, response)

        def read_attachment(self, path):
            """Trusted chooser acceptance: recheck policy at the actual read."""
            if self.require_attachment() is None:
                self.clear_attachment()
                return None
            try:
                route = self.attachment_route()
                item = attachments.load(path)
            except Hold as exc:
                self.clear_attachment()
                self.set_state("ready", str(exc))
                return None
            except Exception:
                self.clear_attachment()
                self.set_state("ready", "local_operation_failed")
                return None
            self.attachment = item
            self.attachment_context = route
            self.attachment_note.set_text(tr("Attached: %s" % attachments.describe(item),
                                             "Eklendi: %s" % attachments.describe(item),
                                             "Angehängt: %s" % attachments.describe(item)))
            return item

        def send_text(self):
            text=self.entry.get_text().strip()
            if self.busy or not text:
                return
            if analysis_intent.direct(text) is not None or analysis_intent.explicit_task(text) is not None:
                if self.attachment is not None:
                    self.set_state("ready", tr("Local CSV analysis uses its own picker, not text attachments.",
                        "Yerel CSV analizi kendi dosya seçicisini kullanır.",
                        "Lokale CSV-Analyse verwendet einen eigenen Dateidialog."))
                    return
                if self.analysis.dialog is not None:
                    return
                self.entry.set_text("")
                self.turn_note = tr("Local analysis request", "Yerel analiz isteği", "Lokale Analyseanfrage")
                async def task_run(epoch):
                    self.bubble("You", text)
                    await self.analysis.command(text, epoch)
                self.submit(task_run)
                return
            if self.cfg["llm"] not in self.inventory:
                return
            self.record_disclosure()
            # The attachment is a bounded block in the same message, and it is
            # cleared afterwards so it can never be sent twice.
            item = self.attachment
            if item is not None:
                try:
                    if (self.attachment_context != self.attachment_route()
                            or self.require_attachment() is None):
                        raise Hold("attachment_context_changed")
                except Hold as exc:
                    self.clear_attachment()
                    self.set_state("ready", str(exc))
                    return
                text = text + "\n\n" + attachments.prompt_block(item)
                self.clear_attachment()
            self.entry.set_text("")
            self.turn_note = text[:80]
            async def run(epoch):
                self.bubble("You",text)
                self.set_state("thinking")
                answer=self.bubble("JARVIS","")
                def grow(piece):
                    # Only what the server actually sent is shown, in order.
                    if epoch == self.session.epoch:
                        self.extend(answer,"JARVIS",piece)
                result,_=await self.session.reply(text,Jan(self.require_endpoint(), self.cfg["llm"]),
                                                  epoch,on_delta=grow)
                self.session.check(epoch)
                self.extend(answer,"JARVIS",result,replace=True)
            self.submit(run)

        def voice(self):
            async def run(epoch):
                def progress(stage):
                    if epoch == self.session.epoch and not self.playback_stopping:
                        self.set_state(stage)

                self.require_tools(("local_audio_device", ("push_to_talk",)))
                self.turn_note = tr("a spoken turn", "sesli tur", "eine gesprochene Runde")
                voices = audio.SelectedVoices(self.cfg["voices"])
                self.finish=asyncio.Event()
                # The microphone is exclusive: a turn that cannot get the lease
                # is refused before capture starts, never after it fails.
                async with self.leases.hold("microphone", "voice-turn-%d" % epoch):
                    self.note_leases()
                    text=await audio.listen(self.cfg["stt"],self.finish,progress)
                # The microphone is free again the moment the capture ends.
                self.note_leases()
                self.session.check(epoch)
                self.bubble("You",text)
                if analysis_intent.direct(text) is not None or analysis_intent.explicit_task(text) is not None:
                    if self.analysis.dialog is not None:
                        raise Hold("analysis_picker_busy")
                    await self.analysis.command(text, epoch)
                    return
                self.set_state("thinking")
                result,speech=await self.session.reply(text,Jan(self.require_endpoint(),self.cfg["llm"]),epoch,voices)
                self.session.check(epoch)
                self.bubble("JARVIS",result)
                self.spoken_line(speech, epoch)
                if self.mute.get_active():
                    # Muted: the reply is shown, and nothing is spoken.
                    self.turn_outcome = tr("Reply muted; shown as text.",
                                           "Yanıt sessiz; metin olarak gösterildi.",
                                           "Antwort stumm; als Text angezeigt.")
                    self.session.check(epoch)
                    self.set_state("ready", tr("Reply muted; shown as text.",
                                               "Yanıt sessiz; metin olarak gösterildi.",
                                               "Antwort stumm; als Text angezeigt."))
                    return
                async with self.leases.hold("audio_playback", "voice-turn-%d" % epoch):
                    self.note_leases()
                    playback = loop.create_task(audio.speak(speech, progress))
                    self.playback = playback
                    try:
                        # Stop Speaking preserves the turn. Cancel Turn invalidates
                        # it, but both paths must await the same playback cleanup.
                        await asyncio.shield(playback)
                    except asyncio.CancelledError:
                        self.stop_speaking()
                        try:
                            await playback
                        except asyncio.CancelledError:
                            pass
                        self.session.check(epoch)
                    finally:
                        self.playback = None
                        self.playback_stopping = False
                self.note_leases()
            self.submit(run)

        def close(self,*_):
            self.dismiss_picker()
            self.clear_attachment()
            if hasattr(self, "analysis"):
                self.analysis.reset()
            if not self.busy:
                return False
            if not self.closing:
                self.closing=True
                self.interrupt(True)
                def drained():
                    if self.busy:
                        return True
                    self.win.close()
                    return False
                GLib.timeout_add(30,drained)
            return True

    return Desktop(app, loop, bridge)


def main():
    Gio, _, Gtk = _gtk()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app=Gtk.Application(application_id="io.github.Tihulu.Jarvis", flags=Gio.ApplicationFlags.FLAGS_NONE)
    state=[]
    def activate(a):
        if a.get_active_window():
            a.get_active_window().present()
        else:
            state.append(create_window(a, loop))
    app.connect("activate",activate)
    try:
        with gtk_asyncio(loop):
            return app.run(None)
    finally:
        pending=asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    raise SystemExit(main())

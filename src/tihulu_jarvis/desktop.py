# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Opt-in GTK 4 desktop client. No listener, background capture or provider login."""
import asyncio
import json
import locale
import os
import time
from threading import Event
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from . import (a11y, agents, attachments, catalog_sources, files, keys, queries,
                 readiness, receipts, resources, tasks, transcript, usage, web)
from .voice.jan import MAX_TEXT as STREAM_CAP
from . import catalog_fetch, disclosure, routing, model_discovery
from .owned_wait import finish_owned
from .analysis_ui import AnalysisView
from .chat_files import ChatFiles
from .selected_csv import selection_identity
from . import analysis_intent, local_actions
from . import locks, search, states
from . import audio_choice, market, profiles, settings
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
            self.attachment_live = None
            self.history_attachment_live = None
            self.leases = locks.Leases()
            self.search_matches = ()
            self.search_recall_epoch = 0
            self.growing = {}
            self.transcribe_runner = None
            self.result_watch = None
            self.research_folder = self.research_context = None
            self.win = Gtk.ApplicationWindow(application=app, title="TIHULU JARVIS")
            self.win.set_default_size(920, 650)
            self.session, self.inventory = Session(), ()
            self.stage, self.busy = "ready", False
            self.finish, self.playback, self.closing = None, None, False
            self.playback_stopping = False
            self.cfg = settings.empty()
            read_error = ""
            try:
                if settings.choice_pending():
                    if settings.audio_choice_pending():
                        raise Hold("llm_choice_recovery_required")
                    settings.recover_jan_choice(profiles.Store(profiles.profile_dir()))
            except (Hold, OSError):
                read_error = "llm_choice_recovery_required"
            try:
                if settings.audio_choice_pending():
                    registry = market.Registry(market.market_dir())
                    if settings.choice_pending() or registry.recovery_pending():
                        raise Hold(audio_choice.REASON)
                    audio_choice.recover(registry)
            except (Hold, OSError):
                read_error = (read_error + " " if read_error else "") + audio_choice.REASON
            try:
                self.cfg = settings.load()
            except Hold as e:
                read_error = (read_error + " " if read_error else "") + str(e)
            self.saved_cfg = deepcopy(self.cfg)
            self.runtime_model_proofs = market.RuntimeModelProofs()
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
            workspace = Gtk.Box(spacing=12, vexpand=True)
            root.append(workspace)
            self.navigation = Gtk.StackSidebar(stack=self.stack)
            nav_scroll = Gtk.ScrolledWindow(vexpand=True)
            nav_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            nav_scroll.set_child(self.navigation)
            workspace.append(nav_scroll)
            self.stack.set_hexpand(True)
            workspace.append(self.stack)
            chat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
            self.stack.add_titled(chat,"conversation",tr("Chat & Voice","Sohbet ve ses","Chat & Sprache"))
            self.setup = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            chat.append(self.setup)
            self.setup_note = Gtk.Label(xalign=0, wrap=True)
            self.setup.append(self.setup_note)
            self.setup_connect = self.button(self.setup, tr(
                "Connect local model", "Yerel model bağla", "Lokales Modell verbinden"),
                lambda: self.stack.set_visible_child_name("models"))
            self.setup_connect.set_halign(Gtk.Align.START)
            self.log = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.scroll = Gtk.ScrolledWindow(vexpand=True)
            self.scroll.set_child(self.log)
            chat.append(self.scroll)
            self.result_actions_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            chat.append(self.result_actions_slot)
            self.search_panel = Gtk.Expander(label=tr(
                "Find in conversation", "Sohbette bul", "Im Gespräch suchen"))
            searchbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.search_panel.set_child(searchbox)
            self.search_panel.connect("notify::expanded", lambda *_: self.run_search())
            chat.append(self.search_panel)
            searchrow = Gtk.Box(spacing=8)
            self.search_entry = Gtk.Entry(hexpand=True,placeholder_text=tr("Search this conversation…","Bu sohbette ara…","In diesem Gespräch suchen…"))
            searchrow.append(self.search_entry)
            self.search_submit = self.button(searchrow, tr("Search", "Ara", "Suchen"),
                                             lambda: self.run_search(remember=True))
            self.search_clear = self.button(searchrow,tr("Clear","Temizle","Leeren"),self.clear_search)
            self.export_chat = self.button(searchrow, tr("Export conversation",
                                                         "Sohbeti dışa aktar",
                                                         "Gespräch exportieren"),
                                           self.export_conversation)
            searchbox.append(searchrow)
            self.search_results = Gtk.ListBox()
            searchbox.append(self.search_results)
            self.search_note = Gtk.Label(xalign=0, wrap=True)
            searchbox.append(self.search_note)
            self.search_entry.connect("changed", lambda *_: self.run_search())
            self.search_entry.connect("activate", lambda *_: self.run_search(remember=True))
            self.search_results.connect("row-selected", lambda *_: self.show_match())
            self.search_history = Gtk.ListBox()
            searchbox.append(self.search_history)
            historyrow = Gtk.Box(spacing=8)
            searchbox.append(historyrow)
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
            self.talk = self.button(entryrow, tr("Talk", "Konuş", "Sprechen"), self.voice)
            chat.append(entryrow)
            chat.append(self.attachment_note)
            self.attachment = None
            self.attachment_invalidated = False
            self.remove_attachment_button = self.button(chat, tr("Remove attachment", "Eki kaldır", "Anhang entfernen"), self.remove_attachment)
            self.remove_attachment_button.set_visible(False)
            controls = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6,row_spacing=4)
            chat.append(controls)
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
            self.model_discovery_note = Gtk.Label(xalign=0, wrap=True)
            models.append(self.model_discovery_note)
            self.models = Gtk.ComboBoxText()
            models.append(self.models)
            self.models.connect("changed", self.select_model)
            self.return_to_chat = self.button(models, tr(
                "Return to chat", "Sohbete dön", "Zum Chat zurück"),
                lambda: self.stack.set_visible_child_name("conversation"))
            models.append(Gtk.Label(label=tr(
                "Optional voice setup", "İsteğe bağlı ses ayarları", "Optionale Spracheinrichtung"),
                xalign=0))
            self.url.connect("changed", lambda *_:self.forget_inventory())
            self.labels, self.pickers, self.previews = {}, [], {}
            self.preview_locales = {}
            self.voice_setup_note = Gtk.Label(xalign=0, wrap=True)
            models.append(self.voice_setup_note)
            self.model_scan_result = self.model_scan_context = None
            self.model_scan = self.button(models, tr(
                "Find existing audio models in a folder", "Klasörde mevcut ses modellerini bul",
                "Vorhandene Audiomodelle im Ordner suchen"), self.choose_model_folder)
            self.model_scan_note = Gtk.Label(xalign=0, wrap=True)
            models.append(self.model_scan_note)
            self.model_candidates = Gtk.ComboBoxText()
            models.append(self.model_candidates)
            self.model_kind = Gtk.ComboBoxText()
            for key, caption in (("stt", "Whisper STT"), ("tr", "Piper TR"),
                                 ("en", "Piper EN"), ("de", "Piper DE")):
                self.model_kind.append(key, caption)
            self.model_kind.set_active_id("stt")
            models.append(self.model_kind)
            self.model_candidate_choose = self.button(models, tr(
                "Review selected file", "Seçilen dosyayı incele", "Ausgewählte Datei prüfen"),
                self.review_model_candidate)
            self.model_candidates.connect("changed", lambda *_: self.refresh_model_scan())
            self.model_kind.connect("changed", lambda *_: self.refresh_model_scan())
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
            self.test_probe_route = None
            self.test_probe_pending = False
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
            self.download_queue_error = ""
            recovery_at_start = self.registry_recovery_needed()
            if recovery_at_start:
                self.download_queue_error = tr("Registry recovery pending.",
                    "Model kaydı kurtarması bekliyor.", "Registry-Wiederherstellung ausstehend.")
            else:
                self.restore_download_queue()
            self.build_downloads_page()
            self.build_profiles_page()
            self.build_catalog_page()
            try:
                if recovery_at_start:
                    self.build_market_page(cards=(), recovery_error=self.download_queue_error)
                else:
                    self.build_market_page()
            except (Hold, OSError) as exc:
                read_error = (read_error + " " if read_error else "") + str(exc)
            try:
                profiles.ensure_default(self.store)
                active = self.store.startup_profile(self.cfg.get("profile"))
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
            self.tool_panel = Gtk.Expander(label=tr("Local tools", "Yerel araçlar", "Lokale Werkzeuge"))
            toolbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.tool_panel.set_child(toolbox)
            chat.insert_child_after(self.tool_panel, self.search_panel)
            self.chat_transcribe = self.button(toolbox, tr(
                "Transcribe a recording", "Kayıttan yazıya çevir", "Aufnahme transkribieren"),
                self.transcribe_recording)
            self.chat_transcribe_note = Gtk.Label(xalign=0, wrap=True)
            toolbox.append(self.chat_transcribe_note)
            self.analysis = AnalysisView(self, toolbox, Gtk, GLib, tr, lang,
                                         result_parent=self.result_actions_slot)
            self.chat_files = ChatFiles(self, toolbox, self.result_actions_slot, Gtk, tr)
            self.refresh()
            self.win.present()
            if recovery_at_start:
                self.start_registry_recovery()

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
            self.stack.set_visible_child_name("conversation")
            self.search_panel.set_expanded(True)
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
            if detail == "conversation_route_changed_end_conversation":
                detail = tr(
                    "Connection or profile changed. End the conversation or restore the previous connection before continuing.",
                    "Bağlantı veya profil değişti. Devam etmek için sohbeti sonlandırın veya önceki bağlantıya dönün.",
                    "Verbindung oder Profil geändert. Beenden Sie das Gespräch oder stellen Sie die vorherige Verbindung wieder her.")
            self.status.set_text(detail or state.replace("_"," ").capitalize())
            self.refresh()

        def build_profiles_page(self):
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
            view = Gtk.ScrolledWindow(vexpand=True)
            view.set_child(page)
            self.stack.add_titled(view,"profiles",tr("Profiles","Profiller","Profile"))
            self.profile_note = Gtk.Label(xalign=0,wrap=True)
            page.append(self.profile_note)
            self.profile_storage_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.profile_storage_note)
            self.policy_clock_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.policy_clock_note)
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
            except Hold:
                self.audit_list.set_text("audit_unavailable")
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
                self.audit_list.set_text("audit_unavailable")
                return
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
            invalid = 0
            for pid in self.store.ids():
                try:
                    entry = self.store.load(pid)
                except Hold:
                    invalid += 1
                    continue
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
            self.profile_storage_note.set_text(tr(
                "%d unreadable profile(s); skipped, not activated." % invalid,
                "%d okunamayan profil; atlandı, etkinleştirilmedi." % invalid,
                "%d unlesbare Profile; übersprungen, nicht aktiviert." % invalid
            ) if invalid else "")
            self.refresh_profile_buttons()

        def picker_context(self):
            profile, stamp = self.store.snapshot(self.cfg.get("profile"))
            if profile.state != "active":
                raise Hold("profile_not_active")
            return (profile.id, profile.version, profile.based_on_mode, self.session.epoch, stamp)

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
            return True

        def clear_attachment(self):
            self.attachment = self.attachment_context = self.attachment_live = None
            self.attachment_invalidated = False
            self.attachment_note.set_text("")
            self.remove_attachment_button.set_visible(False)

        def remove_attachment(self):
            if not self.busy:
                self.clear_attachment()
                self.refresh()

        def invalidate_attachment(self):
            # Drop the data immediately, but never silently send an existing
            # draft without the attachment the owner thought was included.
            self.clear_attachment()
            self.attachment_invalidated = True
            self.attachment_note.set_text(tr(
                "Attachment permission expired or changed. Reattach or remove before sending.",
                "Dosya eki izni sona erdi veya değişti. Göndermeden önce yeniden ekle veya kaldır.",
                "Anhangberechtigung abgelaufen oder geändert. Vor dem Senden erneut anhängen oder entfernen."))
            self.remove_attachment_button.set_visible(True)

        def drop_attachment_for_context_change(self):
            # A retained draft must not silently lose its selected data.
            if self.attachment_invalidated or (self.attachment is not None and self.entry.get_text().strip()):
                self.invalidate_attachment()
            else:
                self.clear_attachment()

        def refresh_attachment_scope(self):
            if self.attachment is not None and not self.busy:
                try:
                    self.attachment_policy()
                    valid = (self.attachment_route() == self.attachment_context
                             and self.attachment_live_allowed(self.attachment_live))
                except Hold:
                    valid = False
                if not valid:
                    self.invalidate_attachment()
            self.remove_attachment_button.set_sensitive(not self.busy)

        def jan_choice_blocked(self):
            try:
                return settings.choice_pending()
            except Hold:
                return True

        def audio_choice_blocked(self):
            try:
                return settings.audio_choice_pending()
            except Hold:
                return True

        def require_audio_choice(self):
            if self.audio_choice_blocked():
                raise Hold(audio_choice.REASON)
            settings.require_current_audio_selection(self.saved_cfg)

        def attachment_route(self):
            if self.jan_choice_blocked():
                raise Hold("llm_choice_recovery_required")
            profile, stamp = self.store.snapshot(self.cfg.get("profile"))
            if profile.state != "active":
                raise Hold("profile_not_active")
            return (profile.id, profile.version, profile.based_on_mode,
                    self.url.get_text().strip(), self.cfg["llm"], stamp)

        def activate_profile(self, profile_id, quiet=False, expected=None, *, persist=True):
            entry, stamp = self.store.snapshot(profile_id)
            if expected is not None and (entry, stamp) != expected:
                raise Hold("profile_changed")
            if entry.state != "active":
                raise Hold("profile_not_active")
            self.dismiss_picker()
            self.search_recall_epoch += 1
            self.drop_attachment_for_context_change()
            self.clear_model_scan()
            self.clear_model_probe()
            if hasattr(self, "folder_entries"):
                self.clear_folder_view()
            if hasattr(self, "chat_files"):
                self.chat_files.reset()
            if hasattr(self, "analysis"):
                self.analysis.reset()
            self.cfg["profile"] = entry.id
            self.policy = profiles.effective(entry)
            if persist:
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

        def switch_context(self):
            active = self.cfg.get("profile")
            try:
                current = self.store.snapshot(active)
            except (Hold, KeyError):
                current = None
            return active, current

        def switch_profile(self, profile_id):
            if self.busy or self.closing:
                raise Hold("profile_switch_busy")
            target, stamp = self.store.snapshot(profile_id)
            if target.state != "active":
                raise Hold("profile_not_active")
            context = self.switch_context()
            current = context[1]
            now = self.store.policy_now()
            extra = (profiles.granted(target, now) if current is None
                     else profiles.escalation(current[0], target, now))
            if not extra:
                if self.switch_context() != context:
                    raise Hold("profile_changed")
                self.activate_profile(profile_id, expected=(target, stamp))
                return None
            return self.confirm_escalation(target, extra, stamp, context)

        def confirm_escalation(self, target, extra, stamp, context):
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
                        lambda: self.apply_escalation(target, dialog, lines, extra, stamp, context))
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

        def apply_escalation(self, target, dialog, lines, extra, stamp, context):
            if dialog not in self.confirmations:
                return
            self.forget_confirmation(dialog)
            dialog.close()
            try:
                if (self.busy or self.closing or self.switch_context() != context
                        or self.store.snapshot(target.id) != (target, stamp)):
                    raise Hold("profile_changed")
                now = self.store.policy_now()
                current = context[1]
                remaining = (profiles.granted(target, now) if current is None
                             else profiles.escalation(current[0], target, now))
                if not remaining <= extra or self.switch_context() != context:
                    raise Hold("profile_changed")
                self.activate_profile(target.id, expected=(target, stamp))
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
            # Both visible entry points must use the same consent boundary.
            self.switch_selected()

        def clone_selected(self):
            chosen = self.selected_profile()
            if chosen is None or self.busy:
                return
            try:
                current, _ = self.store.snapshot(chosen.id)
                if current != chosen:
                    raise Hold("profile_changed")
                clone = profiles.clone_to_custom(current, current.name + " copy")
                self.store.save(clone, create_only=True)
                self.refresh_profiles()
                self.switch_profile(clone.id)
            except Hold as exc:
                self.set_state("ready", str(exc))

        def delete_selected(self):
            chosen = self.selected_profile()
            if chosen is None or self.busy or chosen.id == self.cfg.get("profile"):
                return
            try:
                self.store.delete(chosen.id, expected=chosen)
                self.refresh_profiles()
            except Hold as exc:
                self.set_state("ready", str(exc))

        def create_template(self):
            if self.busy:
                return
            key = self.profile_template.get_active_text()
            try:
                entry = profiles.template(key)
                if (self.store.root / (entry.id + ".json")).is_file():
                    raise Hold("profile_already_exists")
                self.store.save(entry, create_only=True)
                self.refresh_profiles()
                self.switch_profile(entry.id)
            except Hold as exc:
                self.set_state("ready", str(exc))

        def import_profile_text(self, raw):
            """An imported copy arrives pending review, never activated."""
            return self.store_imported_profile(profiles.Store._parse(raw))

        def store_imported_profile(self, entry):
            clean = profiles.sanitized(entry)
            self.store.save(clean, create_only=True)
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
                    self.store_imported_profile(self.store.import_file(path))
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
            if self.jan_choice_blocked():
                self.route_note.set_text("llm_choice_recovery_required")
                return
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

        def run_search(self, *, remember=False):
            """Search the local conversation; results are snippets, bounded.

            Nothing leaves this machine, and the field is disabled while a turn
            runs so a search can never race a reply.
            """
            if not hasattr(self, "search_note"):
                return
            query = self.search_entry.get_text().strip()
            self.search_matches = ()
            item = self.search_results.get_first_child()
            while item is not None:
                nxt = item.get_next_sibling()
                self.search_results.remove(item)
                item = nxt
            if not query or not self.search_panel.get_expanded():
                self.search_note.set_text("")
                return
            try:
                self.search_matches = search.find(self.session.history, query)
            except Hold as exc:
                self.search_matches = ()
                self.search_note.set_text(str(exc))
                return
            # Live results are transient. Only explicit submission saves a query.
            if remember and not self.busy:
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
                row.connect("activate", self.activate_search_history)
                self.search_history.append(row)
            self.search_history_label.set_visible(bool(self.cfg.get("search_history")))
            self.search_forget.set_sensitive(bool(self.cfg.get("search_history")))

        def activate_search_history(self, row):
            # Rebuilding the list during GTK's row activation can invalidate
            # the row whose native activation is still being dispatched.
            if not self.busy and self.search_panel.get_expanded():
                try:
                    context = self.picker_context()
                except Hold:
                    return
                GLib.idle_add(self.recall_search, row.query, self.search_recall_epoch, context)

        def recall_search(self, query, epoch, context):
            try:
                if self.picker_context() != context:
                    return
            except Hold:
                return
            if (epoch == self.search_recall_epoch and query in self.cfg.get("search_history", [])
                    and not self.busy and not self.closing and self.search_panel.get_expanded()):
                self.search_entry.set_text(query)
                self.run_search(remember=True)

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
            self.search_recall_epoch += 1
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
            self.search_recall_epoch += 1
            self.search_entry.set_text("")
            self.run_search()
            if self.search_panel.get_expanded():
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
            boundary = tr("privacy boundary unverified",
                          "gizlilik sınırı doğrulanmadı",
                          "Datenschutzgrenze nicht verifiziert")
            try:
                data = disclosure.facts(profile, self.url.get_text().strip(), self.store.policy_now())
            except (Hold, UnboundLocalError):
                data = None
            if data is not None:
                if data["boundary"] != disclosure.STAYS_LOCAL:
                    boundary = tr("cloud reasoning permitted; upstream unverified",
                                  "bulut çıkarımına izinli; üst servis doğrulanmadı",
                                  "Cloud-Schlussfolgerung erlaubt; Upstream nicht verifiziert")
                elif data["endpoint"] == "loopback":
                    boundary = tr("loopback only; upstream unverified",
                                  "yalnızca geri döngü; üst servis doğrulanmadı",
                                  "Nur Loopback; Upstream nicht verifiziert")
            parts = [mode or tr("no mode", "mod yok", "kein Modus"), name, model, boundary]
            if not self.url.get_text().strip():
                parts.append(tr("connection not configured", "bağlantı yapılandırılmadı",
                                "Verbindung nicht eingerichtet"))
            elif data is not None and data["endpoint"] != "loopback":
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
                data = disclosure.facts(profile, self.url.get_text().strip(), self.store.policy_now())
                grants = data["grants"]
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
            data = disclosure.facts(profile, endpoint, self.store.policy_now())
            if not endpoint:
                body = tr("No connection configured. Choose a local Jan endpoint to chat.",
                          "Bağlantı yapılandırılmadı. Sohbet için yerel Jan bağlantısını seç.",
                          "Keine Verbindung eingerichtet. Lokalen Jan-Zugang zum Chatten wählen.")
            elif data["endpoint"] != "loopback":
                body = tr("The configured endpoint is not a loopback address; this build refuses it.",
                          "Yapılandırılan uç nokta geri döngü adresi değil; bu sürüm onu reddeder.",
                          "Der konfigurierte Endpunkt ist keine Loopback-Adresse; dieser Build lehnt ihn ab.")
            else:
                body = tr("Loopback transport only. Upstream processing is unverified.",
                          "Yalnızca geri döngü bağlantısı. Üst servisin veri işlemesi doğrulanmadı.",
                          "Nur Loopback-Transport. Upstream-Verarbeitung nicht verifiziert.")
                if data["boundary"] == disclosure.CLOUD_WITH_APPROVAL:
                    body += " " + tr("Approved providers: %s. Direct cloud transport is unavailable." % ", ".join(data["providers"]),
                                     "Onaylı sağlayıcılar: %s. Doğrudan bulut bağlantısı kullanılamıyor." % ", ".join(data["providers"]),
                                     "Genehmigte Anbieter: %s. Direkter Cloud-Transport nicht verfügbar." % ", ".join(data["providers"]))
                elif data["boundary"] == disclosure.CLOUD_WITHOUT_APPROVAL:
                    body += " " + tr("No provider is approved; direct cloud routes are refused.",
                                     "Onaylı sağlayıcı yok; doğrudan bulut rotaları reddedilir.",
                                     "Kein Anbieter genehmigt; direkte Cloud-Routen werden abgelehnt.")
            self.disclosure_note.set_text(tr("Privacy: ", "Gizlilik: ", "Privatsphäre: ") + body)

        def refresh_send(self):
            text = self.entry.get_text()
            connected = self.cfg["llm"] in self.inventory and not self.jan_choice_blocked()
            direct = analysis_intent.direct(text) is not None or local_actions.direct(text) is not None
            local = getattr(self, "policy", None) is not None and self.policy.mode == "LOCAL_ONLY"
            self.send.set_sensitive(not self.busy and (connected or (local and direct)))

        def refresh(self):
            blocked = self.jan_choice_blocked()
            connected = self.cfg["llm"] in self.inventory and not blocked
            self.setup.set_visible(not connected)
            self.return_to_chat.set_sensitive(not self.busy and connected)
            if blocked:
                model_note = tr("Model choice recovery required. Local model connection blocked.",
                                "Model seçimi kurtarması gerekli. Yerel model bağlantısı engellendi.",
                                "Modellauswahl muss wiederhergestellt werden. Lokale Verbindung gesperrt.")
            elif connected:
                model_note = tr("Model selected. Text chat is available.",
                                "Model seçildi. Yazılı sohbet kullanılabilir.",
                                "Modell ausgewählt. Textchat verfügbar.")
            elif self.inventory:
                model_note = tr("Choose one of the discovered models.",
                                "Bulunan modellerden birini seç.",
                                "Eines der gefundenen Modelle auswählen.")
            else:
                model_note = tr("No current model inventory. Discover or retry the connection.",
                                "Güncel model listesi yok. Bağlantıyı keşfet veya yeniden dene.",
                                "Keine aktuelle Modellliste. Verbindung suchen oder erneut versuchen.")
            self.model_discovery_note.set_text(model_note)
            self.refresh_model_scan()
            self.refresh_research_scope()
            self.refresh_attachment_scope()
            self.refresh_model_probe(connected)
            self.setup_connect.set_sensitive(not self.busy and not blocked)
            if hasattr(self, "settings_choice_inspect"):
                self.refresh_choice_recovery()
            if not self.url.get_text().strip():
                self.setup_note.set_text(tr(
                    "Start with your local model. Open Jan, enter its local API URL, discover models and choose one. No download is needed.",
                    "Yerel modelinle başla. Jan'ı aç, yerel API adresini gir, modelleri bul ve birini seç. İndirme gerekmez.",
                    "Mit dem lokalen Modell starten. Jan öffnen, lokale API-Adresse eingeben, Modelle suchen und eines wählen. Kein Download nötig."))
            else:
                self.setup_note.set_text(tr(
                    "Discover and select a model to start chatting. Voice setup is optional for text chat.",
                    "Sohbet için model bul ve seç. Yazılı sohbet için ses ayarları isteğe bağlıdır.",
                    "Zum Chatten ein Modell suchen und auswählen. Spracheinrichtung ist für Textchat optional."))
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
            self.search_submit.set_sensitive(not self.busy)
            self.search_history.set_sensitive(not self.busy)
            # An export needs something to export; an empty log offers nothing.
            self.export_chat.set_sensitive(not self.busy and bool(self.session.history))
            self.attach.set_sensitive(not self.busy and connected)
            self.entry.set_sensitive(not self.busy)
            self.refresh_send()
            now = self.store.policy_now()
            self.policy_clock_note.set_text(tr(
                "Permission clock unavailable. Timed permissions are blocked; restart after checking local storage.",
                "İzin saati kullanılamıyor. Süreli izinler engellendi; yerel depolamayı kontrol edip yeniden başlatın.",
                "Berechtigungsuhr nicht verfügbar. Befristete Berechtigungen sind gesperrt; lokalen Speicher prüfen und neu starten."
            ) if now == float("inf") else "")
            try:
                active = self.active_profile()
                allowed = (active.state == "active" and profiles.effective(active).evaluate(
                    "local_audio_device", ("push_to_talk",), now)[0] == profiles.ALLOW)
            except Hold:
                allowed = False
            stt_present = bool(self.cfg["stt"]) and Path(self.cfg["stt"]).is_file()
            voices_present = tuple(lang for lang, path in self.cfg["voices"].items()
                                   if path and Path(path).is_file())
            missing_tools = tuple(name for name in readiness.AUDIO_TOOLS
                                  if not audio.tools_ready(name))
            audio_blocked = self.audio_choice_blocked()
            voice_ready = allowed and stt_present and bool(voices_present) and not missing_tools and not audio_blocked
            if audio_blocked:
                voice_note = tr("Audio model choice recovery required. Voice operations blocked.",
                                "Ses modeli seçimi kurtarması gerekli. Ses işlemleri engellendi.",
                                "Audiomodellauswahl muss wiederhergestellt werden. Sprachfunktionen gesperrt.")
            elif not allowed:
                voice_note = tr("Audio permission is not granted.", "Ses izni verilmemiş.",
                                "Audiofreigabe fehlt.")
            elif missing_tools:
                voice_note = tr("Missing audio tools: ", "Eksik ses araçları: ",
                                "Fehlende Audiowerkzeuge: ") + ", ".join(missing_tools)
            elif not stt_present:
                voice_note = tr("Choose an existing Whisper model.", "Mevcut bir Whisper modeli seç.",
                                "Vorhandenes Whisper-Modell wählen.")
            elif not voices_present:
                voice_note = tr("Choose an existing Piper voice.", "Mevcut bir Piper sesi seç.",
                                "Vorhandene Piper-Stimme wählen.")
            else:
                voice_note = tr("Audio files and tools detected; test playback and microphone.",
                                "Ses dosyaları ve araçlar bulundu; ses ve mikrofonu test et.",
                                "Audiodateien und Werkzeuge gefunden; Wiedergabe und Mikrofon testen.")
                absent = tuple(lang for lang in ("tr", "en", "de") if lang not in voices_present)
                if absent:
                    voice_note += " " + tr("Unconfigured voices: ", "Ayarlanmamış sesler: ",
                                           "Nicht eingerichtete Stimmen: ") + ", ".join(absent)
            self.voice_setup_note.set_text(voice_note)
            self.talk.set_sensitive(not self.busy and connected and voice_ready)
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
            if hasattr(self, "chat_files"):
                self.chat_files.refresh()
                if self.chat_files.folder is not None:
                    self.hard.set_sensitive(True)
                    self.quick_stop.set_sensitive(True)
            self.refresh_local_stop()
            self.watch_local_results()
            # Keep turn-specific actions visible only while they can be used.
            # The top-bar hard stop remains permanently visible.
            self.finish_btn.set_visible(self.finish_btn.get_sensitive())
            self.stop.set_visible(self.stop.get_sensitive())
            self.cancel.set_visible(self.cancel.get_sensitive())
            self.hard.set_visible(self.hard.get_sensitive())
            for w in (self.reload,self.url,self.models,*self.pickers,self.test_chat_button,
                      self.transcribe_button,*self.previews):
                w.set_sensitive(not self.busy)
            transcribe_ready = stt_present and audio.tools_ready("whisper-cli")
            self.transcribe_button.set_sensitive(not self.busy and transcribe_ready and not audio_blocked)
            if hasattr(self, "chat_transcribe"):
                self.chat_transcribe.set_sensitive(not self.busy and transcribe_ready and not audio_blocked)
                if audio_blocked:
                    note = tr("Audio model choice recovery required.",
                              "Ses modeli seçimi kurtarması gerekli.",
                              "Audiomodellauswahl muss wiederhergestellt werden.")
                elif not stt_present:
                    note = tr("Select a local Whisper model in Installed models.",
                              "Kurulu modellerden yerel Whisper modeli seç.",
                              "Unter Installierte Modelle ein lokales Whisper-Modell wählen.")
                elif not transcribe_ready:
                    note = tr("Missing whisper-cli.", "whisper-cli eksik.", "whisper-cli fehlt.")
                else:
                    note = tr("Local WAV transcription; no chat model or microphone needed.",
                              "Yerel WAV yazıya çevirme; sohbet modeli veya mikrofon gerekmez.",
                              "Lokale WAV-Transkription; kein Chatmodell oder Mikrofon nötig.")
                self.chat_transcribe_note.set_text(note)
            if not self.busy:
                self.test_chat_button.set_sensitive(connected)
                for widget, lang in self.previews.items():
                    # A preview speaks only the locale that is actually selected.
                    widget.set_sensitive(lang in voices_present and allowed and not audio_blocked
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
                snapshot = deepcopy(self.cfg)
                settings.save(snapshot, expected=self.saved_cfg)
                self.saved_cfg = snapshot
                return True
            except (Hold,OSError) as exc:
                self.status.set_text(str(exc) if isinstance(exc, Hold) else "Could not save local choices")
                return False

        def clear_model_probe(self):
            self.test_probe_route = None
            self.test_probe_pending = False
            self.test_note.set_text("")

        def refresh_model_probe(self, connected):
            if self.test_probe_route is not None and (
                    not connected or not self.route_current(self.test_probe_route)):
                self.clear_model_probe()
            if self.test_probe_pending and not self.busy:
                # Also covers cancellation before the coroutine starts.
                self.test_probe_pending = False
                self.test_note.set_text(tr(
                    "Model test did not complete. Check status and retry.",
                    "Model testi tamamlanmadı. Durumu kontrol edip yeniden dene.",
                    "Modelltest nicht abgeschlossen. Status prüfen und erneut versuchen."))

        def forget_inventory(self):
            self.search_recall_epoch += 1
            self.clear_model_probe()
            self.inventory = ()
            self.models.remove_all()
            self.refresh()

        def select_model(self, widget):
            value = widget.get_active_text()
            if not value or value not in self.inventory or value == self.cfg["llm"]:
                return
            previous = self.cfg["llm"]
            try:
                if self.busy or self.closing:
                    raise Hold("model_selection_busy")
                card = self.market.lookup_card(value)
                if card is None:
                    # This ID was returned by the current local inventory.
                    discovered = market.ModelCard(
                        model_id=value, display_name=value, category="llm",
                        provider="jan", engine="jan", installed=True,
                        trust_level="curated")
                    self.market.record_jan_inventory((discovered,))
                    card = self.market.card(value)
                if not self.market._jan_inventory_card(card):
                    raise Hold("registry_discovery_conflict")
                self.use_card(value, expected=card)
                self.build_market_page()
            except (Hold, OSError) as exc:
                widget.set_active(self.inventory.index(previous)
                                  if previous in self.inventory else -1)
                self.set_state("ready", str(exc) if isinstance(exc, Hold)
                               else "model_selection_write_failed")

        def update_labels(self):
            for key,label in self.labels.items():
                p = self.cfg["stt"] if key=="stt" else self.cfg["voices"].get(key)
                label.set_text(Path(p).name if p else "—")

        @staticmethod
        def inspect_audio_import(key, path, *, checkpoint=lambda: None,
                                 expected_weights=None, expected_voice=None):
            """Disk-only inspection, with no GTK or registry effects."""
            if key not in ("stt", "tr", "en", "de"):
                raise Hold("invalid_audio_choice_transition")
            checkpoint()
            category = "stt" if key == "stt" else "tts"
            engine = "whisper.cpp" if key == "stt" else "piper"
            card, proof = market.inspect_file(
                str(audio.selected_file(path, ".bin" if key == "stt" else ".onnx")),
                category, engine=engine, languages=(() if key == "stt" else (key,)),
                checkpoint=checkpoint, expected_identity=expected_weights)
            voice_proof = None
            if key != "stt":
                _, voice_proof = audio.inspect_voice(path, key, checkpoint=checkpoint,
                                                      expected_identity=expected_voice)
            market.require_import_proof(proof, checkpoint=checkpoint)
            return card, proof, voice_proof

        @staticmethod
        def check_import_inspection(key, path, inspected, *, checkpoint=lambda: None):
            card, proof, voice_proof = inspected
            if (proof.path != Path(path)
                    or card.category != ("stt" if key == "stt" else "tts")
                    or card.languages != (() if key == "stt" else (key,))
                    or card.storage_ref != "sha256-" + proof.digest[:32]
                    or card.disk_size_bytes != proof.identity[2]
                    or (key != "stt" and (voice_proof is None or
                        voice_proof[0] != Path(path).with_name(Path(path).name + ".json")))):
                raise Hold("model_file_changed")
            market.require_import_proof(proof, checkpoint=checkpoint)
            if voice_proof is not None:
                audio.require_voice_proof(voice_proof, checkpoint=checkpoint)

        def register_choice(self, key, path, *, inspected=None, checkpoint=lambda: None):
            """Register a truthful card, then remember the local reference."""
            settings._require_no_choice(settings.config_path())
            if inspected is None:
                inspected = self.inspect_audio_import(key, path, checkpoint=checkpoint)
            self.check_import_inspection(key, path, inspected, checkpoint=checkpoint)
            card = inspected[0]
            known = self.market.lookup_card(card.model_id)
            if known is not None:
                if (known.owned_by_jarvis or known.shared_storage_owner
                        or known.storage_ref != card.storage_ref
                        or known.category != card.category or known.engine != card.engine
                        or known.languages != card.languages or known.disk_size_bytes != card.disk_size_bytes
                        or known.local_or_cloud != "local"
                        or known.state not in ("REGISTERED", "TESTABLE", "ACTIVE")):
                    raise Hold("registry_import_conflict")
                # Re-import is not a downgrade of a previously selected card.
                card = known
            proposed = settings.checked(self.cfg)
            proposed["market_refs"][card.storage_ref] = str(path)
            digests = proposed.setdefault("audio_digests", {})
            previous_digest = digests.get(card.storage_ref)
            if previous_digest is not None and previous_digest != inspected[1].digest:
                raise Hold("model_digest_mismatch")
            digests[card.storage_ref] = inspected[1].digest
            if key != "stt":
                voices = proposed.setdefault("voice_digests", {})
                previous_voice = voices.get(card.storage_ref)
                if previous_voice is not None and previous_voice != inspected[2][2]:
                    raise Hold("voice_digest_mismatch")
                voices[card.storage_ref] = inspected[2][2]
            with self.market.transfer_lease(card.model_id), self.market._write_guard():
                if self.market.lookup_card(card.model_id) != known:
                    raise Hold("registry_card_changed")
                with settings.choice_transaction(proposed, expected=self.cfg):
                    self.check_import_inspection(key, path, inspected, checkpoint=checkpoint)
                    if known is None:
                        card = self.market.save_card(card, expected=None)
            self.cfg = proposed
            self.saved_cfg = deepcopy(proposed)
            self.build_market_page()
            return card

        def select_imported_audio(self, key, path, *, inspected=None, checkpoint=lambda: None):
            """One picker selection uses the same durable preference as AI Market."""
            if inspected is None:
                inspected = self.inspect_audio_import(key, path, checkpoint=checkpoint)
            card = self.register_choice(key, path, inspected=inspected, checkpoint=checkpoint)
            def checked_import():
                self.check_import_inspection(key, path, inspected, checkpoint=checkpoint)
            checked_import()
            self.use_card(card.model_id, expected=card, checkpoint=checked_import)
            self.build_market_page()

        def start_audio_import(self, key, path):
            if self.busy or self.closing:
                return
            try:
                settings._require_no_choice(settings.config_path())
                profile, stamp = self.store.snapshot(self.cfg["profile"])
                if profile.state != "active":
                    raise Hold("profile_not_active")
                original_cfg = settings.checked(self.cfg)
                if key not in ("stt", "tr", "en", "de"):
                    raise Hold("invalid_audio_choice_transition")
                path = str(audio.selected_file(path, ".bin" if key == "stt" else ".onnx"))
                weights_identity = market.import_file_identity(path)
                voice_identity = None if key == "stt" else audio.voice_config_identity(path)
            except (Hold, OSError) as exc:
                self.set_state("ready", str(exc))
                return
            stop = Event()

            def checkpoint():
                if stop.is_set():
                    raise Hold("turn_cancelled")
                if not self.store.is_current(profile.id, stamp):
                    raise Hold("profile_changed")

            async def run(epoch):
                self.session.check(epoch)
                checkpoint()
                self.registry_worker_stop = stop.set
                worker = asyncio.create_task(asyncio.to_thread(
                    self.inspect_audio_import, key, path, checkpoint=checkpoint,
                    expected_weights=weights_identity, expected_voice=voice_identity))
                try:
                    try:
                        inspected = await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        stop.set()
                        await finish_owned(asyncio.gather(worker, return_exceptions=True))
                        raise
                    self.session.check(epoch)
                    checkpoint()
                    if self.cfg != original_cfg:
                        raise Hold("settings_changed")
                    self.select_imported_audio(key, path, inspected=inspected, checkpoint=checkpoint)
                    self.turn_outcome = tr("Model selected.", "Model seçildi.", "Modell ausgewählt.")
                finally:
                    self.registry_worker_stop = None

            self.submit(run, label=tr("Verify local model file", "Yerel model dosyasını doğrula",
                                      "Lokale Modelldatei prüfen"))

        def persist_jan_choice(self, card, expected):
            """Commit a local Jan selection without publishing a premature UI choice.

            A truthful registration can survive a failed choice. Profile history
            writes can fail after the active profile changed; report that as
            recovery required rather than rewriting policy or claiming success.
            """
            profile, stamp = self.store.snapshot(self.cfg["profile"])
            if profile.state != "active":
                raise Hold("profile_not_active")
            proposed = settings.checked(self.cfg)
            proposed["llm"] = card.model_id
            updated = replace(profile, version=profile.version + 1,
                              llm_model_id=card.model_id).checked()
            with self.market.transfer_lease(card.model_id), self.market._write_guard():
                if self.market.card(card.model_id) != expected:
                    raise Hold("registry_card_changed")
                with self.store._mutation_lock():
                    if self.store.snapshot(profile.id) != (profile, stamp):
                        raise Hold("profile_changed")
                    with settings.choice_transaction(proposed, expected=self.cfg,
                            jan_transition=(self.store, profile, updated)):
                        if card != expected:
                            self.market.save_card(card, expected=expected)
                        try:
                            self.store._save_locked(updated)
                        except (Hold, OSError):
                            try:
                                unchanged = self.store.snapshot(profile.id) == (profile, stamp)
                            except (Hold, OSError):
                                unchanged = False
                            if not unchanged:
                                raise Hold("llm_choice_recovery_required") from None
                            raise
            self.cfg = proposed
            self.saved_cfg = deepcopy(proposed)
            self.activate_profile(updated.id, quiet=True, persist=False)
            self.models.set_active(self.inventory.index(card.model_id))
            self.refresh()

        def use_card(self, model_id, *, expected=None, checkpoint=lambda: None):
            """Validate selection before recording an activation; never start a model."""
            checkpoint()
            card = self.market.card(model_id)
            if expected is not None and card != expected:
                raise Hold("registry_card_changed")
            expected = card
            if card.compatibility_status == "incompatible_format":
                raise Hold("incompatible_model_format")
            if card.category == "llm":
                # A cloud-visible model must be one the endpoint actually serves.
                if model_id not in self.inventory:
                    raise Hold("model_not_in_inventory")
                if not self.market._jan_inventory_card(card):
                    raise Hold("registry_discovery_conflict")
                # Existing Jan registrations remain valid for repeat selection.
                if card.state == "DISCOVERED":
                    card = market.register_installed(card)
                elif not card.registered:
                    raise Hold("model_not_selectable")
                self.persist_jan_choice(card, expected)
                return
            if card.state not in ("REGISTERED", "TESTABLE", "ACTIVE"):
                raise Hold("model_not_selectable")
            if card.category not in ("stt", "tts"):
                raise Hold("model_category_not_selectable_here")
            path = self.cfg.get("market_refs", {}).get(card.storage_ref)
            if card.category == "tts":
                if len(card.languages) != 1 or card.languages[0] not in ("tr", "en", "de"):
                    raise Hold("voice_locale_unverified")
                lang = card.languages[0]
                audio.voice_for(path, lang)
            else:
                audio.selected_file(path, ".bin")
            # No invalid selection may advance the lifecycle. This is a
            # preflight, not a runtime model-load or full content verification.
            if card.state == "REGISTERED":
                card = market.advance(card, "TESTABLE")
            if card.state == "TESTABLE":
                card = market.advance(card, market.ACTIVE)
            proposed = settings.checked(self.cfg)
            if card.category == "stt":
                proposed["stt"] = path
            else:
                proposed["voices"][lang] = path
            # Bind both durable generations before changing either store.
            audio_choice.commit(self.market, expected, card, self.cfg, proposed,
                                "stt" if card.category == "stt" else lang,
                                checkpoint=checkpoint)
            self.cfg = proposed
            self.saved_cfg = deepcopy(proposed)
            self.update_labels()
            self.refresh()

        def build_market_page(self, *, cards=None, recovery_error=""):
            # An owned publication/rollback may hold the registry lock.
            # Preserve the visible snapshot until that operation drains.
            if getattr(self, "registry_worker_active", False):
                return
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
                self.market_filter.connect("changed", lambda *_: self.refresh_market_view())
                page.append(self.market_filter)
                self.market_refresh = self.button(page, tr(
                    "Refresh registry", "Model kaydını yenile", "Registry aktualisieren"),
                    self.refresh_market_view)
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
                self.market_retry = self.button(row, tr("Reset interrupted download",
                    "Kesilen indirmeyi sıfırla", "Unterbrochenen Download zurücksetzen"),
                    self.retry_selected_transfer)
                cleanup = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                page.append(cleanup)
                self.staging_note = Gtk.Label(xalign=0, wrap=True)
                cleanup.append(self.staging_note)
                self.staging_choice = Gtk.ComboBoxText()
                cleanup.append(self.staging_choice)
                controls = Gtk.Box(spacing=8)
                cleanup.append(controls)
                self.staging_scan = self.button(controls, tr(
                    "Inspect abandoned transfers", "Yarım kalan aktarımları incele",
                    "Verwaiste Übertragungen prüfen"), self.inspect_staging)
                self.staging_discard = self.button(controls, tr(
                    "Delete selected temporary file", "Seçili geçici dosyayı sil",
                    "Gewählte temporäre Datei löschen"), self.discard_staging)
                self.staging_choice.connect("changed", lambda *_: self.refresh_staging_buttons())
                self.archive_staging_scan = self.button(controls, tr(
                    "Inspect incomplete archive copies", "Yarım kalan arşiv kopyalarını incele",
                    "Unvollständige Archivkopien prüfen"),
                    lambda: self.inspect_staging(archive=True))

            self.clear_staging_review()
            wanted = self.market_filter.get_active_text() or "stt"
            try:
                if cards is None:
                    cards = self.market.cards()
            except (Hold, OSError) as exc:
                self.show_registry_hold(str(exc))
                raise
            if recovery_error:
                self.show_registry_hold(recovery_error)
                return
            rows = [c for c in cards if c.category == wanted]
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
                item.card_snapshot = card
            self.market_note.set_text(tr(
                "Installed models and voices known to this computer. Unknown values stay unknown/not tested. Import happens in Installed models.",
                "Bu bilgisayarda bilinen kurulu modeller ve sesler. Bilinmeyen değerler bilinmiyor/denenmedi olarak kalır. İçe alma Kurulu modeller bölümünde yapılır.",
                "Bekannte installierte Modelle und Stimmen. Unbekannte Werte bleiben unbekannt/nicht getestet. Import unter Installierte Modelle."))
            self.storage_note.set_text(self.storage_report(cards))
            self.refresh_market_buttons()

        def show_registry_hold(self, reason):
            self.market_note.set_text(tr(
                "Registry unavailable: ", "Model kaydı kullanılamıyor: ",
                "Registry nicht verfügbar: ") + reason)
            self.storage_note.set_text(tr(
                "Storage unknown until recovery.", "Kurtarmaya kadar depolama bilinmiyor.",
                "Speicher bis zur Wiederherstellung unbekannt."))
            self.refresh_market_buttons()

        def registry_recovery_needed(self):
            try:
                return self.market.recovery_pending()
            except OSError:
                return True

        def refresh_market_view(self):
            if self.busy or self.closing:
                return
            if self.registry_recovery_needed() or self.download_queue_error:
                self.start_registry_recovery()
                return
            try:
                self.build_market_page()
            except (Hold, OSError) as exc:
                self.set_state("ready", str(exc))

        def start_registry_recovery(self):
            """Own local journal recovery until it drains, even on window close."""
            if self.busy or self.closing:
                return
            completion, failure = [], []
            pending = tr("Registry recovery pending.", "Model kaydı kurtarması bekliyor.",
                         "Registry-Wiederherstellung ausstehend.")
            # Revoke all displayed partial identities before the worker starts.
            # Keep completed transfer history, not stale actionable queue rows.
            self.downloads[:] = [entry for entry in self.downloads
                                 if entry["state"] not in ("RESUMABLE", "UNVERIFIED")]
            self.download_queue_error = pending
            self.build_downloads_page()
            self.build_market_page(cards=(), recovery_error=pending)

            async def run(epoch):
                self.registry_worker_active = True
                self.market_filter.set_sensitive(False)
                self.refresh_market_buttons()
                worker = asyncio.create_task(asyncio.to_thread(self.market.recovery_snapshot))
                snapshot = None
                try:
                    try:
                        snapshot = await finish_owned(worker)
                    except asyncio.CancelledError:
                        # Recovery is transactional, not an optional transfer.
                        # Read its actual outcome only after the worker drains.
                        snapshot = worker.result()
                except (Hold, OSError) as exc:
                    failure.append(str(exc))
                    raise
                except Exception:
                    failure.append("Local operation failed")
                    raise
                finally:
                    self.registry_worker_active = False
                    self.market_filter.set_sensitive(True)
                    if snapshot is not None:
                        cards, rows, error = snapshot
                        self.restore_download_queue(rows=rows, error=error)
                        self.build_market_page(cards=cards)
                        completion.append(tr("Registry refreshed.", "Model kaydı yenilendi.",
                                             "Registry aktualisiert."))
                    else:
                        reason = failure[0] if failure else "registry_recovery_required"
                        self.download_queue_error = reason
                        self.build_market_page(cards=(), recovery_error=reason)
                    self.build_downloads_page()
            self.submit(run, kind="update", label=tr(
                "Recover model registry", "Model kaydını kurtar", "Modellregistry wiederherstellen"),
                committed=lambda: completion[0] if completion else "",
                worker_failure=lambda: failure[0] if failure else "")

        def storage_report(self, cards=None):
            """Aggregate one validated registry snapshot, not a second read."""
            summary = market.usage(self.market.cards() if cards is None else cards)
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
                self.market.record_discovered(entry.checked())
                self.build_market_page()
                self.set_state("ready", tr("Added to registry.","Kayda eklendi.","Registriert."))
            except Hold as exc:
                self.set_state("ready", str(exc))

        def approve_download(self):
            entry = self.selected_catalog_entry()
            if entry is None or self.busy:
                return
            try:
                if not self.catalog.verified:
                    raise Hold("catalog_not_verified")
                self.market.approve_catalog_entry(entry, "owner-approved-download", 1e12)
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
            self.web_note.set_text(tr("Plan only: %s (%s); no network request." % (url, method),
                                      "Yalnızca plan: %s (%s); ağ isteği yok." % (url, method),
                                      "Nur Plan: %s (%s); keine Netzwerkanfrage." % (url, method)))

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
            self.web_note.set_text(tr("Plan only: %s (POST); no network request." % target.url,
                                      "Yalnızca plan: %s (POST); ağ isteği yok." % target.url,
                                      "Nur Plan: %s (POST); keine Netzwerkanfrage." % target.url))

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

        def finish_task(self, board_id, t, message, *, invalidated=False, committed=False,
                        worker_failed=False):
            """Publish a terminal state only after the owned task has drained."""
            try:
                row = self.board.get(board_id)
                state = "failed" if message else "done"
                if committed:
                    state = "done"
                elif worker_failed:
                    state = "failed"
                elif t.cancelled() or invalidated:
                    state = "cancelled"
                    message = row.reason or message or "Stopped"
                self.board.finish(board_id, state, message or "")
                self.board.update(board_id, locks=())
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
            selected = self.activity_list.get_selected_row()
            selected_id = selected.task.id if selected is not None else None
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
                elif row.kind in ("download", "update"):
                    detail += " · %d bytes · " % row.counted + tr(
                        "total unknown", "toplam bilinmiyor", "Gesamtgröße unbekannt")
                label = Gtk.Label(label=detail, xalign=0, wrap=True)
                line.set_child(label)
                line.set_activatable(True)
                self.activity_list.append(line)
                if row.id == selected_id:
                    self.activity_list.select_row(line)
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
            if chosen.id != self.active_task or not self.busy or self.session.active.done():
                return
            # Cancellation is a request, not completion. Keep the task and its
            # leases visible until the real owned worker has finished cleanup.
            self.board.update(chosen.id, reason="stopped_by_owner")
            self.interrupt(False)
            self.refresh_activity()
            self.set_state("stopping", tr("Stopping task…", "Görev durduruluyor…",
                                          "Aufgabe wird gestoppt…"))

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
            self.research_folder = self.research_context = None
            self.folder_root.connect("changed", lambda *_: self.clear_folder_view())
            self.refresh_files_buttons()

        def research_allowed(self, scope, generation):
            # Display-only check. Actual effects still use audited require_tools
            # and live operation checkpoints. Never mint a new selection grant.
            try:
                profile, stamp = self.store.snapshot(self.cfg.get("profile"))
                if generation != (profile.id, stamp):
                    return False
                profiles.require_tool(profile, "filesystem_read", (scope,),
                                      self.store.policy_now(), selected=True)
                return self.cfg.get("profile") == profile.id and self.store.is_current(profile.id, stamp)
            except Hold:
                return False

        def refresh_research_scope(self):
            # Initial profile activation refreshes before secondary pages exist.
            if not hasattr(self, "file_read"):
                return
            if self.research_folder is not None and not self.research_allowed("folder", self.research_context):
                self.clear_folder_view()
            row = self.folder_entries.get_selected_row()
            readable = (not self.busy and self.research_folder is not None
                        and row is not None and not row.entry.directory
                        and self.research_allowed("file", self.research_context))
            if not readable:
                # A busy task alone does not revoke an already accepted preview.
                if self.research_folder is None or not self.research_allowed("file", self.research_context):
                    self.file_preview.set_text("")
            self.file_read.set_sensitive(readable)

        def refresh_local_stop(self):
            active = (self.busy or self.owned_picker is not None
                      or self.attachment is not None or self.attachment_invalidated
                      or self.research_folder is not None or self.model_scan_result is not None
                      or (hasattr(self, "chat_files") and self.chat_files.folder is not None)
                      or (hasattr(self, "analysis") and
                          (self.analysis.dialog is not None or self.analysis.last is not None)))
            self.hard.set_sensitive(bool(active))
            self.quick_stop.set_sensitive(bool(active))
            self.hard.set_visible(bool(active))

        def watch_local_results(self):
            # No polling when there is no retained private result. This checks
            # display lifetimes only, not a filesystem or background task grant.
            if self.closing or self.result_watch is not None:
                return
            if not (self.attachment is not None or self.research_folder is not None or self.model_scan_result is not None
                    or (hasattr(self, "chat_files") and self.chat_files.folder is not None)):
                return
            def tick():
                self.refresh_attachment_scope()
                self.refresh_research_scope()
                self.refresh_model_scan()
                if hasattr(self, "chat_files"):
                    self.chat_files.refresh()
                self.refresh_local_stop()
                if (self.attachment is None and self.research_folder is None and self.model_scan_result is None
                        and (not hasattr(self, "chat_files") or self.chat_files.folder is None)):
                    self.result_watch = None
                    return False
                return True
            self.result_watch = GLib.timeout_add(500, tick)

        def stop_result_watch(self):
            if self.result_watch is not None:
                GLib.source_remove(self.result_watch)
                self.result_watch = None

        def clear_folder_view(self):
            self.research_folder = self.research_context = None
            item = self.folder_entries.get_first_child()
            while item is not None:
                nxt = item.get_next_sibling()
                self.folder_entries.remove(item)
                item = nxt
            self.file_note.set_text("")
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
                self.folder_root.set_text(root)
                if self.list_folder(root) is None:
                    return None
            except Hold as exc:
                self.set_state("ready", str(exc))
                return None
            return root

        def list_folder(self, root=None):
            if self.busy:
                return
            root = root or self.folder_root.get_text().strip()
            self.clear_folder_view()
            try:
                context = self.picker_context()
                self.require_tools(("filesystem_read", ("folder",)), selected=True)
                check = self.tool_checkpoint("filesystem_read", ("folder",),
                    self.session.epoch, selected=True,
                    expected_generation=(context[0], context[4]))
                folder = files.list_dir(root, checkpoint=check)
                check()
            except Hold as exc:
                # A refused or unreadable folder is a bounded reason.
                if "filesystem_read" in str(exc) or "profile_forbids" in str(exc):
                    self.receipt("refused", "list a folder (filesystem_read)")
                self.file_note.set_text(str(exc))
                return None
            self.cfg["research_root"] = folder.root
            self.save()
            try:
                check()
            except Hold as exc:
                self.file_note.set_text(str(exc))
                return None
            self.folder_root.set_text(folder.root)
            self.research_folder = folder
            self.research_context = (context[0], context[4])
            self.show_folder(folder)
            note = tr("%d entries, folders first." % len(folder.entries),
                      "%d girdi, önce klasörler." % len(folder.entries),
                      "%d Einträge, Ordner zuerst." % len(folder.entries))
            if folder.limited:
                note += " " + tr("Listing limit reached.", "Listeleme sınırına ulaşıldı.",
                                  "Listengrenze erreicht.")
            self.file_note.set_text(note)
            self.refresh_research_scope()
            self.refresh_local_stop()
            self.watch_local_results()
            return self.research_folder

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
            self.file_read.set_sensitive(not self.busy and row is not None and not row.entry.directory
                                         and self.research_folder is not None
                                         and self.research_allowed("folder", self.research_context)
                                         and self.research_allowed("file", self.research_context))

        def read_selected_file(self):
            row = self.folder_entries.get_selected_row()
            if row is None or self.busy:
                return None
            entry = row.entry
            if entry.directory:
                return None
            self.file_preview.set_text("")
            try:
                folder = self.research_folder
                if folder is None or folder.root != self.folder_root.get_text().strip():
                    raise Hold("folder_changed")
                generation = self.research_context
                self.require_tools(("filesystem_read", ("folder",)),
                                   ("filesystem_read", ("file",)), selected=True)
                folder_check = self.tool_checkpoint("filesystem_read", ("folder",),
                    self.session.epoch, selected=True, expected_generation=generation)
                file_check = self.tool_checkpoint("filesystem_read", ("file",),
                    self.session.epoch, selected=True, expected_generation=generation)
                def check():
                    folder_check()
                    file_check()
                text = files.preview(folder.root, entry.name, expected_root=folder.identity,
                                     expected_entry=entry.identity, checkpoint=check)
                check()
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
            recovery = Gtk.Box(spacing=8)
            page.append(recovery)
            self.settings_choice_review = None
            self.settings_choice_inspect = self.button(recovery, tr(
                "Review interrupted model choice", "Kesilen model seçimini incele",
                "Unterbrochene Modellauswahl prüfen"), self.inspect_choice_recovery)
            self.settings_choice_confirm = self.button(recovery, tr(
                "Confirm recovered preference", "Kurtarılan seçimi onayla",
                "Wiederhergestellte Auswahl bestätigen"), self.confirm_choice_recovery)
            self.settings_choice_note = Gtk.Label(xalign=0, wrap=True)
            page.append(self.settings_choice_note)
            self.refresh_choice_recovery()

        def refresh_choice_recovery(self):
            pending = self.jan_choice_blocked()
            available = pending and not self.busy and not self.closing
            self.settings_choice_inspect.set_sensitive(available)
            self.settings_choice_confirm.set_sensitive(available and self.settings_choice_review is not None)
            self.settings_forget.set_sensitive(not self.busy and not pending and not self.audio_choice_blocked())
            if not pending:
                self.settings_choice_review = None

        def inspect_choice_recovery(self):
            if self.busy or self.closing or not self.jan_choice_blocked():
                return
            self.settings_choice_review = None
            try:
                if settings.load() != settings.checked(self.cfg):
                    raise Hold("settings_changed_restart_required")
                review = settings.review_committed_jan_choice(self.store, self.market)
                self.settings_choice_review = review
                self.settings_choice_note.set_text(tr(
                    "The active profile already records local Jan model %s. Confirm to align local preferences only; no model is loaded and no permission changes." % review[0].model_id,
                    "Etkin profil yerel Jan modeli %s kaydını içeriyor. Yalnızca yerel seçimi eşitlemek için onayla; model yüklenmez ve izinler değişmez." % review[0].model_id,
                    "Das aktive Profil enthält das lokale Jan-Modell %s. Nur die lokale Auswahl abgleichen; kein Modell wird geladen und keine Berechtigung geändert." % review[0].model_id))
            except (Hold, OSError) as exc:
                self.settings_choice_note.set_text(str(exc))
            self.refresh_choice_recovery()

        def confirm_choice_recovery(self):
            if self.busy or self.closing or self.settings_choice_review is None:
                return
            review = self.settings_choice_review
            self.settings_choice_review = None
            try:
                if settings.load() != settings.checked(self.cfg):
                    raise Hold("settings_changed_restart_required")
                proposed = settings.resolve_committed_jan_choice(self.store, self.market, expected=review)
                self.cfg = proposed
                self.saved_cfg = deepcopy(proposed)
                self.forget_inventory()
                self.activate_profile(proposed["profile"], quiet=True, persist=False)
                self.settings_choice_note.set_text(tr(
                    "Local preference recovered. Discover models again before connecting.",
                    "Yerel seçim kurtarıldı. Bağlanmadan önce modelleri yeniden keşfet.",
                    "Lokale Auswahl wiederhergestellt. Vor dem Verbinden Modelle erneut suchen."))
                self.set_state("ready", self.settings_choice_note.get_text())
            except (Hold, OSError) as exc:
                self.settings_choice_note.set_text(str(exc))
                self.set_state("ready", str(exc))
            self.refresh_choice_recovery()

        def forget_choices(self):
            """Erase the stored choices, and state what it did not erase."""
            # Keep the active policy, and reset preferences in one locked write.
            profile = self.store_active_profile_id()
            try:
                removed, proposed = settings.reset_choices(profile, expected=self.saved_cfg)
            except (Hold, OSError) as exc:
                self.settings_note.set_text(str(exc) if isinstance(exc, Hold) else "settings_unwritable")
                return
            self.cfg = proposed
            self.saved_cfg = deepcopy(proposed)
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

            The same scoped network policy as a catalog fetch applies. The
            production opener is held before network I/O; trusted synthetic
            fixtures may inject a transport.
            """
            if self.busy:
                return None
            source = self.selected_source()
            if source is None:
                return None
            url = catalog_sources.safe_url(source)
            try:
                self.require_tools(("external_network", ("catalog", source.host)))
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
            """The listing opener shares the catalog transport HOLD."""
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
                known = self.market.lookup_card(recorded.model_id)
                if known is not None and replace(known, revision=0) != replace(recorded, revision=0):
                    raise Hold("registry_discovery_conflict")
                recorded = self.market.save_card(recorded if known is None else known, expected=known)
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
            host = catalog_fetch.allowed(url, hosts)
            self.require_tools(("external_network", ("catalog", host)))

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

        def selected_card_snapshot(self):
            return getattr(self.market_list.get_selected_row(), "card_snapshot", None)

        def refresh_market_buttons(self):
            chosen = self.selected_card()
            row = self.market_list.get_selected_row()
            snapshot = getattr(row, "card_snapshot", None)
            self.market_retry.set_sensitive(not self.busy and chosen is not None
                and snapshot is not None and snapshot.state in market.RETRYABLE_TRANSFER
                and not snapshot.registered and not snapshot.loaded and not snapshot.owned_by_jarvis
                and not snapshot.shared_storage_owner and not snapshot.storage_ref
                and not snapshot.artifact_digest and snapshot.local_or_cloud == "local"
                and bool(snapshot.source_ref and snapshot.source_checksum))
            if getattr(self, "staging_scan", None) is not None:
                if self.staging_model != chosen:
                    self.clear_staging_review()
                self.refresh_staging_buttons()
            for w in (self.market_use, self.market_forget):
                w.set_sensitive(not self.busy and chosen is not None)
            self.market_list.set_sensitive(not self.busy)
            self.market_refresh.set_sensitive(not self.busy)
            if self.busy:
                for widget in (self.market_fetch, self.market_rollback, self.market_update):
                    widget.set_sensitive(False)
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

        def clear_staging_review(self):
            self.staging_model = None
            self.staging_context = None
            self.staging_archive = False
            self.staging_rows = ()
            self.staging_choice.remove_all()
            self.staging_note.set_text(tr(
                "Inspect temporary files first. Installed models and archives are not removed.",
                "Önce geçici dosyaları inceleyin. Kurulu modeller ve arşivler silinmez.",
                "Zuerst temporäre Dateien prüfen. Installierte Modelle und Archive bleiben erhalten."))

        def refresh_staging_buttons(self):
            chosen = self.selected_card()
            index = self.staging_choice.get_active()
            self.staging_scan.set_sensitive(not self.busy and chosen is not None)
            self.archive_staging_scan.set_sensitive(not self.busy and chosen is not None)
            self.staging_discard.set_sensitive(
                not self.busy and chosen is not None and chosen == self.staging_model
                and 0 <= index < len(self.staging_rows))

        def inspect_staging(self, *, archive=False):
            chosen = self.selected_card()
            if self.busy or chosen is None:
                return
            self.clear_staging_review()
            try:
                context = self.picker_context()
                rows = (self.market.archive_staging_candidates(chosen) if archive
                        else self.market.staging_candidates(chosen))
                if chosen != self.selected_card() or context != self.picker_context():
                    raise Hold("profile_changed")
                self.staging_model, self.staging_context, self.staging_rows = chosen, context, rows
                self.staging_archive = archive
                for row in rows:
                    name, identity = row[:2]
                    self.staging_choice.append_text("%s · %s" % (name[-8:], market.human_size(identity[2])))
                self.staging_note.set_text(tr(
                    "%d inactive temporary files for %s. Select one to delete." % (len(rows), chosen),
                    "%s için %d etkin olmayan geçici dosya. Silmek için birini seçin." % (chosen, len(rows)),
                    "%d inaktive temporäre Dateien für %s. Eine zum Löschen auswählen." % (len(rows), chosen)))
                if archive:
                    self.staging_note.set_text(tr(
                        "%d incomplete archive copies for %s. Verified revisions are excluded." % (len(rows), chosen),
                        "%s için %d yarım arşiv kopyası. Doğrulanmış sürümler hariç." % (chosen, len(rows)),
                        "%d unvollständige Archivkopien für %s. Geprüfte Revisionen ausgeschlossen." % (len(rows), chosen)))
            except (Hold, OSError) as exc:
                self.clear_staging_review()
                self.set_state("ready", str(exc))
            self.refresh_staging_buttons()

        def discard_staging(self):
            chosen = self.selected_card()
            index = self.staging_choice.get_active()
            if (self.busy or chosen is None or chosen != self.staging_model
                    or not 0 <= index < len(self.staging_rows)):
                return
            try:
                if self.staging_context != self.picker_context():
                    raise Hold("profile_changed")
            except (Hold, OSError) as exc:
                self.clear_staging_review()
                self.refresh_staging_buttons()
                self.set_state("ready", str(exc))
                return
            row = self.staging_rows[index]
            archive = self.staging_archive
            # A new scan and selection are required after every attempt.
            self.clear_staging_review()
            try:
                if archive:
                    self.market.discard_archive_staging(chosen, *row)
                else:
                    self.market.discard_staging(chosen, *row)
                self.set_state("ready", tr("Temporary file deleted.", "Geçici dosya silindi.",
                                           "Temporäre Datei gelöscht."))
            except (Hold, OSError) as exc:
                self.set_state("ready", str(exc))
            self.refresh_staging_buttons()

        def retry_selected_transfer(self):
            model_id = self.selected_card()
            row = self.market_list.get_selected_row()
            expected = getattr(row, "card_snapshot", None)
            if self.busy or model_id is None or expected is None:
                return
            try:
                self.market.retry_transfer(model_id, expected)
            except (Hold, OSError) as exc:
                self.set_state("ready", str(exc))
            else:
                for entry in self.downloads:
                    if entry["id"] == model_id:
                        self.refresh_partial_entry(entry)
                self.build_downloads_page()
                self.build_market_page()
                self.set_state("ready", tr(
                    "Reset to discovered. Existing temporary files preserved; start a new download explicitly.",
                    "Keşfedildi durumuna sıfırlandı. Geçici dosyalar korundu; yeni indirmeyi açıkça başlatın.",
                    "Auf entdeckt zurückgesetzt. Temporäre Dateien bleiben erhalten; neuen Download ausdrücklich starten."))

        def use_selected_card(self):
            model_id = self.selected_card()
            if model_id is None or self.busy:
                return
            try:
                expected = self.selected_card_snapshot()
                if expected is None:
                    raise Hold("registry_card_changed")
                self.use_card(model_id, expected=expected)
                self.build_market_page()
                self.set_state("ready", tr("Model selected.","Model seçildi.","Modell ausgewählt."))
            except Hold as exc:
                self.set_state("ready", str(exc))
            except OSError:
                self.set_state("ready", "model_selection_write_failed")

        def discover_market(self):
            if self.busy:
                return
            self.forget_inventory()
            async def run(epoch):
                client, checkpoint, route = self.bound_jan(epoch, discovery=True)
                cards = await market.from_models(client, languages=(), now=0.0)
                checkpoint()
                inventory = self.market.record_jan_inventory(cards)
                checkpoint()
                self.cfg["endpoint"] = route[3]
                self.inventory = inventory
                self.models.remove_all()
                for model_id in self.inventory:
                    self.models.append_text(model_id)
                if self.cfg["llm"] in self.inventory:
                    self.models.set_active(self.inventory.index(self.cfg["llm"]))
                self.save()
                self.build_market_page()
                self.refresh()
            self.submit(run, label=tr("Discover model inventory", "Model listesini keşfet",
                                      "Modellbestand suchen"))

        def download_selected_card(self):
            """Queue a discovered item for download; nothing installed is replaced."""
            model_id = self.selected_card()
            if model_id is None or self.busy:
                return
            expected = self.selected_card_snapshot()
            if expected is None:
                return
            completion, failure = [], []
            self.submit(lambda epoch: self.download(epoch, model_id, expected=expected,
                                                    completion=completion, failure=failure),
                        kind="download",
                        label=tr("Model download", "Model indirme", "Modelldownload"),
                        committed=lambda: completion[0] if completion else "",
                        worker_failure=lambda: failure[0] if failure else "")

        def transfer_checkpoint(self, epoch):
            context = self.picker_context()
            def check():
                # No GTK access: publication may use this from a worker.
                self.session.check(epoch)
                if self.closing or self.picker_context() != context:
                    raise Hold("profile_changed")
            check()
            return check

        def bound_transfer(self, epoch, model_id, *, expected=None):
            return market.Transfer.from_registry(
                self.market, model_id, transport=self.transport,
                checkpoint=self.transfer_checkpoint(epoch), expected=expected)

        async def download(self, epoch, model_id, *, expected=None, completion=None, failure=None):
            completion = [] if completion is None else completion
            failure = [] if failure is None else failure
            transfer = self.bound_transfer(epoch, model_id, expected=expected)
            source = transfer.source
            entry = {"id": model_id, "state": "QUEUED", "received": 0, "total": source.size,
                     "note": "", "resume": 0}
            self.downloads.append(entry)

            def started():
                entry["state"] = "DOWNLOADING"
                self.build_downloads_page()

            self.registry_worker_active = True
            self.registry_worker_stop = transfer.request_publication_stop
            self.market_filter.set_sensitive(False)
            self.refresh_market_buttons()
            try:
                progress = self.download_progress(entry, 0)
                self.session.check(epoch)
                started()
                result = await transfer.run(progress, threaded_publication=True)
                completion.append(tr("Registered.","Kaydedildi.","Registriert."))
                self.note_transfer_bytes(result.disk_size_bytes, source.size, epoch, self.active_task)
                entry["state"] = result.state
                entry["note"] = completion[0]
            except (Hold, OSError, asyncio.TimeoutError) as exc:
                self.record_transfer_failure(failure, exc)
                raise
            finally:
                if transfer.state == "CANCELLED":
                    self.refresh_partial_entry(entry)
                elif transfer.state in market.TERMINAL:
                    # A failed transfer is reported by the reason in the status line.
                    entry["state"], entry["note"] = transfer.state, ""
                elif transfer.state in ("REGISTERED", "TESTABLE", "ACTIVE"):
                    entry["state"], entry["note"] = transfer.state, tr(
                        "Registered.", "Kaydedildi.", "Registriert.")
                else:
                    entry["state"] = "FAILED"
                self.finish_transfer_views(completion)

        def record_transfer_failure(self, failure, exc):
            if not isinstance(exc, Hold):
                failure.append("Operation timed out" if isinstance(exc, asyncio.TimeoutError)
                               else "Local operation failed")
            elif str(exc) != "turn_cancelled":
                failure.append(str(exc))

        def finish_transfer_views(self, completion):
            self.registry_worker_stop = None
            self.registry_worker_active = False
            self.market_filter.set_sensitive(True)
            self.build_downloads_page()
            try:
                self.build_market_page()
            except (Hold, OSError):
                if completion:
                    completion[0] += tr(" Registry refresh unavailable.",
                                        " Kayıt yenilenemedi.",
                                        " Registry-Aktualisierung nicht verfügbar.")

        def refresh_partial_entry(self, entry):
            entry["resume"], entry["partial_identity"] = 0, None
            try:
                status = self.market.partial_status(entry["id"])
            except (Hold, OSError):
                entry["state"], entry["note"] = "UNVERIFIED", tr(
                    "Partial files require review.", "Kısmi dosyalar incelenmeli.",
                    "Teildateien müssen geprüft werden.")
                return
            if status is None:
                entry["state"], entry["note"] = "CANCELLED", tr(
                    "Cancelled.", "İptal edildi.", "Abgebrochen.")
                return
            entry["state"], entry["partial_identity"] = status["state"], status["identity"]
            entry["received"] = status["size"]
            if status["state"] == "RESUMABLE":
                entry["resume"] = status["size"]
                entry["note"] = tr("Prefix will be verified before resuming.",
                                   "Devam etmeden önce kısmi içerik doğrulanacak.",
                                   "Teilinhalt wird vor der Fortsetzung geprüft.")
            else:
                entry["note"] = tr("Unverified partial; resume blocked.",
                                   "Doğrulanmamış kısmi dosya; sürdürme engellendi.",
                                   "Ungeprüfte Teildatei; Fortsetzung gesperrt.")

        def build_downloads_page(self):
            selected = None
            if getattr(self, "download_list", None) is not None:
                selected = self.download_entries.get(self.download_list.get_selected_row())
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
                if entry is selected:
                    self.download_list.select_row(item)
            note = tr(
                "Only approved, verified transfers. Nothing is downloaded without an approved source and checksum.",
                "Yalnızca onaylı ve doğrulanmış aktarımlar. Onaylı kaynak ve sağlama toplamı olmadan hiçbir şey indirilmez.",
                "Nur genehmigte, geprüfte Übertragungen. Ohne genehmigte Quelle und Prüfsumme wird nichts geladen.")
            if self.download_queue_error:
                note += "\n" + tr("Queue inspection unavailable: ", "Kuyruk incelemesi yapılamadı: ",
                                  "Warteschlangenprüfung nicht verfügbar: ") + self.download_queue_error
            self.download_note.set_text(note)
            self.refresh_download_buttons()

        def download_line(self, entry):
            total = market.human_size(entry["total"]) if entry["total"] else "unknown/not tested"
            part = "%s · %s · %d of %s bytes" % (entry["id"], entry["state"], entry["received"], total)
            return part + (" · " + entry["note"] if entry["note"] else "")

        def in_flight(self):
            return any(entry["state"] in ("QUEUED", "DOWNLOADING") for entry in self.downloads)

        def partial_entry(self, *, discard=False):
            states = ("RESUMABLE", "UNVERIFIED") if discard else ("RESUMABLE",)
            row = self.download_list.get_selected_row()
            selected = self.download_entries.get(row)
            if selected is not None:
                return selected if (selected["state"] in states
                                    and selected.get("partial_identity") is not None) else None
            eligible = [entry for entry in self.downloads if entry["state"] in states
                        and entry.get("partial_identity") is not None]
            return eligible[0] if len(eligible) == 1 else None

        def resumable(self):
            return self.resumable_entry() is not None

        def resumable_entry(self):
            return self.partial_entry()

        def refresh_download_buttons(self):
            # Cancelling is available while a transfer runs; it is not new work.
            self.download_cancel.set_sensitive(self.in_flight())
            self.download_resume.set_sensitive(self.resumable())
            self.download_discard.set_sensitive(self.partial_entry(discard=True) is not None)

        def cancel_download(self):
            if not self.in_flight():
                return
            for entry in self.downloads:
                if entry["state"] in ("QUEUED", "DOWNLOADING"):
                    entry["note"] = tr("Cancelling…","İptal ediliyor…","Wird abgebrochen…")
            self.interrupt(False)
            self.build_downloads_page()

        def discard_download(self):
            entry = self.partial_entry(discard=True)
            if entry is None or self.busy:
                return
            try:
                self.market.discard_partial(entry["id"], entry["partial_identity"])
            except (Hold, OSError) as exc:
                self.refresh_partial_entry(entry)
                self.set_state("ready", str(exc))
            else:
                entry["state"], entry["resume"], entry["note"] = "CANCELLED", 0, tr(
                    "Partial discarded.", "Kısmi atıldı.", "Teil verworfen.")
                entry["partial_identity"] = None
            self.build_downloads_page()

        def resume_download(self):
            entry = self.resumable_entry()
            if entry is None or self.busy:
                return
            start = entry["resume"]

            expected = entry["partial_identity"]
            completion, failure = [], []

            async def run(epoch):
                status = self.market.partial_status(entry["id"])
                if (status is None or status["state"] != "RESUMABLE"
                        or status["identity"] != expected or status["size"] != start):
                    self.refresh_partial_entry(entry)
                    self.build_downloads_page()
                    raise Hold("partial_changed")
                transfer = self.bound_transfer(epoch, entry["id"])
                source = transfer.source
                entry["state"], entry["note"] = "DOWNLOADING", tr("Resuming…","Sürdürülüyor…",
                                                                 "Fortsetzung…")
                self.registry_worker_active = True
                self.registry_worker_stop = transfer.request_publication_stop
                self.market_filter.set_sensitive(False)
                self.refresh_market_buttons()
                try:
                    result = await transfer.resume(self.download_progress(entry, start), start,
                                                   expected_pair=expected, threaded_publication=True)
                    completion.append(tr("Registered.", "Kaydedildi.", "Registriert."))
                    self.note_transfer_bytes(result.disk_size_bytes, source.size, epoch, self.active_task)
                    entry["state"], entry["note"] = result.state, completion[0]
                    entry["resume"], entry["partial_identity"] = 0, None
                except (Hold, OSError, asyncio.TimeoutError) as exc:
                    self.record_transfer_failure(failure, exc)
                    raise
                finally:
                    if transfer.state in ("CANCELLED", "FAILED"):
                        self.refresh_partial_entry(entry)
                        if transfer.state == "FAILED":
                            entry["state"], entry["resume"] = "UNVERIFIED", 0
                    self.finish_transfer_views(completion)
            self.submit(run, kind="download",
                        label=tr("Resume model download", "Model indirmeyi sürdür",
                                 "Modelldownload fortsetzen"),
                        committed=lambda: completion[0] if completion else "",
                        worker_failure=lambda: failure[0] if failure else "")

        def restore_download_queue(self, *, rows=None, error=""):
            """Rebuild pending rows from one bounded registry snapshot."""
            if rows is None:
                try:
                    rows = self.market.pending_partials()
                except (Hold, OSError) as exc:
                    self.download_queue_error = str(exc)
                    return
            self.download_queue_error = error
            for model_id, total, status in rows:
                verified = status["state"] == "RESUMABLE"
                note = (tr("Prefix will be verified before resuming.",
                           "Devam etmeden önce kısmi içerik doğrulanacak.",
                           "Teilinhalt wird vor der Fortsetzung geprüft.") if verified else
                        tr("Unverified partial; resume blocked.",
                           "Doğrulanmamış kısmi dosya; sürdürme engellendi.",
                           "Ungeprüfte Teildatei; Fortsetzung gesperrt."))
                self.downloads.append({"id": model_id, "state": status["state"],
                                       "received": status["size"], "total": total,
                                       "note": note, "resume": status["size"] if verified else 0,
                                       "partial_identity": status["identity"]})

        def note_transfer_bytes(self, count, total, epoch, task_id, *, redraw=True):
            # Ignore callbacks from old work, not merely from another model.
            if epoch != self.session.epoch or task_id is None or task_id != self.active_task:
                return
            row = self.board.get(task_id)
            if not row.busy or row.kind not in ("download", "update"):
                return
            if (type(count) is not int or type(total) is not int
                    or not 0 <= count <= market.DIGEST_LIMIT
                    or not 0 <= total <= market.DIGEST_LIMIT):
                raise Hold("invalid_task_count")
            self.board.update(task_id, counted=count, counted_total=total)
            if redraw:
                self.refresh_activity()

        def download_progress(self, entry, offset):
            epoch, task_id = self.session.epoch, self.active_task
            total = entry["total"]
            self.note_transfer_bytes(offset, total, epoch, task_id)
            last_draw = 0.0
            def progress(count):
                nonlocal last_draw
                if epoch != self.session.epoch or task_id != self.active_task:
                    return
                now = time.monotonic()
                redraw = now - last_draw >= .25
                self.note_transfer_bytes(count, total, epoch, task_id, redraw=redraw)
                entry["received"] = count
                entry["state"] = "DOWNLOADING"
                if redraw:
                    last_draw = now
                    self.build_downloads_page()
            return progress

        def rollback_selected_card(self):
            """Restore the previous revision of a registered item, as a new revision."""
            model_id = self.selected_card()
            if model_id is None or self.busy or self.closing:
                return
            try:
                revisions = self.market.history(model_id)
                expected = self.selected_card_snapshot()
                if expected is None or self.market.card(model_id) != expected:
                    raise Hold("registry_card_changed")
                if not revisions:
                    raise Hold("revision_not_found")
                candidate = revisions[-1]
                profile, stamp = self.store.snapshot(self.cfg["profile"])
                if profile.state != "active":
                    raise Hold("profile_not_active")
            except (Hold, OSError) as exc:
                self.set_state("ready", str(exc))
                return
            # Worker checkpoints use immutable context and a thread-safe
            # stop flag, never GTK widgets or callbacks.
            stop = Event()
            completion = []
            failure = []
            restored_note = tr("Previous revision restored.",
                               "Önceki sürüm geri yüklendi.",
                               "Vorherige Revision wiederhergestellt.")
            def checkpoint():
                if stop.is_set():
                    raise Hold("turn_cancelled")
                if not self.store.is_current(profile.id, stamp):
                    raise Hold("profile_changed")
            def restore():
                result = self.market.rollback(model_id, candidate.revision,
                                              expected=expected, checkpoint=checkpoint)
                return result
            async def run(epoch):
                self.session.check(epoch)
                checkpoint()
                self.registry_worker_active = True
                self.registry_worker_stop = stop.set
                self.market_filter.set_sensitive(False)
                self.refresh_market_buttons()
                worker = asyncio.create_task(asyncio.to_thread(restore))
                try:
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        stop.set()
                        await finish_owned(asyncio.gather(worker, return_exceptions=True))
                        raise
                finally:
                    # The worker cannot outlive the owner's busy state.
                    # A late stop after a committed generation is not a rollback.
                    if worker.done() and not worker.cancelled():
                        error = worker.exception()
                        if error is None:
                            completion.append(restored_note)
                        elif isinstance(error, Hold) and str(error) != "turn_cancelled":
                            failure.append(str(error))
                        elif not isinstance(error, Hold):
                            failure.append("Local operation failed")
                    self.registry_worker_stop = None
                    self.registry_worker_active = False
                    self.market_filter.set_sensitive(True)
                    try:
                        self.build_market_page()
                    except (Hold, OSError):
                        if completion:
                            completion[0] = tr("Revision restored; registry refresh unavailable.",
                                               "Sürüm geri yüklendi; kayıt yenilenemedi.",
                                               "Revision wiederhergestellt; Registry-Aktualisierung nicht verfügbar.")
            self.submit(run, kind="update",
                        label=tr("Restore model revision", "Model sürümünü geri yükle",
                                 "Modellrevision wiederherstellen"),
                        committed=lambda: completion[0] if completion else "",
                        worker_failure=lambda: failure[0] if failure else "")

        def update_selected_card(self):
            """Re-fetch the approved source of an installed item, keeping history."""
            model_id = self.selected_card()
            if model_id is None or self.busy or self.closing:
                return

            expected = self.selected_card_snapshot()
            if expected is None:
                return
            completion, failure = [], []

            async def run(epoch):
                transfer = self.bound_transfer(epoch, model_id, expected=expected)
                source = transfer.source
                entry = {"id": model_id, "state": "DOWNLOADING", "received": 0,
                         "total": source.size, "note": "", "resume": 0}
                self.downloads.append(entry)
                self.registry_worker_active = True
                self.registry_worker_stop = transfer.request_publication_stop
                self.market_filter.set_sensitive(False)
                self.refresh_market_buttons()
                try:
                    result = await transfer.update(self.download_progress(entry, 0),
                                                   threaded_publication=True)
                    completion.append(tr("Updated.", "Güncellendi.", "Aktualisiert."))
                    self.note_transfer_bytes(result.disk_size_bytes, source.size, epoch, self.active_task)
                    entry["state"], entry["note"] = result.state, completion[0]
                except (Hold, OSError, asyncio.TimeoutError) as exc:
                    self.record_transfer_failure(failure, exc)
                    raise
                finally:
                    if transfer.state == "CANCELLED":
                        entry["state"], entry["resume"], entry["partial_identity"] = "CANCELLED", 0, None
                    elif not completion:
                        entry["state"] = "FAILED"
                        entry["note"] = failure[0] if failure else ""
                    self.finish_transfer_views(completion)
            self.submit(run, kind="update",
                        label=tr("Model update", "Model güncelleme", "Modellaktualisierung"),
                        committed=lambda: completion[0] if completion else "",
                        worker_failure=lambda: failure[0] if failure else "")

        def forget_selected_card(self):
            model_id = self.selected_card()
            if model_id is None or self.busy:
                return
            try:
                expected = self.selected_card_snapshot()
                if expected is None:
                    raise Hold("registry_card_changed")
                p = settings.config_path()
                with self.market.transfer_lease(model_id), self.market._write_guard(), settings._write_guard(p):
                    settings._require_no_choice(p)
                    if self.market.card(model_id) != expected:
                        raise Hold("registry_card_changed")
                    current = settings.load(p)
                    if current != self.saved_cfg or current != settings.checked(self.cfg):
                        raise Hold("settings_changed")
                    selected = {current["stt"], *current["voices"].values()} - {""}
                    ref = expected.storage_ref
                    if ref and current["market_refs"].get(ref) in selected:
                        raise Hold("audio_model_selected")
                    proposed = settings.checked(current)
                    # Only remove this card's reference, never unrelated orphan
                    # references (which may still protect a selected model).
                    other_refs = {c.storage_ref for c in self.market.cards()
                                  if c.model_id != model_id}
                    if ref and ref not in other_refs:
                        proposed["market_refs"].pop(ref, None)
                        for field in ("audio_digests", "voice_digests"):
                            proposed.get(field, {}).pop(ref, None)
                            if not proposed.get(field):
                                proposed.pop(field, None)
                    text = settings._serialize(proposed)
                    self.market.unregister(model_id, expected=expected)
                    # If publication fails, retaining an old reference is safer
                    # than silently removing the verification requirement.
                    if proposed != current:
                        settings._replace_text(p, text)
                self.cfg = proposed
                self.saved_cfg = deepcopy(proposed)
                self.build_market_page()
            except (Hold, OSError) as exc:
                self.set_state("ready", str(exc) if isinstance(exc, Hold) else "settings_unwritable")

        def ref_still_registered(self, ref):
            return any(card.storage_ref == ref for card in self.market.cards())

        def clear_model_scan(self):
            self.model_scan_result = self.model_scan_context = None
            self.model_candidates.remove_all()
            self.model_scan_note.set_text("")

        def refresh_model_scan(self):
            result = self.model_scan_result
            current = False
            if result is not None:
                try:
                    context = self.picker_context()
                    current = (context == self.model_scan_context
                               and self.research_allowed("folder", (context[0], context[4])))
                except Hold:
                    pass
                if not current:
                    self.clear_model_scan()
                    result = None
            index = self.model_candidates.get_active()
            kind = self.model_kind.get_active_id()
            candidate = result.candidates[index] if current and 0 <= index < len(result.candidates) else None
            matches = candidate is not None and (
                (candidate.kind == "stt" and kind == "stt")
                or (candidate.kind == "tts" and kind in ("tr", "en", "de")))
            idle = not self.busy and not self.closing and self.owned_picker is None
            local = getattr(self, "policy", None) is not None and self.policy.mode == "LOCAL_ONLY"
            self.model_scan.set_sensitive(idle and local)
            for widget in (self.model_candidates, self.model_kind, self.model_candidate_choose):
                widget.set_visible(current and bool(result.candidates))
                widget.set_sensitive(idle and current and (matches if widget is self.model_candidate_choose else True))

        def choose_model_folder(self):
            if self.busy or self.closing:
                return
            try:
                if self.active_profile().based_on_mode != "LOCAL_ONLY":
                    raise Hold("model_discovery_mode_denied")
                self.require_tools(("filesystem_read", ("folder",)), selected=True)
            except Hold as exc:
                self.set_state("ready", str(exc))
                return
            dialog = Gtk.FileChooserNative.new(tr(
                "Choose an existing model folder", "Mevcut model klasörünü seç",
                "Vorhandenen Modellordner wählen"), self.win,
                Gtk.FileChooserAction.SELECT_FOLDER, tr("Choose", "Seç", "Auswählen"),
                tr("Cancel", "İptal", "Abbrechen"))
            def response(d, res):
                try:
                    if res == Gtk.ResponseType.ACCEPT:
                        file = d.get_file()
                        self.scan_model_folder(file.get_path() if file else None)
                finally:
                    d.destroy()
            return self.show_owned_picker(dialog, response)

        def scan_model_folder(self, root):
            if self.busy or self.closing:
                return
            self.clear_model_scan()
            try:
                profile, stamp = self.store.snapshot(self.cfg["profile"])
                if profile.based_on_mode != "LOCAL_ONLY":
                    raise Hold("model_discovery_mode_denied")
                self.require_tools(("filesystem_read", ("folder",)), selected=True)
                live = self.store.bind_live(profile.id)
                if live.stamp != stamp:
                    raise Hold("profile_changed")
                def selected_check():
                    decision, reason = live.evaluate("filesystem_read", ("folder",))
                    if decision != profiles.ALLOW and reason != "no_rule_matches":
                        raise Hold("model_discovery_not_authorized")
                root = files.checked_root(root)
                identity = files.root_identity(root, checkpoint=selected_check)
            except Hold as exc:
                self.set_state("ready", str(exc))
                return
            stop = Event()
            def checkpoint():
                if stop.is_set():
                    raise Hold("turn_cancelled")
                decision, reason = live.evaluate("filesystem_read", ("folder",))
                if decision != profiles.ALLOW and reason != "no_rule_matches":
                    raise Hold("model_discovery_not_authorized")
            async def run(epoch):
                self.session.check(epoch)
                checkpoint()
                worker = asyncio.create_task(asyncio.to_thread(model_discovery.scan, root, checkpoint, expected_root=identity))
                try:
                    result = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    stop.set()
                    await finish_owned(asyncio.gather(worker, return_exceptions=True))
                    raise
                self.session.check(epoch)
                checkpoint()
                if self.cfg["profile"] != profile.id or not self.store.is_current(profile.id, stamp):
                    raise Hold("profile_changed")
                files.require_root_identity(root, identity, checkpoint=checkpoint)
                checkpoint()
                self.model_scan_context = self.picker_context()
                self.model_scan_result = result
                for item in result.candidates:
                    self.model_candidates.append_text(item.relative)
                note = tr("%d candidate files; compatibility not tested.",
                          "%d aday dosya; uyumluluk test edilmedi.",
                          "%d Dateikandidaten; Kompatibilität ungeprüft.") % len(result.candidates)
                if result.limited:
                    note += " " + tr("Scan limit reached.", "Tarama sınırına ulaşıldı.", "Suchgrenze erreicht.")
                self.model_scan_note.set_text(note)
                self.refresh_model_scan()
            self.turn_note = tr("Finding local audio candidates", "Yerel ses adayları aranıyor",
                                "Lokale Audiokandidaten suchen")
            self.submit(run)

        def review_model_candidate(self):
            result = self.model_scan_result
            try:
                if self.busy or self.closing or result is None or self.picker_context() != self.model_scan_context:
                    raise Hold("model_discovery_stale")
                index = self.model_candidates.get_active()
                key = self.model_kind.get_active_id()
                if not 0 <= index < len(result.candidates):
                    raise Hold("select_local_model")
                item = result.candidates[index]
                if not ((item.kind == "stt" and key == "stt") or
                        (item.kind == "tts" and key in ("tr", "en", "de"))):
                    raise Hold("incompatible_model_file")
                self.require_tools(("filesystem_read", ("folder",)), selected=True)
                # This only seeds a native chooser. The actual owner selection
                # and existing registration/locale validation still decide.
                self.choose(key, initial=str(Path(result.root) / item.relative))
            except Hold as exc:
                self.set_state("ready", str(exc))

        def choose(self, key, *, initial=None):
            dialog=Gtk.FileChooserNative.new("Select a trusted installed local model", self.win,Gtk.FileChooserAction.OPEN,"Select","Cancel")
            filt=Gtk.FileFilter()
            filt.add_pattern("*.bin" if key=="stt" else "*.onnx")
            dialog.add_filter(filt)
            if initial is not None:
                from gi.repository import Gio
                try:
                    dialog.set_file(Gio.File.new_for_path(initial))
                except GLib.Error:
                    dialog.destroy()
                    self.set_state("ready", "model_candidate_unavailable")
                    return
            def response(d, res):
                try:
                    if res!=Gtk.ResponseType.ACCEPT or self.busy:
                        return
                    file=d.get_file()
                    p=file.get_path() if file else None
                    # A card is registered before the choice is applied, so an
                    # unsupported or unreadable file never becomes the active model.
                    self.start_audio_import(key, p)
                except (Hold, OSError) as exc:
                    self.set_state("ready", str(exc) if isinstance(exc, Hold)
                                   else "model_selection_write_failed")
                finally:
                    d.destroy()
            self.show_owned_picker(dialog, response)

        def bind_registered_voice(self, speech, checkpoint):
            refs = self.saved_cfg["market_refs"]
            sidecars = self.saved_cfg.get("voice_digests", {})
            matches = [sidecars[ref] for ref, path in refs.items()
                       if path == speech.voice_id and ref in sidecars]
            if len(matches) > 1:
                raise Hold("model_reference_unverified")
            return audio.bind_voice_digest(speech.voice_id, speech.language,
                                           matches[0] if matches else None, checkpoint=checkpoint)

        def preview(self, lang):
            if self.busy or lang not in ("tr", "en", "de"):
                return
            if self.audio_choice_blocked():
                self.set_state("ready", audio_choice.REASON)
                return
            if not audio.tools_ready("piper", "aplay"):
                self.set_state("ready", "audio_backend_missing")
                return
            sample = {"tr": "Bu bir ses önizlemesidir.",
                      "en": "This is a voice preview.",
                      "de": "Das ist eine Stimmprobe."}[lang]
            try:
                self.require_tools(("local_audio_device", ("push_to_talk",)))
                speech = SpeechRequest(sample, lang, audio.voice_for(self.cfg["voices"].get(lang), lang).id)
            except Hold as exc:
                self.set_state("ready", str(exc))
                return
            async def run(epoch):
                def progress(stage):
                    if epoch == self.session.epoch:
                        self.set_state(stage)
                # A preview shares the playback lease with a turn, never the
                # device: a refused lease is a bounded reason.
                async with self.leases.hold("audio_playback", "preview"):
                    self.require_tools(("local_audio_device", ("push_to_talk",)))
                    checkpoint = self.tool_checkpoint("local_audio_device", ("push_to_talk",),
                                                     epoch, selection=lambda: self.cfg["voices"].get(lang),
                                                     expected=speech.voice_id)
                    checked_model = await self.runtime_model_proofs.bind(
                        speech.voice_id, self.saved_cfg["market_refs"], digests=self.saved_cfg.get("audio_digests", {}),
                        checkpoint=checkpoint)
                    checked_voice = self.bind_registered_voice(speech, checked_model)
                    await audio.speak(speech, progress, checkpoint=checked_voice)
            self.submit(run, label=tr("Voice preview", "Ses önizlemesi", "Stimmprobe"))

        def test_chat(self):
            if self.busy or self.cfg["llm"] not in self.inventory:
                return
            self.clear_model_probe()
            self.test_probe_pending = True
            self.test_note.set_text(tr("Testing…","Sınanıyor…","Test läuft…"))
            async def run(epoch):
                # A disposable session: a test never enters the conversation.
                probe = Session()
                # A disposable session shares this turn's epoch, so cancellation
                # and staleness checks still apply; its history is never merged.
                probe.epoch = epoch
                # A short, anchor-bearing probe so the reply language is provable.
                text = tr("Hello there! In one short sentence, what can you do?",
                          "Merhaba! Tek cümleyle cevapla: neler yapabilirsin?",
                          "Hallo! Antworte in einem kurzen Satz: was kannst du?")
                backend, checkpoint, route = self.bound_jan(epoch)
                reply, _ = await probe.reply(text, backend, epoch)
                checkpoint()
                self.test_probe_route = route
                self.test_probe_pending = False
                self.test_note.set_text("%s · %s" % (reply.strip()[:240], probe.context.response_language or "unknown"))
            self.submit(run, label=tr("Test selected model", "Seçilen modeli dene",
                                      "Ausgewähltes Modell testen"))

        def active_profile(self):
            """The profile whose policy is in force for this turn."""
            try:
                return self.store.load(self.cfg["profile"]).checked()
            except (Hold, KeyError):
                raise Hold("no_active_profile") from None

        def transcribe_recording(self):
            if self.busy or self.closing:
                return
            try:
                self.require_audio_choice()
                audio.selected_file(self.cfg["stt"], ".bin")
                if not audio.tools_ready("whisper-cli"):
                    raise Hold("transcription_backend_missing")
                self.require_tools(("filesystem_read", ("recording",)), selected=True)
            except Hold as exc:
                self.set_state("ready", str(exc))
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
            return self.show_owned_picker(dialog, response)

        def run_transcribe(self, path):
            """Own selected-recording transcription as an ordinary cancellable task."""
            if self.busy or self.closing:
                return
            try:
                self.require_audio_choice()
                self.require_tools(("filesystem_read", ("recording",)), selected=True)
                model = self.cfg["stt"]
                context = self.picker_context()
                selection = self.tool_checkpoint("filesystem_read", ("recording",),
                                                 self.session.epoch, selected=True,
                                                 selection=lambda: self.cfg["stt"], expected=model,
                                                 expected_generation=(context[0], context[4]))
                identity = audio.recording_identity(path, checkpoint=selection)
            except Hold as exc:
                self.set_state("ready", str(exc))
                return
            async def run(epoch):
                # submit owns a new epoch; compare the other selection fields.
                current = self.picker_context()
                if current[:3] != context[:3] or current[4] != context[4]:
                    raise Hold("profile_changed")
                checkpoint = self.tool_checkpoint("filesystem_read", ("recording",),
                                                 epoch, selected=True,
                                                 selection=lambda: self.cfg["stt"], expected=model,
                                                 expected_generation=(context[0], context[4]))
                checkpoint()
                checked_model = await self.runtime_model_proofs.bind(
                    model, self.saved_cfg["market_refs"], digests=self.saved_cfg.get("audio_digests", {}),
                        checkpoint=checkpoint)
                self.set_state("transcribing")
                raw = await audio.transcribe(model, path, runner=self.transcribe_runner,
                                             checkpoint=checked_model, expected_identity=identity)
                checked_model()
                checkpoint()
                self.bubble("Recording", raw)
                self.turn_outcome = "transcribed"
                return raw
            self.turn_note = tr("Transcribe selected recording", "Seçili kaydı yazıya çevir",
                                "Ausgewählte Aufnahme transkribieren")
            self.submit(run, kind="transcription")

        def tool_checkpoint(self, action, scope, epoch, *, selected=False,
                            selection=lambda: None, expected=None, expected_generation=None):
            """Bind a trusted operation to its exact generation and selection."""
            active = self.cfg.get("profile")
            profile, stamp = self.store.snapshot(active)
            if expected_generation is not None and (active, stamp) != expected_generation:
                raise Hold("profile_changed")
            def identity():
                self.session.check(epoch)
                if action == "local_audio_device" or (action == "filesystem_read" and scope == ("recording",)):
                    self.require_audio_choice()
                if (self.closing or self.cfg.get("profile") != active
                        or selection() != expected or not self.store.is_current(active, stamp)):
                    raise Hold("profile_changed")
            def check():
                identity()
                profiles.require_tool(profile, action, scope, self.store.policy_now(), selected=selected)
                identity()
            check()
            return check

        def require_tools(self, *requests, selected=False):
            """Refuse an action the active profile denies, at the moment it runs.

            Every decision is appended to the local audit trail.
            """
            active = self.cfg.get("profile")
            profile, stamp = self.store.snapshot(active)
            def unchanged():
                return (self.cfg.get("profile") == active
                        and self.store.is_current(active, stamp))
            for action, scope in requests:
                try:
                    now = self.store.policy_now()
                    if not unchanged():
                        raise Hold("profile_changed")
                    decision, reason = profiles.require_tool(profile, action, scope, now, selected=selected)
                    if not unchanged():
                        raise Hold("profile_changed")
                except Hold as exc:
                    self.record_policy(action, scope, "deny", str(exc))
                    raise
                self.record_policy(action, scope, decision, reason)
                # Audit I/O may take time; do not allow a stale generation
                # to leave this synchronous authorization boundary.
                if not unchanged():
                    raise Hold("profile_changed")
                # Time may have advanced during the audit write.
                profiles.require_tool(profile, action, scope, self.store.policy_now(), selected=selected)
                if not unchanged():
                    raise Hold("profile_changed")

        def record_disclosure(self):
            """Record that the boundary was stated before the message was sent."""
            try:
                data = disclosure.facts(self.active_profile(), self.url.get_text().strip(), self.store.policy_now())
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
            if self.jan_choice_blocked():
                raise Hold("llm_choice_recovery_required")
            raw = self.url.get_text().strip() if raw is None else raw
            try:
                result = profiles.enforce_endpoint(self.store.load(self.cfg["profile"]).checked(), raw)
            except KeyError:
                raise Hold("no_active_profile") from None
            except Hold as exc:
                self.record_policy("endpoint", (disclosure.endpoint_scope(raw),), "deny", str(exc))
                raise
            self.record_policy("endpoint", (disclosure.endpoint_scope(raw),), "allow", "mode_policy")
            return result

        def bound_jan(self, epoch, *, discovery=False, conversation=False, extra=lambda: True):
            """Bind the owned loopback operation to an exact route and live grant."""
            route = self.attachment_route()
            endpoint = self.require_endpoint(route[3])
            if endpoint != route[3] or not self.route_current(route):
                raise Hold("profile_changed")
            if conversation:
                self.session.require_route(route)
            def checkpoint():
                self.session.check(epoch)
                if (not self.route_current(route)
                        or (conversation and not self.conversation_authorized(route))
                        or extra() is not True
                        or not self.route_current(route)):
                    raise Hold("attachment_context_changed" if conversation else "profile_changed")
            checkpoint()
            if discovery:
                client = Jan(endpoint, timeout=10, checkpoint=checkpoint)
            else:
                client = Jan(endpoint, route[4], checkpoint=checkpoint)
            return client, checkpoint, route

        def conversation_backend(self, *, extra=lambda: True):
            """Bind prior messages and the transport to the same live route."""
            client, _, route = self.bound_jan(self.session.epoch, conversation=True, extra=extra)
            return client, route

        @staticmethod
        def attachment_live_allowed(live):
            if live is None:
                return False
            decision, reason = live.evaluate(attachments.ACTION, attachments.SCOPE)
            return decision == profiles.ALLOW or reason == "no_rule_matches"

        def route_current(self, route):
            # Trusted immutable route created from a validated active profile.
            # Compare current UI selection and exact inode metadata instead
            # of parsing the JSON repeatedly for every streamed text delta.
            return (not self.closing and not self.jan_choice_blocked()
                    and self.cfg.get("profile") == route[0]
                    and self.url.get_text().strip() == route[3]
                    and self.cfg["llm"] == route[4]
                    and self.store.is_current(route[0], route[5]))

        def conversation_authorized(self, route):
            try:
                if not self.route_current(route):
                    return False
                if self.session.has_attachment_history:
                    self.attachment_policy()
                    return (self.attachment_live_allowed(self.history_attachment_live)
                            and self.route_current(route))
                return True
            except Hold:
                return False

        def submit(self, work, *, kind="turn", label=None, committed=None, worker_failure=None):
            if self.busy or self.closing:
                return
            task_label = label if label is not None else self.turn_label()
            self.turn_note = ""
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
            board_id = self.board.start(kind, task_label, now=time.time())
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
                committed_note = committed() if committed is not None else ""
                failure_note = worker_failure() if worker_failure is not None else ""
                self.finish_task(board_id, t, committed_note or failure_note or message,
                                 invalidated=epoch != self.session.epoch,
                                 committed=bool(committed_note), worker_failed=bool(failure_note))
                self.active_task = None
                if committed is not None:
                    self.refresh_market_buttons()
                self.refresh_activity()
                # A turn that stated its own outcome keeps it: a generic
                # "ready" must not overwrite "reply muted; shown as text".
                if not message and self.turn_outcome:
                    message = self.turn_outcome
                if committed_note or failure_note:
                    self.set_state("ready", committed_note or failure_note)
                elif epoch==self.session.epoch:
                    self.set_state("ready",message)
                else:
                    self.set_state("ready","Stopped")
                if hasattr(self, "analysis"):
                    self.analysis.open_pending(epoch)
            task.add_done_callback(finished)

        def discover(self):
            if self.busy:
                return
            # A new discovery must not leave an old inventory actionable on failure.
            # Keep the saved preference, but restore it only if returned again.
            self.forget_inventory()
            if not self.url.get_text().strip():
                self.set_state("ready", tr(
                    "Enter the local Jan API URL first.",
                    "Önce yerel Jan API adresini gir.",
                    "Zuerst lokale Jan-API-Adresse eingeben."))
                return
            async def run(epoch):
                client, checkpoint, route = self.bound_jan(epoch, discovery=True)
                models=await client.models()
                checkpoint()
                self.cfg["endpoint"]=route[3]
                previous=self.cfg["llm"]
                # Restoring the saved choice is not a new explicit selection.
                self.cfg["llm"]=previous if previous in models else ""
                self.inventory=models
                self.models.remove_all()
                for m in models:
                    self.models.append_text(m)
                if previous in models:
                    self.models.set_active(models.index(previous))
                self.save()
                if not models:
                    raise Hold("no_installed_chat_models")
            self.submit(run, label=tr("Discover local models", "Yerel modelleri keşfet",
                                      "Lokale Modelle suchen"))

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

        def signal_registry_stop(self):
            signal = getattr(self, "registry_worker_stop", None)
            if signal is not None:
                signal()

        def hard_stop(self):
            self.signal_registry_stop()
            self.drop_attachment_for_context_change()
            if hasattr(self, "chat_files"):
                self.chat_files.reset()
            if hasattr(self, "folder_entries"):
                self.clear_folder_view()
            if hasattr(self, "analysis"):
                self.analysis.reset()
            self.interrupt(False)

        def interrupt(self, clear):
            # The GTK callback is the stop boundary, not the next asyncio tick.
            self.signal_registry_stop()
            self.dismiss_picker()
            self.clear_model_scan()
            if clear:
                self.clear_attachment()
                self.clear_folder_view()
                if hasattr(self, "chat_files"):
                    self.chat_files.reset()
                self.stop_result_watch()
            if hasattr(self, "analysis"):
                if clear:
                    self.analysis.reset()
                else:
                    self.analysis.cancel()
            self.session.invalidate(clear=clear)
            if clear:
                self.history_attachment_live = None
                self.search_recall_epoch += 1
                self.search_entry.set_text("")
                self.run_search()
                child=self.log.get_first_child()
                while child is not None:
                    nxt=child.get_next_sibling()
                    self.log.remove(child)
                    child=nxt
            self.set_state("stopping" if self.busy else "ready")

        def attachment_policy(self):
            """Only the verified local route is available in this preview."""
            profile = self.active_profile()
            if profile.state != "active" or profile.based_on_mode != "LOCAL_ONLY":
                raise Hold("attachment_mode_denied")
            decision, reason = profiles.effective(profile).evaluate(
                attachments.ACTION, attachments.SCOPE, self.store.policy_now())
            if decision == profiles.ALLOW or reason == "no_rule_matches":
                return
            if decision == profiles.ASK:
                raise Hold("attachment_approval_required")
            raise Hold("profile_forbids_filesystem_read")

        def require_attachment(self):
            """Gate the picker and every actual read/send; absence needs a picker."""
            if self.busy or self.cfg["llm"] not in self.inventory:
                return None
            try:
                self.attachment_policy()
            except Hold as exc:
                self.record_policy(attachments.ACTION, attachments.SCOPE, "deny", str(exc))
                self.receipt("refused", "read a file (filesystem_read)")
                self.set_state("ready", str(exc))
                return None
            self.record_policy(attachments.ACTION, attachments.SCOPE, "allow", "rule_or_absent")
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
            replacing = self.attachment is not None or self.attachment_invalidated
            def failed_selection():
                if replacing:
                    self.invalidate_attachment()
                else:
                    self.clear_attachment()
            if self.require_attachment() is None:
                failed_selection()
                return None
            try:
                route = self.attachment_route()
                live = self.store.bind_live(route[0])
                def authorized():
                    try:
                        self.attachment_policy()
                        return (not self.busy and not self.closing
                                and self.attachment_route() == route
                                and live.policy.profile_id == route[0]
                                and live.policy.version == route[1]
                                and live.policy.mode == route[2]
                                and live.stamp == route[5]
                                and self.attachment_live_allowed(live))
                    except Hold:
                        return False
                # Capture identity at the trusted callback before opening data.
                # The synchronous loader still rechecks the same identity.
                try:
                    identity = selection_identity(path, authorized=authorized, allow_empty=True)
                except Hold as exc:
                    raise Hold({
                        "csv_invalid_selection": "attachment_not_found",
                        "csv_not_regular_file": "attachment_not_found",
                        "csv_selection_unreadable": "attachment_not_found",
                        "csv_invalid_size": "attachment_too_large",
                        "csv_selection_not_authorized": "attachment_not_authorized",
                        "csv_secure_reader_unavailable": "attachment_secure_reader_unavailable",
                    }.get(str(exc), "attachment_unreadable")) from None
                item = attachments.load(path, authorized=authorized, expected_identity=identity)
            except Hold as exc:
                failed_selection()
                self.set_state("ready", str(exc))
                return None
            except Exception:
                failed_selection()
                self.set_state("ready", "local_operation_failed")
                return None
            self.attachment = item
            self.attachment_invalidated = False
            self.attachment_context = route
            self.attachment_live = live
            self.attachment_note.set_text(tr("Attached: %s" % attachments.describe(item),
                                             "Eklendi: %s" % attachments.describe(item),
                                             "Angehängt: %s" % attachments.describe(item)))
            self.remove_attachment_button.set_visible(True)
            self.remove_attachment_button.set_sensitive(True)
            self.refresh_local_stop()
            self.watch_local_results()
            return item

        def send_local_action(self, text, action):
            # Only the explicit owner composer reaches this dispatcher.
            # Neither Jan output nor an attachment can provide a file path.
            if self.busy or self.closing or self.owned_picker is not None or self.analysis.dialog is not None:
                self.set_state("ready", "local_picker_busy")
                return
            if self.attachment is not None:
                self.set_state("ready", tr(
                    "Local tools use their own file picker; remove the chat attachment first.",
                    "Yerel araçlar kendi dosya seçicisini kullanır; önce sohbet ekini kaldır.",
                    "Lokale Werkzeuge verwenden einen eigenen Dateidialog; zuerst den Chat-Anhang entfernen."))
                return
            try:
                if self.active_profile().based_on_mode != "LOCAL_ONLY":
                    raise Hold("local_task_mode_denied")
            except Hold as exc:
                self.set_state("ready", str(exc))
                return
            if action == "transcribe_recording":
                opened = self.transcribe_recording()
            elif action == "discover_audio_models":
                opened = self.choose_model_folder()
                if opened:
                    self.stack.set_visible_child_name("models")
            elif action == "explore_folder":
                opened = self.chat_files.choose()
            else:
                self.set_state("ready", "local_task_unsupported")
                return
            if opened:
                self.entry.set_text("")
                self.bubble("You", text)
                self.set_state("ready", tr(
                    "Choose a file or folder to continue. Nothing has been executed yet.",
                    "Devam etmek için dosya veya klasör seç. Henüz işlem yapılmadı.",
                    "Zum Fortfahren Datei oder Ordner wählen. Noch nichts ausgeführt."))

        def send_text(self):
            text=self.entry.get_text().strip()
            if self.busy or not text:
                return
            if self.attachment_invalidated:
                self.set_state("ready", "attachment_context_changed")
                return
            action = local_actions.direct(text)
            if action is not None:
                self.send_local_action(text, action)
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
                self.set_state("ready", tr(
                    "Connect and select a local model before sending a message.",
                    "Mesaj göndermeden önce yerel model bağla ve seç.",
                    "Vor dem Senden ein lokales Modell verbinden und auswählen."))
                return
            self.record_disclosure()
            item = self.attachment
            selected_route = self.attachment_context
            selected_live = self.attachment_live
            def allowed():
                if item is None:
                    return True
                try:
                    self.attachment_policy()
                    return (not self.closing and self.attachment_route() == selected_route
                            and self.attachment_live_allowed(selected_live))
                except Hold:
                    return False
            if item is not None and (not allowed() or self.require_attachment() is None):
                self.invalidate_attachment()
                self.set_state("ready", "attachment_context_changed")
                return
            # Validate language and total wire size before consuming the draft.
            # The file is data, never a source of host language instructions.
            try:
                _, message = self.session.prepare_text(text, attachment=item, model=self.cfg["llm"])
            except Hold as exc:
                self.set_state("ready", str(exc))
                return
            self.turn_note = text[:80]
            async def run(epoch):
                self.session.check(epoch)
                backend, route = self.conversation_backend(extra=allowed)
                def permitted():
                    return allowed() and self.conversation_authorized(route)
                if not permitted():
                    raise Hold("attachment_context_changed")
                self.session.prepare_text(text, attachment=item, model=self.cfg["llm"])
                self.entry.set_text("")
                if item is not None:
                    self.clear_attachment()
                self.bubble("You",message.content)
                self.set_state("thinking")
                answer=self.bubble("JARVIS","")
                def grow(piece):
                    if epoch == self.session.epoch:
                        self.extend(answer,"JARVIS",piece)
                try:
                    result,_=await self.session.reply(text,backend,
                                                      epoch,on_delta=grow,route=route,
                                                      attachment=item,model=self.cfg["llm"],
                                                      authorize=permitted)
                    self.session.check(epoch)
                except (Hold, asyncio.CancelledError):
                    # Earlier authorized deltas cannot be unseen, but a
                    # rejected turn must not retain a partial assistant reply.
                    self.growing.pop(answer, None)
                    if answer.get_parent() == self.log:
                        # Preserve the existing empty cancelled-turn label.
                        answer.set_text("JARVIS\n")
                    raise
                if item is not None and self.history_attachment_live is None:
                    self.history_attachment_live = selected_live
                self.extend(answer,"JARVIS",result,replace=True)
            self.submit(run)

        def voice(self):
            if self.audio_choice_blocked():
                self.set_state("ready", audio_choice.REASON)
                return
            if self.attachment_invalidated or self.attachment is not None:
                self.set_state("ready", tr("Remove the text attachment before starting voice chat.",
                    "Sesli sohbete başlamadan önce metin ekini kaldır.",
                    "Textanhang vor dem Sprachchat entfernen."))
                return
            async def run(epoch):
                def progress(stage):
                    if epoch == self.session.epoch and not self.playback_stopping:
                        self.set_state(stage)

                self.require_tools(("local_audio_device", ("push_to_talk",)))
                capture_route = self.attachment_route()
                capture_live = self.store.bind_live(capture_route[0])
                if capture_live.stamp != capture_route[5]:
                    raise Hold("profile_changed")

                stt_choice = self.cfg["stt"]
                voice_choices = dict(self.cfg["voices"])

                def check_voice_grant():
                    self.session.check(epoch)
                    self.require_audio_choice()
                    if (not self.route_current(capture_route)
                            or self.cfg["stt"] != stt_choice or self.cfg["voices"] != voice_choices):
                        raise Hold("profile_changed")
                    decision, reason = capture_live.evaluate("local_audio_device", ("push_to_talk",))
                    if decision != profiles.ALLOW:
                        raise Hold("approval_expired" if reason == "approval_expired" else
                                   "profile_changed" if reason == "policy_changed" else
                                   "profile_forbids_local_audio_device")
                    if not self.route_current(capture_route):
                        raise Hold("profile_changed")

                def voice_authorized(route):
                    try:
                        check_voice_grant()
                        return self.conversation_authorized(route)
                    except Hold:
                        return False

                voices = audio.SelectedVoices(self.cfg["voices"])
                self.finish=asyncio.Event()
                # The microphone is exclusive: a turn that cannot get the lease
                # is refused before capture starts, never after it fails.
                async with self.leases.hold("microphone", "voice-turn-%d" % epoch):
                    self.require_tools(("local_audio_device", ("push_to_talk",)))
                    self.note_leases()
                    check_voice_grant()
                    checked_model = await self.runtime_model_proofs.bind(
                        stt_choice, self.saved_cfg["market_refs"], digests=self.saved_cfg.get("audio_digests", {}),
                        checkpoint=check_voice_grant)
                    text=await audio.listen(stt_choice,self.finish,progress, checkpoint=checked_model)
                    checked_model()
                # Already captured samples cannot be undone. Refuse to display
                # or dispatch the returned transcription after revocation.
                self.note_leases()
                check_voice_grant()
                self.bubble("You",text)
                if analysis_intent.direct(text) is not None or analysis_intent.explicit_task(text) is not None:
                    if self.analysis.dialog is not None:
                        raise Hold("analysis_picker_busy")
                    await self.analysis.command(text, epoch, extra=check_voice_grant)
                    return
                self.set_state("thinking")
                def audio_allowed():
                    try:
                        check_voice_grant()
                        return True
                    except Hold:
                        return False
                backend, route = self.conversation_backend(extra=audio_allowed)
                check_voice_grant()
                result,speech=await self.session.reply(text,backend,epoch,voices,route=route,
                    authorize=lambda: voice_authorized(route))
                check_voice_grant()
                if not self.conversation_authorized(route):
                    raise Hold("attachment_context_changed")
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
                    self.session.check(epoch)
                    if not self.conversation_authorized(route):
                        raise Hold("attachment_context_changed")
                    self.require_tools(("local_audio_device", ("push_to_talk",)))
                    self.note_leases()
                    check_voice_grant()
                    checked_voice = await self.runtime_model_proofs.bind(
                        speech.voice_id, self.saved_cfg["market_refs"], digests=self.saved_cfg.get("audio_digests", {}),
                        checkpoint=check_voice_grant)
                    checked_voice = self.bind_registered_voice(speech, checked_voice)
                    playback = loop.create_task(audio.speak(speech, progress, checkpoint=checked_voice))
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
            self.submit(run, label=tr("a spoken turn", "sesli tur", "eine gesprochene Runde"))

        def close(self,*_):
            self.search_recall_epoch += 1
            self.dismiss_picker()
            self.clear_attachment()
            if hasattr(self, "file_read"):
                self.clear_folder_view()
            self.clear_model_scan()
            if hasattr(self, "chat_files"):
                self.chat_files.reset()
            self.stop_result_watch()
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
        # Cancel the application owners first, not their subprocess cleanup
        # tasks. A blanket cancel of all descendants can interrupt a reap.
        owners = set()
        for window in state:
            active, playback = window.session.active, window.playback
            window.signal_registry_stop()
            window.session.invalidate()
            window.stop_speaking()
            owners.update(task for task in (active, playback)
                          if task is not None and not task.done())
        if owners:
            loop.run_until_complete(asyncio.gather(*owners, return_exceptions=True))
        pending=asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    raise SystemExit(main())

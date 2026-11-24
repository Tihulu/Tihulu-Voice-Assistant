# SPDX-License-Identifier: AGPL-3.0-only
"""Native GTK adapter for the explicitly selected, local-only R1 task.

No model receives Jarvis-discovered paths, artifacts, or computed private summaries. The UI owns
all grants; a model cannot call these entry points.
"""
import asyncio
import time
from threading import Lock
from uuid import uuid4

from . import analysis_controller as ac, analysis_intent, profiles
from .voice import audio
from .voice.m1 import Hold, SpeechRequest, speech_text


class AnalysisView:
    def __init__(self, desktop, parent, Gtk, GLib, tr, language):
        self.ui, self.Gtk, self.GLib = desktop, Gtk, GLib
        self.tr = tr
        self.language = language if language in ac.LANGUAGES else "en"
        self.context = None
        self.dialog = None
        self.handler = None
        self.grants = set()
        self.lock = Lock()
        self.controller = ac.AnalysisController(self.authorized)
        self.last = None
        self.pending = None
        self.rows = []
        row = Gtk.Box(spacing=6)
        parent.append(row)
        self.choose_button = desktop.button(row, tr("Analyze local CSV",
            "Yerel CSV analiz et", "Lokale CSV analysieren"), self.choose)
        self.color = Gtk.ComboBoxText()
        for token, caption in (("blue", tr("Blue", "Mavi", "Blau")),
                               ("green", tr("Green", "Yeşil", "Grün")),
                               ("orange", tr("Orange", "Turuncu", "Orange"))):
            self.color.append(token, caption)
        self.color.set_active_id("blue")
        row.append(self.color)
        self.recolor_button = desktop.button(row, tr("Recolor last chart",
            "Son grafiği renklendir", "Letztes Diagramm umfärben"), self.recolor)
        self.note = Gtk.Label(xalign=0, wrap=True)
        parent.append(self.note)
        self.note.set_text(tr(
            "Local analysis · time_s,temperature_c · no model required",
            "Yerel analiz · time_s,temperature_c · model gerekmez",
            "Lokale Analyse · time_s,temperature_c · kein Modell erforderlich"))
        self.refresh()

    def authorized(self, context, action, target):
        # Called on a worker: do not access GTK or the profile store here.
        with self.lock:
            return context == self.context and (action, target) in self.grants

    def _profile(self):
        profile = self.ui.active_profile()
        if profile.state != "active" or profile.based_on_mode != "LOCAL_ONLY":
            raise Hold("analysis_mode_denied")
        decision, reason = profiles.effective(profile).evaluate(
            "filesystem_read", ("csv_analysis",), time.time())
        # An absent rule needs an explicit picker grant; explicit deny/ask
        # must not be overridden by this UI. No general file authorization.
        if decision != profiles.ALLOW and reason != "no_rule_matches":
            raise Hold("analysis_not_authorized")
        return profile

    def _matches(self, context):
        profile = self._profile()
        if (context is None or context != self.context
                or profile.id != context.profile_id
                or profile.version != context.profile_version):
            raise Hold("analysis_context_changed")

    def _activate(self):
        profile = self._profile()
        if self.context is not None:
            self._matches(self.context)
            return self.context
        context = ac.Context(uuid4().hex, profile.id, profile.version, "LOCAL_ONLY")
        self.controller.activate(context)
        with self.lock:
            self.context = context
        return context

    def refresh(self):
        policy = getattr(self.ui, "policy", None)
        enabled = (not self.ui.busy and not self.controller.busy
                   and self.dialog is None and policy is not None
                   and policy.mode == "LOCAL_ONLY")
        self.choose_button.set_sensitive(enabled)
        self.color.set_sensitive(enabled)
        self.recolor_button.set_sensitive(enabled and self.last is not None)

    def _drop_dialog(self):
        dialog = self.dialog
        if dialog is not None:
            self.dialog = None
            if self.handler is not None:
                dialog.disconnect(self.handler)
                self.handler = None
            dialog.destroy()

    def cancel(self):
        self.pending = None
        self._drop_dialog()
        with self.lock:
            self.grants = {g for g in self.grants if g[0] == "artifact_read"}
        self.controller.cancel()
        self.refresh()

    def reset(self):
        self.pending = None
        self._drop_dialog()
        with self.lock:
            self.context = None
            self.grants.clear()
        self.controller.hard_stop()
        self.last = None
        for row in self.rows:
            if row.get_parent() is self.ui.log:
                self.ui.log.remove(row)
        self.rows.clear()
        self.refresh()

    def choose(self, *, language=None, speak=False):
        if self.ui.busy or self.dialog is not None or self.controller.busy:
            return
        try:
            context = self._activate()
        except Hold as exc:
            self.ui.set_state("ready", str(exc))
            return
        Gtk = self.Gtk
        dialog = Gtk.FileChooserNative.new(
            self.tr("Select CSV for local analysis", "Yerel analiz için CSV seç",
                    "CSV für lokale Analyse auswählen"), self.ui.win,
            Gtk.FileChooserAction.OPEN, self.tr("Analyze", "Analiz et", "Analysieren"),
            self.tr("Cancel", "İptal", "Abbrechen"))
        dialog.set_modal(True)
        file_filter = Gtk.FileFilter()
        file_filter.set_name("CSV")
        file_filter.add_pattern("*.csv")
        file_filter.add_pattern("*.CSV")
        dialog.add_filter(file_filter)
        self.dialog = dialog

        def response(d, result):
            if d is not self.dialog:
                return
            path = None
            try:
                if result == Gtk.ResponseType.ACCEPT:
                    self._matches(context)
                    file = d.get_file()
                    path = file.get_path() if file is not None else None
                    if not path:
                        raise Hold("csv_invalid_selection")
            except Hold as exc:
                self.ui.set_state("ready", str(exc))
            except Exception:
                self.ui.set_state("ready", "csv_selection_unreadable")
            finally:
                self._drop_dialog()
                self.ui.refresh()
            if path is not None:
                self.start_selected(path, language=language, speak=speak)

        self.handler = dialog.connect("response", response)
        dialog.show()
        self.refresh()
        self.ui.refresh()

    def start_selected(self, path, *, language=None, speak=False):
        """Trusted picker accept path only; not a generic chat command."""
        if self.ui.busy or self.controller.busy or self.dialog is not None:
            return
        try:
            context = self._activate()
            if not isinstance(path, str) or not path:
                raise Hold("csv_invalid_selection")
            self._start(context, "selected_csv_analysis", path,
                        lambda color: self.controller.analyze_selected(
                            path, color=color, language=language or self.language),
                        speak=speak)
        except Hold as exc:
            self.ui.set_state("ready", str(exc))

    def recolor(self):
        if self.ui.busy or self.controller.busy or self.dialog is not None or self.last is None:
            return
        try:
            context = self._activate()
            parent = self.last
            self._start(context, "artifact_recolor", parent,
                        lambda color: self.controller.recolor(
                            parent, color=color, language=self.language))
        except Hold as exc:
            self.ui.set_state("ready", str(exc))

    def _start(self, context, action, target, work, *, speak=False):
        color = self.color.get_active_id()
        if color not in ac.COLORS:
            raise Hold("chart_invalid_color")

        async def run(epoch):
            await self._execute(epoch, context, action, target, work, color, speak)

        self.ui.turn_note = self.tr("Local CSV analysis", "Yerel CSV analizi",
                                    "Lokale CSV-Analyse")
        self.ui.submit(run)

    async def _execute(self, epoch, context, action, target, work, color, speak):
        grant = (action, target)
        self.ui.session.check(epoch)
        self._matches(context)
        with self.lock:
            self.grants.add(grant)
        try:
            self.ui.set_state("thinking")
            worker = asyncio.create_task(asyncio.to_thread(work, color))
            try:
                ident = await asyncio.shield(worker)
            except asyncio.CancelledError:
                self.cancel()
                try:
                    await worker
                except Exception:
                    pass
                raise
            self.ui.session.check(epoch)
            self._matches(context)
            with self.lock:
                self.grants.add(("artifact_read", ident))
            try:
                artifact = self.controller.get(ident)
                self.display(artifact)
            except Exception:
                with self.lock:
                    self.grants.discard(("artifact_read", ident))
                raise
            self.last = ident
        finally:
            with self.lock:
                self.grants.discard(grant)
        if speak:
            await self.speak(artifact, epoch, context)

    async def speak(self, artifact, epoch, context):
        self.ui.session.check(epoch)
        self._matches(context)
        if self.ui.mute.get_active():
            self.ui.turn_outcome = self.tr("Result muted; shown in chat.",
                "Sonuç sessiz; sohbette gösterildi.", "Ergebnis stumm; im Chat angezeigt.")
            return
        try:
            profile = self._profile()
            if profiles.effective(profile).evaluate(
                    "local_audio_device", ("push_to_talk",), time.time())[0] != profiles.ALLOW:
                raise Hold("analysis_audio_denied")
            voice = audio.SelectedVoices(self.ui.cfg["voices"])[artifact.language]
            if not audio.tools_ready("piper", "aplay"):
                raise Hold("analysis_audio_unavailable")
            speech = SpeechRequest(speech_text(artifact.summary), artifact.language, voice.id)
        except (Hold, KeyError):
            self.ui.turn_outcome = self.tr("Result shown; local speech unavailable or denied.",
                "Sonuç gösterildi; yerel ses kullanılamıyor veya izin yok.",
                "Ergebnis angezeigt; lokale Sprachausgabe fehlt oder ist nicht erlaubt.")
            return
        try:
            async with self.ui.leases.hold("audio_playback", "analysis-%d" % epoch):
                self.ui.note_leases()
                self.ui.spoken_line(speech, epoch)
                def progress(stage):
                    if epoch == self.ui.session.epoch and not self.ui.playback_stopping:
                        self.ui.set_state(stage)
                playback = asyncio.create_task(audio.speak(speech, progress))
                self.ui.playback = playback
                try:
                    await asyncio.shield(playback)
                except asyncio.CancelledError:
                    self.ui.stop_speaking()
                    try:
                        await playback
                    except asyncio.CancelledError:
                        pass
                    self.ui.session.check(epoch)
                finally:
                    self.ui.playback = None
                    self.ui.playback_stopping = False
        except Hold:
            self.ui.session.check(epoch)
            self.ui.turn_outcome = "analysis_speech_failed"
        finally:
            self.ui.note_leases()

    async def command(self, text, epoch):
        """Only explicit recognized user commands, never ordinary chat replies."""
        context = self._activate()
        self.ui.session.check(epoch)
        intent = analysis_intent.direct(text)
        if intent is None:
            if self.ui.cfg["llm"] not in self.ui.inventory:
                raise Hold("select_installed_model")
            self.ui.set_state("thinking")
            intent = await analysis_intent.propose(text, self.ui.backend_for_test())
            self.ui.session.check(epoch)
            self._matches(context)
        if intent.action == "analyze":
            # Open the picker only after the existing turn is drained.
            self.pending = (epoch, context, intent.language)
            self.ui.turn_outcome = self.tr("Select a CSV file to analyze.",
                "Analiz etmek için bir CSV dosyası seç.", "CSV-Datei zur Analyse auswählen.")
            return
        if intent.action != "recolor" or self.last is None:
            raise Hold("analysis_artifact_not_found")
        parent = self.last
        self.color.set_active_id(intent.color)
        await self._execute(epoch, context, "artifact_recolor", parent,
            lambda color: self.controller.recolor(
                parent, color=color, language=intent.language), intent.color, True)

    def open_pending(self, epoch):
        pending, self.pending = self.pending, None
        if pending is None or pending[0] != epoch or epoch != self.ui.session.epoch:
            return
        if self.ui.closing or self.ui.busy:
            return
        try:
            self._matches(pending[1])
            self.choose(language=pending[2], speak=True)
        except Hold as exc:
            self.ui.set_state("ready", str(exc))

    def display(self, artifact):
        """Main-thread only; decode the trusted engine's static SVG."""
        from gi.repository import Gdk, GdkPixbuf
        Gtk = self.Gtk
        try:
            loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
            loader.write(artifact.svg)
            loader.close()
            pixbuf = loader.get_pixbuf()
            if pixbuf is None:
                raise Hold("chart_render_failed")
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        except Exception:
            raise Hold("chart_render_failed") from None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("message")
        box.add_css_class("assistant")
        box.artifact_id = artifact.id
        label = Gtk.Label(label="JARVIS · " + artifact.summary, xalign=0,
                          selectable=True, wrap=True)
        box.append(label)
        picture = Gtk.Picture.new_for_paintable(texture)
        picture.set_can_shrink(True)
        picture.set_size_request(320, 160)
        picture.set_halign(Gtk.Align.START)
        picture.set_alternative_text(self.tr("Temperature versus time",
            "Sıcaklık-zaman grafiği", "Temperatur über Zeit"))
        box.append(picture)
        series = artifact.series
        detail = self.tr("Samples: %d · Duration: %.6g s · Change: %.6g °C",
                         "Ölçüm: %d · Süre: %.6g s · Değişim: %.6g °C",
                         "Messwerte: %d · Dauer: %.6g s · Änderung: %.6g °C")
        box.append(Gtk.Label(label=detail % (series.count, series.duration_s,
                             series.delta_c), xalign=0, selectable=True, wrap=True))
        # Deliberately separate from Session.history, which may be sent to a
        # model on a later text turn. No implicit disclosure or export.
        self.ui.log.append(box)
        self.rows.append(box)
        self.GLib.idle_add(self.ui.bottom)

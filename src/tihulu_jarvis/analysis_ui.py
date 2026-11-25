# SPDX-License-Identifier: AGPL-3.0-only
"""Native GTK adapter for the explicitly selected, local-only R1 task.

No model receives Jarvis-discovered paths, artifacts, or computed private summaries. The UI owns
all grants; a model cannot call these entry points.
"""
import asyncio
import time
from threading import Lock
from uuid import uuid4

from . import analysis_controller as ac, analysis_export, analysis_intent, profiles
from .selected_csv import selection_identity
from .voice import audio
from .voice.m1 import Hold, SpeechRequest, speech_text


class AnalysisView:
    def __init__(self, desktop, parent, Gtk, GLib, tr, language, *, result_parent=None):
        self.ui, self.Gtk, self.GLib = desktop, Gtk, GLib
        self.tr = tr
        self.language = language if language in ac.LANGUAGES else "en"
        self.context = None
        self.live_policy = None
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
        self.result_actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        (result_parent if result_parent is not None else parent).append(self.result_actions)
        self.result_actions.append(Gtk.Label(label=tr("Latest result", "Son sonuç",
            "Letztes Ergebnis"), xalign=0))
        result_row = Gtk.Box(spacing=6)
        self.result_actions.append(result_row)
        self.color = Gtk.ComboBoxText()
        for token, caption in (("blue", tr("Blue", "Mavi", "Blau")),
                               ("green", tr("Green", "Yeşil", "Grün")),
                               ("orange", tr("Orange", "Turuncu", "Orange"))):
            self.color.append(token, caption)
        self.color.set_active_id("blue")
        result_row.append(self.color)
        self.recolor_button = desktop.button(result_row, tr("Recolor last chart",
            "Son grafiği renklendir", "Letztes Diagramm umfärben"), self.recolor)
        export_row = Gtk.Box(spacing=6)
        self.result_actions.append(export_row)
        self.export_format = Gtk.ComboBoxText()
        for token, caption in (
            ("svg", tr("Chart (SVG)", "Grafik (SVG)", "Diagramm (SVG)")),
            ("png", tr("Chart (PNG)", "Grafik (PNG)", "Diagramm (PNG)")),
            ("data", tr("Data (CSV)", "Veri (CSV)", "Daten (CSV)")),
            ("summary", tr("Statistics (CSV)", "İstatistik (CSV)", "Statistik (CSV)")),
        ):
            self.export_format.append(token, caption)
        self.export_format.set_active_id("svg")
        export_row.append(self.export_format)
        self.export_button = desktop.button(export_row, tr(
            "Export last result", "Son sonucu dışa aktar", "Letztes Ergebnis exportieren"),
            self.choose_export)
        self.note = Gtk.Label(xalign=0, wrap=True)
        parent.append(self.note)
        self.note.set_text(tr(
            "Local analysis · time_s,temperature_c · no model required",
            "Yerel analiz · time_s,temperature_c · model gerekmez",
            "Lokale Analyse · time_s,temperature_c · kein Modell erforderlich"))
        self.refresh()

    @staticmethod
    def _read_allowed(live):
        if live is None:
            return False
        decision, reason = live.evaluate("filesystem_read", ("csv_analysis",))
        return decision == profiles.ALLOW or reason == "no_rule_matches"

    def authorized(self, context, action, target):
        # Worker-safe: immutable policy plus bounded metadata and clock check.
        # No GTK, mutable UI state or model callback on a worker.
        with self.lock:
            return (context == self.context and (action, target) in self.grants
                    and self._read_allowed(self.live_policy))

    def _profile(self):
        profile = self.ui.active_profile()
        if profile.state != "active" or profile.based_on_mode != "LOCAL_ONLY":
            raise Hold("analysis_mode_denied")
        decision, reason = profiles.effective(profile).evaluate(
            "filesystem_read", ("csv_analysis",), self.ui.store.policy_now())
        if decision != profiles.ALLOW and reason != "no_rule_matches":
            raise Hold("analysis_not_authorized")
        return profile

    def _matches(self, context):
        profile = self._profile()
        with self.lock:
            if (context is None or context != self.context
                    or profile.id != context.profile_id
                    or profile.version != context.profile_version
                    or not self._read_allowed(self.live_policy)):
                raise Hold("analysis_context_changed")

    def _activate(self):
        profile = self._profile()
        if self.context is not None:
            self._matches(self.context)
            return self.context
        live = self.ui.store.bind_live(profile.id)
        if (live.policy != profiles.effective(profile) or not self._read_allowed(live)):
            raise Hold("analysis_context_changed")
        context = ac.Context(uuid4().hex, profile.id, profile.version, "LOCAL_ONLY")
        self.controller.activate(context)
        with self.lock:
            self.context = context
            self.live_policy = live
        return context

    def _selection_allowed(self, context):
        self._matches(context)
        return True

    def refresh(self):
        policy = getattr(self.ui, "policy", None)
        enabled = (not self.ui.busy and not self.controller.busy
                   and self.dialog is None and self.ui.owned_picker is None and policy is not None
                   and policy.mode == "LOCAL_ONLY")
        self.choose_button.set_sensitive(enabled)
        self.result_actions.set_visible(self.last is not None)
        self.color.set_sensitive(enabled and self.last is not None)
        self.recolor_button.set_sensitive(enabled and self.last is not None)
        self.export_button.set_sensitive(enabled and self.last is not None)
        self.export_format.set_sensitive(enabled and self.last is not None)

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
            self.live_policy = None
            self.grants.clear()
        self.controller.hard_stop()
        self.last = None
        for row in self.rows:
            if row.get_parent() is self.ui.log:
                self.ui.log.remove(row)
        self.rows.clear()
        self.refresh()

    def choose(self, *, language=None, speak=False):
        if self.ui.busy or self.dialog is not None or self.ui.owned_picker is not None or self.controller.busy:
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
            identity = None
            try:
                if result == Gtk.ResponseType.ACCEPT:
                    self._matches(context)
                    file = d.get_file()
                    path = file.get_path() if file is not None else None
                    if not path:
                        raise Hold("csv_invalid_selection")
                    identity = selection_identity(path, authorized=lambda: self._selection_allowed(context))
            except Hold as exc:
                self.ui.set_state("ready", str(exc))
            except Exception:
                self.ui.set_state("ready", "csv_selection_unreadable")
            finally:
                self._drop_dialog()
                self.ui.refresh()
            if identity is not None:
                self.start_selected(path, language=language, speak=speak, identity=identity)

        self.handler = dialog.connect("response", response)
        dialog.show()
        self.refresh()
        self.ui.refresh()

    def start_selected(self, path, *, language=None, speak=False, identity=None):
        """Trusted picker accept path only; not a generic chat command."""
        if self.ui.busy or self.controller.busy or self.dialog is not None:
            return
        try:
            context = self._activate()
            if not isinstance(path, str) or not path:
                raise Hold("csv_invalid_selection")
            if identity is None:
                identity = selection_identity(path, authorized=lambda: self._selection_allowed(context))
            self._start(context, "selected_csv_analysis", path,
                        lambda color: self.controller.analyze_selected(
                            path, color=color, language=language or self.language,
                            expected_identity=identity),
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
                    "local_audio_device", ("push_to_talk",), self.ui.store.policy_now())[0] != profiles.ALLOW:
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
                self.ui.session.check(epoch)
                self._matches(context)
                if self.live_policy.evaluate("local_audio_device", ("push_to_talk",))[0] != profiles.ALLOW:
                    raise Hold("analysis_audio_denied")
                self.ui.note_leases()
                if self.ui.cfg.get("show_spoken_text") and self.rows:
                    label = self.Gtk.Label(label=self.tr("Spoken: ", "Okunan: ", "Gesprochen: ") + speech.text,
                                           xalign=0, selectable=True, wrap=True)
                    self.rows[-1].append(label)
                def progress(stage):
                    if epoch == self.ui.session.epoch and not self.ui.playback_stopping:
                        self.ui.set_state(stage)
                def checkpoint():
                    self.ui.session.check(epoch)
                    self._matches(context)
                    if (self.live_policy.evaluate("local_audio_device", ("push_to_talk",))[0] != profiles.ALLOW
                            or self.ui.cfg["voices"].get(artifact.language) != speech.voice_id):
                        raise Hold("analysis_audio_denied")
                playback = asyncio.create_task(audio.speak(speech, progress, checkpoint=checkpoint))
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

    async def command(self, text, epoch, *, extra=lambda: None):
        """Only explicit recognized user commands, never ordinary chat replies."""
        extra()
        context = self._activate()
        self.ui.session.check(epoch)
        def authorized():
            extra()
            self._matches(context)
            return True
        intent = analysis_intent.direct(text)
        if intent is None:
            if self.ui.cfg["llm"] not in self.ui.inventory:
                raise Hold("select_installed_model")
            self.ui.set_state("thinking")
            backend, checkpoint, _ = self.ui.bound_jan(epoch, extra=authorized)
            intent = await analysis_intent.propose(text, backend)
            checkpoint()
        self.ui.session.check(epoch)
        authorized()
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

    def _export_policy(self, context, ident):
        self._matches(context)
        profile = self._profile()
        decision, reason = profiles.effective(profile).evaluate(
            "filesystem_write", ("csv_analysis_export",), self.ui.store.policy_now())
        if decision != profiles.ALLOW and reason != "no_rule_matches":
            raise Hold("export_not_authorized")
        return self.controller.get(ident)

    def choose_export(self):
        """A local save dialog grants one destination, never an overwrite."""
        if self.ui.busy or self.dialog is not None or self.ui.owned_picker is not None or self.last is None:
            return
        context, ident = self.context, self.last
        kind = self.export_format.get_active_id()
        try:
            self._export_policy(context, ident)
            if kind not in analysis_export.FORMATS:
                raise Hold("export_invalid_format")
        except Hold as exc:
            self.ui.set_state("ready", str(exc))
            return
        Gtk = self.Gtk
        dialog = Gtk.FileChooserNative.new(
            self.tr("Save a new local result file", "Yeni yerel sonuç dosyası kaydet",
                    "Neue lokale Ergebnisdatei speichern"), self.ui.win,
            Gtk.FileChooserAction.SAVE, self.tr("Save", "Kaydet", "Speichern"),
            self.tr("Cancel", "İptal", "Abbrechen"))
        dialog.set_modal(True)
        dialog.set_current_name("jarvis-" + kind + analysis_export.FORMATS[kind])
        self.dialog = dialog

        def response(d, result):
            if d is not self.dialog:
                return
            path = None
            try:
                if result == Gtk.ResponseType.ACCEPT:
                    file = d.get_file()
                    path = file.get_path() if file is not None else None
                    if not path:
                        raise Hold("export_invalid_destination")
            except Hold as exc:
                self.ui.set_state("ready", str(exc))
            except Exception:
                self.ui.set_state("ready", "export_invalid_destination")
            finally:
                self._drop_dialog()
                self.ui.refresh()
            if path is not None:
                self.save_selected(path, kind, context, ident)

        self.handler = dialog.connect("response", response)
        dialog.show()
        self.ui.refresh()

    def save_selected(self, path, kind, context, ident):
        """Trusted save acceptance only. No agent or prompt may supply a path."""
        if self.ui.busy or self.dialog is not None or self.ui.closing:
            return False
        try:
            artifact = self._export_policy(context, ident)
            def png(svg):
                return bytes(self._texture(svg).save_to_png_bytes().get_data())
            data = analysis_export.encode(artifact, kind, png=png)
            # Keep this bounded commit on the GTK thread so UI revocation,
            # mode changes and close cannot interleave with final publication.
            # No model or arbitrary executable runs during this operation.
            def authorized():
                self._export_policy(context, ident)
                return not self.ui.busy and not self.ui.closing
            analysis_export.write_new(path, kind, data, authorized=authorized)
        except Hold as exc:
            self.ui.set_state("ready", str(exc))
            return False
        except Exception:
            self.ui.set_state("ready", "export_write_failed")
            return False
        self.ui.set_state("ready", self.tr(
            "New local result file saved.", "Yeni yerel sonuç dosyası kaydedildi.",
            "Neue lokale Ergebnisdatei gespeichert."))
        return True

    @staticmethod
    def _texture(svg):
        from gi.repository import Gdk, GdkPixbuf
        try:
            loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
            loader.write(svg)
            loader.close()
            pixbuf = loader.get_pixbuf()
            if pixbuf is None:
                raise Hold("chart_render_failed")
            return Gdk.Texture.new_for_pixbuf(pixbuf)
        except Exception:
            raise Hold("chart_render_failed") from None

    def display(self, artifact):
        """Main-thread only; decode the trusted engine's static SVG."""
        Gtk = self.Gtk
        texture = self._texture(artifact.svg)
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
        table = Gtk.Grid(column_spacing=18, row_spacing=3)
        table.set_halign(Gtk.Align.START)
        captions = {
            "count": self.tr("Samples", "Ölçümler", "Messwerte"),
            "mean": self.tr("Mean", "Ortalama", "Mittelwert"),
            "minimum": self.tr("Minimum", "En düşük", "Minimum"),
            "maximum": self.tr("Maximum", "En yüksek", "Maximum"),
            "change": self.tr("Change", "Değişim", "Änderung"),
            "duration": self.tr("Duration", "Süre", "Dauer"),
        }
        for index, (metric, value, unit) in enumerate(analysis_export.summary_rows(series)):
            table.attach(Gtk.Label(label=captions[metric], xalign=0), 0, index, 1, 1)
            label = Gtk.Label(label=format(value, ".12g") + (
                " °C" if unit == "degC" else " s" if unit == "s" else ""),
                xalign=1, selectable=True)
            table.attach(label, 1, index, 1, 1)
        box.append(table)
        # Deliberately separate from Session.history, which may be sent to a
        # model on a later text turn. No implicit disclosure or export.
        self.ui.log.append(box)
        self.rows.append(box)
        self.GLib.idle_add(self.ui.bottom)

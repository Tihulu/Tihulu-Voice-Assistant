# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Opt-in GTK 4 desktop client. No listener, background capture or provider login."""
import asyncio
import locale
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

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


@contextmanager
def gtk_asyncio(loop):
    """Run small asyncio slices on the GTK thread until the window closes."""
    _, GLib, _ = _gtk()

    def tick():
        loop.call_soon(loop.stop)
        loop.run_forever()
        return True

    source = GLib.timeout_add(12, tick)
    try:
        yield
    finally:
        GLib.source_remove(source)


def create_window(app, loop):
    """Construct the native window on the caller's GTK thread."""
    _, GLib, Gtk = _gtk()
    lang = (locale.getlocale()[0] or "en").split("_")[0]
    def tr(en, tur, de):
        return {"tr":tur, "de":de}.get(lang,en)

    class Desktop:
        def __init__(self, app):
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
            css = Gtk.CssProvider()
            css.load_from_data(b".workspace {padding:20px;} .hero {font-size:24px;font-weight:bold;} .badge {color:#40ae96;font-weight:bold;} .message {padding:10px 14px;border-radius:12px;background:alpha(currentColor,.07);} .user {border-left:3px solid #39a68f;} .assistant {border-left:3px solid #787ee0;}")
            Gtk.StyleContext.add_provider_for_display(self.win.get_display(),css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
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
            self.status = Gtk.Label(xalign=0, wrap=True)
            root.append(self.status)
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
            entryrow = Gtk.Box(spacing=8)
            self.entry = Gtk.Entry(hexpand=True,placeholder_text=tr("Write a message…","Bir mesaj yaz…","Nachricht schreiben…"))
            entryrow.append(self.entry)
            self.send = self.button(entryrow,tr("Send","Gönder","Senden"),self.send_text)
            self.entry.connect("activate",lambda *_:self.send_text())
            chat.append(entryrow)
            controls = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6,row_spacing=4)
            chat.append(controls)
            self.talk = self.button(controls,tr("Talk","Konuş","Sprechen"),self.voice)
            self.finish_btn = self.button(controls,tr("Finish recording","Kaydı bitir","Aufnahme beenden"),lambda:self.finish.set() if self.finish else None)
            self.stop = self.button(controls,tr("Stop speaking","Sesi durdur","Wiedergabe stoppen"),self.stop_speaking)
            self.cancel = self.button(controls,tr("Cancel turn","Turu iptal et","Antwort abbrechen"),lambda:self.interrupt(False))
            self.end = self.button(controls,tr("End conversation","Sohbeti bitir","Gespräch beenden"),lambda:self.interrupt(True))
            self.hard = self.button(controls,tr("Hard stop","Tümünü durdur","Alles stoppen"),lambda:self.interrupt(False))
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
            self.test_chat_button = self.button(models, tr("Test selected model","Seçilen modeli dene","Ausgewähltes Modell testen"), self.test_chat)
            self.test_note = Gtk.Label(xalign=0, wrap=True)
            models.append(self.test_note)
            self.update_labels()
            self.store = profiles.Store(profiles.profile_dir())
            self.policy_rows = {}
            self.market = market.Registry(market.market_dir())
            self.market_row_ids = {}
            self.build_profiles_page()
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
            self.win.present()

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
            self.profile_clone = self.button(controls,tr("Clone to CUSTOM","CUSTOM kopyala","Als CUSTOM klonen"),self.clone_selected)
            self.profile_delete = self.button(controls,tr("Delete","Sil","Löschen"),self.delete_selected)
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

        def refresh_profile_buttons(self):
            chosen = self.selected_profile()
            for w in (self.profile_activate, self.profile_clone, self.profile_delete, self.profile_export):
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

        def activate_profile(self, profile_id, quiet=False):
            entry = self.store.load(profile_id).checked()
            if entry.state != "active":
                raise Hold("profile_not_active")
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
            dialog.connect("response", response)
            dialog.show()

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

        def refresh(self):
            connected = self.cfg["llm"] in self.inventory
            self.entry.set_sensitive(not self.busy)
            self.send.set_sensitive(not self.busy and connected)
            allowed = getattr(self, "policy", None) is not None and \
                self.policy.evaluate("local_audio_device", ("push_to_talk",))[0] == profiles.ALLOW
            self.talk.set_sensitive(not self.busy and connected and allowed \
                and bool(self.cfg["stt"]) and bool(self.cfg["voices"]) \
                and audio.tools_ready("arecord","whisper-cli","piper","aplay"))
            self.stop.set_sensitive(self.stage in ("synthesizing","speaking") and self.playback is not None)
            self.finish_btn.set_sensitive(self.stage=="listening" and self.finish is not None)
            self.cancel.set_sensitive(self.busy)
            self.hard.set_sensitive(self.busy)
            for w in (self.reload,self.url,self.models,*self.pickers,self.test_chat_button,*self.previews):
                w.set_sensitive(not self.busy)
            if not self.busy:
                self.test_chat_button.set_sensitive(connected)
                for widget, lang in self.previews.items():
                    # A preview speaks only the locale that is actually selected.
                    widget.set_sensitive(bool(self.cfg["voices"].get(lang)) and allowed
                                         and audio.tools_ready("piper","aplay"))

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
                self.market_find = self.button(row, tr("Discover installed Jan models","Kurulu Jan modellerini bul","Installierte Jan-Modelle suchen"), self.discover_market)
            wanted = self.market_filter.get_active_text() or "stt"
            rows = [c for c in self.market.cards() if c.category == wanted]
            for card in sorted(rows, key=lambda c: (c.state, c.model_id)):
                label = Gtk.Label(label="%s · %s · %s · %s · %s" % (
                    card.display_name, card.engine, card.artifact_format,
                    card.compatibility_status, card.state), xalign=0, wrap=True)
                item = Gtk.ListBoxRow()
                item.set_child(label)
                self.market_list.append(item)
                self.market_row_ids[item] = card.model_id
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

        def selected_card(self):
            row = self.market_list.get_selected_row()
            return self.market_row_ids.get(row) if row is not None else None

        def refresh_market_buttons(self):
            for w in (self.market_use, self.market_forget):
                w.set_sensitive(not self.busy and self.selected_card() is not None)
            self.market_list.set_sensitive(not self.busy)

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
                raw = self.url.get_text().strip()
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
            dialog.connect("response",response)
            dialog.show()

        def preview(self, lang):
            if self.busy or self.cfg["llm"] not in self.inventory:
                return
            sample = {"tr": "Bu bir ses önizlemesidir.",
                      "en": "This is a voice preview.",
                      "de": "Das ist eine Stimmprobe."}[lang]
            speech = SpeechRequest(sample, lang, audio.voice_for(self.cfg["voices"][lang], lang).id)
            async def run(epoch):
                def progress(stage):
                    if epoch == self.session.epoch:
                        self.set_state(stage)
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
                # A short, anchor-bearing probe so the reply language is provable.
                text = tr("Hello there! In one short sentence, what can you do?",
                          "Merhaba! Tek cümleyle cevapla: neler yapabilirsin?",
                          "Hallo! Antworte in einem kurzen Satz: was kannst du?")
                reply, _ = await probe.reply(text, self.backend_for_test(), epoch)
                self.session.check(epoch)
                self.test_note.set_text("%s · %s" % (reply.strip()[:240], probe.context.response_language or "unknown"))
            self.submit(run)

        def backend_for_test(self):
            return Jan(self.cfg["endpoint"], self.cfg["llm"])

        def submit(self, work):
            if self.busy:
                return
            self.busy=True
            task=self.session.own(work, loop=loop)
            epoch=self.session.epoch
            self.set_state("starting")
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
                if epoch==self.session.epoch:
                    self.set_state("ready",message)
                else:
                    self.set_state("ready","Stopped")
            task.add_done_callback(finished)

        def discover(self):
            async def run(epoch):
                raw=self.url.get_text().strip()
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

        def bubble(self,speaker,text):
            label=Gtk.Label(label=speaker+"\n"+text,xalign=0,selectable=True,wrap=True)
            label.add_css_class("message")
            label.add_css_class("user" if speaker=="You" else "assistant")
            self.log.append(label)
            def bottom():
                adj=self.scroll.get_vadjustment()
                adj.set_value(max(0,adj.get_upper()-adj.get_page_size()))
                return False
            GLib.idle_add(bottom)

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

        def interrupt(self, clear):
            self.session.invalidate(clear=clear)
            if clear:
                child=self.log.get_first_child()
                while child is not None:
                    nxt=child.get_next_sibling()
                    self.log.remove(child)
                    child=nxt
            self.set_state("stopping" if self.busy else "ready")

        def send_text(self):
            text=self.entry.get_text().strip()
            if self.busy or not text or self.cfg["llm"] not in self.inventory:
                return
            self.entry.set_text("")
            async def run(epoch):
                self.bubble("You",text)
                self.set_state("thinking")
                result,_=await self.session.reply(text,Jan(self.cfg["endpoint"], self.cfg["llm"]),epoch)
                self.session.check(epoch)
                self.bubble("JARVIS",result)
            self.submit(run)

        def voice(self):
            async def run(epoch):
                def progress(stage):
                    if epoch == self.session.epoch and not self.playback_stopping:
                        self.set_state(stage)

                voices = audio.SelectedVoices(self.cfg["voices"])
                self.finish=asyncio.Event()
                text=await audio.listen(self.cfg["stt"],self.finish,progress)
                self.session.check(epoch)
                self.bubble("You",text)
                self.set_state("thinking")
                result,speech=await self.session.reply(text,Jan(self.cfg["endpoint"],self.cfg["llm"]),epoch,voices)
                self.session.check(epoch)
                self.bubble("JARVIS",result)
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
            self.submit(run)

        def close(self,*_):
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

    return Desktop(app)


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

# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only chat file results, never model messages or conversation history."""
import asyncio
from threading import Event

from . import files, profiles
from .owned_wait import finish_owned
from .voice.m1 import Hold


class ChatFiles:
    def __init__(self, ui, tools, parent, Gtk, tr):
        self.ui, self.Gtk, self.tr = ui, Gtk, tr
        self.folder = self.live = self.profile_id = None
        self.previewed = False
        self.choose_button = ui.button(tools, tr("Explore a local folder",
            "Yerel klasörü incele", "Lokalen Ordner untersuchen"), self.choose)
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        parent.append(self.panel)
        self.note = Gtk.Label(xalign=0, wrap=True)
        self.panel.append(self.note)
        self.listing = Gtk.Label(xalign=0, wrap=True, selectable=True)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(80)
        scroll.set_max_content_height(160)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(self.listing)
        self.panel.append(scroll)
        self.entries = Gtk.ComboBoxText()
        self.panel.append(self.entries)
        self.read_button = ui.button(self.panel, tr("Preview selected text file",
            "Seçilen metin dosyasını önizle", "Ausgewählte Textdatei ansehen"), self.read)
        self.preview = Gtk.Label(xalign=0, wrap=True, selectable=True)
        preview_scroll = Gtk.ScrolledWindow()
        preview_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        preview_scroll.set_min_content_height(48)
        preview_scroll.set_max_content_height(180)
        preview_scroll.set_propagate_natural_height(True)
        preview_scroll.set_child(self.preview)
        self.panel.append(preview_scroll)
        self.close_button = ui.button(self.panel, tr("Clear local result",
            "Yerel sonucu temizle", "Lokales Ergebnis löschen"), self.clear)
        self.entries.connect("changed", lambda *_: self.selection_changed())
        self.reset()

    @staticmethod
    def allowed(live, scope):
        if live is None:
            return False
        decision, reason = live.evaluate("filesystem_read", (scope,))
        return decision == profiles.ALLOW or reason == "no_rule_matches"

    def reset(self):
        self.folder = self.live = self.profile_id = None
        self.previewed = False
        self.note.set_text("")
        self.listing.set_text("")
        self.entries.remove_all()
        self.preview.set_text("")
        self.panel.set_visible(False)
        self.read_button.set_sensitive(False)

    def clear(self):
        if not self.ui.busy:
            self.reset()
            self.ui.refresh()

    def current(self, scope):
        return (self.folder is not None and self.ui.cfg.get("profile") == self.profile_id
                and self.allowed(self.live, "folder") and self.allowed(self.live, scope))

    def selection_changed(self):
        self.previewed = False
        self.preview.set_text("")
        self.refresh()

    def refresh(self):
        ui = self.ui
        idle = not ui.busy and not ui.closing and ui.owned_picker is None
        idle = idle and (not hasattr(ui, "analysis") or ui.analysis.dialog is None)
        local = getattr(ui, "policy", None) is not None and ui.policy.mode == "LOCAL_ONLY"
        self.choose_button.set_sensitive(idle and local)
        if self.folder is not None and not self.current("folder"):
            self.reset()
        elif self.previewed and not self.current("file"):
            self.previewed = False
            self.preview.set_text("")
        self.entries.set_sensitive(idle)
        self.close_button.set_sensitive(not ui.busy)
        self.read_button.set_sensitive(idle and local and self.current("file")
                                       and self.entries.get_active_id() is not None)

    def bind(self, scope):
        ui = self.ui
        profile, stamp = ui.store.snapshot(ui.cfg["profile"])
        if profile.state != "active" or profile.based_on_mode != "LOCAL_ONLY":
            raise Hold("local_task_mode_denied")
        ui.require_tools(("filesystem_read", (scope,)), selected=True)
        live = ui.store.bind_live(profile.id)
        if live.stamp != stamp or not self.allowed(live, scope):
            raise Hold("profile_changed")
        return profile.id, live

    def choose(self):
        ui, Gtk = self.ui, self.Gtk
        if ui.busy or ui.closing:
            return
        try:
            self.bind("folder")
        except Hold as exc:
            ui.set_state("ready", str(exc))
            return
        dialog = Gtk.FileChooserNative.new(self.tr("Choose a local folder",
            "Yerel klasör seç", "Lokalen Ordner wählen"), ui.win,
            Gtk.FileChooserAction.SELECT_FOLDER, self.tr("Choose", "Seç", "Wählen"),
            self.tr("Cancel", "İptal", "Abbrechen"))
        def response(d, res):
            try:
                if res == Gtk.ResponseType.ACCEPT:
                    file = d.get_file()
                    self.start(file.get_path() if file else None)
            finally:
                d.destroy()
        return ui.show_owned_picker(dialog, response)

    async def run_worker(self, epoch, profile_id, live, scopes, work):
        # The worker never reads GTK or mutable UI state.
        stop = Event()
        def checkpoint():
            if stop.is_set():
                raise Hold("turn_cancelled")
            if not all(self.allowed(live, scope) for scope in scopes):
                raise Hold("local_file_not_authorized")
        def publish_check():
            self.ui.session.check(epoch)
            checkpoint()
            if (self.ui.closing or self.ui.cfg.get("profile") != profile_id
                    or not self.ui.store.is_current(profile_id, live.stamp)):
                raise Hold("profile_changed")
        publish_check()
        worker = asyncio.create_task(asyncio.to_thread(work, checkpoint))
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            stop.set()
            await finish_owned(asyncio.gather(worker, return_exceptions=True))
            raise
        publish_check()
        return result

    def start(self, root):
        ui = self.ui
        if ui.busy or ui.closing:
            return
        self.reset()
        try:
            root = files.checked_root(root)
            profile_id, live = self.bind("folder")
        except Hold as exc:
            ui.set_state("ready", str(exc))
            return
        async def run(epoch):
            folder = await self.run_worker(epoch, profile_id, live, ("folder",),
                lambda checkpoint: files.list_dir(root, checkpoint=checkpoint))
            self.folder, self.profile_id, self.live = folder, profile_id, live
            rows = []
            for index, entry in enumerate(folder.entries):
                rows.append(entry.name + ("/" if entry.directory else " · %d bytes" % entry.size))
                if not entry.directory:
                    self.entries.append(str(index), entry.name)
            self.listing.set_text("\n".join(rows))
            note = self.tr("Local folder · %d entries · not sent to a model",
                "Yerel klasör · %d girdi · modele gönderilmedi",
                "Lokaler Ordner · %d Einträge · nicht an ein Modell gesendet") % len(rows)
            if folder.limited:
                note += " · " + self.tr("Listing limit reached", "Listeleme sınırı", "Listengrenze erreicht")
            self.note.set_text(note)
            self.panel.set_visible(True)
        ui.submit(run, kind="file_research", label=self.tr("List local folder",
            "Yerel klasörü listele", "Lokalen Ordner auflisten"))

    def read(self):
        ui = self.ui
        if ui.busy or ui.closing:
            return
        token = self.entries.get_active_id()
        self.previewed = False
        self.preview.set_text("")
        try:
            if token is None or not self.current("file"):
                raise Hold("local_file_not_authorized")
            folder = self.folder
            entry = folder.entries[int(token)]
            if entry.directory:
                raise Hold("file_not_found")
            profile_id, live = self.bind("file")
            if profile_id != self.profile_id or live.stamp != self.live.stamp:
                raise Hold("profile_changed")
        except (Hold, ValueError, IndexError) as exc:
            ui.set_state("ready", str(exc) if isinstance(exc, Hold) else "file_not_found")
            return
        async def run(epoch):
            text = await self.run_worker(epoch, profile_id, live, ("folder", "file"),
                lambda checkpoint: files.preview(folder.root, entry.name,
                    expected_root=folder.identity, expected_entry=entry.identity,
                    checkpoint=checkpoint))
            if self.folder is not folder or self.entries.get_active_id() != token:
                raise Hold("file_selection_changed")
            self.preview.set_text(text)
            self.previewed = True
        ui.submit(run, kind="file_research", label=self.tr("Preview local text file",
            "Yerel metin dosyasını önizle", "Lokale Textdatei ansehen"))

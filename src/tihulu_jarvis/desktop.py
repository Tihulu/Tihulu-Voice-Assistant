# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Opt-in GTK 4 desktop client. No listener, background capture or provider login."""
import asyncio
import locale
from pathlib import Path

from . import settings
from .voice import audio
from .voice.jan import Jan
from .voice.m1 import Hold
from .voice.session import Session


def main():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gio, GLib, Gtk
    except (ImportError, ValueError):
        raise SystemExit("GTK 4 with PyGObject is required for the desktop client.")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
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
            self.stop = self.button(controls,tr("Stop speaking","Sesi durdur","Wiedergabe stoppen"),lambda:self.playback.cancel() if self.playback else None)
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
            self.labels, self.pickers = {}, []
            for key, caption in [("stt","STT · Whisper (.bin)"),("tr","Piper · Türkçe"),("en","Piper · English"),("de","Piper · Deutsch")]:
                row = Gtk.Box(spacing=8)
                models.append(row)
                btn = self.button(row, caption, lambda k=key:self.choose(k))
                self.pickers.append(btn)
                self.labels[key] = Gtk.Label(xalign=0, wrap=True)
                row.append(self.labels[key])
            self.update_labels()
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

        def refresh(self):
            connected = self.cfg["llm"] in self.inventory
            self.entry.set_sensitive(not self.busy)
            self.send.set_sensitive(not self.busy and connected)
            self.talk.set_sensitive(not self.busy and connected and bool(self.cfg["stt"]) and bool(self.cfg["voices"]) and audio.tools_ready("arecord","whisper-cli","piper","aplay"))
            self.stop.set_sensitive(self.stage in ("synthesizing","speaking") and self.playback is not None)
            self.finish_btn.set_sensitive(self.stage=="listening" and self.finish is not None)
            self.cancel.set_sensitive(self.busy)
            self.hard.set_sensitive(self.busy)
            for w in (self.reload,self.url,self.models,*self.pickers):
                w.set_sensitive(not self.busy)

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
                voices={lang:audio.voice_for(p,lang) for lang,p in self.cfg["voices"].items()}
                self.finish=asyncio.Event()
                text=await audio.listen(self.cfg["stt"],self.finish,self.set_state)
                self.session.check(epoch)
                self.bubble("You",text)
                self.set_state("thinking")
                result,speech=await self.session.reply(text,Jan(self.cfg["endpoint"],self.cfg["llm"]),epoch,voices)
                self.session.check(epoch)
                self.bubble("JARVIS",result)
                self.playback=loop.create_task(audio.speak(speech,self.set_state))
                try:
                    await self.playback
                except asyncio.CancelledError:
                    self.session.check(epoch)
                finally:
                    self.playback=None
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

    app=Gtk.Application(application_id="io.github.Tihulu.Jarvis", flags=Gio.ApplicationFlags.FLAGS_NONE)
    state=[]
    def activate(a):
        if a.get_active_window():
            a.get_active_window().present()
        else:
            state.append(Desktop(a))
    app.connect("activate",activate)
    def tick():
        loop.call_soon(loop.stop)
        loop.run_forever()
        return True
    timer=GLib.timeout_add(12,tick)
    try:
        return app.run(None)
    finally:
        GLib.source_remove(timer)
        pending=asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    raise SystemExit(main())

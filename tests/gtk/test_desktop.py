# SPDX-License-Identifier: AGPL-3.0-only
"""Actual GTK widgets and GLib/asyncio scheduling; synthetic backends only.

Run separately under dbus-run-session and Xvfb. Missing GTK/display is a
failure, not a skip. No microphone, installed models or Jan service is used.
"""
import asyncio
import gc
import json
import sys
import tempfile
import time
import unittest
from collections import deque
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tihulu_jarvis import desktop, settings
from tihulu_jarvis.voice.m1 import Hold

Gio, GLib, Gtk = desktop._gtk()

# A Gtk.Application exports a fixed D-Bus interface at its application id, and that
# export cannot be taken twice in one process. One application serves every test.
APP_ID = "io.github.Tihulu.Jarvis.Test"


class Backend:
    def __init__(self):
        self.inventory = ("fixture-chat",)
        self.requests, self.speeches = [], []
        self.answers = deque()
        self.transcript = "naber nasilsin"
        self.capture_error = False
        self.hold_transcription = False
        self.hold_chat = False
        self.capture_cleaning = self.capture_cleaned = False
        self.playback_cleaning = self.playback_cleaned = False
        self.chat_entered = self.chat_cancelled = False
        self.capture_release = asyncio.Event()
        self.playback_release = asyncio.Event()
        self.chat_release = asyncio.Event()
        self.playback_stage = "speaking"

    async def models(self):
        return self.inventory

    async def __call__(self, messages):
        self.requests.append(messages)
        self.chat_entered = True
        if self.hold_chat:
            try:
                await self.chat_release.wait()
            except asyncio.CancelledError:
                # Deliberately uncooperative backend: return a late reply.
                self.chat_cancelled = True
                await self.chat_release.wait()
        return self.answers.popleft() if self.answers else "Merhaba!"

    async def listen(self, model, finish, publish):
        if self.capture_error:
            raise Hold("capture_fixture_failure")
        try:
            publish("listening")
            await finish.wait()
            publish("transcribing")
            if self.hold_transcription:
                await asyncio.Future()
            return self.transcript
        except asyncio.CancelledError:
            self.capture_cleaning = True
            await self.capture_release.wait()
            publish("listening")  # Late progress must not restore controls.
            self.capture_cleaned = True
            raise

    async def speak(self, speech, publish):
        self.speeches.append(speech)
        publish(self.playback_stage)
        try:
            await asyncio.Future()
        finally:
            self.playback_cleaning = True
            await self.playback_release.wait()
            publish("speaking")
            self.playback_cleaned = True


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)
        cls.app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)
        assert cls.app.get_is_registered() is True

    @classmethod
    def tearDownClass(cls):
        pending = asyncio.all_tasks(cls.loop)
        for task in pending:
            task.cancel()
        if pending:
            cls.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        cls.loop.run_until_complete(cls.loop.shutdown_asyncgens())
        cls.loop.close()
        asyncio.set_event_loop(None)
        cls.app = None
        gc.collect()  # Release the application's D-Bus export before exit.

    def setUp(self):
        self.ui = None
        self.bridge = None
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.backend = Backend()
        cfg = settings.empty()
        cfg.update(endpoint="http://127.0.0.1:9876", llm="fixture-chat",
                   stt=str(tmp / "synthetic.bin"))
        Path(cfg["stt"]).touch()
        for lang in ("tr", "en", "de"):
            model = tmp / (lang + ".onnx")
            model.touch()
            model.with_name(model.name + ".json").write_text(
                json.dumps({"language": {"code": lang}}), encoding="utf-8")
            cfg["voices"][lang] = str(model)
        settings.save(cfg, tmp / "models.json")
        self.stack.enter_context(patch.object(settings, "config_path", return_value=tmp / "models.json"))
        self.jan = self.stack.enter_context(patch.object(desktop, "Jan", return_value=self.backend))
        self.stack.enter_context(patch.object(desktop.audio, "listen", side_effect=self.backend.listen))
        self.stack.enter_context(patch.object(desktop.audio, "speak", side_effect=self.backend.speak))
        self.stack.enter_context(patch.object(desktop.audio, "tools_ready", return_value=True))
        self.callback_errors = []
        self.stack.enter_context(patch.object(
            sys, "excepthook", side_effect=lambda *args: self.callback_errors.append(args[1])))
        self.loop.set_exception_handler(lambda loop, ctx: self.callback_errors.append(ctx.get("exception", ctx.get("message"))))
        self.bridge = desktop.gtk_asyncio(self.loop)
        self.bridge.__enter__()
        self.addCleanup(self.close_bridge)
        self.ui = desktop.create_window(self.app, self.loop)
        self.until(lambda: self.ui.win.get_mapped())

    def close_bridge(self):
        if self.bridge is not None:
            bridge, self.bridge = self.bridge, None
            bridge.__exit__(None, None, None)

    def tearDown(self):
        try:
            for event in (self.backend.capture_release, self.backend.playback_release,
                          self.backend.chat_release):
                event.set()
            if self.ui is not None and self.ui.busy:
                self.ui.interrupt(True)
                self.until(lambda: not self.ui.busy)
            if self.ui is not None:
                self.ui.win.close()
                self.until(lambda: not self.app.get_windows())
            self.assertFalse(self.callback_errors, self.callback_errors)
            self.assertFalse(asyncio.all_tasks(self.loop), "Owned task outlived the window")
            self.close_bridge()
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            self.close_bridge()
            raise

    def until(self, predicate):
        """Condition-based GLib wait, with a bounded failure deadline."""
        pump = GLib.MainLoop()
        deadline = time.monotonic() + 4
        outcome = []

        def inspect():
            try:
                ready = predicate()
            except Exception as exc:
                self.callback_errors.append(exc)
                ready = False
            if ready:
                outcome.append(True)
            elif self.callback_errors or time.monotonic() >= deadline:
                outcome.append(False)
            else:
                return True
            pump.quit()
            return False

        GLib.timeout_add(5, inspect)
        pump.run()
        self.assertTrue(outcome and outcome[0], "GTK state transition timed out")
        self.assertFalse(self.callback_errors, self.callback_errors)

    def click(self, widget):
        self.assertTrue(widget.get_sensitive())
        widget.emit("clicked")

    def connect_model(self):
        self.click(self.ui.reload)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.inventory, ("fixture-chat",))
        self.assertTrue(self.ui.send.get_sensitive())

    def text(self, message):
        self.ui.entry.set_text(message)
        self.click(self.ui.send)
        self.until(lambda: not self.ui.busy)

    def begin_voice(self):
        self.click(self.ui.talk)
        self.until(lambda: self.ui.stage == "listening")
        self.assertFalse(self.ui.send.get_sensitive())
        self.assertFalse(self.ui.reload.get_sensitive())

    def begin_playback(self):
        self.begin_voice()
        self.click(self.ui.finish_btn)
        self.until(lambda: self.ui.stage == self.backend.playback_stage)

    def bubbles(self):
        rows, item = [], self.ui.log.get_first_child()
        while item is not None:
            rows.append(item.get_text())
            item = item.get_next_sibling()
        return rows

    def test_startup_is_idle_and_text_is_independent_of_audio(self):
        self.assertFalse(self.ui.busy)
        self.assertFalse(self.ui.send.get_sensitive())
        self.assertFalse(self.ui.talk.get_sensitive())
        self.assertFalse(self.ui.stop.get_sensitive())
        self.jan.assert_not_called()
        self.assertFalse(self.backend.requests)
        self.connect_model()
        self.ui.cfg["stt"], self.ui.cfg["voices"] = "", {}
        self.ui.refresh()
        self.assertFalse(self.ui.talk.get_sensitive())
        self.text("naber nasilsin")
        self.assertEqual(self.bubbles(), ["You\nnaber nasilsin", "JARVIS\nMerhaba!"])
        self.assertEqual(len(self.ui.session.history), 2)
        self.assertFalse(self.backend.speeches)

    def test_four_turn_tr_en_de_tr_preserves_memory(self):
        self.connect_model()
        messages = ("Merhaba! Benim kelimem mavi. Hatırla.",
                    "What was my word?", "Was war mein Wort?", "Benim kelimem ne?")
        self.backend.answers.extend(("Merhaba, kelimen mavi.", "Your word was mavi.",
                                     "Dein Wort war mavi.", "Evet, kelimen mavi."))
        for index, (message, lang) in enumerate(zip(messages, ("tr", "en", "de", "tr"))):
            self.text(message)
            self.assertEqual(self.ui.session.context.response_language, lang)
            request = self.backend.requests[-1]
            self.assertEqual(len(request), 2 + index * 2)
            if index:
                self.assertEqual(request[1].content, messages[0])
        self.assertEqual(len(self.ui.session.history), 8)
        self.assertEqual(len(self.bubbles()), 8)

    def test_voice_uses_each_response_locale(self):
        self.connect_model()
        for lang, text, answer in (("tr", "naber nasilsin", "Merhaba!"),
                                   ("en", "Hello how are you?", "Hello!"),
                                   ("de", "Hallo wie geht es dir?", "Hallo!")):
            with self.subTest(lang=lang):
                self.backend.transcript = text
                self.backend.answers.append(answer)
                self.backend.playback_cleaning = self.backend.playback_cleaned = False
                self.backend.playback_release.clear()
                self.begin_playback()
                speech = self.backend.speeches[-1]
                self.assertEqual((speech.language, speech.voice_id),
                                 (lang, self.ui.cfg["voices"][lang]))
                self.click(self.ui.stop)
                self.until(lambda: self.backend.playback_cleaning)
                self.backend.playback_release.set()
                self.until(lambda: not self.ui.busy)
        self.assertEqual(len(self.ui.session.history), 6)

    def test_stop_speaking_drains_once_and_preserves_conversation(self):
        self.connect_model()
        self.begin_playback()
        history = self.ui.session.history
        epoch = self.ui.session.epoch
        self.click(self.ui.stop)
        self.until(lambda: self.backend.playback_cleaning)
        self.assertFalse(self.ui.stop.get_sensitive())
        self.ui.stop.emit("clicked")  # Queued duplicate signal after disabling.
        self.assertTrue(self.ui.busy)
        self.assertFalse(self.ui.playback.done())
        self.assertFalse(self.ui.send.get_sensitive())
        self.backend.playback_release.set()
        self.until(lambda: not self.ui.busy)
        self.assertTrue(self.backend.playback_cleaned)
        self.assertEqual(self.ui.session.epoch, epoch)
        self.assertEqual(self.ui.session.history, history)
        self.text("Merhaba!")
        self.assertEqual(len(self.ui.session.history), 4)

    def cancel_capture(self, transcribing=False):
        self.connect_model()
        self.backend.hold_transcription = transcribing
        self.begin_voice()
        if transcribing:
            self.click(self.ui.finish_btn)
            self.until(lambda: self.ui.stage == "transcribing")
        self.click(self.ui.cancel)
        self.until(lambda: self.backend.capture_cleaning)
        self.click(self.ui.hard)
        self.click(self.ui.cancel)
        self.assertTrue(self.ui.busy)
        self.assertFalse(self.ui.session.active.done())
        self.assertFalse(self.ui.talk.get_sensitive())
        self.backend.capture_release.set()
        self.until(lambda: not self.ui.busy)
        self.assertTrue(self.backend.capture_cleaned)
        self.assertEqual(self.ui.stage, "ready")
        self.assertFalse(self.ui.finish_btn.get_sensitive())
        self.assertFalse(self.backend.requests)
        self.assertEqual(self.ui.session.history, ())
        self.assertEqual(self.bubbles(), [])

    def test_cancel_during_capture(self):
        self.cancel_capture()

    def test_cancel_during_transcription(self):
        self.cancel_capture(transcribing=True)

    def test_end_discards_late_chat_and_clears_memory(self):
        self.connect_model()
        self.text("Merhaba!")
        self.backend.hold_chat = True
        self.backend.chat_entered = False
        self.ui.entry.set_text("naber nasilsin")
        self.click(self.ui.send)
        self.until(lambda: self.backend.chat_entered)
        self.click(self.ui.end)
        self.until(lambda: self.backend.chat_cancelled)
        self.click(self.ui.hard)
        self.assertEqual(self.bubbles(), [])
        self.backend.chat_release.set()
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.session.history, ())
        self.assertIsNone(self.ui.session.context.response_language)
        self.assertEqual(self.bubbles(), [])

    def test_hard_stop_after_stop_speaking_waits_for_synthesis_cleanup(self):
        self.connect_model()
        self.backend.playback_stage = "synthesizing"
        self.begin_playback()
        self.click(self.ui.stop)
        self.until(lambda: self.backend.playback_cleaning)
        self.click(self.ui.hard)
        self.click(self.ui.end)
        self.assertTrue(self.ui.busy)
        self.assertFalse(self.ui.playback.done())
        self.backend.playback_release.set()
        self.until(lambda: not self.ui.busy)
        self.assertTrue(self.backend.playback_cleaned)
        self.assertEqual(self.ui.session.history, ())
        self.assertEqual(self.bubbles(), [])
        self.assertFalse(self.ui.stop.get_sensitive())

    def test_close_waits_for_playback_cleanup(self):
        self.connect_model()
        self.begin_playback()
        self.ui.win.close()
        self.until(lambda: self.backend.playback_cleaning)
        self.assertTrue(self.ui.win.get_visible())
        self.assertTrue(self.ui.busy)
        self.ui.win.close()
        self.backend.playback_release.set()
        self.until(lambda: not self.app.get_windows())
        self.assertTrue(self.backend.playback_cleaned)
        self.assertFalse(self.ui.busy)

    def test_capture_error_recovers_for_text_chat(self):
        self.connect_model()
        self.backend.capture_error = True
        self.click(self.ui.talk)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.status.get_text(), "capture_fixture_failure")
        self.assertFalse(self.backend.requests)
        self.text("Merhaba!")
        self.assertEqual(len(self.ui.session.history), 2)

    def test_endpoint_change_and_empty_inventory_disable_submission(self):
        self.connect_model()
        self.ui.url.set_text("http://127.0.0.1:9877")
        self.assertFalse(self.ui.send.get_sensitive())
        self.assertFalse(self.ui.talk.get_sensitive())
        self.backend.inventory = ()
        self.click(self.ui.reload)
        self.until(lambda: not self.ui.busy)
        self.assertFalse(self.ui.send.get_sensitive())
        self.assertEqual(self.ui.status.get_text(), "no_installed_chat_models")

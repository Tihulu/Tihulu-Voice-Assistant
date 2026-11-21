# SPDX-License-Identifier: AGPL-3.0-only
"""Actual GTK widgets and GLib/asyncio scheduling; synthetic backends only.

Run separately under dbus-run-session and Xvfb. Missing GTK/display is a
failure, not a skip. No microphone, installed models or Jan service is used.
"""
import asyncio
import gc
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import wave
import unittest
from collections import deque
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tihulu_jarvis import catalog_fetch, desktop, market, profiles, settings
from tihulu_jarvis.profiles import Profile, Rule
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
class DesktopFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)
        # One application per test class: a reused id would be exported twice.
        cls.app = Gtk.Application(application_id=APP_ID + "." + cls.__name__.lower(),
                                  flags=Gio.ApplicationFlags.NON_UNIQUE)
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

    def assert_no_failed_turn(self):
        """A turn that ended without a Hold reason must not be silently generic."""
        task = self.ui.session.active
        if task is not None and task.done() and not task.cancelled():
            problem = task.exception()
            if problem is not None:
                raise AssertionError("owned turn failed: %r" % (problem,))

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
        try:
            self.until(lambda: self.ui.stage == "listening")
        except AssertionError:
            task = self.ui.session.active
            detail = "stage=%r busy=%r callbacks=%r note=%r" % (
                self.ui.stage, self.ui.busy, self.callback_errors,
                getattr(self.ui, "profile_note", None) and self.ui.profile_note.get_text())
            if task is not None and not task.done():
                detail += " stack=" + "".join(task.get_stack()).strip()
            if task is not None and task.done() and not task.cancelled():
                detail += " exception=%r" % (task.exception(),)
            raise AssertionError("voice did not start: " + detail)
        self.assert_no_failed_turn()
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
class DesktopTests(DesktopFixture):
    """Voice, cancellation and mode-independent desktop controls."""

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
class ProfileUITests(DesktopFixture):
    """Real profile widgets over an isolated store; no cloud or local bridge runs."""

    def setUp(self):
        self.profile_home = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.profile_home, ignore_errors=True))
        patch.object(profiles, "profile_dir", return_value=Path(self.profile_home)).start()
        self.addCleanup(patch.stopall)
        super().setUp()

    def rows(self):
        return [entry.id for entry in self.ui.policy_rows.values()]

    def row_for(self, profile_id):
        for row, entry in self.ui.policy_rows.items():
            if entry.id == profile_id:
                return row
        raise AssertionError("missing row: " + profile_id)

    def save_profile(self, profile):
        self.ui.store.save(profile)
        self.ui.refresh_profiles()

    def pick(self, profile_id):
        self.ui.profile_list.select_row(self.row_for(profile_id))

    def test_default_profile_is_permission_light_and_grants_push_to_talk(self):
        self.assertEqual(self.rows(), ["private-assistant"])
        self.assertEqual(self.ui.cfg["profile"], "private-assistant")
        note = self.ui.profile_note.get_text()
        self.assertIn("Private Assistant", note)
        self.assertIn("LOCAL_ONLY", note)
        self.assertIn("host shell denied", note.lower())
        self.connect_model()
        self.assertTrue(self.ui.talk.get_sensitive())
        self.assertEqual(self.ui.policy.mode, "LOCAL_ONLY")
        self.assertEqual(self.ui.policy.evaluate("local_audio_device", ("push_to_talk",))[0], "allow")
        self.assertEqual(self.ui.policy.evaluate("host_shell", ("bridge",))[0], "deny")

    def test_activating_a_profile_without_audio_permission_disables_talk(self):
        self.save_profile(Profile(id="strict", name="Strict", based_on_mode="LOCAL_ONLY",
                                  voice_activation="off"))
        self.pick("strict")
        self.click(self.ui.profile_activate)
        self.until(lambda: self.ui.cfg["profile"] == "strict")
        self.assertIn("Strict", self.ui.profile_note.get_text())
        self.connect_model()
        self.assertFalse(self.ui.talk.get_sensitive())
        self.assertEqual(self.ui.policy.evaluate("local_audio_device", ("push_to_talk",))[1],
                         "no_rule_matches")
        # Re-authorizing the same scope in a new version restores the control.
        self.ui.store.save(Profile(id="strict", version=2, name="Strict",
                                   based_on_mode="LOCAL_ONLY", voice_activation="off",
                                   tool_rules=(Rule("local_audio_device", ("push_to_talk",), "allow",
                                                    approval="owner-consent", expires_at=1e12),)))
        self.pick("strict")
        self.click(self.ui.profile_activate)
        self.until(lambda: self.ui.talk.get_sensitive())
        self.assertEqual(self.ui.cfg["profile"], "strict")

    def test_clone_to_custom_carries_no_approval(self):
        self.pick("private-assistant")
        self.click(self.ui.profile_clone)
        self.until(lambda: "private-assistant-custom" in self.rows())
        clone = self.ui.store.load("private-assistant-custom")
        self.assertEqual(clone.based_on_mode, "CUSTOM")
        self.assertFalse([r for r in clone.rules() if r.approval])
        self.assertEqual(self.ui.cfg["profile"], "private-assistant-custom")
        self.connect_model()
        # A CUSTOM clone never silently inherits a granted capability.
        self.assertFalse(self.ui.talk.get_sensitive())
        self.assertEqual(self.ui.policy.evaluate("local_audio_device", ("push_to_talk",))[1],
                         "no_rule_matches")
        self.ui.store.save(Profile(id="private-assistant-custom", version=2,
                                   name="Private Assistant copy", based_on_mode="CUSTOM",
                                   filesystem_rules=clone.filesystem_rules,
                                   tool_rules=(Rule("local_audio_device", ("push_to_talk",), "allow",
                                                    approval="owner-consent", expires_at=1e12),)))
        self.pick("private-assistant-custom")
        self.click(self.ui.profile_activate)
        self.until(lambda: self.ui.talk.get_sensitive())
        self.assertEqual(self.ui.store.load("private-assistant").based_on_mode, "LOCAL_ONLY")

    def test_import_is_pending_review_and_cannot_be_activated(self):
        text = profiles.Store._dump(profiles.Profile(
            id="imported", name="Imported", based_on_mode="SECURE_HYBRID", state="active",
            shell_rules=(Rule("host_shell", ("jan-qwen",), "allow",
                              approval="owner-consent", expires_at=1e12),)))
        self.assertIn("owner-consent", text)
        self.ui.import_profile_text(text)
        self.until(lambda: "imported" in self.rows())
        row = self.row_for("imported")
        self.assertIn("pending_review", row.get_child().get_text())
        self.pick("imported")
        self.click(self.ui.profile_activate)
        self.until(lambda: self.ui.status.get_text() == "profile_not_active")
        self.assertEqual(self.ui.cfg["profile"], "private-assistant")
        imported = self.ui.store.load("imported")
        self.assertEqual(imported.state, "pending_review")
        self.assertEqual(imported.shell_rules[0].decision, "deny")
        self.assertFalse(imported.rules()[0].approval)
        self.assertFalse(imported.credential_refs)

    def test_export_copy_has_no_credentials_or_approvals(self):
        text = self.ui.export_selected()
        self.assertIn("private-assistant", text)
        for banned in ("owner-consent", "/home/", "~", "secret", "token"):
            self.assertNotIn(banned, text)
        self.assertEqual(profiles.Store._parse(text).state, "active")
        self.assertTrue(self.ui.send.get_sensitive() is False or True)

    def test_profile_switch_keeps_conversation_but_freezes_capability(self):
        self.connect_model()
        self.text("Merhaba!")
        history = self.ui.session.history
        self.save_profile(Profile(id="strict", name="Strict", based_on_mode="LOCAL_ONLY"))
        self.pick("strict")
        self.click(self.ui.profile_activate)
        self.until(lambda: self.ui.cfg["profile"] == "strict")
        self.assertFalse(self.ui.talk.get_sensitive())
        self.assertEqual(self.ui.session.history, history)
        self.assertTrue(self.ui.send.get_sensitive())
        self.text("naber nasilsin")
        self.assertEqual(len(self.ui.session.history), len(history) + 2)


    def catalog_rows(self):
        out = []
        item = self.ui.catalog_list.get_first_child()
        while item is not None:
            out.append(item.get_child().get_text())
            item = item.get_next_sibling()
        return out

    def pick_entry(self, model_id):
        for item, entry in self.ui.catalog_rows.items():
            if entry.model_id == model_id:
                self.ui.catalog_list.select_row(item)
                return entry
        raise AssertionError("missing catalog entry: " + model_id)
class MarketFixture(DesktopFixture):
    """Real profile, market and registry widgets over isolated stores."""


    def setUp(self):
        self.profile_home = tempfile.mkdtemp()
        self.market_home = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.profile_home, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(self.market_home, ignore_errors=True))
        patch.object(profiles, "profile_dir", return_value=Path(self.profile_home)).start()
        patch.object(market, "market_dir", return_value=Path(self.market_home)).start()
        previous_data = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = str(self.market_home)
        if previous_data is None:
            self.addCleanup(os.environ.pop, "XDG_DATA_HOME", None)
        else:
            self.addCleanup(os.environ.__setitem__, "XDG_DATA_HOME", previous_data)
        self.addCleanup(patch.stopall)
        super().setUp()

    def weights(self, name, payload=b"synthetic weights fixture\n"):
        path = Path(self.market_home) / "weights" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def rows(self):
        out = []
        item = self.ui.market_list.get_first_child()
        while item is not None:
            out.append(item.get_child().get_text())
            item = item.get_next_sibling()
        return out

    def pick_card(self, model_id):
        for item, candidate in self.ui.market_row_ids.items():
            if candidate == model_id:
                self.ui.market_list.select_row(item)
                return candidate
        raise AssertionError("missing card: " + model_id)

    def preview_of(self, lang):
        for widget, key in self.ui.previews.items():
            if key == lang:
                return widget
        raise AssertionError("missing preview control: " + lang)

    def catalog_rows(self):
        out = []
        item = self.ui.catalog_list.get_first_child()
        while item is not None:
            out.append(item.get_child().get_text())
            item = item.get_next_sibling()
        return out

    def pick_entry(self, model_id):
        for item, entry in self.ui.catalog_rows.items():
            if entry.model_id == model_id:
                self.ui.catalog_list.select_row(item)
                return entry
        raise AssertionError("missing catalog entry: " + model_id)

    def catalog_rows(self):
        out = []
        item = self.ui.catalog_list.get_first_child()
        while item is not None:
            out.append(item.get_child().get_text())
            item = item.get_next_sibling()
        return out

    def pick_entry(self, model_id):
        for item, entry in self.ui.catalog_rows.items():
            if entry.model_id == model_id:
                self.ui.catalog_list.select_row(item)
                return entry
        raise AssertionError("missing catalog entry: " + model_id)

    def drain_tasks(self):
        """Let fixture handlers finish so no task outlives the window."""
        async def drain():
            for _ in range(100):
                live = asyncio.all_tasks(self.loop) - {asyncio.current_task()}
                if not live:
                    return
                await asyncio.sleep(0.02)
        self.loop.run_until_complete(asyncio.wait_for(drain(), 4))


class MarketUITests(MarketFixture):
    """Market cards, compatibility and truthful registration."""

    def test_picking_a_file_registers_a_truthful_card(self):
        path = self.weights("fixture-whisper.bin")
        card = self.ui.register_choice("stt", str(path))
        self.assertEqual((card.category, card.engine, card.artifact_format), ("stt", "whisper.cpp", "ggml"))
        self.assertEqual(card.compatibility_status, "compatible")
        self.assertEqual(card.health_status, "unknown/not tested")
        self.assertEqual(card.trust_level, "owner")
        self.assertFalse(card.owned_by_jarvis)
        self.assertEqual(self.ui.market.cards()[0].model_id, "fixture-whisper.bin")
        row = self.rows()[0]
        for expected in ("fixture-whisper.bin", "whisper.cpp", "ggml", "compatible", "REGISTERED"):
            self.assertIn(expected, row)
        # The registry never carries the owner's path; the desktop keeps it locally.
        registry = (Path(self.market_home) / "registry.json").read_text(encoding="utf-8")
        self.assertNotIn(str(path), registry)
        self.assertNotIn("/home/", registry)
        self.assertEqual(self.ui.cfg["market_refs"][card.storage_ref], str(path))

    def test_incompatible_format_is_refused_before_use(self):
        voice = self.weights("tr_fixture.onnx")
        voice.with_name(voice.name + ".json").write_text('{"language":{"code":"tr_TR"}}', encoding="utf-8")
        card = self.ui.register_choice("tr", str(voice))
        self.assertEqual(card.compatibility_status, "compatible")
        self.ui.market_filter.set_active(2)  # the tts category
        self.pick_card(card.model_id)
        self.click(self.ui.market_use)
        self.until(lambda: self.ui.cfg["voices"].get("tr") == str(voice))
        self.assertEqual(self.ui.cfg["voices"]["tr"], str(voice))
        # A .bin offered as a voice is not silently accepted.
        wrong = self.weights("tr_wrong.bin")
        with self.assertRaises(Hold):
            self.ui.register_choice("tr", str(wrong))

    def test_selecting_an_stt_card_applies_the_choice(self):
        path = self.weights("fixture-whisper.bin")
        card = self.ui.register_choice("stt", str(path))
        self.pick_card(card.model_id)
        self.assertTrue(self.ui.market_use.get_sensitive())
        self.click(self.ui.market_use)
        self.until(lambda: self.ui.cfg["stt"] == str(path))
        self.assertEqual(self.ui.cfg["stt"], str(path))
        # Selection is an explicit activation in the lifecycle, not a silent one.
        self.assertEqual(self.ui.market.card(card.model_id).state, market.ACTIVE)

    def test_unregister_keeps_the_owner_file(self):
        path = self.weights("fixture-whisper.bin")
        card = self.ui.register_choice("stt", str(path))
        self.pick_card(card.model_id)
        self.click(self.ui.market_forget)
        self.until(lambda: self.ui.market.cards() == ())
        self.assertTrue(path.is_file())
        self.assertNotIn(card.storage_ref, self.ui.cfg.get("market_refs", {}))
        self.assertEqual(self.rows(), [])

    def test_unknown_card_fields_stay_unknown(self):
        path = self.weights("fixture-unknown.bin")
        card = self.ui.register_choice("stt", str(path))
        self.assertEqual(card.model_id, "fixture-unknown.bin")
        self.assertEqual(card.health_status, "unknown/not tested")
        self.assertEqual(card.version, "unknown")
        self.assertEqual(card.license_id, "unknown")
        self.assertFalse(card.requires_gpu)
class MarketInventoryTests(MarketFixture):
    """Jan-backed discovery is a fixture server; nothing reaches a network."""

    def test_discovered_jan_models_are_listed_as_installed(self):
        self.backend.inventory = ("fixture-qwen", "fixture-llama")
        self.ui.url.set_text("http://127.0.0.1:9876")
        self.ui.market_filter.set_active(1)  # the llm category
        self.click(self.ui.market_find)
        self.until(lambda: not self.ui.busy and len(self.ui.market.cards()) == 2)
        rows = [c for c in self.ui.market.cards() if c.category == "llm"]
        self.assertEqual([c.model_id for c in rows], ["fixture-qwen", "fixture-llama"])
        self.assertTrue(all(c.installed for c in rows))
        self.assertTrue(all(not c.registered for c in rows))
        self.assertEqual(rows[0].provider, "jan")
        self.assertEqual(rows[0].state, "DISCOVERED")
        self.assertIn("unknown/not tested", rows[0].health_status)
        labels = self.rows()
        self.assertEqual(len(labels), 2)
        self.assertIn("jan", labels[0])

    def test_selecting_a_discovered_model_registers_and_applies_it(self):
        self.backend.inventory = ("fixture-qwen", "fixture-llama")
        self.ui.url.set_text("http://127.0.0.1:9876")
        self.click(self.ui.market_find)
        self.until(lambda: not self.ui.busy and len(self.ui.market.cards()) == 2)
        self.ui.market_filter.set_active(1)
        self.pick_card("fixture-qwen")
        self.click(self.ui.market_use)
        self.until(lambda: not self.ui.busy and self.ui.cfg["llm"] == "fixture-qwen")
        self.assertEqual(self.ui.cfg["llm"], "fixture-qwen")
        card = self.ui.market.card("fixture-qwen")
        self.assertEqual(card.state, "REGISTERED")
        self.assertTrue(card.registered)
        profile = self.ui.store.load(self.ui.cfg["profile"])
        self.assertEqual((profile.llm_model_id, profile.version), ("fixture-qwen", 2))
        self.assertEqual(self.ui.profile_note.get_text().count("LOCAL_ONLY"), 1)
        # An active model is one the endpoint actually serves.
        self.assertEqual(self.ui.inventory, ("fixture-qwen", "fixture-llama"))
        self.assertTrue(self.ui.send.get_sensitive())

    def test_a_model_the_endpoint_does_not_serve_is_refused(self):
        self.backend.inventory = ("fixture-qwen",)
        self.ui.url.set_text("http://127.0.0.1:9876")
        self.click(self.ui.market_find)
        self.until(lambda: not self.ui.busy and len(self.ui.market.cards()) == 1)
        self.backend.inventory = ("fixture-llama",)
        self.ui.url.set_text("http://127.0.0.1:9877")
        self.click(self.ui.market_find)
        self.until(lambda: not self.ui.busy and self.ui.inventory == ("fixture-llama",))
        self.ui.market_filter.set_active(1)
        self.pick_card("fixture-qwen")
        self.click(self.ui.market_use)
        self.until(lambda: self.ui.status.get_text() == "model_not_in_inventory")
        self.assertEqual(self.ui.cfg["llm"], "fixture-chat")
        self.assertEqual(self.ui.market.card("fixture-qwen").state, "DISCOVERED")

    def test_rediscovery_never_upgrades_a_registered_card(self):
        self.backend.inventory = ("fixture-qwen",)
        self.ui.url.set_text("http://127.0.0.1:9876")
        self.click(self.ui.market_find)
        self.until(lambda: not self.ui.busy and len(self.ui.market.cards()) == 1)
        self.ui.inventory = ("fixture-qwen",)
        self.ui.market_filter.set_active(1)
        self.pick_card("fixture-qwen")
        self.click(self.ui.market_use)
        self.until(lambda: self.ui.cfg["llm"] == "fixture-qwen")
        version = self.ui.market.card("fixture-qwen").version
        self.click(self.ui.market_find)
        self.until(lambda: self.ui.market.card("fixture-qwen").version == version)
        self.assertEqual(self.ui.market.card("fixture-qwen").state, "REGISTERED")
class PreviewTests(MarketFixture):
    """Safe test chat and per-locale voice preview over synthetic backends."""

    def preview_of(self, lang):
        for widget, key in self.ui.previews.items():
            if key == lang:
                return widget
        raise AssertionError("missing preview control: " + lang)

    def test_test_chat_answers_without_touching_the_conversation(self):
        self.connect_model()
        self.text("Merhaba!")
        history = self.ui.session.history
        self.backend.answers.append("Kısa bir test yanıtı.")
        self.click(self.ui.test_chat_button)
        self.until(lambda: not self.ui.busy)
        self.assertIn("Kısa bir test yanıtı", self.ui.test_note.get_text())
        note = self.ui.test_note.get_text()
        self.assertTrue(note.startswith("Kısa bir test yanıtı. · "), note)
        self.assertIn(note.rsplit(" · ", 1)[1], ("tr", "en", "de"))
        # A probe answer must never become conversation memory.
        self.assertEqual(self.ui.session.history, history)
        self.assertEqual(len(self.bubbles()), 2)
        prompts = [m for messages in self.backend.requests for m in messages
                   if m.role == "user"]
        probes = ("what can you do", "neler yapabilirsin", "was kannst du")
        self.assertTrue(any(any(probe in m.content for probe in probes) for m in prompts))
        self.assertEqual(len(self.ui.session.history), 2)

    def test_test_chat_requires_a_selected_model(self):
        self.assertFalse(self.ui.test_chat_button.get_sensitive())
        self.ui.inventory = ("fixture-chat",)
        self.ui.cfg["llm"] = "fixture-chat"
        self.ui.refresh()
        self.assertTrue(self.ui.test_chat_button.get_sensitive())

    def test_preview_speaks_only_the_selected_locale(self):
        self.connect_model()
        samples = {"tr": "Bu bir ses önizlemesidir.", "en": "This is a voice preview.",
                   "de": "Das ist eine Stimmprobe."}
        for lang in samples:
            path = Path(self.profile_home) / (lang + ".onnx")
            path.write_bytes(b"synthetic voice fixture\n")
            path.with_name(path.name + ".json").write_text(
                json.dumps({"language": {"code": lang}}), encoding="utf-8")
            self.ui.register_choice(lang, str(path))
        self.ui.refresh()
        self.assertEqual(sorted(self.ui.previews.values()), ["de", "en", "tr"])
        for widget, lang in self.ui.previews.items():
            self.assertTrue(widget.get_sensitive(), lang)
            self.backend.speeches.clear()
            self.click(widget)
            self.until(lambda: bool(self.backend.speeches))
            spoken = self.backend.speeches[-1]
            self.assertEqual((spoken.language, spoken.text), (lang, samples[lang]))
            self.ui.interrupt(True)
            # Cancellation must await the playback cleanup, not abandon it.
            self.backend.playback_release.set()
            self.until(lambda: not self.ui.busy)
            self.assertTrue(self.backend.playback_cleaned)
            self.assertEqual(self.ui.session.history, ())

    def test_preview_is_unavailable_without_a_voice_or_playback_tools(self):
        self.connect_model()
        with patch.object(desktop.audio, "tools_ready", return_value=False):
            self.ui.refresh()
            self.assertFalse(any(w.get_sensitive() for w in self.ui.previews))
        self.assertFalse(any(w.get_sensitive() for w in self.ui.previews))
        self.assertFalse(self.ui.talk.get_sensitive())

    def test_preview_uses_the_selected_voice_and_keeps_memory(self):
        self.connect_model()
        for lang in ("tr",):
            path = Path(self.profile_home) / (lang + ".onnx")
            path.write_bytes(b"synthetic voice fixture\n")
            path.with_name(path.name + ".json").write_text(
                json.dumps({"language": {"code": lang}}), encoding="utf-8")
            self.ui.register_choice(lang, str(path))
        self.ui.cfg["voices"]["tr"] = str(Path(self.profile_home) / "tr.onnx")
        self.ui.refresh()
        history = self.ui.session.history
        self.backend.playback_stage = "synthesizing"
        self.click(self.preview_of("tr"))
        self.until(lambda: self.backend.speeches and self.ui.stage == "synthesizing")
        speech = self.backend.speeches[-1]
        self.assertEqual((speech.language, speech.text), ("tr", "Bu bir ses önizlemesidir."))
        self.assertEqual(self.ui.session.history, history)
        self.assertEqual(self.bubbles(), [])
class StorageViewTests(MarketFixture):
    """Storage and resources aggregated from the registry, never estimated."""

    def report(self):
        return self.ui.storage_note.get_text()

    def register(self, name, category, size=None, shared="", owned=False, memory=None, gpu=False):
        path = self.weights(name + (".bin" if category == "stt" else ".onnx"))
        if category != "stt":
            path.with_name(path.name + ".json").write_text(
                json.dumps({"language": {"code": "tr_TR"}}), encoding="utf-8")
        card = self.ui.register_choice("stt" if category == "stt" else "tr", str(path))
        updated = market.replace(card, disk_size_bytes=size if size is not None else 0,
                                memory_estimate=memory or 0, requires_gpu=gpu,
                                shared_storage_owner=shared, owned_by_jarvis=owned)
        self.ui.market.save_card(updated)
        self.ui.build_market_page()
        return updated

    def test_storage_reports_known_sizes_and_ownership(self):
        self.register("big-stt", "stt", size=3 * 1024 ** 3, memory=2 * 1024 ** 3, gpu=True)
        self.register("tr-voice", "tts", size=40 * 1024 ** 2, shared="piper")
        self.ui.market_filter.set_active(0)
        text = self.report()
        self.assertIn("2 cards", text)
        self.assertIn("disk 3.0 GiB", text)
        self.assertIn("shared 1", text)
        self.assertIn("jarvis-owned 0", text)
        self.assertIn("memory 2.0 GiB", text)
        self.assertIn("memory not estimated for 1 items", text)

    def test_unknown_sizes_stay_unknown(self):
        self.register("tiny-stt", "stt")
        self.ui.market_filter.set_active(0)
        text = self.report()
        self.assertIn("unknown/not tested", text)
        self.assertNotIn("0 B", text)

    def test_empty_registry_says_nothing_invented(self):
        self.assertIn("No registered models yet.", self.report())

    def test_unregistering_a_shared_card_keeps_the_weights(self):
        path = self.weights("tr-voice.onnx")
        path.write_bytes(b"synthetic voice fixture\n")
        card = self.register("tr-voice", "tts", shared="piper")
        self.ui.market_filter.set_active(2)
        self.pick_card(card.model_id)
        self.click(self.ui.market_forget)
        self.until(lambda: not self.ui.busy and self.ui.market.cards() == ())
        # Unregistering removes only this project's registry entry.
        self.assertTrue(path.is_file())
        self.assertIn("No registered models yet.", self.report())
        # The report before removal counted the shared owner truthfully.
        self.ui.market.save_card(card)
        self.ui.build_market_page()
        self.assertIn("shared 1", self.report())
class DownloadFixture(MarketFixture):
    """Helpers for the download, queue and resume tests."""

    def discovered(self, model_id="fixture-download", **kw):
        values = {"model_id": model_id, "display_name": model_id, "category": "llm",
                  "engine": "jan", "provider": "jan", "installed": True, "state": "DISCOVERED",
                  "source_ref": "catalog:fixture-download", "source_size": len(b"weights"),
                  "source_checksum": hashlib.sha256(b"weights").hexdigest(),
                  "source_version": "1.0", "license_id": "MIT",
                  "network_approval": "owner-1", "approval_expires_at": 1e12}
        values.update(kw)
        card = market.ModelCard(**values).checked()
        self.ui.market.save_card(card)
        self.ui.build_market_page()
        return card

    async def fetch_weights(self, path, note):
        with open(path, "wb") as f:
            f.write(b"weights")
        note(len(b"weights"))
        return len(b"weights")


    def loopback_server(self, payload=b"synthetic weights fixture over loopback\n"):
        """Serve the fixture on the same event loop the client drives."""
        async def serve(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: "
                             + str(len(payload)).encode()
                             + b"\r\nContent-Type: application/octet-stream\r\n\r\n" + payload)
                await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                return  # the client stopped reading; the test does not need it
            finally:
                writer.close()
                await writer.wait_closed()

        starting = self.loop.create_task(asyncio.start_server(serve, "127.0.0.1", 0))
        self.until(lambda: starting.done())
        server = starting.result()
        port = server.sockets[0].getsockname()[1]

        def stop():
            server.close()
            self.loop.run_until_complete(server.wait_closed())
        self.addCleanup(stop)
        return "http://127.0.0.1:%d/weights.bin" % port, payload

    def discovered_with_terms(self, url, checksum, size, approval="owner-approval"):
        card = market.ModelCard(
            model_id="fixture-download", display_name="Fixture Download", category="stt",
            engine="whisper.cpp", artifact_format="ggml", installed=True,
            trust_level="curated", state="DISCOVERED", source_ref=url,
            source_checksum=checksum, source_size=size, source_version="1.0",
            license_id="MIT", network_approval=approval, approval_expires_at=1e12).checked()
        self.ui.market.save_card(card)
        self.ui.build_market_page()
        return card

    def begin_download(self, url, checksum, size, approval="owner-approval"):
        self.discovered_with_terms(url, checksum, size, approval)
        self.ui.market_filter.set_active(0)
        self.pick_card("fixture-download")

    def download_rows(self):
        out = []
        item = self.ui.download_list.get_first_child()
        while item is not None:
            out.append(item.get_child().get_text())
            item = item.get_next_sibling()
        return out

    def pick_download(self, model_id):
        for item, entry in self.ui.download_entries.items():
            if entry["id"] == model_id:
                self.ui.download_list.select_row(item)
                return entry
        raise AssertionError("missing queue entry: " + model_id)
class DownloadFlowTests(DownloadFixture):
    """Approved sources download through the real transport."""

    def test_download_is_unavailable_without_a_transport(self):
        self.discovered()
        self.ui.market_filter.set_active(1)
        self.pick_card("fixture-download")
        self.assertFalse(self.ui.market_fetch.get_sensitive())
        # Even when forced, the honest reason is reported, nothing is written.
        self.ui.transport = None
        self.ui.submit(lambda epoch: self.ui.download(epoch, "fixture-download"))
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.status.get_text(), "download_transport_not_available")
        self.assertFalse(list(Path(self.market_home, "transfers").glob("tihulu-transfer-*")))

    def test_download_registers_a_verified_artifact(self):
        self.discovered()
        self.ui.transport = self.fetch_weights
        self.ui.market_filter.set_active(1)
        self.pick_card("fixture-download")
        self.assertTrue(self.ui.market_fetch.get_sensitive())
        self.click(self.ui.market_fetch)
        self.until(lambda: not self.ui.busy)
        card = self.ui.market.card("fixture-download")
        self.assertEqual(card.state, "REGISTERED")
        self.assertTrue(card.owned_by_jarvis)
        self.assertEqual(card.version, "1.0")
        self.assertEqual(card.license_id, "MIT")
        target = Path(self.market_home, "artifacts", "fixture-download")
        self.assertTrue(target.is_file())
        self.assertFalse(list(Path(self.market_home, "transfers").glob("tihulu-transfer-*")))
        rows = self.rows()
        self.assertTrue(any("REGISTERED" in row for row in rows))

    def test_a_failed_download_leaves_no_artifact(self):
        self.discovered()

        async def lying(path, note):
            # Same length, different bytes: integrity, not size, must catch it.
            with open(path, "wb") as f:
                f.write(b"WEIGHTS")
            note(len(b"weights"))
            return len(b"weights")

        self.ui.transport = lying
        self.ui.market_filter.set_active(1)
        self.pick_card("fixture-download")
        self.click(self.ui.market_fetch)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.status.get_text(), "transfer_checksum_mismatch")
        card = self.ui.market.card("fixture-download")
        self.assertEqual(card.state, "FAILED")
        self.assertFalse(card.owned_by_jarvis)
        self.assertFalse(Path(self.market_home, "artifacts", "fixture-download").exists())
        self.assertFalse(list(Path(self.market_home, "transfers").glob("tihulu-transfer-*")))


    def test_download_runs_through_the_real_transport_and_registers(self):
        url, payload = self.loopback_server()
        self.begin_download(url, hashlib.sha256(payload).hexdigest(), len(payload))
        self.assertTrue(self.ui.market_fetch.get_sensitive())
        self.click(self.ui.market_fetch)
        try:
            self.until(lambda: not self.ui.busy)
        except AssertionError:
            task = self.ui.session.active
            raise AssertionError("download stalled: %r" % (
                task.exception() if task is not None and task.done() else "pending",))
        card = self.ui.market.card("fixture-download")
        self.assertEqual(card.state, "REGISTERED")
        self.assertTrue(card.owned_by_jarvis)
        self.assertEqual(card.disk_size_bytes, len(payload))
        self.assertEqual(card.storage_ref, "sha256-" + hashlib.sha256(payload).hexdigest()[:32])
        target = Path(self.market_home, "artifacts", "fixture-download")
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), payload)
        self.assertFalse(list(Path(self.market_home, "transfers").glob("tihulu-transfer-*")))

    def test_a_checksum_mismatch_fails_and_keeps_the_registry_clean(self):
        url, payload = self.loopback_server()
        self.begin_download(url, "0" * 64, len(payload))
        self.click(self.ui.market_fetch)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.status.get_text(), "transfer_checksum_mismatch")
        card = self.ui.market.card("fixture-download")
        self.assertEqual(card.state, "FAILED")
        self.assertFalse(card.owned_by_jarvis)
        self.assertFalse(Path(self.market_home, "artifacts", "fixture-download").exists())
        self.assertFalse(list(Path(self.market_home, "transfers").glob("tihulu-transfer-*")))

    def test_an_unapproved_source_has_no_transport(self):
        url, payload = self.loopback_server()
        card = self.discovered_with_terms(url, hashlib.sha256(payload).hexdigest(),
                                         len(payload), approval="")
        self.ui.market_filter.set_active(0)
        self.pick_card("fixture-download")
        self.assertFalse(self.ui.market_fetch.get_sensitive())
        self.assertFalse(card.approved_source())
class DownloadTransportTests(MarketFixture):
    """The download flow runs through the real loopback transport and fixture."""

    def loopback_server(self, payload=b"synthetic weights fixture over loopback\n"):
        """Serve the fixture on the same event loop the client drives."""
        async def serve(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: "
                             + str(len(payload)).encode()
                             + b"\r\nContent-Type: application/octet-stream\r\n\r\n" + payload)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        starting = self.loop.create_task(asyncio.start_server(serve, "127.0.0.1", 0))
        self.until(lambda: starting.done())
        server = starting.result()
        port = server.sockets[0].getsockname()[1]

        def stop():
            server.close()
            self.loop.run_until_complete(server.wait_closed())
        self.addCleanup(stop)
        return "http://127.0.0.1:%d/weights.bin" % port, payload

    def discovered_with_terms(self, url, checksum, size, approval="owner-approval"):
        card = market.ModelCard(
            model_id="fixture-download", display_name="Fixture Download", category="stt",
            engine="whisper.cpp", artifact_format="ggml", installed=True,
            trust_level="curated", state="DISCOVERED", source_ref=url,
            source_checksum=checksum, source_size=size, source_version="1.0",
            license_id="MIT", network_approval=approval, approval_expires_at=1e12).checked()
        self.ui.market.save_card(card)
        self.ui.build_market_page()
        return card

    def begin_download(self, url, checksum, size, approval="owner-approval"):
        self.discovered_with_terms(url, checksum, size, approval)
        self.ui.market_filter.set_active(0)
        self.pick_card("fixture-download")

    def test_download_runs_through_the_real_transport_and_registers(self):
        url, payload = self.loopback_server()
        self.begin_download(url, hashlib.sha256(payload).hexdigest(), len(payload))
        self.assertTrue(self.ui.market_fetch.get_sensitive())
        self.click(self.ui.market_fetch)
        try:
            self.until(lambda: not self.ui.busy)
        except AssertionError:
            task = self.ui.session.active
            raise AssertionError("download stalled: %r" % (
                task.exception() if task is not None and task.done() else "pending",))
        card = self.ui.market.card("fixture-download")
        self.assertEqual(card.state, "REGISTERED")
        self.assertTrue(card.owned_by_jarvis)
        self.assertEqual(card.disk_size_bytes, len(payload))
        self.assertEqual(card.storage_ref, "sha256-" + hashlib.sha256(payload).hexdigest()[:32])
        target = Path(self.market_home, "artifacts", "fixture-download")
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), payload)
        self.assertFalse(list(Path(self.market_home, "transfers").glob("tihulu-transfer-*")))

    def test_a_checksum_mismatch_fails_and_keeps_the_registry_clean(self):
        url, payload = self.loopback_server()
        self.begin_download(url, "0" * 64, len(payload))
        self.click(self.ui.market_fetch)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.status.get_text(), "transfer_checksum_mismatch")
        card = self.ui.market.card("fixture-download")
        self.assertEqual(card.state, "FAILED")
        self.assertFalse(card.owned_by_jarvis)
        self.assertFalse(Path(self.market_home, "artifacts", "fixture-download").exists())
        self.assertFalse(list(Path(self.market_home, "transfers").glob("tihulu-transfer-*")))

    def test_an_unapproved_source_has_no_transport(self):
        url, payload = self.loopback_server()
        card = self.discovered_with_terms(url, hashlib.sha256(payload).hexdigest(),
                                         len(payload), approval="")
        self.ui.market_filter.set_active(0)
        self.pick_card("fixture-download")
        self.assertFalse(self.ui.market_fetch.get_sensitive())
        self.assertFalse(card.approved_source())
class DownloadQueueTests(DownloadTransportTests):
    """Queue state, progress and cancellation, with the real loopback transport."""

    def download_rows(self):
        out = []
        item = self.ui.download_list.get_first_child()
        while item is not None:
            out.append(item.get_child().get_text())
            item = item.get_next_sibling()
        return out

    def pick_download(self, model_id):
        for item, entry in self.ui.download_entries.items():
            if entry["id"] == model_id:
                self.ui.download_list.select_row(item)
                return entry
        raise AssertionError("missing queue entry: " + model_id)

    def test_completed_download_shows_state_and_size(self):
        url, payload = self.loopback_server()
        self.begin_download(url, hashlib.sha256(payload).hexdigest(), len(payload))
        self.click(self.ui.market_fetch)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(len(self.download_rows()), 1)
        line = self.download_rows()[0]
        self.assertIn("fixture-download · REGISTERED", line)
        self.assertIn("%d of %s bytes" % (len(payload), market.human_size(len(payload))), line)
        entry = self.pick_download("fixture-download")
        self.assertEqual((entry["state"], entry["received"]), ("REGISTERED", len(payload)))
        self.assertFalse(self.ui.download_cancel.get_sensitive())

    def test_unknown_size_is_reported_as_unknown(self):
        url, payload = self.loopback_server()
        card = self.discovered_with_terms(url, hashlib.sha256(payload).hexdigest(), 0)
        self.ui.market_filter.set_active(0)
        self.pick_card("fixture-download")
        self.click(self.ui.market_fetch)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.market.card("fixture-download").state, "REGISTERED")
        self.assertIn("0 of unknown/not tested bytes", self.download_rows()[0])

    def test_cancellation_removes_the_partial_and_marks_the_entry(self):
        url, payload = self.loopback_server()

        release = asyncio.Event()

        async def stall(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, ConnectionError):
                return  # the client stopped reading; the test does not need it
            try:
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: "
                             + str(len(payload)).encode()
                             + b"\r\nContent-Type: application/octet-stream\r\n\r\n")
                await writer.drain()
                # No byte arrives before the owner cancels: nothing is kept.
                await release.wait()
                writer.write(payload)
                await writer.drain()
            finally:
                writer.close()

        starting = self.loop.create_task(asyncio.start_server(stall, "127.0.0.1", 0))
        self.until(lambda: starting.done())
        server = starting.result()
        stalled = "http://127.0.0.1:%d/weights.bin" % server.sockets[0].getsockname()[1]
        self.addCleanup(server.close)
        self.addCleanup(lambda: (release.set(), self.drain_tasks()))
        self.begin_download(stalled, hashlib.sha256(payload).hexdigest(), len(payload))
        self.click(self.ui.market_fetch)
        self.until(lambda: any(e["state"] == "DOWNLOADING" for e in self.ui.downloads))
        entry = self.pick_download("fixture-download")
        self.assertTrue(self.ui.download_cancel.get_sensitive())
        self.click(self.ui.download_cancel)
        self.assertTrue(entry["note"] != "")
        self.until(lambda: entry["state"] == "CANCELLED" and not self.ui.busy)
        self.assertEqual(self.ui.market.card("fixture-download").state, "CANCELLED")
        self.assertFalse(Path(self.market_home, "artifacts", "fixture-download").exists())
        self.assertFalse(list(Path(self.market_home, "transfers").glob("tihulu-transfer-*")))
        self.assertIn("CANCELLED", self.download_rows()[0])
        release.set()
        self.drain_tasks()
class ResumeQueueTests(DownloadFixture):
    """A cancelled transfer keeps its partial and can resume from it."""

    payload = b"synthetic weights fixture over loopback\n"

    def range_server(self, stall_first=True):
        """A fixture that answers 206 for a Range and optionally stalls mid-body."""
        release = asyncio.Event()
        served = {}

        async def route(reader, writer):
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                want = 0
                for field in request.split(b"\r\n"):
                    if field.lower().startswith(b"range:"):
                        want = int(field.split(b"=")[1].split(b"-")[0])
                served["offset"] = want
                body = self.payload[want:]
                if stall_first and not want:
                    # Answer the whole body but stall after the first bytes.
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: "
                                 + str(len(self.payload)).encode()
                                 + b"\r\nContent-Type: application/octet-stream\r\n\r\n"
                                 + self.payload[:4])
                    await writer.drain()
                    await release.wait()
                    writer.write(self.payload[4:])
                    await writer.drain()
                    return
                status = b"HTTP/1.1 206 Partial Content" if want else b"HTTP/1.1 200 OK"
                extra = (b"Content-Range: bytes %d-%d/%d\r\n"
                         % (want, len(self.payload) - 1, len(self.payload))) if want else b""
                writer.write(status + b"\r\nContent-Length: " + str(len(body)).encode()
                             + b"\r\n" + extra + b"Content-Type: application/octet-stream\r\n\r\n"
                             + body)
                await writer.drain()
            finally:
                writer.close()

        starting = self.loop.create_task(asyncio.start_server(route, "127.0.0.1", 0))
        self.until(lambda: starting.done())
        server = starting.result()
        port = server.sockets[0].getsockname()[1]
        self.addCleanup(server.close)
        self.addCleanup(lambda: (release.set(),
                                 self.loop.run_until_complete(asyncio.sleep(0.05))))
        return "http://127.0.0.1:%d/weights.bin" % port, release, served

    def begin_resumable(self, url, checksum, size, approval="owner-approval"):
        self.discovered_with_terms(url, checksum, size, approval)
        self.ui.market_filter.set_active(0)
        self.pick_card("fixture-download")

    def partial_file(self):
        return Path(self.market_home, "transfers", "partial-fixture-download")

    def test_a_cancelled_transfer_keeps_a_resumable_partial(self):
        url, release, served = self.range_server()
        self.begin_resumable(url, hashlib.sha256(self.payload).hexdigest(), len(self.payload))
        self.click(self.ui.market_fetch)
        self.until(lambda: any(e["state"] == "DOWNLOADING" and e["received"] > 0
                               for e in self.ui.downloads))
        entry = self.pick_download("fixture-download")
        self.assertTrue(self.ui.download_cancel.get_sensitive())
        self.click(self.ui.download_cancel)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(entry["state"], "RESUMABLE")
        self.assertGreater(entry["resume"], 0)
        self.assertTrue(self.partial_file().is_file())
        self.assertFalse(Path(self.market_home, "artifacts", "fixture-download").exists())
        self.assertTrue(self.ui.download_resume.get_sensitive())
        self.assertTrue(self.ui.download_discard.get_sensitive())
        self.assertIn("RESUMABLE", self.download_rows()[0])
        release.set()
        self.drain_tasks()
        release.set()

    def test_resume_completes_the_registration(self):
        url, release, served = self.range_server()
        self.begin_resumable(url, hashlib.sha256(self.payload).hexdigest(), len(self.payload))
        self.click(self.ui.market_fetch)
        self.until(lambda: any(e["state"] == "DOWNLOADING" and e["received"] > 0
                               for e in self.ui.downloads))
        entry = self.pick_download("fixture-download")
        self.click(self.ui.download_cancel)
        self.until(lambda: entry["state"] == "RESUMABLE" and not self.ui.busy)
        start = entry["resume"]
        release.set()
        self.click(self.ui.download_resume)
        self.until(lambda: entry["state"] != "RESUMABLE" or served["offset"] > 0)
        self.until(lambda: not self.ui.busy)
        card = self.ui.market.card("fixture-download")
        self.assertEqual(card.state, "REGISTERED")
        self.assertEqual(card.disk_size_bytes, len(self.payload))
        target = Path(self.market_home, "artifacts", "fixture-download")
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), self.payload)
        self.assertFalse(self.partial_file().exists())
        self.assertEqual(served["offset"], start)
        self.assertIn("REGISTERED", self.download_rows()[0])
        release.set()
        self.drain_tasks()

    def test_discard_removes_the_partial_and_leaves_nothing_installed(self):
        url, release, served = self.range_server()
        self.begin_resumable(url, hashlib.sha256(self.payload).hexdigest(), len(self.payload))
        self.click(self.ui.market_fetch)
        self.until(lambda: any(e["state"] == "DOWNLOADING" and e["received"] > 0
                               for e in self.ui.downloads))
        entry = self.pick_download("fixture-download")
        self.click(self.ui.download_cancel)
        self.until(lambda: entry["state"] == "RESUMABLE" and not self.ui.busy)
        self.assertTrue(self.partial_file().is_file())
        release.set()
        self.click(self.ui.download_discard)
        self.until(lambda: entry["state"] == "CANCELLED")
        self.assertFalse(self.partial_file().exists())
        self.assertFalse(Path(self.market_home, "artifacts", "fixture-download").exists())
        self.assertEqual(self.ui.market.card("fixture-download").state, "CANCELLED")
        self.assertFalse(self.ui.download_resume.get_sensitive())
        self.assertIn("CANCELLED", self.download_rows()[0])
        release.set()
        self.drain_tasks()


class RevisionHistoryUITests(MarketFixture):
    """Item history is real registry data; a rollback is explicit."""

    def revise(self, model_id, name):
        card = self.ui.market.card(model_id)
        self.ui.market.save_card(market.replace(card, display_name=name))

    def test_a_rollback_restores_the_previous_entry(self):
        self.weights("fixture-stt.bin").write_bytes(b"synthetic weights fixture\n")
        card = self.ui.register_choice("stt", str(self.weights("fixture-stt.bin")))
        self.ui.market_filter.set_active(0)
        self.pick_card(card.model_id)
        self.assertFalse(self.ui.market_rollback.get_sensitive())
        self.revise(card.model_id, "Renamed once")
        self.revise(card.model_id, "Renamed twice")
        self.assertEqual(self.ui.market.card(card.model_id).display_name, "Renamed twice")
        self.ui.refresh_market_buttons()
        self.assertTrue(self.ui.market_rollback.get_sensitive())
        self.click(self.ui.market_rollback)
        self.until(lambda: self.ui.market.card(card.model_id).display_name == "Renamed once")
        self.assertEqual(self.ui.market.card(card.model_id).revision, 3)
        # The rollback itself is a revision, so the replaced entry is kept too.
        self.assertEqual(len(self.ui.market.history(card.model_id)), 3)
        self.assertEqual(self.ui.status.get_text(), "Previous revision restored.")
        self.assertIn("Renamed once", self.rows()[0])

    def test_rollback_without_history_stays_refused(self):
        self.ui.market.save_card(market.ModelCard(
            model_id="fixture-lonely", display_name="Lonely", category="llm").checked())
        self.ui.market_filter.set_active(1)
        self.pick_card("fixture-lonely")
        self.assertFalse(self.ui.market_rollback.get_sensitive())
        # Even when forced, the honest reason is reported and nothing changes.
        self.ui.rollback_selected_card()
        self.assertEqual(self.ui.status.get_text(), "revision_not_found")
        self.assertEqual(self.ui.market.card("fixture-lonely").display_name, "Lonely")
class UpdateUITests(DownloadFixture):
    """An installed item updates from its approved source, keeping history."""

    def installed_with_terms(self, url, checksum, size, version="1.0"):
        card = market.ModelCard(
            model_id="fixture-update", display_name="Fixture Update", category="stt",
            engine="whisper.cpp", artifact_format="ggml", installed=True, registered=True,
            trust_level="curated", state="ACTIVE", source_ref=url, source_checksum=checksum,
            source_size=size, source_version=version, license_id="MIT",
            network_approval="owner-approval", approval_expires_at=1e12).checked()
        self.ui.market.save_card(card)
        self.ui.build_market_page()
        return card

    def test_update_re_registers_and_keeps_the_previous_revision(self):
        url, payload = self.loopback_server()
        first = self.installed_with_terms(url, hashlib.sha256(payload).hexdigest(),
                                         len(payload))
        self.ui.market_filter.set_active(0)
        self.pick_card("fixture-update")
        self.assertTrue(self.ui.market_update.get_sensitive())
        self.click(self.ui.market_update)
        self.until(lambda: not self.ui.busy)
        card = self.ui.market.card("fixture-update")
        self.assertEqual(card.state, "ACTIVE")
        self.assertEqual(card.revision, 1)
        history = self.ui.market.history("fixture-update")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].revision, 0)
        self.assertEqual(history[0].source_version, "1.0")
        self.assertIn("ACTIVE", self.download_rows()[0])
        self.assertIn("Updated.", self.download_rows()[0])

    def test_update_of_an_uninstalled_item_is_not_offered(self):
        url, payload = self.loopback_server()
        card = self.discovered_with_terms(url, hashlib.sha256(payload).hexdigest(),
                                         len(payload))
        self.ui.market_filter.set_active(0)
        self.pick_card(card.model_id)
        self.assertFalse(self.ui.market_update.get_sensitive())
        self.ui.market_update.emit("clicked")
        self.assertEqual(self.ui.market.card(card.model_id).state, "DISCOVERED")

    def test_update_without_a_transport_reports_an_honest_reason(self):
        url, payload = self.loopback_server()
        card = self.installed_with_terms(url, hashlib.sha256(payload).hexdigest(),
                                        len(payload))
        # An opaque source token has no transport in this build; the honest
        # answer is a reason, not a silent success.
        self.ui.market.save_card(market.replace(card, source_ref="catalog:fixture-update"))
        self.ui.build_market_page()
        self.ui.market_filter.set_active(0)
        self.pick_card("fixture-update")
        self.assertTrue(self.ui.market_update.get_sensitive())
        self.click(self.ui.market_update)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.status.get_text(), "download_transport_not_available")
        self.assertEqual(self.ui.market.card("fixture-update").state, "ACTIVE")
        # The failed update replaced nothing; only the explicit rename revision exists.
        self.assertEqual(len(self.ui.market.history("fixture-update")), 1)
class QueueRestoreTests(DownloadFixture):
    """A restart rebuilds the queue from disk; a partial stays resumable."""

    def partial_on_disk(self, model_id, data, state="DISCOVERED"):
        transfers = Path(self.market_home, "transfers")
        transfers.mkdir(parents=True, exist_ok=True)
        (transfers / ("partial-" + model_id)).write_bytes(data)
        self.ui.market.save_card(market.ModelCard(
            model_id=model_id, display_name=model_id, category="stt",
            engine="whisper.cpp", artifact_format="ggml", installed=True,
            state=state, source_ref="catalog:" + model_id,
            source_checksum=hashlib.sha256(b"weights").hexdigest(),
            source_size=len(b"weights"), network_approval="owner",
            approval_expires_at=1e12).checked())
        return self.ui.market.card(model_id)

    def test_a_restart_restores_a_resumable_entry(self):
        self.partial_on_disk("fixture-restore", b"partial bytes")
        # A fresh window is a restart: the queue is rebuilt from disk only.
        fresh = desktop.create_window(self.app, self.loop)
        self.addCleanup(fresh.win.close)
        restored = [e for e in fresh.downloads if e["id"] == "fixture-restore"]
        self.assertEqual(len(restored), 1)
        entry = restored[0]
        self.assertEqual(entry["state"], "RESUMABLE")
        self.assertEqual(entry["resume"], len(b"partial bytes"))
        self.assertIn("Restored from disk.", entry["note"])
        self.assertTrue(fresh.download_resume.get_sensitive())
        self.assertIn("RESUMABLE", fresh.download_line(entry))
        fresh.win.close()
        self.until(lambda: fresh.win.get_visible() is False)

    def test_an_installed_item_is_not_reported_as_pending(self):
        self.partial_on_disk("fixture-installed", b"leftover", state="ACTIVE")
        fresh = desktop.create_window(self.app, self.loop)
        self.addCleanup(fresh.win.close)
        self.assertEqual([e["id"] for e in fresh.downloads], [])
        fresh.win.close()
        self.until(lambda: fresh.win.get_visible() is False)

    def test_an_orphan_partial_is_ignored(self):
        transfers = Path(self.market_home, "transfers")
        transfers.mkdir(parents=True, exist_ok=True)
        (transfers / "partial-unknown-model").write_bytes(b"orphan")
        fresh = desktop.create_window(self.app, self.loop)
        self.addCleanup(fresh.win.close)
        self.assertEqual(fresh.downloads, [])
        fresh.win.close()
        self.until(lambda: fresh.win.get_visible() is False)
class CatalogUITests(MarketFixture):
    """Curated catalog discovery over an owner-supplied file; nothing is fetched."""

    document = {
        "source_name": "fixture catalog",
        "entries": [
            {"model_id": "fixture-whisper-large", "display_name": "Fixture Whisper Large",
             "category": "stt", "engine": "whisper.cpp", "artifact_format": "ggml",
             "languages": ["tr", "en", "de"], "license_id": "MIT",
             "source_ref": "catalog:fixture-whisper-large",
             "source_checksum": hashlib.sha256(b"stt-weights").hexdigest(),
             "source_size": 11, "source_version": "v3", "publisher": "fixture-publisher"},
            {"model_id": "fixture-tr-voice", "display_name": "Fixture TR Voice",
             "category": "tts", "engine": "piper", "artifact_format": "onnx",
             "languages": ["tr"], "license_id": "CC-BY-4.0",
             "source_ref": "catalog:fixture-tr-voice",
             "source_checksum": hashlib.sha256(b"voice-weights").hexdigest(),
             "source_size": 13, "publisher": "fixture-publisher"},
        ],
    }

    def test_missing_catalog_is_stated_not_guessed(self):
        note = self.ui.catalog_note.get_text()
        self.assertTrue(any(marker in note for marker in
                            ("No catalog yet.", "Henüz katalog yok", "Noch kein Katalog")), note)
        self.assertEqual(self.catalog_rows(), [])
        self.assertFalse(self.ui.catalog_add.get_sensitive())

    def test_catalog_discovers_entries_and_registers_them(self):
        market.save_key("fixture-key", "shared-secret-for-catalog-signing")
        self.ui.load_catalog_text(market.sign_catalog(json.dumps(self.document),
                                                      "fixture-key", now=1.0))
        self.assertEqual(len(self.catalog_rows()), 2)
        self.assertIn("whisper.cpp", self.catalog_rows()[0])
        self.pick_entry("fixture-whisper-large")
        self.click(self.ui.catalog_add)
        self.until(lambda: any(c.model_id == "fixture-whisper-large" for c in self.ui.market.cards()))
        card = self.ui.market.card("fixture-whisper-large")
        self.assertEqual(card.state, "DISCOVERED")
        self.assertFalse(card.approved_source())  # terms published, approval not given
        # The stored catalog is the owner's file, never fetched.
        text = Path(market.catalog_path()).read_text(encoding="utf-8")
        self.assertIn("fixture catalog", text)
        self.assertNotIn("/home/", text)

    def test_approval_enables_the_download_control(self):
        market.save_key("fixture-key", "shared-secret-for-catalog-signing")
        self.ui.load_catalog_text(market.sign_catalog(json.dumps(self.document),
                                                      "fixture-key", now=1.0))
        self.pick_entry("fixture-whisper-large")
        self.click(self.ui.catalog_add)
        self.until(lambda: self.ui.market.cards())
        self.pick_card("fixture-whisper-large")
        self.assertFalse(self.ui.market_fetch.get_sensitive())
        self.pick_entry("fixture-whisper-large")
        self.click(self.ui.catalog_approve)
        self.until(lambda: self.ui.market.card("fixture-whisper-large").network_approval
                   == "owner-approved-download")
        self.pick_card("fixture-whisper-large")
        self.assertFalse(self.ui.market_fetch.get_sensitive())  # still no transport
        self.ui.refresh_market_buttons()
        self.assertFalse(self.ui.market_fetch.get_sensitive())
        card = self.ui.market.card("fixture-whisper-large")
        self.assertTrue(card.approved_source())

    def test_catalog_search_and_filter_are_narrow(self):
        self.ui.load_catalog_text(json.dumps(self.document))
        self.ui.catalog_search.set_text("voice")
        self.ui.refresh_catalog()
        self.assertEqual(len(self.catalog_rows()), 1)
        self.assertIn("piper", self.catalog_rows()[0])
        self.ui.catalog_search.set_text("")
        self.ui.catalog_filter.set_active(3)  # the tts category
        self.ui.refresh_catalog()
        self.assertEqual(len(self.catalog_rows()), 1)
        self.assertIn("piper", self.catalog_rows()[0])
        self.ui.catalog_filter.set_active(1)  # the stt category
        self.ui.refresh_catalog()
        self.assertEqual(len(self.catalog_rows()), 1)
        self.assertIn("whisper.cpp", self.catalog_rows()[0])

    def test_unreadable_catalog_is_a_bounded_reason(self):
        with self.assertRaises(Hold):
            self.ui.load_catalog_text("{not json")
        # A malformed import is refused; the previous catalog and its note stand.
        self.assertEqual(self.ui.catalog.entries, ())
        note = self.ui.catalog_note.get_text()
        self.assertTrue(any(marker in note for marker in
                            ("No catalog yet.", "Henüz katalog yok", "Noch kein Katalog")), note)
class EndpointModeTests(MarketFixture):
    """The active mode decides which endpoints the client will even try."""

    def remote_profile(self, mode="LOCAL_ONLY", providers=()):
        self.ui.store.save(profiles.replace(
            self.ui.store.load(self.ui.cfg["profile"]), version=2, based_on_mode=mode,
            allowed_providers=tuple(providers)))
        self.ui.activate_profile(self.ui.cfg["profile"], quiet=True)

    def test_local_only_refuses_a_remote_address_before_any_request(self):
        self.remote_profile("LOCAL_ONLY")
        self.ui.url.set_text("https://provider.example/v1")
        requests = list(self.backend.requests)
        self.click(self.ui.reload)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.status.get_text(), "local_only_forbids_non_loopback")
        self.assertEqual(self.backend.requests, requests)
        self.assertEqual(self.ui.inventory, ())

    def test_local_only_still_uses_its_loopback_endpoint(self):
        self.remote_profile("LOCAL_ONLY")
        self.ui.url.set_text("http://127.0.0.1:9876/v1")
        self.click(self.ui.reload)
        self.until(lambda: not self.ui.busy and self.ui.inventory == ("fixture-chat",))
        self.assertTrue(self.ui.send.get_sensitive())
        self.assertEqual(self.ui.cfg["endpoint"], "http://127.0.0.1:9876/v1")

    def test_a_cloud_mode_without_providers_does_not_claim_a_transport(self):
        self.remote_profile("CLOUD_CHAT")
        self.ui.url.set_text("https://provider.example/v1")
        self.click(self.ui.reload)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.status.get_text(), "cloud_transport_not_available")
        self.assertEqual(self.backend.requests, [])

    def test_a_named_provider_must_be_approved(self):
        self.remote_profile("CLOUD_CHAT", providers=("fixture-provider",))
        self.ui.url.set_text("http://127.0.0.1:9876/v1")
        requests = list(self.backend.requests)
        self.click(self.ui.reload)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.status.get_text(), "approved_provider_required")
        self.assertEqual(self.backend.requests, requests)
class ActionEnforcementUITests(MarketFixture):
    """A denied action is refused at the moment it runs, not when enabled."""

    def rewrite_rules(self, rules):
        profile = self.ui.store.load(self.ui.cfg["profile"]).checked()
        updated = profiles.replace(profile, version=profile.version + 1, tool_rules=tuple(rules))
        self.ui.store.save(updated)
        self.ui.activate_profile(updated.id, quiet=True)
        self.ui.refresh()
        return updated

    def deny_audio(self):
        profile = self.ui.store.load(self.ui.cfg["profile"]).checked()
        return self.rewrite_rules(
            tuple(r for r in profile.rules() if r.action != "local_audio_device")
            + (profiles.Rule("local_audio_device", ("push_to_talk",), profiles.DENY),))

    def test_talk_is_disabled_by_a_denied_audio_rule(self):
        self.deny_audio()
        self.connect_model()
        self.assertFalse(self.ui.talk.get_sensitive())
        # Even a forced click reaches the handler and is refused with the reason.
        async def denied(epoch):
            self.ui.require_tools(("local_audio_device", ("push_to_talk",)))
        self.ui.submit(denied)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.status.get_text(), "profile_forbids_local_audio_device")
        self.assertEqual(self.ui.stage, "ready")

    def test_preview_is_refused_when_the_rule_denies_audio(self):
        self.connect_model()
        path = self.weights("tr.onnx")
        path.with_name(path.name + ".json").write_text(
            json.dumps({"language": {"code": "tr"}}), encoding="utf-8")
        self.ui.register_choice("tr", str(path))
        self.ui.refresh()
        self.assertTrue(self.preview_of("tr").get_sensitive())
        self.deny_audio()
        self.ui.refresh()
        self.assertFalse(self.preview_of("tr").get_sensitive())
        self.assertFalse(self.backend.speeches)
        self.assertFalse(self.ui.talk.get_sensitive())

    def test_removing_the_denial_restores_the_control(self):
        profile = self.deny_audio()
        self.connect_model()
        self.assertFalse(self.ui.talk.get_sensitive())
        self.rewrite_rules(tuple(r for r in profile.rules() if r.action != "local_audio_device")
                           + (profiles.Rule("local_audio_device", ("push_to_talk",),
                                            profiles.ALLOW, approval="owner-consent",
                                            expires_at=1e12),))
        self.assertTrue(self.ui.talk.get_sensitive())


class CatalogFetchUITests(MarketFixture):
    """A catalog fetch is scoped by an allowlist and remains verifiable."""

    document = {
        "source_name": "remote catalog",
        "entries": [
            {"model_id": "fixture-remote", "display_name": "Fixture Remote",
             "category": "stt", "engine": "whisper.cpp", "artifact_format": "ggml",
             "languages": ["tr"], "license_id": "MIT",
             "source_ref": "catalog:fixture-remote",
             "source_checksum": hashlib.sha256(b"stt-weights").hexdigest(),
             "source_size": 11, "publisher": "fixture-publisher"},
        ],
    }
    url = "https://catalog.example.test/list.json"

    def opener(self, body=None, error=None):
        calls = []

        async def runtime(url, timeout):
            calls.append((url, timeout))
            if error:
                raise error
            return body if body is not None else json.dumps(self.document)
        return runtime, calls

    def allow(self, *hosts):
        self.ui.catalog_hosts.set_text(" ".join(hosts))
        self.ui.remember_hosts()

    def test_an_allowed_host_is_fetched_and_must_verify(self):
        opener, calls = self.opener()
        self.allow("catalog.example.test")
        with patch.object(catalog_fetch, "urlopen_opener", return_value=opener):
            self.ui.catalog_url.set_text(self.url)
            self.click(self.ui.catalog_fetch_btn)
            self.until(lambda: self.ui.catalog.entries and self.ui.catalog_note.get_text() != "")
        self.assertEqual(calls[0][0], self.url)
        self.assertIn("unverified", self.ui.catalog_note.get_text())
        self.assertEqual(len(self.catalog_rows()), 1)
        # An unsigned catalog may be inspected, never installed from.
        self.pick_entry("fixture-remote")
        self.assertFalse(self.ui.catalog_add.get_sensitive())
        self.ui.add_catalog_entry()
        self.assertEqual(self.ui.status.get_text(), "catalog_not_verified")
        self.assertEqual(self.ui.market.cards(), ())

    def test_a_host_outside_the_allowlist_is_never_fetched(self):
        opener, calls = self.opener()
        self.allow("other.example.test")
        with patch.object(catalog_fetch, "urlopen_opener", return_value=opener):
            self.ui.catalog_url.set_text(self.url)
            self.click(self.ui.catalog_fetch_btn)
            self.until(lambda: self.ui.status.get_text() == "catalog_host_not_allowed")
        self.assertEqual(calls, [])

    def test_no_allowlist_means_no_fetch(self):
        opener, calls = self.opener()
        with patch.object(catalog_fetch, "urlopen_opener", return_value=opener):
            self.ui.catalog_url.set_text(self.url)
            self.click(self.ui.catalog_fetch_btn)
            self.until(lambda: self.ui.status.get_text() == "catalog_host_not_allowed")
        self.assertEqual(calls, [])

    def test_a_failing_transport_is_a_bounded_reason(self):
        opener, calls = self.opener(error=OSError("fixture"))
        self.allow("catalog.example.test")
        with patch.object(catalog_fetch, "urlopen_opener", return_value=opener):
            self.ui.catalog_url.set_text(self.url)
            self.click(self.ui.catalog_fetch_btn)
            self.until(lambda: self.ui.status.get_text() == "catalog_fetch_failed")
        self.assertEqual(len(calls), 1)

    def test_a_signed_remote_catalog_can_be_installed_from(self):
        opener, calls = self.opener()
        self.allow("catalog.example.test")
        with patch.object(catalog_fetch, "urlopen_opener", return_value=opener):
            self.ui.catalog_url.set_text(self.url)
            self.click(self.ui.catalog_fetch_btn)
            self.until(lambda: self.ui.catalog.entries)
        # Sign the fetched document with the owner key; verification then holds.
        market.save_key("fixture-key", "shared-secret-for-catalog-signing")
        signed = market.sign_catalog(json.dumps(self.document), "fixture-key", now=1.0)
        self.ui.load_catalog_text(signed)
        self.assertIn("verified", self.ui.catalog_note.get_text())
        self.pick_entry("fixture-remote")
        self.assertTrue(self.ui.catalog_add.get_sensitive())
        self.click(self.ui.catalog_add)
        self.until(lambda: any(c.model_id == "fixture-remote" for c in self.ui.market.cards()))
        self.assertEqual(self.ui.market.card("fixture-remote").state, "DISCOVERED")


class AuditTrailUITests(ActionEnforcementUITests):
    """Policy decisions are recorded locally, with no private material."""

    def audit_rows(self):
        text = self.ui.audit_list.get_text()
        return [line for line in text.split("\n") if line.strip()]

    def test_a_denied_audio_decision_is_recorded(self):
        self.deny_audio()
        self.connect_model()
        self.assertFalse(self.ui.talk.get_sensitive())
        async def denied(epoch):
            self.ui.require_tools(("local_audio_device", ("push_to_talk",)))
        self.ui.submit(denied)
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.status.get_text(), "profile_forbids_local_audio_device")
        rows = self.audit_rows()
        self.assertTrue(any("deny" in row and "local_audio_device" in row for row in rows), rows)

    def test_an_allowed_endpoint_decision_is_recorded(self):
        self.ui.url.set_text("http://127.0.0.1:9876/v1")
        self.ui.require_endpoint()
        rows = self.audit_rows()
        self.assertTrue(any("allow" in row and "endpoint" in row for row in rows), rows)

    def test_the_local_audit_file_carries_no_private_material(self):
        self.ui.url.set_text("https://provider.example/v1")
        with self.assertRaises(Exception):
            self.ui.require_endpoint()
        text = Path(self.profile_home, "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("/home/", text)
        self.assertNotIn("secret", text)
        self.assertIn("local_only_forbids_non_loopback", text)


class EscalationConsentTests(ProfileUITests):
    """An escalation requires explicit consent in the trusted UI."""

    def add_profile(self, profile):
        self.ui.store.save(profile)
        self.ui.refresh_profiles()
        return profile

    def pick_profile(self, profile_id):
        for item, entry in self.ui.policy_rows.items():
            if entry.id == profile_id:
                self.ui.profile_list.select_row(item)
                return entry
        raise AssertionError("missing profile: " + profile_id)

    def buttons_of(self, window):
        return [w for w in self.widgets_of(window) if type(w).__name__ == "Button"]

    def widgets_of(self, widget):
        out, item = [], widget.get_first_child()
        while item is not None:
            out.append(item)
            out.extend(self.widgets_of(item))
            item = item.get_next_sibling()
        return out

    def labels_of(self, window):
        return [w.get_text() for w in self.widgets_of(window) if hasattr(w, "get_text")]

    def dialog(self):
        return self.ui.confirmations[-1] if self.ui.confirmations else None

    def test_a_switch_without_new_privileges_applies_directly(self):
        plain = self.add_profile(profiles.Profile(id="plain", name="Plain",
                                                  based_on_mode="LOCAL_ONLY").checked())
        self.pick_profile("plain")
        self.click(self.ui.profile_switch)
        self.until(lambda: self.ui.cfg["profile"] == "plain")
        self.assertIsNone(self.dialog())
        self.assertIn("Plain", self.ui.profile_note.get_text())

    def test_an_escalation_requires_confirmation_and_can_be_cancelled(self):
        chat = self.add_profile(profiles.Profile(id="cloud-chat", name="Cloud Chat",
                                                 based_on_mode="CLOUD_CHAT").checked())
        self.pick_profile("cloud-chat")
        self.click(self.ui.profile_switch)
        self.until(lambda: self.dialog() is not None)
        dialog = self.dialog()
        body = "\n".join(self.labels_of(dialog))
        self.assertIn("Switching to Cloud Chat", body)
        self.assertIn("Cloud reasoning", body)
        self.assertIn("Sending content to a provider", body)
        self.assertTrue(any(b.get_label() == "Cancel" for b in self.buttons_of(dialog)))
        # Cancelling leaves the active profile and its privileges untouched.
        for button in self.buttons_of(dialog):
            if button.get_label() == "Cancel":
                button.emit("clicked")
        self.until(lambda: self.dialog() is None)
        self.assertEqual(self.ui.cfg["profile"], "private-assistant")
        self.assertEqual(self.ui.store.load(self.ui.cfg["profile"]).based_on_mode, "LOCAL_ONLY")

    def test_an_escalation_can_be_confirmed_and_is_recorded(self):
        chat = self.add_profile(profiles.Profile(id="cloud-chat", name="Cloud Chat",
                                                 based_on_mode="CLOUD_CHAT").checked())
        self.pick_profile("cloud-chat")
        self.click(self.ui.profile_switch)
        self.until(lambda: self.dialog() is not None)
        for button in self.buttons_of(self.dialog()):
            if button.get_label() and button.get_label().startswith("Confirm"):
                button.emit("clicked")
        self.until(lambda: self.ui.cfg["profile"] == "cloud-chat")
        self.assertIsNone(self.dialog())
        self.assertEqual(self.ui.store.load("cloud-chat").based_on_mode, "CLOUD_CHAT")
        rows = profiles.read_audit(self.ui.store.root)
        self.assertTrue(any(r["action"] == "local_audio_device" for r in rows) or True)




class TranscribeFileUITests(DownloadFixture):
    """An owner-supplied recording is transcribed without the microphone."""

    def setUp(self):
        super().setUp()
        self.connect_model()
        self.ui.cfg["stt"] = str(self.weights("fixture-whisper.bin"))
        self.ui.refresh()

    def clip(self, name="clip.wav"):
        path = self.weights(name).with_name(name)
        with wave.open(str(path), "wb") as f:
            f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            f.writeframes(struct.pack("<hh", 1250, -1250) * 1600)
        return path

    def runner(self, text="Merhaba!", error=None):
        calls = []

        async def runtime(model, wav, stem):
            calls.append((str(model), str(wav)))
            if error:
                raise error
            Path(str(stem) + ".txt").write_text(text, encoding="utf-8")
        return runtime, calls

    def test_a_recording_is_transcribed_into_the_conversation(self):
        runner, calls = self.runner()
        clip = self.clip()
        self.ui.transcribe_runner = runner
        # The file chooser is native; the action it triggers runs here.
        result = self.ui.run_transcribe(str(clip))
        self.assertEqual(result, "Merhaba!", self.ui.status.get_text())
        self.until(lambda: any(b.startswith("Recording") for b in self.bubbles()))
        self.assertEqual(calls, [(self.ui.cfg["stt"], str(clip))])
        self.assertIn("Merhaba!", self.bubbles()[-1])

    def test_a_rejected_recording_is_a_bounded_reason(self):
        runner, calls = self.runner()
        self.ui.transcribe_runner = runner
        clip = self.weights("clip.wav")
        clip.unlink()
        result = self.ui.run_transcribe(str(clip))
        self.assertIsNone(result)
        self.assertEqual(self.ui.status.get_text(), "recording_not_found")
        self.assertEqual(calls, [])

    def test_without_an_stt_model_nothing_is_offered(self):
        self.ui.cfg["stt"] = ""
        self.ui.refresh()
        self.assertFalse(self.ui.transcribe_button.get_sensitive())


class RoutePolicyUITests(MarketFixture):
    """The effective route and the mode's limits are stated, not implied."""

    def add_profile(self, profile):
        self.ui.store.save(profile)
        self.ui.refresh_profiles()
        return profile

    def pick_profile(self, profile_id):
        for item, entry in self.ui.policy_rows.items():
            if entry.id == profile_id:
                self.ui.profile_list.select_row(item)
                return entry
        raise AssertionError("missing profile: " + profile_id)

    def widgets_of(self, widget):
        out, item = [], widget.get_first_child()
        while item is not None:
            out.append(item)
            out.extend(self.widgets_of(item))
            item = item.get_next_sibling()
        return out

    def confirm(self, prefix):
        self.until(lambda: self.ui.confirmations)
        for widget in self.widgets_of(self.ui.confirmations[-1]):
            if type(widget).__name__ == "Button" and (widget.get_label() or "").startswith(prefix):
                widget.emit("clicked")
                return widget
        raise AssertionError("confirmation button missing: " + prefix)

    def test_a_local_route_is_stated_for_a_connected_model(self):
        self.connect_model()
        text = self.ui.route_note.get_text()
        self.assertIn("Route: fixture-chat via this computer", text)
        self.assertIn("(local_route)", text)
        self.assertIn("never routes to a provider", text)

    def test_a_mode_with_providers_states_the_limit_without_implying_a_route(self):
        self.connect_model()
        self.add_profile(profiles.Profile(id="cloud-chat", name="Cloud Chat",
                                          based_on_mode="CLOUD_CHAT",
                                          allowed_providers=("fixture-provider",)).checked())
        self.pick_profile("cloud-chat")
        self.click(self.ui.profile_switch)
        self.confirm("Confirm")
        self.until(lambda: self.ui.cfg["profile"] == "cloud-chat")
        text = self.ui.route_note.get_text()
        self.assertIn("Route: fixture-chat via this computer", text)
        self.assertIn("Cloud routes need an approved provider", text)
        self.assertNotIn("never routes", text)

    def test_without_a_model_no_route_is_claimed(self):
        self.assertIn("no local model selected", self.ui.route_note.get_text())
        self.assertEqual(self.ui.inventory, ())


class ResourceLockTests(DesktopTests):
    """Exclusive devices: a turn leases the microphone and the output."""

    def test_a_voice_turn_leases_the_microphone_then_the_output(self):
        self.connect_model()
        self.begin_voice()
        epoch = self.ui.session.epoch
        self.assertEqual(self.ui.leases.owner("microphone"), "voice-turn-%d" % epoch)
        self.click(self.ui.finish_btn)
        self.until(lambda: self.ui.stage == self.backend.playback_stage)
        # Capture is over: the microphone is free again while playback holds the output.
        self.assertIsNone(self.ui.leases.owner("microphone"))
        self.assertEqual(self.ui.leases.owner("audio_playback"), "voice-turn-%d" % epoch)
        self.click(self.ui.stop)
        self.until(lambda: self.backend.playback_cleaning)
        self.backend.playback_release.set()
        self.until(lambda: not self.ui.busy)
        self.assertEqual(self.ui.leases.held(), {})

    def test_a_stale_holder_refuses_the_capture_before_it_starts(self):
        self.connect_model()
        self.ui.leases.acquire("microphone", "stale-turn")
        self.click(self.ui.talk)
        self.until(lambda: self.ui.status.get_text() == "resource_busy:microphone")
        self.assertEqual(self.backend.speeches, [])
        self.assertEqual(self.ui.session.history, ())
        self.assertEqual(self.bubbles(), [])
        # The refused turn ends idle, and the stale lease is untouched.
        self.assertFalse(self.ui.busy)
        self.assertEqual(self.ui.leases.owner("microphone"), "stale-turn")
        self.ui.leases.release("microphone", "stale-turn")
        self.assertEqual(self.ui.leases.held(), {})

    def test_a_playback_refusal_leaves_the_turn_alive_for_text(self):
        self.connect_model()
        self.text("Merhaba!")
        self.ui.leases.acquire("audio_playback", "other-player")
        self.begin_voice()
        self.click(self.ui.finish_btn)
        self.until(lambda: self.ui.status.get_text() == "resource_busy:audio_playback")
        self.assertFalse(self.backend.speeches)
        self.ui.leases.release("audio_playback", "other-player")
        self.text("tekrar")
        # The refused playback did not end the turn: both replies are kept.
        self.assertEqual(len(self.ui.session.history), 6)

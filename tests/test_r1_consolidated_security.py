# SPDX-License-Identifier: AGPL-3.0-only
"""Regression coverage for the four independent R1 findings."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tihulu_jarvis import profiles
from tihulu_jarvis.owned_wait import await_authorized
from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.m1 import Hold


class PolicyScopeTests(unittest.TestCase):
    def test_duplicate_tokens_fail_closed(self):
        deny = profiles.Rule("filesystem_read", ("attachment",), profiles.DENY)
        allow = profiles.Rule("filesystem_read", ("attachment", "attachment"), profiles.ALLOW)
        for rules in ((deny, allow), (allow, deny)):
            with self.subTest(rules=rules):
                self.assertEqual(profiles.decide(rules, "filesystem_read", ("attachment",))[0], profiles.DENY)
                with self.assertRaisesRegex(Hold, "invalid_rule_scope"):
                    profiles.Profile("fixture", filesystem_rules=rules).checked()

    def test_equivalent_permutations_rejected_on_parse(self):
        rule = profiles.Rule("filesystem_read", ("attachment", "file"), profiles.DENY)
        other = profiles.Rule("filesystem_read", ("file", "attachment"), profiles.ALLOW)
        profile = profiles.Profile("fixture", filesystem_rules=(rule, other))
        with self.assertRaisesRegex(Hold, "duplicate_rule"):
            profile.checked()
        # Use the real parser, not a replacement Store fixture.
        data = json.loads(profiles.Store._dump(profiles.Profile("fixture", filesystem_rules=(rule,))))
        data["rules"].append({"action": other.action, "scope": list(other.scope),
                              "decision": other.decision})
        with self.assertRaises(Hold):
            profiles.Store._parse(json.dumps(data))

    def test_persisted_duplicate_tokens_refused(self):
        profile = profiles.Profile("fixture", filesystem_rules=(
            profiles.Rule("filesystem_read", ("attachment",), profiles.ALLOW),))
        data = json.loads(profiles.Store._dump(profile))
        data["rules"][0]["scope"].append("attachment")
        with self.assertRaises(Hold):
            profiles.Store._parse(json.dumps(data))

    def test_legitimate_narrower_rule_and_mode_denial(self):
        rules = (profiles.Rule("filesystem_read", ("attachment",), profiles.DENY),
                 profiles.Rule("filesystem_read", ("attachment", "file"), profiles.ALLOW))
        for ordered in (rules, rules[::-1]):
            policy = profiles.effective(profiles.Profile("fixture", filesystem_rules=ordered))
            self.assertEqual(policy.evaluate("filesystem_read", ("file", "attachment"))[0], profiles.ALLOW)
            self.assertEqual(policy.evaluate("filesystem_read", ("attachment",))[0], profiles.DENY)
            self.assertEqual(policy.evaluate("external_network", ("provider",))[0], profiles.DENY)


class PortableProfileTests(unittest.TestCase):
    def test_model_choices_are_not_exported_or_imported(self):
        profile = profiles.Profile("fixture", stt_model_id="/fixture/private-stt.bin",
                                   llm_model_id="private-model-canary",
                                   tts_model_id="/fixture/private-voice.onnx",
                                   voice_by_language={"en": "/fixture/private-en.onnx"})
        clean = profiles.sanitized(profile)
        text = profiles.Store._dump(clean)
        self.assertNotIn("private-", text)
        self.assertEqual((clean.stt_model_id, clean.llm_model_id, clean.tts_model_id,
                          clean.voice_by_language), ("", "", "", {}))
        self.assertEqual(clean.state, "pending_review")
        self.assertEqual(profile.llm_model_id, "private-model-canary")
        self.assertEqual(profile.voice_by_language["en"], "/fixture/private-en.onnx")


class AuditTextTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = profiles.audit_path(self.root)

    def test_invalid_existing_rows_refused_without_rewrite(self):
        for bad in ("\ud800", "a\x00b"):
            for key in ("action", "decision", "reason", "scope"):
                with self.subTest(key=key, bad=repr(bad)):
                    row = {"at": 1, "profile": "fixture", "action": "read",
                           "decision": "deny", "reason": "rule", "scope": ["file"]}
                    row[key] = [bad] if key == "scope" else bad
                    original = json.dumps(row).encode() + b"\n"
                    self.path.write_bytes(original)
                    with self.assertRaisesRegex(Hold, "audit_unreadable"):
                        profiles.read_audit(self.root)
                    with self.assertRaisesRegex(Hold, "audit_unreadable"):
                        profiles.record_decision(self.root, "fixture", "read", (), "deny", "rule")
                    self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_new_fields_refused_before_append(self):
        profiles.record_decision(self.root, "fixture", "read", (), "deny", "rule")
        original = self.path.read_bytes()
        for bad in ("\ud800", "a\x00b"):
            for i in range(4):
                with self.subTest(i=i, bad=repr(bad)):
                    args = ["read", (), "deny", "rule"]
                    args[i] = (bad,) if i == 1 else bad
                    with self.assertRaisesRegex(Hold, "audit_unavailable"):
                        profiles.record_decision(self.root, "fixture", *args)
                    self.assertEqual(self.path.read_bytes(), original)

    def test_international_text_and_unicode_line_separator_roundtrip(self):
        reason = "Türkçe · Deutsch · 日本語\u2028test"
        profiles.record_decision(self.root, "fixture", "read", ("özel",), "deny", reason)
        profiles.record_decision(self.root, "fixture", "write", (), "allow", "rule")
        self.assertEqual(profiles.read_audit(self.root)[0]["reason"], reason)

    def test_unknown_field_cannot_poison_rewrite(self):
        row = {"at": 1, "profile": "fixture", "action": "read", "decision": "deny",
               "reason": "rule", "scope": [], "extra": "\ud800"}
        self.path.write_text(json.dumps(row) + "\n")
        with self.assertRaisesRegex(Hold, "audit_unreadable"):
            profiles.read_audit(self.root)


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, revoke):
        # A real disposable child ignores TERM. No audio or network.
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); time.sleep(30)",
            stdout=asyncio.subprocess.PIPE)
        allowed = True
        entered, terminating = asyncio.Event(), asyncio.Event()
        actual_terminate = audio.terminate

        def check():
            if not allowed:
                raise Hold("fixture_revoked")

        async def terminate(p):
            terminating.set()
            await actual_terminate(p)

        async def worker():
            async with audio.child("arecord", checkpoint=check):
                entered.set()
                await asyncio.Future()

        owner = None
        try:
            self.assertEqual(await asyncio.wait_for(proc.stdout.readline(), 3), b"ready\n")
            with patch.object(audio.shutil, "which", return_value=sys.executable), \
                 patch.object(audio.asyncio, "create_subprocess_exec", return_value=proc), \
                 patch.object(audio, "terminate", side_effect=terminate):
                owner = asyncio.create_task(await_authorized(worker, 5 if revoke else .2,
                                                              check, interval=.01))
                await asyncio.wait_for(entered.wait(), 2)
                if revoke:
                    allowed = False
                await asyncio.wait_for(terminating.wait(), 3)
                owner.cancel()
                await asyncio.sleep(.02)
                owner.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(owner), 4)
                self.assertIsNotNone(proc.returncode, "owner completed before child reaped")
        finally:
            if owner is not None and not owner.done():
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
            if proc.returncode is None:
                proc.kill()
            await proc.wait()

    async def test_deadline_then_repeated_cancel_reaps(self):
        await self.exercise(False)

    async def test_revocation_then_repeated_cancel_reaps(self):
        await self.exercise(True)

    async def test_delayed_spawn_reclaimed_after_repeated_cancel(self):
        entered, release = asyncio.Event(), asyncio.Event()
        reaped = []
        proc = object()
        async def spawn(*args, **kwargs):
            entered.set()
            await release.wait()
            return proc
        async def terminate(p):
            reaped.append(p)
        async def worker():
            async with audio.child("arecord"):
                self.fail("cancelled child yielded")
        with patch.object(audio.shutil, "which", return_value=sys.executable), \
             patch.object(audio.asyncio, "create_subprocess_exec", side_effect=spawn), \
             patch.object(audio, "terminate", side_effect=terminate):
            owner = asyncio.create_task(worker())
            await asyncio.wait_for(entered.wait(), 1)
            owner.cancel()
            await asyncio.sleep(0)
            owner.cancel()
            await asyncio.sleep(0)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(owner, 2)
        self.assertEqual(reaped, [proc])

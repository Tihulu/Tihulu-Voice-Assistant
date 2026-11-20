# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Mode and profile policy tests. No process, network, device or model is used."""
import json
import tempfile
import unittest
from pathlib import Path

from tihulu_jarvis.profiles import (ALLOW, ASK, DENY, MODES, Profile, Rule, Store,
                                    clone_to_custom, decide, effective, sanitized)
from tihulu_jarvis.voice.m1 import Hold


def base(**kw):
    values = {"id": "local-only", "name": "Assistant", "based_on_mode": "LOCAL_ONLY", "version": 1}
    values.update(kw)
    return Profile(**values).checked()


class ModeTests(unittest.TestCase):
    def test_exactly_four_modes_and_unknown_mode_rejected(self):
        self.assertEqual(MODES, ("LOCAL_ONLY", "CLOUD_CHAT", "SECURE_HYBRID", "CUSTOM"))
        with self.assertRaisesRegex(Hold, "unknown_operating_mode"):
            base(based_on_mode="UNLIMITED")

    def test_mode_capability_is_not_only_a_system_prompt(self):
        grant = (Rule("cloud_inference", ("provider-x",), ALLOW),)
        for mode, expected in (("LOCAL_ONLY", DENY), ("CLOUD_CHAT", ALLOW), ("CUSTOM", ALLOW)):
            policy = effective(base(based_on_mode=mode, tool_rules=grant))
            self.assertEqual(policy.evaluate("cloud_inference", ("provider-x",))[0], expected, mode)
        chat = effective(base(based_on_mode="CLOUD_CHAT"))
        self.assertEqual(chat.evaluate("host_shell", ("bridge",))[0], DENY)
        self.assertEqual(chat.evaluate("host_shell", ("bridge",))[1], "mode_without_capability")
        hybrid = effective(base(based_on_mode="SECURE_HYBRID", shell_rules=(
            Rule("host_shell", ("jan-qwen",), ALLOW, approval="auth-local-1", expires_at=1e12),)))
        self.assertEqual(hybrid.evaluate("host_shell", ("jan-qwen",))[0], ALLOW)
        self.assertEqual(hybrid.evaluate("host_shell", ("jan-qwen",))[1], "rule")
        self.assertEqual(hybrid.evaluate("host_shell", ("other-worker",))[0], DENY)


class RuleTests(unittest.TestCase):
    def rules(self, *rules):
        return effective(base(based_on_mode="SECURE_HYBRID",
                              shell_rules=tuple(r for r in rules if r.action == "host_shell"),
                              tool_rules=tuple(r for r in rules if r.action not in ("host_shell",))))

    def test_specificity_and_deny_precedence(self):
        decide_rules = (Rule("host_shell", (), DENY), Rule("host_shell", ("jan-qwen",), ALLOW, "auth-1", 1e12))
        policy = self.rules(*decide_rules)
        self.assertEqual(policy.evaluate("host_shell", ("jan-qwen",))[0], ALLOW)
        self.assertEqual(policy.evaluate("host_shell", ("other",))[0], DENY)
        # Equal specificity: deny must win even when allow is listed second.
        self.assertEqual(decide((Rule("host_shell", (), ALLOW), Rule("host_shell", (), DENY)), "host_shell"),
                         (DENY, "rule"))

    def test_unknown_action_and_missing_rule_fail_closed(self):
        policy = self.rules(Rule("host_shell", ("jan-qwen",), ALLOW))
        self.assertEqual(policy.evaluate("anything_new", ("jan-qwen",))[0], DENY)
        self.assertEqual(policy.evaluate("anything_new", ("jan-qwen",))[1], "unknown_action")
        self.assertEqual(policy.evaluate("publish_artifact", ("repo",)), (DENY, "no_rule_matches"))

    def test_expired_approval_degrades_to_ask(self):
        policy = self.rules(Rule("host_shell", ("jan-qwen",), ALLOW, approval="auth-1", expires_at=100.0))
        self.assertEqual(policy.evaluate("host_shell", ("jan-qwen",), now=99.0), (ALLOW, "rule"))
        self.assertEqual(policy.evaluate("host_shell", ("jan-qwen",), now=101.0), (ASK, "approval_expired"))

    def test_global_law_cannot_be_granted(self):
        with self.assertRaisesRegex(Hold, "global_deny_not_weakable"):
            base(tool_rules=(Rule("credential_material_in_profile_data", ("any",), ALLOW),))
        with self.assertRaisesRegex(Hold, "global_deny_not_weakable"):
            base(tool_rules=(Rule("silent_capability_inheritance", ("any",), ALLOW),))
        policy = effective(base(based_on_mode="CUSTOM"))
        self.assertEqual(policy.evaluate("silent_capability_inheritance", ("agent",)),
                         (DENY, "global_deny"))


class ProfileTests(unittest.TestCase):
    def test_locale_activation_and_model_references_are_validated(self):
        with self.assertRaisesRegex(Hold, "invalid_locale_or_activation"):
            base(ui_locale="fr")
        with self.assertRaisesRegex(Hold, "invalid_locale_or_activation"):
            base(voice_activation="always_on")
        with self.assertRaisesRegex(Hold, "unsupported_voice_language"):
            base(voice_by_language={"fr": "fixture.onnx"})
        with self.assertRaisesRegex(Hold, "invalid_rule_scope"):
            base(filesystem_rules=(Rule("filesystem_write", ("~",), ALLOW),))
        # A real local scope may be an absolute path; export/import must strip it.
        local = base(filesystem_rules=(Rule("filesystem_write", ("/home/owner/project",), ALLOW),))
        self.assertEqual(sanitized(local).filesystem_rules, ())
        with self.assertRaisesRegex(Hold, "invalid_approval_reference"):
            base(filesystem_rules=(Rule("filesystem_write", ("project",), ALLOW, approval="secret/key"),))
        with self.assertRaisesRegex(Hold, "credential_material_in_profile_data"):
            base(credential_refs=("/home/owner/keys/token.txt",))

    def test_credential_references_are_opaque_tokens(self):
        self.assertEqual(base(credential_refs=("keyring-ref-01",)).credential_refs, ("keyring-ref-01",))

    def test_effective_policy_is_an_immutable_snapshot(self):
        profile = base()
        policy = effective(profile)
        later = Profile(id=profile.id, version=2, based_on_mode="CUSTOM",
                        shell_rules=(Rule("host_shell", ("jan-qwen",), ALLOW),)).checked()
        self.assertEqual(policy.rules, ())
        self.assertEqual(effective(later).rules[0].action, "host_shell")

    def test_custom_clone_of_local_only_profile(self):
        source = base(id="private", based_on_mode="LOCAL_ONLY", credential_refs=("keyring-ref-01",),
                      filesystem_rules=(Rule("filesystem_write", ("project",), ALLOW, approval="owner-1", expires_at=1e12),))
        clone = clone_to_custom(source, "Private plus")
        self.assertEqual(clone.based_on_mode, "CUSTOM")
        self.assertNotEqual(clone.id, source.id)
        self.assertEqual(clone.credential_refs, ())
        self.assertEqual(source.based_on_mode, "LOCAL_ONLY")
        self.assertEqual(source.id, "private")
        self.assertFalse(clone.rules()[0].approval)
        with self.assertRaisesRegex(Hold, "already_custom"):
            clone_to_custom(clone, "again")


class ImportExportTests(unittest.TestCase):
    def imported(self):
        source = base(id="hybrid", based_on_mode="SECURE_HYBRID", credential_refs=("keyring-ref-01",),
                      shell_rules=(Rule("host_shell", ("jan-qwen",), ALLOW, approval="auth-local-1", expires_at=1e12),),
                      filesystem_rules=(Rule("filesystem_write", ("/home/owner/project",), ALLOW),))
        return source, sanitized(source)

    def test_import_is_pending_review_without_privileges_or_private_paths(self):
        source, clean = self.imported()
        self.assertEqual(clean.state, "pending_review")
        self.assertEqual(clean.credential_refs, ())
        self.assertEqual(clean.allowed_providers, ())
        denied = effective(clean).evaluate("host_shell", ("jan-qwen",))
        self.assertEqual(denied, (DENY, "rule"))
        self.assertEqual(effective(clean).evaluate("filesystem_write", ("/home/owner/project",)),
                         (DENY, "no_rule_matches"))
        self.assertFalse(any(r.approval for r in clean.rules()))

    def test_owner_can_reauthorize_privileged_scope_after_import(self):
        _, clean = self.imported()
        granted = Profile(id=clean.id, version=2, based_on_mode="SECURE_HYBRID",
                          shell_rules=(Rule("host_shell", ("jan-qwen",), ALLOW,
                                            approval="auth-local-2", expires_at=1e12),)).checked()
        self.assertEqual(effective(granted).evaluate("host_shell", ("jan-qwen",))[0], ALLOW)

    def test_export_view_carries_no_secret_material(self):
        source, clean = self.imported()
        text = json.dumps({"rules": [{"action": r.action, "scope": list(r.scope), "decision": r.decision,
                                      "approval": r.approval, "expires_at": r.expires_at}
                                     for r in clean.rules()]})
        for banned in ("keyring-ref-01", "auth-local-1", "/home/", "~"):
            self.assertNotIn(banned, text)
        self.assertIn("pending_review", json.dumps({"state": clean.state}))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)

    def test_save_load_roundtrip_and_version_history(self):
        profile = base()
        self.store.save(profile)
        self.assertEqual(self.store.load("local-only").rules(), ())
        with self.assertRaisesRegex(Hold, "version_must_increase"):
            self.store.save(profile)
        self.store.save(Profile(id="local-only", version=2, name="Renamed"))
        self.assertEqual(self.store.load("local-only").name, "Renamed")
        self.assertEqual([p.version for p in self.store.versions("local-only")], [1, 2])
        rolled = self.store.rollback("local-only", 1)
        self.assertEqual((rolled.version, rolled.name), (3, "Assistant"))
        self.assertEqual([p.version for p in self.store.versions("local-only")], [1, 2, 3])

    def test_rollback_restores_valid_policy_and_drops_later_grant(self):
        self.store.save(base(id="hybrid", based_on_mode="SECURE_HYBRID", version=1))
        self.store.save(base(id="hybrid", based_on_mode="SECURE_HYBRID", version=2,
                             shell_rules=(Rule("host_shell", ("jan-qwen",), ALLOW,
                                               approval="auth-1", expires_at=1e12),)))
        before = effective(self.store.load("hybrid"))
        self.assertEqual(before.evaluate("host_shell", ("jan-qwen",))[0], ALLOW)
        rolled = self.store.rollback("hybrid", 1)
        after = effective(self.store.load("hybrid"))
        self.assertEqual(after.evaluate("host_shell", ("jan-qwen",)), (DENY, "no_rule_matches"))
        self.assertEqual(rolled.version, 3)
        self.assertNotIn("auth-1", (self.tmp / "hybrid.json").read_text(encoding="utf-8"))

    def test_stored_profiles_have_no_arbitrary_cap(self):
        for i in range(120):
            self.store.save(base(id="profile-%03d" % i, version=1))
        self.assertEqual(len(self.store.ids()), 120)
        self.assertEqual(self.store.load("profile-119").based_on_mode, "LOCAL_ONLY")

    def test_profile_files_never_carry_private_paths_or_secrets(self):
        self.store.save(base(credential_refs=("keyring-ref-01",), stt_model_id="fixture-whisper",
                             llm_model_id="fixture-chat"))
        for path in self.tmp.glob("*.json"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("/home/", text)
            self.assertNotIn("~", text)
        self.assertEqual(self.store.load("local-only").llm_model_id, "fixture-chat")

    def test_malformed_and_oversize_profiles_are_rejected(self):
        with self.assertRaisesRegex(Hold, "profile_not_found"):
            self.store.load("missing")
        (self.tmp / "broken.json").write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(Hold, "profile_unreadable"):
            self.store.load("broken")
        with self.assertRaisesRegex(Hold, "profile_too_large"):
            Store._parse("x" * (Store.LIMIT + 1))

    def test_global_law_in_stored_data_is_rejected(self):
        text = json.dumps({"id": "bad", "version": 1, "based_on_mode": "CUSTOM", "state": "active",
                           "ui_locale": "tr", "voice_activation": "off", "rules": [
                               {"action": "silent_capability_inheritance", "scope": [], "decision": "allow",
                                "approval": "", "expires_at": 0.0}]})
        with self.assertRaisesRegex(Hold, "global_deny_not_weakable"):
            Store._parse(text)

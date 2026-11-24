# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Mode and profile policy tests. No process, network, device or model is used."""
import json
import tempfile
import unittest
from pathlib import Path

from tihulu_jarvis.profiles import (ALLOW, ASK, DENY, MODES, Profile, Rule, Store,
                                    clone_to_custom, decide, effective, sanitized)
from tihulu_jarvis import profiles
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


class StoreMutationTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.store = Store(root.name)
        self.other = Store(root.name)
        self.store.save(base())

    def test_conflicting_writer_and_rollback_cannot_pass_version_check(self):
        with self.store._mutation_lock():
            with self.assertRaisesRegex(Hold, "profile_store_busy_or_unavailable"):
                self.other.save(base(version=2, name="stale"))
            with self.assertRaisesRegex(Hold, "profile_store_busy_or_unavailable"):
                self.other.rollback("local-only", 1)
            self.store._save_locked(base(version=2, name="winner"))
        with self.assertRaisesRegex(Hold, "version_must_increase"):
            self.other.save(base(version=2, name="stale"))
        self.assertEqual(self.other.load("local-only").name, "winner")
        self.assertEqual([p.version for p in self.other.versions("local-only")], [1, 2])

    def test_delete_refuses_stale_or_contended_generation_and_preserves_history(self):
        shown = self.store.load("local-only")
        with self.store._mutation_lock():
            with self.assertRaisesRegex(Hold, "profile_store_busy_or_unavailable"):
                self.other.delete("local-only", expected=shown)
        updated = self.store.save(base(version=2, name="new"))
        with self.assertRaisesRegex(Hold, "profile_changed"):
            self.other.delete("local-only", expected=shown)
        self.other.delete("local-only", expected=updated)
        self.assertNotIn("local-only", self.store.ids())
        self.assertEqual([p.version for p in self.store.versions("local-only")], [1, 2])

    def test_startup_does_not_select_arbitrary_or_disabled_profile(self):
        self.store.save(base(id="privileged", based_on_mode="CUSTOM"))
        with self.assertRaisesRegex(Hold, "profile_not_found"):
            self.store.startup_profile("")
        self.store.save(base(id="private-assistant"))
        self.assertEqual(self.store.startup_profile("").id, "private-assistant")
        self.assertEqual(self.store.startup_profile("privileged").id, "privileged")
        self.store.save(base(id="privileged", version=2, state="disabled"))
        with self.assertRaisesRegex(Hold, "profile_not_active"):
            self.store.startup_profile("privileged")
        with self.assertRaisesRegex(Hold, "profile_not_found"):
            self.store.startup_profile("missing")

    def test_create_only_refuses_even_higher_version_of_existing_profile(self):
        with self.assertRaisesRegex(Hold, "profile_already_exists"):
            self.other.save(base(version=9, name="import"), create_only=True)
        self.assertEqual(self.store.load("local-only").version, 1)

    def test_existing_history_generation_cannot_be_replaced(self):
        path = self.store.history / "local-only-v2.json"
        original = Store._dump(base(version=2, name="archived"))
        path.write_text(original)
        with self.assertRaisesRegex(Hold, "version_must_increase"):
            self.store.save(base(version=2, name="replacement"))
        self.assertEqual(path.read_text(), original)
        self.assertEqual(self.store.load("local-only").version, 1)

    def test_profile_identity_and_history_prefix_are_isolated(self):
        self.store.save(base(id="local-only-v2", name="other profile"))
        self.assertEqual([p.id for p in self.store.versions("local-only")], ["local-only"])
        rolled = self.store.rollback("local-only", 1)
        self.assertEqual((rolled.id, rolled.version), ("local-only", 2))
        self.store._path("local-only").write_text(Store._dump(base(id="wrong")))
        with self.assertRaisesRegex(Hold, "profile_changed"):
            self.store.load("local-only")
        with self.assertRaisesRegex(Hold, "profile_changed"):
            self.store.save(base(version=3))

    def test_rollback_after_failed_history_write_uses_active_version(self):
        from unittest.mock import patch
        original = self.store._write
        def write(path, text):
            if path.parent == self.store.history:
                raise OSError("fixture")
            original(path, text)
        with patch.object(self.store, "_write", side_effect=write):
            with self.assertRaisesRegex(Hold, "profile_history_write_failed"):
                self.store.save(base(version=2, name="active without archive"))
        self.assertEqual(self.store.load("local-only").version, 2)
        self.assertEqual(self.store.rollback("local-only", 1).version, 3)
        self.assertEqual([p.version for p in self.store.versions("local-only")], [1, 2, 3])
        self.assertEqual(self.store.versions("local-only")[1].name, "active without archive")

    def test_failed_recovery_preserves_active_generation(self):
        from unittest.mock import patch
        self.store.save(base(version=2, name="active"))
        (self.store.history / "local-only-v2.json").unlink()
        with patch.object(self.other, "_write", side_effect=OSError("fixture")):
            with self.assertRaisesRegex(Hold, "profile_history_write_failed"):
                self.other.save(base(version=3, name="new"))
        self.assertEqual(self.store.load("local-only").name, "active")
        self.assertFalse((self.store.history / "local-only-v3.json").exists())

    def test_recovery_never_replays_historical_grants(self):
        granted = base(version=2, tool_rules=(Rule("local_audio_device", (), ALLOW),))
        self.store.save(granted)
        revoked = base(version=3, tool_rules=(Rule("local_audio_device", (), DENY),))
        self.store.save(revoked)
        (self.store.history / "local-only-v3.json").unlink()
        reopened = Store(self.store.root)
        self.assertEqual(effective(reopened.load("local-only")).evaluate("local_audio_device")[0], DENY)
        reopened.save(base(version=4, name="updated", tool_rules=revoked.tool_rules))
        self.assertEqual(effective(reopened.versions("local-only")[2]).evaluate("local_audio_device")[0], DENY)


class EndpointEnforcementTests(unittest.TestCase):
    def profile(self, **kw):
        values = {"id": "local-only", "name": "Assistant", "based_on_mode": "LOCAL_ONLY", "version": 1}
        values.update(kw)
        return Profile(**values).checked()

    def test_local_only_accepts_a_loopback_endpoint(self):
        for url in ("http://127.0.0.1:9876/v1", "http://[::1]:1337"):
            with self.subTest(url=url):
                self.assertEqual(profiles.enforce_endpoint(self.profile(), url),
                                 url)

    def test_local_only_refuses_a_remote_address(self):
        with self.assertRaisesRegex(Hold, "local_only_forbids_non_loopback"):
            profiles.enforce_endpoint(self.profile(), "https://api.example.test/v1")
        with self.assertRaisesRegex(Hold, "local_only_forbids_non_loopback"):
            profiles.enforce_endpoint(self.profile(), "http://localhost:9876/v1")

    def test_cloud_modes_do_not_promise_a_missing_transport(self):
        with self.assertRaisesRegex(Hold, "cloud_transport_not_available"):
            profiles.enforce_endpoint(self.profile(based_on_mode="CLOUD_CHAT"),
                                      "https://provider.example/v1")

    def test_cloud_providers_must_be_approved_by_name(self):
        chat = self.profile(based_on_mode="CLOUD_CHAT", allowed_providers=("fixture-provider",))
        with self.assertRaisesRegex(Hold, "approved_provider_required"):
            profiles.enforce_endpoint(chat, "http://127.0.0.1:9876/v1")

    def test_local_only_cannot_carry_a_cloud_provider(self):
        local = self.profile(allowed_providers=("fixture-provider",))
        with self.assertRaisesRegex(Hold, "local_only_forbids_providers"):
            profiles.enforce_endpoint(local, "http://127.0.0.1:9876/v1")

    def test_empty_or_oversize_endpoints_are_rejected(self):
        for bad in ("", "   ", "x" * 5000, None):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(Hold, "invalid_local_settings"):
                    profiles.enforce_endpoint(self.profile(), bad)


class ActionEnforcementTests(unittest.TestCase):
    def profile(self, **kw):
        values = {"id": "local-only", "name": "Assistant", "based_on_mode": "LOCAL_ONLY", "version": 1}
        values.update(kw)
        return Profile(**values).checked()

    def test_an_explicit_deny_refuses_the_action(self):
        denied = self.profile(tool_rules=(Rule("local_audio_device", ("push_to_talk",), DENY),))
        with self.assertRaisesRegex(Hold, "profile_forbids_local_audio_device"):
            profiles.require_tool(denied, "local_audio_device", ("push_to_talk",))

    def test_grant_allows_but_explicit_ask_requires_approval(self):
        granting = self.profile(tool_rules=(Rule("local_audio_device", ("push_to_talk",), ALLOW),))
        self.assertEqual(profiles.require_tool(granting, "local_audio_device", ("push_to_talk",))[0], ALLOW)
        asking = self.profile(tool_rules=(Rule("local_audio_device", ("push_to_talk",), ASK),))
        for selected in (False, True):
            with self.assertRaisesRegex(Hold, "^approval_required$"):
                profiles.require_tool(asking, "local_audio_device", ("push_to_talk",), selected=selected)

    def test_missing_rule_requires_a_trusted_selection(self):
        with self.assertRaisesRegex(Hold, "^approval_required$"):
            profiles.require_tool(self.profile(), "local_audio_device", ("push_to_talk",))
        self.assertEqual(profiles.require_tool(self.profile(), "filesystem_read", ("file",),
                                               selected=True), (ALLOW, "explicit_selection"))

    def test_an_expired_approval_asks_again(self):
        expired = self.profile(based_on_mode="SECURE_HYBRID",
                               shell_rules=(Rule("host_shell", ("jan-qwen",), ALLOW,
                                                 approval="owner-1", expires_at=5.0),))
        with self.assertRaisesRegex(Hold, "approval_expired"):
            profiles.require_tool(expired, "host_shell", ("jan-qwen",), now=10.0)
        self.assertEqual(profiles.require_tool(expired, "host_shell", ("jan-qwen",), now=1.0)[0], ALLOW)

    def test_default_clock_is_current_time_and_selection_cannot_override_expiry(self):
        from unittest.mock import patch
        profile = self.profile(tool_rules=(Rule("local_audio_device", ("push_to_talk",),
                                               ALLOW, approval="fixture", expires_at=50.0),))
        with patch.object(profiles.time, "time", return_value=51.0):
            self.assertEqual(effective(profile).evaluate("local_audio_device", ("push_to_talk",)),
                             (ASK, "approval_expired"))
            for selected in (False, True):
                with self.assertRaisesRegex(Hold, "^approval_expired$"):
                    profiles.require_tool(profile, "local_audio_device", ("push_to_talk",),
                                          selected=selected)

    def test_mode_restriction_cannot_be_overridden_by_selection(self):
        with self.assertRaisesRegex(Hold, "^mode_without_capability$"):
            profiles.require_tool(self.profile(), "host_shell", ("jan-qwen",), selected=True)

    def test_a_global_law_is_never_granted_by_a_rule(self):
        with self.assertRaisesRegex(Hold, "global_deny"):
            profiles.require_tool(self.profile(), "credential_material_in_profile_data", ("any",))


class AuditTrailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_decisions_are_appended_and_bounded(self):
        profiles.record_decision(self.tmp, "local-only", "local_audio_device", ("push_to_talk",), "allow", "rule", now=1.0)
        profiles.record_decision(self.tmp, "local-only", "host_shell", ("bridge",), "deny", "profile_forbids_host_shell", now=2.0)
        rows = profiles.read_audit(self.tmp)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["decision"], "allow")
        self.assertEqual(rows[1]["reason"], "profile_forbids_host_shell")
        self.assertEqual(rows[1]["scope"], ["bridge"])
        for index in range(profiles.AUDIT_LIMIT + 20):
            profiles.record_decision(self.tmp, "local-only", "local_audio_device", ("push_to_talk",), "allow", "rule", now=3.0 + index)
        self.assertEqual(len(profiles.read_audit(self.tmp, profiles.AUDIT_LIMIT)), profiles.AUDIT_LIMIT)

    def test_an_empty_or_broken_log_is_honest(self):
        self.assertEqual(profiles.read_audit(self.tmp), ())
        profiles.audit_path(self.tmp).write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(Hold, "audit_unreadable"):
            profiles.read_audit(self.tmp)

    def test_audit_rejects_special_oversize_and_invalid_rows(self):
        import os
        from unittest.mock import patch
        path = profiles.audit_path(self.tmp)
        target = self.tmp / "fixture"
        target.write_text("secret")
        for kind in ("fifo", "symlink", "oversize"):
            if kind == "fifo":
                os.mkfifo(path)
            elif kind == "symlink":
                path.symlink_to(target)
            else:
                path.write_bytes(b"x" * (profiles.AUDIT_BYTES + 1))
            try:
                with patch.object(profiles, "read_selected_bytes") as reader:
                    with self.assertRaisesRegex(Hold, "audit_unreadable"):
                        profiles.read_audit(self.tmp)
                    reader.assert_not_called()
            finally:
                path.unlink()
        for raw in ("[]", '{"at":1}', '{"at":NaN}'):
            path.write_text(raw + "\n")
            with self.assertRaisesRegex(Hold, "audit_unreadable"):
                profiles.read_audit(self.tmp)

    def test_endpoint_credentials_not_recorded_or_rewritten(self):
        path = profiles.audit_path(self.tmp)
        old = {"at": 1, "profile": "local-only", "action": "endpoint",
               "scope": ["http://user:fixture-secret@example.invalid"], "decision": "deny",
               "reason": "mode_policy"}
        path.write_text(json.dumps(old) + "\n")
        self.assertEqual(profiles.read_audit(self.tmp)[0]["scope"], ["redacted"])
        profiles.record_decision(self.tmp, "local-only", "endpoint",
                                 ("http://fixture-new-secret@example.invalid",),
                                 "deny", "mode_policy", now=2)
        self.assertNotIn("fixture-secret", path.read_text())
        self.assertNotIn("fixture-new-secret", path.read_text())

    def test_contended_audit_writer_does_not_lose_previous_record(self):
        profiles.record_decision(self.tmp, "local-only", "endpoint", ("loopback",),
                                 "allow", "mode_policy", now=1)
        with profiles._audit_lock(self.tmp):
            with self.assertRaisesRegex(Hold, "audit_unavailable"):
                profiles.record_decision(self.tmp, "local-only", "endpoint", ("not_loopback",),
                                         "deny", "mode_policy", now=2)
        self.assertEqual(len(profiles.read_audit(self.tmp)), 1)

    def test_the_log_carries_no_private_material(self):
        profiles.record_decision(self.tmp, "local-only", "local_audio_device", ("push_to_talk",), "deny", "profile_forbids_local_audio_device", now=9.0)
        text = profiles.audit_path(self.tmp).read_text(encoding="utf-8")
        self.assertNotIn("/home/", text)
        self.assertNotIn("secret", text)
        self.assertIn("profile_forbids_local_audio_device", text)
        with self.assertRaisesRegex(Hold, "invalid_profile_id"):
            profiles.record_decision(self.tmp, "bad/id", "local_audio_device", (), "deny", "reason")


class EscalationTests(unittest.TestCase):
    def profile(self, **kw):
        values = {"id": "local-only", "name": "Assistant", "based_on_mode": "LOCAL_ONLY", "version": 1}
        values.update(kw)
        return Profile(**values).checked()

    def test_privileges_are_derived_from_mode_and_rules(self):
        local = self.profile()
        self.assertEqual(profiles.granted(local), frozenset())
        chat = self.profile(based_on_mode="CLOUD_CHAT")
        self.assertEqual(profiles.granted(chat), frozenset({"cloud_inference", "cloud_disclosure"}))
        hybrid = self.profile(based_on_mode="SECURE_HYBRID",
                             shell_rules=(Rule("host_shell", ("jan-qwen",), "allow", "owner-1", 1e12),))
        self.assertEqual(profiles.granted(hybrid),
                         frozenset({"cloud_inference", "cloud_disclosure", "host_shell"}))
        denied = self.profile(based_on_mode="SECURE_HYBRID",
                              shell_rules=(Rule("host_shell", ("jan-qwen",), "deny"),))
        self.assertEqual(profiles.granted(denied), frozenset({"cloud_inference", "cloud_disclosure"}))
        wake = self.profile(voice_activation="wake_word")
        self.assertEqual(profiles.granted(wake), frozenset({"wake_word"}))
        busy = self.profile(background_processing_enabled=True)
        self.assertEqual(profiles.granted(busy), frozenset({"background_processing"}))

    def test_escalation_is_computed_by_token_not_by_name(self):
        local = self.profile()
        renamed = self.profile(name="Totally different name")
        self.assertEqual(profiles.escalation(renamed, local), frozenset())
        chat = self.profile(based_on_mode="CLOUD_CHAT")
        extra = profiles.escalation(local, chat)
        self.assertEqual(extra, frozenset({"cloud_inference", "cloud_disclosure"}))
        lines = profiles.describe(extra)
        self.assertEqual(sorted(lines), ["Cloud reasoning", "Sending content to a provider"])
        self.assertEqual(profiles.escalation(chat, local), frozenset())

    def test_expired_grant_cannot_hide_new_escalation(self):
        expired = self.profile(based_on_mode="SECURE_HYBRID",
            shell_rules=(Rule("host_shell", ("jan-qwen",), ALLOW, "old", 1.0),))
        fresh = self.profile(based_on_mode="SECURE_HYBRID",
            shell_rules=(Rule("host_shell", ("jan-qwen",), ALLOW, "new", 1e12),))
        self.assertNotIn("host_shell", profiles.granted(expired))
        self.assertIn("host_shell", profiles.escalation(expired, fresh))

    def test_a_denial_never_counts_as_an_escalation(self):
        local = self.profile()
        denied = self.profile(tool_rules=(Rule("host_shell", ("bridge",), "deny"),))
        self.assertEqual(profiles.escalation(local, denied), frozenset())


class AuditFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        profiles.record_decision(self.tmp, "reader", "filesystem_read", ("attachment",),
                                 "allow", "rule_or_absent", now=1.0)
        profiles.record_decision(self.tmp, "reader", "endpoint", ("http://127.0.0.1:9876/",),
                                 "deny", "local_only_forbids_non_loopback", now=2.0)
        profiles.record_decision(self.tmp, "reader", "disclosure", ("stays_local", "loopback"),
                                 "shown", "loopback_transport_only", now=3.0)

    def test_a_filter_narrows_without_adding_or_hiding(self):
        self.assertEqual(len(profiles.read_audit(self.tmp, decision="deny")), 1)
        self.assertEqual(len(profiles.read_audit(self.tmp, action="filesystem_read")), 1)
        self.assertEqual(profiles.read_audit(self.tmp, decision="allow")[0]["action"],
                         "filesystem_read")
        self.assertEqual(len(profiles.read_audit(self.tmp, profile="reader")), 3)
        self.assertEqual(profiles.read_audit(self.tmp, profile="other"), ())
        self.assertEqual(profiles.read_audit(self.tmp, action="no_such_action"), ())

    def test_a_combined_filter_is_the_intersection(self):
        rows = profiles.read_audit(self.tmp, action="endpoint", decision="deny",
                                  profile="reader")
        self.assertEqual([r["reason"] for r in rows], ["local_only_forbids_non_loopback"])

    def test_the_actions_offered_come_from_the_log(self):
        self.assertEqual(profiles.audit_actions(self.tmp),
                         ("filesystem_read", "endpoint", "disclosure"))
        self.assertEqual(profiles.audit_actions(Path(tempfile.mkdtemp())), ())

    def test_a_bad_filter_is_refused(self):
        for bad in ("", "x" * 65, 42, []):
            with self.subTest(action=bad):
                with self.assertRaises(Hold):
                    profiles.read_audit(self.tmp, action=bad)
        with self.assertRaisesRegex(Hold, "audit_decision_filter"):
            profiles.read_audit(self.tmp, decision=7)
        with self.assertRaisesRegex(Hold, "audit_profile_filter"):
            profiles.read_audit(self.tmp, profile="a" * 100)


class TemplateTests(unittest.TestCase):
    def test_every_template_builds_without_duplicate_rules(self):
        for key in profiles.TEMPLATES:
            with self.subTest(key=key):
                profile = profiles.template(key)
                self.assertEqual(profile.id, key)
                # The concatenation of the kinds holds each rule exactly once.
                actions = [rule.action for rule in profile.rules()]
                self.assertEqual(len(actions), len(set(actions)))

    def test_a_rule_lands_in_the_kind_the_policy_reads_it_from(self):
        agent = profiles.template("coding-agent")
        self.assertEqual([r.action for r in agent.shell_rules], ["host_shell"])
        self.assertEqual([r.action for r in agent.filesystem_rules], ["filesystem_write"])
        self.assertEqual([r.action for r in agent.cloud_disclosure_rules],
                         ["publish_artifact"])
        # A shell rule is never left in the generic tool rules as well.
        self.assertEqual([r.action for r in agent.tool_rules], [])
        self.assertEqual(profiles.KIND_OF["publish_artifact"], "cloud_disclosure_rules")

    def test_a_template_is_refused_when_its_rules_cannot_land(self):
        with self.assertRaises(profiles.Profile.__init__.__globals__["Hold"]):
            profiles.template("unknown-template")


class LivePolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name))
        self.profile = base(filesystem_rules=(Rule("filesystem_read", ("csv_analysis",),
                              ALLOW, approval="fixture", expires_at=100.0),))
        self.store.save(self.profile)

    def test_expiry_and_backward_clock(self):
        from unittest.mock import patch
        with patch.object(profiles.time, "time", return_value=90.0), \
             patch.object(profiles.time, "monotonic", return_value=10.0):
            live = self.store.bind_live(self.profile.id)
            self.assertEqual(live.evaluate("filesystem_read", ("csv_analysis",))[0], ALLOW)
        with patch.object(profiles.time, "time", return_value=80.0), \
             patch.object(profiles.time, "monotonic", return_value=21.0):
            self.assertEqual(live.evaluate("filesystem_read", ("csv_analysis",)),
                             (ASK, "approval_expired"))

    def test_external_policy_generation_change_refuses_even_same_version(self):
        live = self.store.bind_live(self.profile.id)
        path = self.store._path(self.profile.id)
        path.write_text(Store._dump(base(filesystem_rules=(Rule("filesystem_read",
                        ("csv_analysis",), DENY),))), encoding="utf-8")
        self.assertEqual(live.evaluate("filesystem_read", ("csv_analysis",)),
                         (DENY, "policy_changed"))

    def test_policy_replaced_during_clock_check_is_denied(self):
        from unittest.mock import patch
        live = self.store.bind_live(self.profile.id)
        def replace_policy():
            self.store.save(base(version=2, filesystem_rules=(
                Rule("filesystem_read", ("csv_analysis",), DENY),)))
            return 90.0
        with patch.object(self.store._clock, "now", side_effect=replace_policy):
            self.assertEqual(live.evaluate("filesystem_read", ("csv_analysis",)),
                             (DENY, "policy_changed"))

    def test_snapshot_identity_checkpoint_does_not_read_profile_contents(self):
        from unittest.mock import patch
        _, stamp = self.store.snapshot(self.profile.id)
        with patch.object(self.store, "_read_profile") as read:
            self.assertTrue(self.store.is_current(self.profile.id, stamp))
            read.assert_not_called()
        self.store._write(self.store._path(self.profile.id), Store._dump(self.profile))
        self.assertFalse(self.store.is_current(self.profile.id, stamp))

    def test_removed_policy_fails_closed(self):
        live = self.store.bind_live(self.profile.id)
        self.store._path(self.profile.id).unlink()
        self.assertFalse(live.unchanged())
        self.assertEqual(live.evaluate("filesystem_read", ("csv_analysis",)),
                         (DENY, "policy_changed"))


class GrantInheritanceTests(unittest.TestCase):
    def source(self):
        return base(id="private", filesystem_rules=(
            Rule("filesystem_read", ("file",), ALLOW, "expired", 99),
            Rule("filesystem_read", ("live",), ALLOW, "current", 1e12),
            Rule("filesystem_read", ("timed",), ALLOW, expires_at=99),
            Rule("filesystem_read", ("plain",), ALLOW),
            Rule("filesystem_write", ("project",), DENY),
            Rule("filesystem_write", ("notes",), ASK),
        ), network_rules=(Rule("browser_navigate", ("public",), ALLOW),))

    def assert_no_copied_grants(self, profile):
        for rule in profile.rules():
            self.assertNotEqual(rule.decision, ALLOW)
            self.assertEqual((rule.approval, rule.expires_at), ("", 0))
        self.assertEqual(effective(profile).evaluate("filesystem_write", ("project",))[0], DENY)
        self.assertEqual(effective(profile).evaluate("filesystem_write", ("notes",))[0], ASK)

    def test_clone_requires_new_authorization_even_for_plain_or_live_allow(self):
        source = self.source()
        clone = clone_to_custom(source, "Clone")
        self.assert_no_copied_grants(clone)
        for scope in ("file", "live", "timed", "plain"):
            with self.subTest(scope=scope), self.assertRaisesRegex(Hold, "approval_required"):
                profiles.require_tool(clone, "filesystem_read", (scope,), now=100, selected=True)
        self.assertEqual(effective(clone).evaluate("browser_navigate", ("public",))[0], ASK)
        self.assertEqual(effective(source).evaluate("filesystem_read", ("live",), now=100)[0], ALLOW)

    def test_sanitization_never_launders_an_allow(self):
        clean = sanitized(self.source())
        self.assertEqual(clean.state, "pending_review")
        self.assert_no_copied_grants(clean)

    def test_rollback_requires_reauthorization_even_for_historical_allow(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            source = replace(self.source(), voice_activation="wake_word",
                             background_processing_enabled=True)
            store.save(source)
            store.save(replace(source, version=2, filesystem_rules=(), network_rules=()))
            rolled = store.rollback(source.id, 1)
            self.assertEqual(rolled.version, 3)
            self.assert_no_copied_grants(rolled)
            self.assertEqual(rolled.voice_activation, "off")
            self.assertFalse(rolled.background_processing_enabled)
            with self.assertRaisesRegex(Hold, "approval_required"):
                profiles.require_tool(store.load(source.id), "filesystem_read", ("file",), now=100)


class LiveExpiryHighWaterTests(unittest.TestCase):
    def test_observed_forward_expiry_cannot_revive_after_clock_correction(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            profile = base(filesystem_rules=(Rule("filesystem_read", ("csv_analysis",),
                           ALLOW, approval="fixture", expires_at=100),))
            store.save(profile)
            with (patch.object(profiles.time, "time", return_value=90),
                  patch.object(profiles.time, "monotonic", return_value=10)):
                live = store.bind_live(profile.id)
            with (patch.object(profiles.time, "time", return_value=120),
                  patch.object(profiles.time, "monotonic", return_value=11)):
                self.assertEqual(live.evaluate("filesystem_read", ("csv_analysis",)),
                                 (ASK, "approval_expired"))
            with (patch.object(profiles.time, "time", return_value=91),
                  patch.object(profiles.time, "monotonic", return_value=12)):
                self.assertEqual(live.evaluate("filesystem_read", ("csv_analysis",)),
                                 (ASK, "approval_expired"))


class SharedPolicyClockTests(unittest.TestCase):
    def test_new_binding_and_direct_gate_share_irreversible_expiry(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            profile = base(tool_rules=(Rule("local_audio_device", ("push_to_talk",),
                           ALLOW, approval="fixture", expires_at=110),))
            store.save(profile)
            wall, mono = [100], [50]
            with (patch.object(profiles.time, "time", side_effect=lambda: wall[0]),
                  patch.object(profiles.time, "monotonic", side_effect=lambda: mono[0])):
                live = store.bind_live(profile.id)
                self.assertEqual(live.evaluate("local_audio_device", ("push_to_talk",))[0], ALLOW)
                wall[0], mono[0] = 120, 51
                self.assertEqual(live.evaluate("local_audio_device", ("push_to_talk",)),
                                 (ASK, "approval_expired"))
                wall[0], mono[0] = 101, 52
                rebound = store.bind_live(profile.id)
                self.assertGreaterEqual(store.policy_now(), 121)
                self.assertEqual(rebound.evaluate("local_audio_device", ("push_to_talk",)),
                                 (ASK, "approval_expired"))
                with self.assertRaisesRegex(Hold, "approval_expired"):
                    profiles.require_tool(store.load(profile.id), "local_audio_device",
                                          ("push_to_talk",), store.policy_now())

    def test_clone_and_import_do_not_copy_automatic_activation(self):
        source = base(id="private", voice_activation="wake_word",
                      background_processing_enabled=True)
        for copied in (clone_to_custom(source, "Clone"), sanitized(source)):
            self.assertEqual(copied.voice_activation, "off")
            self.assertFalse(copied.background_processing_enabled)


class BoundedProfileReadTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.store = Store(Path(root.name))
        self.path = self.store._path("local-only")

    def test_fifo_symlink_and_oversize_refused_before_content_reader(self):
        import os
        from unittest.mock import patch
        target = self.store.root / "fixture"
        target.write_text(Store._dump(base()), encoding="utf-8")
        for kind in ("fifo", "symlink", "oversize"):
            with self.subTest(kind=kind):
                if kind == "fifo":
                    os.mkfifo(self.path)
                elif kind == "symlink":
                    self.path.symlink_to(target)
                else:
                    self.path.write_bytes(b"x" * (Store.LIMIT * 4 + 1))
                try:
                    with patch.object(profiles, "read_selected_bytes") as reader:
                        with self.assertRaisesRegex(Hold, "profile_unreadable"):
                            self.store.load("local-only")
                        with self.assertRaisesRegex(Hold, "profile_unreadable"):
                            self.store.bind_live("local-only")
                        reader.assert_not_called()
                finally:
                    self.path.unlink()

    def test_profile_import_rejects_special_and_oversize_files_before_read(self):
        import os
        from unittest.mock import patch
        source = self.store.root / "import.json"
        target = self.store.root / "fixture"
        target.write_text(Store._dump(base()))
        for kind in ("fifo", "symlink", "oversize"):
            with self.subTest(kind=kind):
                if kind == "fifo":
                    os.mkfifo(source)
                elif kind == "symlink":
                    source.symlink_to(target)
                else:
                    source.write_bytes(b"x" * (Store.LIMIT * 4 + 1))
                try:
                    with patch.object(profiles, "read_selected_bytes") as reader:
                        with self.assertRaisesRegex(Hold, "profile_import_rejected"):
                            self.store.import_file(str(source))
                        reader.assert_not_called()
                finally:
                    source.unlink()
        source.write_text(Store._dump(base()))
        self.assertEqual(self.store.import_file(str(source)).id, "local-only")

    def test_replacement_between_stamp_and_read_is_refused(self):
        from unittest.mock import patch
        self.store.save(base())
        reader = profiles.read_selected_bytes
        def replaced(path, **kwargs):
            self.path.rename(self.path.with_suffix(".old"))
            self.path.write_text(Store._dump(base(version=2)), encoding="utf-8")
            return reader(path, **kwargs)
        with patch.object(profiles, "read_selected_bytes", side_effect=replaced):
            with self.assertRaisesRegex(Hold, "profile_unreadable"):
                self.store.load("local-only")

    def test_malformed_fields_and_encoding_fail_closed(self):
        for raw in (b"{}", b"[]", b"\xff"):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaisesRegex(Hold, "profile_unreadable"):
                    self.store.load("local-only")


class StrictProfileDecodeTests(unittest.TestCase):
    def test_no_implicit_conversion_of_security_relevant_fields(self):
        original = json.loads(Store._dump(base()))
        for key, value in (
                ("background_processing_enabled", "false"),
                ("background_processing_enabled", 1),
                ("version", True), ("version", "1"), ("version", 1.5),
                ("credential_refs", "key"), ("allowed_providers", {"x": True}),
                ("voice_by_language", []), ("rules", {}),
                ("created_at", float("inf")), ("updated_at", True)):
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(Hold, "profile_unreadable"):
                    Store._parse(json.dumps(dict(original, **{key: value})))

    def test_rule_fields_and_duplicate_keys_are_rejected(self):
        original = json.loads(Store._dump(base()))
        rule = {"action": "local_audio_device", "decision": ALLOW,
                "scope": [], "approval": "", "expires_at": 0}
        for key, value in (("scope", "push_to_talk"), ("scope", [True]),
                           ("approval", 0), ("expires_at", True),
                           ("expires_at", "120"), ("expires_at", float("nan")),
                           ("action", [])):
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(Hold, "profile_unreadable"):
                    Store._parse(json.dumps(dict(original, rules=[dict(rule, **{key: value})])))
        for raw in ('{"id":"a","id":"b"}',
                    '{"rules":[{"decision":"deny","decision":"allow"}]}'):
            with self.assertRaisesRegex(Hold, "profile_unreadable"):
                Store._parse(raw)

    def test_save_does_not_emit_non_roundtrippable_policy(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        store = Store(root.name)
        with self.assertRaisesRegex(Hold, "profile_unreadable"):
            store.save(base(background_processing_enabled="false"))
        self.assertEqual(store.ids(), ())


class RestartPolicyClockTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.store = Store(self.root)
        self.profile = base(tool_rules=(Rule("local_audio_device", ("push_to_talk",),
                            ALLOW, approval="fixture", expires_at=110),))
        self.store.save(self.profile)
        self.scope = ("push_to_talk",)

    def test_observed_expiry_survives_reopen_with_earlier_wall_clock(self):
        from unittest.mock import patch
        with (patch.object(profiles.time, "time", return_value=100),
              patch.object(profiles.time, "monotonic", return_value=50)):
            live = self.store.bind_live(self.profile.id)
            self.assertEqual(live.evaluate("local_audio_device", self.scope)[0], ALLOW)
        with (patch.object(profiles.time, "time", return_value=120),
              patch.object(profiles.time, "monotonic", return_value=51)):
            self.assertEqual(live.evaluate("local_audio_device", self.scope),
                             (ASK, "approval_expired"))
        with (patch.object(profiles.time, "time", return_value=101),
              patch.object(profiles.time, "monotonic", return_value=1)):
            reopened = Store(self.root)
            self.assertGreaterEqual(reopened.policy_now(), 120)
            self.assertEqual(reopened.bind_live(self.profile.id).evaluate(
                "local_audio_device", self.scope), (ASK, "approval_expired"))
            with self.assertRaisesRegex(Hold, "approval_expired"):
                profiles.require_tool(reopened.load(self.profile.id), "local_audio_device",
                                      self.scope, reopened.policy_now())
        self.assertNotIn(".policy-clock", self.store.ids())
        self.assertEqual((self.root / ".policy-clock").stat().st_mode & 0o077, 0)

    def test_invalid_checkpoint_denies_timed_allow(self):
        path = self.root / ".policy-clock"
        path.write_bytes(b"not a clock")
        self.assertEqual(self.store.bind_live(self.profile.id).evaluate(
            "local_audio_device", self.scope), (ASK, "approval_expired"))

    def test_failed_checkpoint_write_latches_timed_denial(self):
        from unittest.mock import patch
        with patch.object(self.store._clock, "_persist", side_effect=OSError("fixture")):
            self.assertEqual(self.store.bind_live(self.profile.id).evaluate(
                "local_audio_device", self.scope), (ASK, "approval_expired"))
        self.assertEqual(self.store.bind_live(self.profile.id).evaluate(
            "local_audio_device", self.scope), (ASK, "approval_expired"))

    def test_checkpoint_reserves_ceiling_without_writing_each_check(self):
        from unittest.mock import patch
        with (patch.object(profiles.time, "time", return_value=100.1),
              patch.object(profiles.time, "monotonic", side_effect=[10.0, 10.1, 10.2]),
              patch.object(self.store._clock, "_persist", wraps=self.store._clock._persist) as persist):
            for _ in range(3):
                self.assertLess(self.store.policy_now(), 101)
            persist.assert_called_once_with(101)

    def test_two_stores_cannot_overwrite_or_authorize_below_newer_floor(self):
        from unittest.mock import patch
        other = Store(self.root)
        with (patch.object(profiles.time, "time", return_value=100),
              patch.object(profiles.time, "monotonic", return_value=10)):
            old = self.store.bind_live(self.profile.id)
            newer = other.bind_live(self.profile.id)
            self.assertEqual(old.evaluate("local_audio_device", self.scope)[0], ALLOW)
        with (patch.object(profiles.time, "time", return_value=120),
              patch.object(profiles.time, "monotonic", return_value=11)):
            self.assertEqual(newer.evaluate("local_audio_device", self.scope)[0], ASK)
        with (patch.object(profiles.time, "time", return_value=102),
              patch.object(profiles.time, "monotonic", return_value=12)):
            self.assertEqual(old.evaluate("local_audio_device", self.scope),
                             (ASK, "approval_expired"))
            self.assertGreaterEqual(int((self.root / ".policy-clock").read_text()), 120)
            self.assertEqual(Store(self.root).bind_live(self.profile.id).evaluate(
                "local_audio_device", self.scope)[0], ASK)

    def test_deleted_or_lowered_checkpoint_fails_closed_in_observing_store(self):
        from unittest.mock import patch
        with (patch.object(profiles.time, "time", return_value=100),
              patch.object(profiles.time, "monotonic", return_value=10)):
            live = self.store.bind_live(self.profile.id)
            self.assertEqual(live.evaluate("local_audio_device", self.scope)[0], ALLOW)
            path = self.root / ".policy-clock"
            path.unlink()
            self.assertEqual(live.evaluate("local_audio_device", self.scope)[0], ASK)
            path.write_text("100\n")
            self.assertEqual(live.evaluate("local_audio_device", self.scope)[0], ASK)
            other = Store(self.root)
            second = other.bind_live(self.profile.id)
            self.assertEqual(second.evaluate("local_audio_device", self.scope)[0], ALLOW)
            path.write_text("99\n")
            self.assertEqual(second.evaluate("local_audio_device", self.scope)[0], ASK)

    def test_missing_initialized_checkpoint_refused_after_reopen(self):
        from unittest.mock import patch
        with (patch.object(profiles.time, "time", return_value=120),
              patch.object(profiles.time, "monotonic", return_value=10)):
            self.assertEqual(self.store.bind_live(self.profile.id).evaluate(
                "local_audio_device", self.scope)[0], ASK)
        marker = self.root / ".policy-clock-initialized"
        self.assertTrue(marker.is_file())
        self.assertEqual(marker.stat().st_mode & 0o077, 0)
        (self.root / ".policy-clock").unlink()
        with (patch.object(profiles.time, "time", return_value=100),
              patch.object(profiles.time, "monotonic", return_value=1)):
            reopened = Store(self.root)
            self.assertEqual(reopened.bind_live(self.profile.id).evaluate(
                "local_audio_device", self.scope)[0], ASK)
            self.assertFalse((self.root / ".policy-clock").exists())

    def test_first_checkpoint_failure_is_not_fresh_store_on_restart(self):
        from unittest.mock import patch
        with (patch.object(profiles.time, "time", return_value=100),
              patch.object(profiles.time, "monotonic", return_value=10),
              patch.object(self.store._clock, "_persist", side_effect=OSError("fixture"))):
            self.assertEqual(self.store.bind_live(self.profile.id).evaluate(
                "local_audio_device", self.scope)[0], ASK)
            self.assertEqual(Store(self.root).bind_live(self.profile.id).evaluate(
                "local_audio_device", self.scope)[0], ASK)

    def test_intact_legacy_checkpoint_is_migrated_without_reset(self):
        from unittest.mock import patch
        (self.root / ".policy-clock").write_text("120\n")
        with (patch.object(profiles.time, "time", return_value=100),
              patch.object(profiles.time, "monotonic", return_value=10)):
            self.assertEqual(self.store.bind_live(self.profile.id).evaluate(
                "local_audio_device", self.scope)[0], ASK)
        self.assertTrue((self.root / ".policy-clock-initialized").is_file())
        self.assertEqual((self.root / ".policy-clock").read_text(), "120\n")


    def test_contended_checkpoint_lock_fails_closed_without_waiting(self):
        import fcntl
        import os
        from unittest.mock import patch
        with (patch.object(profiles.time, "time", return_value=100),
              patch.object(profiles.time, "monotonic", return_value=10)):
            live = self.store.bind_live(self.profile.id)
            self.assertEqual(live.evaluate("local_audio_device", self.scope)[0], ALLOW)
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(live.evaluate("local_audio_device", self.scope)[0], ASK)
            finally:
                os.close(fd)
            self.assertEqual(live.evaluate("local_audio_device", self.scope)[0], ASK)


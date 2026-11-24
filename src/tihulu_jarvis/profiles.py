# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Operating modes and enforceable saved profiles.

This module describes the policy envelope of a profile. It starts no
process, reads no model and touches no network: it only proves that the
four operating modes, per-profile scoped rules, global laws, import
sanitization, approval expiry and rollback behave as specified.

Decision order (fail closed):
  1. a global law cannot be granted by any profile, preset or import;
  2. an action unsupported by the current mode is denied by the mode;
  3. the most specific matching rule wins; on equal specificity deny wins;
  4. an approval that has expired degrades to ASK, never to silent ALLOW.
"""
import json
import os
import re
import tempfile
import stat
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from .voice.m1 import Hold

MODES = ("LOCAL_ONLY", "CLOUD_CHAT", "SECURE_HYBRID", "CUSTOM")
LANGUAGES = ("tr", "en", "de")
ACTIVATION = ("off", "push_to_talk", "wake_word")
STATES = ("active", "disabled", "pending_review")
ALLOW, ASK, DENY = "allow", "ask", "deny"
DECISIONS = (ALLOW, ASK, DENY)

# Structural law. No profile, preset, import or rollback may grant these.
GLOBAL_DENIES = frozenset((
    "credential_material_in_profile_data",
    "silent_capability_inheritance",
))

CAPABILITIES = {
    "LOCAL_ONLY": {"cloud_inference": False, "cloud_disclosure": False, "external_network": None,
                   "privileged_local_tools": ()},
    "CLOUD_CHAT": {"cloud_inference": True, "cloud_disclosure": True, "external_network": ("provider",),
                   "privileged_local_tools": ()},
    "SECURE_HYBRID": {"cloud_inference": True, "cloud_disclosure": True,
                      "external_network": ("provider", "authorized_local_worker"),
                      "privileged_local_tools": ("local_policy_bridge",)},
    "CUSTOM": {"cloud_inference": True, "cloud_disclosure": True, "external_network": ("constrained",),
               "privileged_local_tools": ("scoped",)},
}
RULE_KINDS = ("tool_rules", "network_rules", "filesystem_rules", "shell_rules", "cloud_disclosure_rules")
RULE_ACTIONS = frozenset((
    "cloud_inference", "cloud_disclosure", "external_network", "host_shell",
    "local_audio_device", "filesystem_read", "filesystem_write", "history_transfer",
    "wake_word", "background_processing", "publish_artifact", "browser_navigate",
))
REF = re.compile(r"[A-Za-z0-9_-]{1,64}$")
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}$")


@dataclass(frozen=True)
class Rule:
    """One scoped permission. ``scope`` narrows a rule; narrower wins."""
    action: str
    scope: tuple = ()
    decision: str = DENY
    approval: str = ""        # opaque reference, never secret material
    expires_at: float = 0.0

    def matches(self, action, scope):
        return self.action == action and set(scope) >= set(self.scope)


def decide(profile_rules, action, scope=(), now=None):
    """Return (decision, reason). Unmatched or unknown actions are denied."""
    if now is None:
        now = time.time()
    if action in GLOBAL_DENIES:
        return DENY, "global_deny"
    if action not in RULE_ACTIONS:
        return DENY, "unknown_action"
    best, best_len = None, -1
    for rule in profile_rules:
        if not rule.matches(action, scope):
            continue
        if len(rule.scope) > best_len or (len(rule.scope) == best_len and rule.decision == DENY):
            best, best_len = rule, len(rule.scope)
    if best is None:
        return DENY, "no_rule_matches"
    if best.decision == ALLOW and best.approval and best.expires_at <= now:
        return ASK, "approval_expired"
    return best.decision, "rule"


@dataclass(frozen=True)
class Profile:
    id: str
    version: int = 1
    name: str = ""
    description: str = ""
    based_on_mode: str = "LOCAL_ONLY"
    ui_locale: str = "tr"
    stt_model_id: str = ""
    llm_model_id: str = ""
    tts_model_id: str = ""
    voice_by_language: dict = field(default_factory=dict)
    allowed_providers: tuple = ()
    credential_refs: tuple = ()
    tool_rules: tuple = ()
    network_rules: tuple = ()
    filesystem_rules: tuple = ()
    shell_rules: tuple = ()
    cloud_disclosure_rules: tuple = ()
    voice_activation: str = "off"
    background_processing_enabled: bool = False
    state: str = "active"
    created_at: float = 0.0
    updated_at: float = 0.0

    def rules(self):
        out = []
        for kind in RULE_KINDS:
            out.extend(getattr(self, kind))
        return tuple(out)

    def checked(self):
        if not isinstance(self.id, str) or not ID.fullmatch(self.id):
            raise Hold("invalid_profile_id")
        if self.based_on_mode not in MODES:
            raise Hold("unknown_operating_mode")
        if not isinstance(self.version, int) or not 0 < self.version <= 1000000:
            raise Hold("invalid_profile_version")
        if self.state not in STATES:
            raise Hold("invalid_profile_state")
        if self.ui_locale not in LANGUAGES or self.voice_activation not in ACTIVATION:
            raise Hold("invalid_locale_or_activation")
        if set(self.voice_by_language) - set(LANGUAGES):
            raise Hold("unsupported_voice_language")
        if any(not isinstance(v, str) or not v for v in self.voice_by_language.values()):
            raise Hold("invalid_voice_reference")
        if len(self.name) > 80 or len(self.description) > 400:
            raise Hold("profile_text_too_long")
        for key in ("stt_model_id", "llm_model_id", "tts_model_id"):
            value = getattr(self, key)
            if not isinstance(value, str) or len(value) > 256:
                raise Hold("invalid_local_model_choice")
        for ref in self.credential_refs:
            if not isinstance(ref, str) or not REF.fullmatch(ref):
                raise Hold("credential_material_in_profile_data")
        for provider in self.allowed_providers:
            if not isinstance(provider, str) or len(provider) > 64 or "/" in provider:
                raise Hold("invalid_provider_reference")
        seen = set()
        for rule in self.rules():
            if not isinstance(rule, Rule):
                raise Hold("invalid_rule")
            if rule.action in GLOBAL_DENIES:
                # Law is never grantable, whatever the scope or mode.
                raise Hold("global_deny_not_weakable")
            if rule.action not in RULE_ACTIONS or rule.decision not in DECISIONS:
                raise Hold("invalid_rule")
            if any(not isinstance(s, str) or len(s) > 96 or not s or "~" in s for s in rule.scope):
                raise Hold("invalid_rule_scope")
            if rule.approval and not REF.fullmatch(rule.approval):
                raise Hold("invalid_approval_reference")
            if not 0 <= rule.expires_at <= 1e12:
                raise Hold("invalid_approval_expiry")
            key = (rule.action, rule.scope)
            if key in seen:
                raise Hold("duplicate_rule")
            seen.add(key)
        return self


@dataclass(frozen=True)
class EffectivePolicy:
    """Immutable per-task snapshot: a task never rides a mutated profile."""
    profile_id: str
    version: int
    mode: str
    rules: tuple
    capabilities: tuple

    def supports(self, action):
        return action in self.capabilities

    def evaluate(self, action, scope=(), now=None):
        if action in GLOBAL_DENIES:
            return DENY, "global_deny"
        if action not in RULE_ACTIONS:
            return DENY, "unknown_action"
        if not self.supports(action):
            return DENY, "mode_without_capability"
        return decide(self.rules, action, scope, now)


def effective(profile):
    """Mode selection changes capabilities, not only a system prompt."""
    profile = profile.checked()
    caps = CAPABILITIES[profile.based_on_mode]
    unsupported = set(GLOBAL_DENIES)
    if not caps["cloud_inference"]:
        unsupported |= {"cloud_inference", "cloud_disclosure"}
    if not caps["privileged_local_tools"]:
        unsupported |= {"host_shell"}
    if caps["external_network"] is None:
        unsupported |= {"external_network", "browser_navigate"}
    return EffectivePolicy(profile.id, profile.version, profile.based_on_mode, profile.rules(),
                           tuple(sorted(set(RULE_ACTIONS) - unsupported)))


def _policy_stamp(path):
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_size > Store.LIMIT * 4:
        raise Hold("profile_unreadable")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode)


@dataclass(frozen=True)
class LivePolicy:
    """Bounded policy snapshot checked without GTK or model access.

    External edits/replacements are detected at each checkpoint. A monotonic
    floor prevents a backward wall-clock adjustment extending a grant.
    """
    policy: EffectivePolicy
    path: Path
    stamp: tuple
    wall: float
    monotonic: float

    def unchanged(self):
        try:
            return _policy_stamp(self.path) == self.stamp
        except (OSError, Hold):
            return False

    def evaluate(self, action, scope=()):
        if not self.unchanged():
            return DENY, "policy_changed"
        now = max(time.time(), self.wall + max(0, time.monotonic() - self.monotonic))
        return self.policy.evaluate(action, scope, now)


def clone_to_custom(profile, name):
    """Customizing a protected preset means a CUSTOM clone, not a weaken preset."""
    profile = profile.checked()
    if profile.based_on_mode == "CUSTOM":
        raise Hold("already_custom")
    carried = tuple(r for r in profile.rules()
                    if r.action in ("filesystem_read", "filesystem_write", "browser_navigate"))
    # An owner's approval is a grant, not an inherited token: the clone re-asks.
    carried = tuple(replace(r, approval="", expires_at=0.0) for r in carried)
    filesystem = tuple(r for r in carried if r.action.startswith("filesystem_"))
    network = tuple(r for r in carried if r.action == "browser_navigate")
    out = replace(profile, id=profile.id + "-custom", name=name, based_on_mode="CUSTOM",
                  version=1, state="active", credential_refs=(), allowed_providers=(),
                  tool_rules=(), network_rules=network, filesystem_rules=filesystem,
                  shell_rules=(), cloud_disclosure_rules=())
    return out.checked()


PRIVATE = ("home", "users", "root")


def sanitized(profile):
    """Import/export view: no credentials, approvals, private paths or grants."""
    def clean(rules):
        out = []
        for rule in rules:
            if any(part.lower() in PRIVATE or part.startswith("/") or part.startswith("~")
                   for part in rule.scope):
                continue  # workstation-specific scope never travels
            out.append(replace(rule, approval="", expires_at=0.0))
        return tuple(out)

    out = replace(profile, state="pending_review", credential_refs=(), allowed_providers=(),
                  tool_rules=clean(profile.tool_rules), network_rules=clean(profile.network_rules),
                  filesystem_rules=clean(profile.filesystem_rules), shell_rules=(),
                  cloud_disclosure_rules=clean(profile.cloud_disclosure_rules))
    # Privileged rules arrive denied until the owner re-authorizes them.
    return replace(out, shell_rules=(Rule("host_shell", (), DENY),)).checked()


def profile_dir():
    base = os.environ.get("XDG_DATA_HOME")
    folder = Path(base) if base and Path(base).is_absolute() else Path.home() / ".local" / "share"
    return folder / "tihulu-jarvis" / "profiles"


KIND_OF = {
    "host_shell": "shell_rules",
    "external_network": "network_rules",
    "filesystem_read": "filesystem_rules",
    "filesystem_write": "filesystem_rules",
    "publish_artifact": "cloud_disclosure_rules",
}


def template(key):
    """Permission-light starting point. Accepting one is the owner's decision."""
    specs = {
        "private-assistant": ("Private Assistant", "LOCAL_ONLY",
                              (Rule("local_audio_device", ("push_to_talk",), ALLOW,
                                    approval="owner-consent", expires_at=1e12),)),
        "daily-assistant": ("Daily Assistant", "LOCAL_ONLY",
                            (Rule("local_audio_device", ("push_to_talk",), ALLOW,
                                  approval="owner-consent", expires_at=1e12),
                             Rule("wake_word", ("always",), DENY))),
        "research-assistant": ("Research Assistant", "CLOUD_CHAT",
                               (Rule("external_network", ("provider",), ALLOW),
                                Rule("filesystem_read", ("notes",), ALLOW),
                                Rule("host_shell", ("bridge",), DENY))),
        "coding-agent": ("Coding Agent", "SECURE_HYBRID",
                         (Rule("host_shell", ("jan-qwen",), ALLOW,
                               approval="owner-consent", expires_at=1e12),
                          Rule("filesystem_write", ("project",), ALLOW),
                          Rule("publish_artifact", ("repo",), ASK))),
        "custom-developer": ("Custom Developer", "CUSTOM",
                             (Rule("host_shell", ("bridge",), DENY),
                              Rule("filesystem_read", ("project",), ALLOW))),
    }
    if key not in specs:
        raise Hold("unknown_profile_template")
    name, mode, rules = specs[key]
    # Each rule belongs to exactly one kind. Copying a rule into two lists
    # would read as a duplicate when the kinds are concatenated, so the bucket
    # is decided by the action rather than by repeating the rule.
    buckets = {kind: [] for kind in RULE_KINDS}
    for rule in rules:
        buckets[KIND_OF.get(rule.action, "tool_rules")].append(rule)
    return Profile(id=key, name=name, based_on_mode=mode, version=1,
                  tool_rules=tuple(buckets["tool_rules"]),
                  shell_rules=tuple(buckets["shell_rules"]),
                  network_rules=tuple(buckets["network_rules"]),
                  filesystem_rules=tuple(buckets["filesystem_rules"]),
                  cloud_disclosure_rules=tuple(buckets["cloud_disclosure_rules"]),
                  voice_activation="push_to_talk" if key in ("private-assistant", "daily-assistant") else "off",
                  background_processing_enabled=False).checked()


TEMPLATES = ("private-assistant", "daily-assistant", "research-assistant",
             "coding-agent", "custom-developer")

MODE_TEXT = {
    "LOCAL_ONLY": "Local models only; no cloud inference or disclosure; host shell denied.",
    "CLOUD_CHAT": "Approved cloud reasoning; no privileged local tools, even if a model asks.",
    "SECURE_HYBRID": "Cloud reasoning plus an authorized local worker behind the policy bridge.",
    "CUSTOM": "Your configuration inside the global envelope; presets are never weakened.",
}


ENDPOINT_LIMIT = 4096


AUDIT_LIMIT = 512
AUDIT_BYTES = 262144


def audit_path(root):
    return Path(root) / "audit.jsonl"


def record_decision(root, profile_id, action, scope, decision, reason, now=0.0):
    """Append one policy decision. No secret, path or transcript is recorded."""
    if not isinstance(profile_id, str) or not ID.fullmatch(profile_id):
        raise Hold("invalid_profile_id")
    path = audit_path(root)
    row = {"at": now or time.time(), "profile": profile_id, "action": str(action)[:64],
           "scope": [str(s)[:64] for s in scope], "decision": str(decision),
           "reason": str(reason)[:80]}
    rows = []
    if path.is_file():
        try:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        except (ValueError, OSError):
            raise Hold("audit_unreadable") from None
    rows.append(row)
    if len(rows) > AUDIT_LIMIT:
        rows = rows[-AUDIT_LIMIT:]
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    if len(text.encode("utf-8")) > AUDIT_BYTES:
        raise Hold("audit_too_large")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="tihulu-audit-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def read_audit(root, limit=100, action=None, decision=None, profile=None):
    """The most recent decisions, oldest first. Never invented.

    A filter narrows the rows that already exist; it never adds one, and a
    filter that is not a short string is refused rather than guessed at.
    """
    for value, name in ((action, "audit_action_filter"), (decision, "audit_decision_filter"),
                        (profile, "audit_profile_filter")):
        if value is not None and (not isinstance(value, str) or not value or len(value) > 64):
            raise Hold(name)
    path = audit_path(root)
    if not path.is_file():
        return ()
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (ValueError, OSError):
        raise Hold("audit_unreadable") from None
    if action is not None:
        rows = [r for r in rows if r.get("action") == action]
    if decision is not None:
        rows = [r for r in rows if r.get("decision") == decision]
    if profile is not None:
        rows = [r for r in rows if r.get("profile") == profile]
    return tuple(rows[-limit:])


def audit_actions(root, limit=40):
    """The distinct actions a log already contains, in the order they appear.

    A filter is offered for what happened, never for a name the log cannot
    show.
    """
    seen = []
    for row in read_audit(root, AUDIT_LIMIT):
        action = row.get("action")
        if isinstance(action, str) and action not in seen:
            seen.append(action)
    return tuple(seen[:limit])


def require_tool(profile, action, scope=(), now=None, *, selected=False):
    """Explicit ASK is not permission; absent rules require a trusted selection.

    selected=True is only for an owner-initiated, narrowly scoped local picker
    or an already selected UI object, never for an agent or background action.
    It cannot override a denial, expired approval or mode restriction.
    """
    if profile.state != "active":
        raise Hold("profile_not_active")
    decision, reason = effective(profile).evaluate(action, scope, now)
    if decision == ASK:
        raise Hold("approval_expired" if reason == "approval_expired"
                   else "approval_required")
    if decision == ALLOW:
        return decision, reason
    if reason == "no_rule_matches" and selected is True:
        return ALLOW, "explicit_selection"
    raise Hold("profile_forbids_" + action if reason == "rule" else
               "approval_required" if reason == "no_rule_matches" else reason)


PRIVILEGES = {
    "cloud_inference": "Cloud reasoning",
    "cloud_disclosure": "Sending content to a provider",
    "host_shell": "Running local programs",
    "external_network": "Reaching other hosts",
    "background_processing": "Working while the app is idle",
    "wake_word": "Listening for a wake word",
}


def granted(profile):
    """Privileges the profile's policy actually grants, as stable tokens."""
    rules = profile.rules()
    out = set()
    # A mode grants a capability; a rule can still deny it.
    if profile.based_on_mode in ("CLOUD_CHAT", "SECURE_HYBRID", "CUSTOM"):
        out |= {"cloud_inference", "cloud_disclosure"}
    for action in PRIVILEGES:
        # A scoped allow still grants the capability; a deny removes it.
        if any(rule.action == action and rule.decision == "allow" for rule in rules):
            out.add(action)
        elif any(rule.action == action and rule.decision == "deny" for rule in rules):
            out.discard(action)
    if profile.voice_activation == "wake_word":
        out.add("wake_word")
    if profile.background_processing_enabled:
        out.add("background_processing")
    return frozenset(out)


def escalation(current, target):
    """Privileges the target would add. An empty result needs no confirmation.

    Privileges are compared by granted token, never by name, so renaming a
    profile cannot hide an escalation.
    """
    return frozenset(granted(target) - granted(current))


def describe(escalation_set):
    """Human-readable lines for a confirmation prompt; never invented."""
    return [PRIVILEGES[token] for token in sorted(escalation_set) if token in PRIVILEGES]


def enforce_endpoint(profile, endpoint):
    """The active mode decides what kind of endpoint may be used at all.

    This client's only transport is a numeric loopback Jan endpoint, so the
    modes make that explicit instead of promising capabilities that do not
    exist here.
    """
    if not isinstance(endpoint, str) or len(endpoint) > ENDPOINT_LIMIT or not endpoint.strip():
        raise Hold("invalid_local_settings")
    raw = endpoint.strip()
    try:
        from .voice.jan import endpoint as parse
        ip, port = parse(raw)
    except Hold:
        # A non-loopback address is only a capability question for modes that
        # actually carry a cloud route; this build has none.
        if profile.based_on_mode == "LOCAL_ONLY":
            raise Hold("local_only_forbids_non_loopback") from None
        raise Hold("cloud_transport_not_available") from None
    if profile.based_on_mode == "CLOUD_CHAT" and profile.allowed_providers:
        # A cloud mode names its providers; a bare address is not one of them.
        raise Hold("approved_provider_required")
    if profile.based_on_mode == "LOCAL_ONLY" and profile.allowed_providers:
        raise Hold("local_only_forbids_providers")
    return raw


def ensure_default(store):
    """Create the permission-light default only when no profile exists."""
    if store.ids():
        return None
    profile = template("private-assistant")
    store.save(profile)
    return profile


class Store:
    """Profile storage with version history. No arbitrary cap is imposed."""

    LIMIT = 32768

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.history = self.root / "history"
        self.history.mkdir(mode=0o700, exist_ok=True)

    def _path(self, profile_id):
        if not isinstance(profile_id, str) or not ID.fullmatch(profile_id):
            raise Hold("invalid_profile_id")
        return self.root / (profile_id + ".json")

    @staticmethod
    def _dump(profile):
        out = {"id": profile.id, "version": profile.version, "name": profile.name,
               "description": profile.description, "based_on_mode": profile.based_on_mode,
               "ui_locale": profile.ui_locale, "stt_model_id": profile.stt_model_id,
               "llm_model_id": profile.llm_model_id, "tts_model_id": profile.tts_model_id,
               "voice_by_language": dict(profile.voice_by_language),
               "allowed_providers": list(profile.allowed_providers),
               "credential_refs": list(profile.credential_refs),
               "voice_activation": profile.voice_activation,
               "background_processing_enabled": profile.background_processing_enabled,
               "state": profile.state, "created_at": profile.created_at, "updated_at": profile.updated_at,
               "rules": [{"action": r.action, "scope": list(r.scope), "decision": r.decision,
                          "approval": r.approval, "expires_at": r.expires_at} for r in profile.rules()]}
        return json.dumps(out, ensure_ascii=False, sort_keys=True)

    @classmethod
    def _parse(cls, text):
        if len(text) > cls.LIMIT:
            raise Hold("profile_too_large")
        data = json.loads(text)
        by_kind = {"tool_rules": [], "network_rules": [], "filesystem_rules": [],
                   "shell_rules": [], "cloud_disclosure_rules": []}
        for item in data.get("rules", []):
            rule = Rule(item["action"], tuple(item["scope"]), item["decision"],
                        item.get("approval", ""), item.get("expires_at", 0.0))
            if rule.action in GLOBAL_DENIES:
                raise Hold("global_deny_not_weakable")
            if rule.action == "host_shell":
                kind = "shell_rules"
            elif rule.action.startswith("filesystem_"):
                kind = "filesystem_rules"
            elif rule.action in ("cloud_disclosure", "history_transfer"):
                kind = "cloud_disclosure_rules"
            elif rule.action in ("external_network", "browser_navigate"):
                kind = "network_rules"
            else:
                kind = "tool_rules"
            by_kind[kind].append(rule)
        return Profile(data["id"], int(data["version"]), data.get("name", ""),
                       data.get("description", ""), data["based_on_mode"], data.get("ui_locale", "tr"),
                       data.get("stt_model_id", ""), data.get("llm_model_id", ""),
                       data.get("tts_model_id", ""), data.get("voice_by_language", {}),
                       tuple(data.get("allowed_providers", ())), tuple(data.get("credential_refs", ())),
                       tuple(by_kind["tool_rules"]), tuple(by_kind["network_rules"]),
                       tuple(by_kind["filesystem_rules"]), tuple(by_kind["shell_rules"]),
                       tuple(by_kind["cloud_disclosure_rules"]), data.get("voice_activation", "off"),
                       bool(data.get("background_processing_enabled", False)),
                       data.get("state", "active"), float(data.get("created_at", 0.0)),
                       float(data.get("updated_at", 0.0))).checked()

    def _write(self, path, text):
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(path.parent), delete=False) as f:
                tmp = f.name
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)

    def save(self, profile):
        profile = profile.checked()
        target = self._path(profile.id)
        if target.is_file():
            try:
                if self._parse(target.read_text(encoding="utf-8")).version >= profile.version:
                    raise Hold("version_must_increase")
            except FileNotFoundError:
                pass
            except (ValueError, OSError):
                raise Hold("profile_unreadable") from None
        self._write(target, self._dump(profile))
        self._write(self.history / (profile.id + "-v%d.json" % profile.version), self._dump(profile))
        return profile

    def load(self, profile_id):
        try:
            return self._parse(self._path(profile_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise Hold("profile_not_found") from None
        except (ValueError, OSError):
            raise Hold("profile_unreadable") from None

    def bind_live(self, profile_id):
        """Bind a checked active policy to the exact stored file generation."""
        path = self._path(profile_id)
        try:
            before = _policy_stamp(path)
            profile = self.load(profile_id)
            if profile.id != profile_id or profile.state != "active" or _policy_stamp(path) != before:
                raise Hold("profile_changed")
            return LivePolicy(effective(profile), path, before, time.time(), time.monotonic())
        except OSError:
            raise Hold("profile_unreadable") from None

    def ids(self):
        # A fixed-width slice would corrupt any name containing a dot.
        return tuple(sorted(p.stem for p in self.root.glob("*.json")))

    def versions(self, profile_id):
        stem = self._path(profile_id).stem
        out = []
        for path in sorted(self.history.glob(stem + "-v*.json")):
            try:
                out.append(self._parse(path.read_text(encoding="utf-8")))
            except (Hold, ValueError, OSError):
                out.append(path.name)
        return tuple(out)

    def rollback(self, profile_id, version):
        """Restore an earlier policy as a new version, never re-granting later ones."""
        latest = 0
        for candidate in self.versions(profile_id):
            if isinstance(candidate, Profile):
                latest = max(latest, candidate.version)
                if candidate.version == version:
                    target = candidate
        if latest and "target" in locals():
            return self.save(replace(target, version=latest + 1))
        raise Hold("profile_version_not_found")

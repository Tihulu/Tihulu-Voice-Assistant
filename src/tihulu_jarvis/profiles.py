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
import fcntl
import json
import math
import os
import re
import tempfile
import stat
import time
from contextlib import contextmanager
from threading import Lock
from dataclasses import dataclass, field, replace
from pathlib import Path

from .voice.m1 import Hold
from .selected_csv import read_selected_bytes

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
        rank = {ALLOW: 0, ASK: 1, DENY: 2}
        if len(rule.scope) > best_len or (len(rule.scope) == best_len and
                rank[rule.decision] > rank[best.decision]):
            best, best_len = rule, len(rule.scope)
    if best is None:
        return DENY, "no_rule_matches"
    if (best.decision == ALLOW and (best.approval or best.expires_at > 0)
            and best.expires_at <= now):
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


class PolicyClock:
    """Nondecreasing policy time with a shared restart floor for cooperating Stores.

    Persist a rounded-up one-second ceiling before returning an observed time.
    Cooperating Stores serialize checkpoint reads and updates using a local
    directory advisory lock. A durable initialization marker distinguishes
    a missing checkpoint from first use when the marker survives. No trusted
    elapsed time across shutdown or anti-tamper guarantee. An unavailable
    checkpoint or contended lock fails closed for timed permissions in that Store.
    """
    def __init__(self, wall=None, monotonic=None, *, checkpoint=None):
        self._lock = Lock()
        self._observed = wall
        self._monotonic = monotonic
        self._checkpoint = checkpoint
        self._reserved = None
        self._failed = False

    def _marker(self):
        return self._checkpoint.with_name(self._checkpoint.name + "-initialized")

    def _marker_exists(self):
        try:
            info = self._marker().lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(info.st_mode) or info.st_size != 0:
            raise Hold("policy_clock_unreadable")
        return True

    def _ensure_marker(self):
        # Called under the checkpoint lock. Persist the marker first: an
        # interrupted first write must not be interpreted as a fresh store.
        if self._marker_exists():
            return
        fd = os.open(self._marker(), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        fd = os.open(self._checkpoint.parent, os.O_RDONLY | os.O_DIRECTORY |
                     os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _restore(self):
        if self._checkpoint is None:
            return 0
        try:
            before = _policy_stamp(self._checkpoint)
        except FileNotFoundError:
            if self._marker_exists():
                raise Hold("policy_clock_unreadable")
            return 0  # Legacy/first use; not proof that no floor ever existed.
        if before[2] > 32:
            raise Hold("policy_clock_unreadable")
        raw = read_selected_bytes(str(self._checkpoint.absolute()),
                                  authorized=lambda: True, expected_identity=before)
        if not re.fullmatch(rb"[0-9]{1,13}\n", raw):
            raise Hold("policy_clock_unreadable")
        value = int(raw)
        if value > 10**12 or _policy_stamp(self._checkpoint) != before:
            raise Hold("policy_clock_unreadable")
        self._ensure_marker()  # Also migrate an intact legacy checkpoint.
        return value

    def _persist(self, value):
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="ascii",
                    dir=str(self._checkpoint.parent), prefix=".policy-clock-",
                    delete=False) as f:
                tmp = f.name
                f.write(str(value) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._checkpoint)
            tmp = None
            fd = os.open(self._checkpoint.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if tmp is not None:
                Path(tmp).unlink(missing_ok=True)

    @contextmanager
    def _checkpoint_lock(self):
        if self._checkpoint is None:
            yield
            return
        # Lock the directory inode, not the atomically replaced checkpoint.
        # No lock file to create, truncate or follow. Never wait on another
        # process from a UI authorization path.
        fd = os.open(self._checkpoint.parent, os.O_RDONLY | os.O_DIRECTORY |
                     os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def now(self):
        with self._lock:
            if self._failed:
                return float("inf")
            try:
                with self._checkpoint_lock():
                    wall, mono = time.time(), time.monotonic()
                    if not math.isfinite(wall) or not math.isfinite(mono):
                        raise Hold("policy_clock_unreadable")
                    # Reload under the same lock as the write. A stale Store
                    # must neither authorize below nor overwrite a newer floor.
                    disk = self._restore()
                    if self._reserved is None:
                        self._reserved = disk
                        self._observed = max(disk, self._observed or 0)
                    elif self._checkpoint is not None and disk < self._reserved:
                        # Missing or regressed after this Store saw a floor.
                        raise Hold("policy_clock_unreadable")
                    if self._monotonic is None:
                        self._observed = max(wall, self._observed)
                    else:
                        self._observed = max(wall, self._observed +
                                             max(0, mono - self._monotonic))
                    self._monotonic = mono if self._monotonic is None else max(mono, self._monotonic)
                    # Do not advance elapsed time twice when adopting a floor
                    # written by another process. Our own rounded reservation
                    # is not an observation until a new Store opens it.
                    if disk > self._reserved:
                        self._observed = max(self._observed, disk)
                        self._reserved = disk
                    if not 0 <= self._observed <= 10**12:
                        raise Hold("policy_clock_unreadable")
                    if self._checkpoint is not None and self._observed > self._reserved:
                        ceiling = math.ceil(self._observed)
                        self._ensure_marker()
                        self._persist(ceiling)
                        self._reserved = ceiling
                    return self._observed
            except (OSError, Hold, ValueError, OverflowError):
                # Do not revive a timed ALLOW after a failed clock check.
                # Untimed permissions retain their ordinary policy semantics.
                self._failed = True
                return float("inf")


@dataclass(frozen=True)
class LivePolicy:
    """Bounded policy snapshot checked without GTK or model access.

    External edits/replacements are detected at each checkpoint. A monotonic
    floor and shared nondecreasing clock prevent an observed expiry from
    being reversed by a wall-clock correction or a new binding in one Store.
    """
    policy: EffectivePolicy
    path: Path
    stamp: tuple
    wall: float
    monotonic: float
    clock: PolicyClock = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.clock is None:
            object.__setattr__(self, "clock", PolicyClock(self.wall, self.monotonic))

    def unchanged(self):
        try:
            return _policy_stamp(self.path) == self.stamp
        except (OSError, Hold):
            return False

    def evaluate(self, action, scope=()):
        if not self.unchanged():
            return DENY, "policy_changed"
        return self.policy.evaluate(action, scope, self.clock.now())


def _without_grant(rule):
    """A copied rule is a scope template, never a live authorization."""
    return replace(rule, decision=ASK if rule.decision == ALLOW else rule.decision,
                   approval="", expires_at=0.0)


def clone_to_custom(profile, name):
    """Customizing a protected preset means a CUSTOM clone, not a weaken preset."""
    profile = profile.checked()
    if profile.based_on_mode == "CUSTOM":
        raise Hold("already_custom")
    carried = tuple(r for r in profile.rules()
                    if r.action in ("filesystem_read", "filesystem_write", "browser_navigate"))
    # An owner's approval is a grant, not an inherited token: the clone re-asks.
    carried = tuple(_without_grant(r) for r in carried)
    filesystem = tuple(r for r in carried if r.action.startswith("filesystem_"))
    network = tuple(r for r in carried if r.action == "browser_navigate")
    out = replace(profile, id=profile.id + "-custom", name=name, based_on_mode="CUSTOM",
                  version=1, state="active", credential_refs=(), allowed_providers=(),
                  tool_rules=(), network_rules=network, filesystem_rules=filesystem,
                  shell_rules=(), cloud_disclosure_rules=(), voice_activation="off",
                  background_processing_enabled=False)
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
            out.append(_without_grant(rule))
        return tuple(out)

    out = replace(profile, state="pending_review", credential_refs=(), allowed_providers=(),
                  tool_rules=clean(profile.tool_rules), network_rules=clean(profile.network_rules),
                  filesystem_rules=clean(profile.filesystem_rules), shell_rules=(),
                  cloud_disclosure_rules=clean(profile.cloud_disclosure_rules),
                  voice_activation="off", background_processing_enabled=False)
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
    if now is None:
        now = time.time()
    # Preserve a specific explicit refusal reason even when the mode also
    # forbids that action. Never treat a mode denial as an approval.
    decision, reason = decide(profile.rules(), action, scope, now)
    if decision == DENY and reason == "rule":
        raise Hold("profile_forbids_" + action)
    decision, reason = effective(profile).evaluate(action, scope, now)
    if decision == ASK:
        raise Hold("approval_expired" if reason == "approval_expired"
                   else "approval_required")
    if decision == ALLOW:
        return decision, reason
    if (reason == "no_rule_matches" and selected is True and action == "filesystem_read"
            and scope in (("folder",), ("file",), ("recording",))):
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


def granted(profile, now=None):
    """Conservative capability summary for escalation/UI, not an I/O grant."""
    if profile.state != "active":
        return frozenset()
    if now is None:
        now = time.time()
    policy = effective(profile)
    rules = profile.rules()
    out = set()
    # A mode may advertise a future cloud capability; runtime remains gated.
    if profile.based_on_mode in ("CLOUD_CHAT", "SECURE_HYBRID", "CUSTOM"):
        out |= {"cloud_inference", "cloud_disclosure"}
    if profile.voice_activation == "wake_word":
        out.add("wake_word")
    if profile.background_processing_enabled:
        out.add("background_processing")
    for action in PRIVILEGES:
        scoped = tuple(rule for rule in rules if rule.action == action)
        if not policy.supports(action):
            out.discard(action)
        elif scoped:
            # Expired and ASK rules cannot suppress a subsequent escalation.
            if any(policy.evaluate(action, rule.scope, now)[0] == ALLOW for rule in scoped):
                out.add(action)
            else:
                out.discard(action)
    return frozenset(out)


def escalation(current, target, now=None):
    """Privileges the target would add. An empty result needs no confirmation.

    Privileges are compared by granted token, never by name, so renaming a
    profile cannot hide an escalation.
    """
    if now is None:
        now = time.time()
    return frozenset(granted(target, now) - granted(current, now))


def describe(escalation_set):
    """Human-readable lines for a confirmation prompt; never invented."""
    return [PRIVILEGES[token] for token in sorted(escalation_set) if token in PRIVILEGES]


def enforce_endpoint(profile, endpoint):
    """The active mode decides what kind of endpoint may be used at all.

    This client's only transport is a numeric loopback Jan endpoint, so the
    modes make that explicit instead of promising capabilities that do not
    exist here.
    """
    if profile.state != "active":
        raise Hold("profile_not_active")
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
        self._clock = PolicyClock(checkpoint=self.root / ".policy-clock")
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
        if not isinstance(data, dict):
            raise Hold("profile_unreadable")
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
            # Persist the directory entry as well as the file contents.
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY |
                         os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)

    def _read_profile(self, path):
        """Read a bounded regular inode, never a FIFO, device or symlink.

        Reuse the pinned local reader; policy storage has a stricter byte
        ceiling (four UTF-8 bytes per allowed JSON character). This guards
        file identity, not hostile same-UID or mount namespaces.
        """
        before = _policy_stamp(path)
        try:
            raw = read_selected_bytes(str(path.absolute()), authorized=lambda: True,
                                      allow_empty=True, expected_identity=before)
            if _policy_stamp(path) != before:
                raise Hold("profile_changed")
        except Hold:
            raise Hold("profile_unreadable") from None
        return self._parse(raw.decode("utf-8"))

    @contextmanager
    def _mutation_lock(self):
        """Serialize cooperating profile writers separately from policy time."""
        fd = None
        try:
            fd = os.open(self.history, os.O_RDONLY | os.O_DIRECTORY |
                         os.O_CLOEXEC | os.O_NOFOLLOW)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if fd is not None:
                os.close(fd)
            raise Hold("profile_store_busy_or_unavailable") from None
        try:
            yield
        finally:
            os.close(fd)

    def save(self, profile):
        with self._mutation_lock():
            return self._save_locked(profile)

    def _ensure_current_archive_locked(self, current):
        """Recover missing history from the authoritative active generation.

        Never restore active permissions from history or overwrite an
        existing archive. A missing archive can be repaired on a subsequent
        explicit mutation, before replacing the active generation.
        """
        archived = self.history / (current.id + "-v%d.json" % current.version)
        try:
            archived.lstat()
        except FileNotFoundError:
            try:
                self._write(archived, self._dump(current))
            except OSError:
                raise Hold("profile_history_write_failed") from None
        except OSError:
            raise Hold("profile_unreadable") from None

    def _save_locked(self, profile):
        profile = profile.checked()
        target = self._path(profile.id)
        text = self._dump(profile)
        if len(text) > self.LIMIT:
            raise Hold("profile_too_large")
        try:
            current = self._read_profile(target)
        except FileNotFoundError:
            current = None
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            raise Hold("profile_unreadable") from None
        if current is not None:
            if current.id != profile.id:
                raise Hold("profile_changed")
            if current.version >= profile.version:
                raise Hold("version_must_increase")
        archived = self.history / (profile.id + "-v%d.json" % profile.version)
        try:
            # Do not replace an existing historical generation, including a
            # dangling symlink. The active file and history are not a single
            # crash-atomic transaction.
            archived.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            raise Hold("profile_unreadable") from None
        else:
            raise Hold("version_must_increase")
        if current is not None:
            self._ensure_current_archive_locked(current)
        try:
            self._write(target, text)
        except OSError:
            raise Hold("profile_write_failed") from None
        try:
            self._write(archived, text)
        except OSError:
            raise Hold("profile_history_write_failed") from None
        return profile

    def load(self, profile_id):
        try:
            profile = self._read_profile(self._path(profile_id))
            if profile.id != profile_id:
                raise Hold("profile_changed")
            return profile
        except FileNotFoundError:
            raise Hold("profile_not_found") from None
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            raise Hold("profile_unreadable") from None

    def policy_now(self):
        return self._clock.now()

    def bind_live(self, profile_id):
        """Bind a checked active policy to the exact stored file generation."""
        path = self._path(profile_id)
        try:
            before = _policy_stamp(path)
            profile = self.load(profile_id)
            if profile.id != profile_id or profile.state != "active" or _policy_stamp(path) != before:
                raise Hold("profile_changed")
            return LivePolicy(effective(profile), path, before, self.policy_now(),
                              time.monotonic(), self._clock)
        except OSError:
            raise Hold("profile_unreadable") from None

    def ids(self):
        # A fixed-width slice would corrupt any name containing a dot.
        return tuple(sorted(p.stem for p in self.root.glob("*.json")))

    def versions(self, profile_id):
        stem = self._path(profile_id).stem
        out = []
        for path in sorted(self.history.glob(stem + "-v*.json")):
            if not re.fullmatch(re.escape(stem) + r"-v[1-9][0-9]*\.json", path.name):
                continue
            try:
                # A different profile ID can share the glob prefix. Only
                # accept a canonical filename for this exact generation.
                candidate = self._read_profile(path)
                if candidate.id != profile_id or path.name != (
                        stem + "-v%d.json" % candidate.version):
                    continue
                out.append(candidate)
            except (Hold, ValueError, OSError, KeyError, TypeError, RecursionError):
                out.append(path.name)
        return tuple(out)

    def rollback(self, profile_id, version):
        """Restore an earlier policy as a new version, never re-granting later ones."""
        with self._mutation_lock():
            return self._rollback_locked(profile_id, version)

    def _rollback_locked(self, profile_id, version):
        # Include the active version even if its history write failed.
        latest = self.load(profile_id).version
        for candidate in self.versions(profile_id):
            if isinstance(candidate, Profile):
                latest = max(latest, candidate.version)
                if candidate.version == version:
                    target = candidate
        if latest and "target" in locals():
            # Restoring descriptive policy must not resurrect revoked/expired
            # grants, or re-enable previously authorized background capture.
            rules = {kind: tuple(_without_grant(r) for r in getattr(target, kind))
                     for kind in RULE_KINDS}
            return self._save_locked(replace(target, version=latest + 1,
                                     credential_refs=(), allowed_providers=(),
                                     voice_activation="off",
                                     background_processing_enabled=False, **rules))
        raise Hold("profile_version_not_found")

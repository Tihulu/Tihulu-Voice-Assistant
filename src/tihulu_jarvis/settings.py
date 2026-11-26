# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Desktop-owned local model choices. Nothing is discovered, loaded or downloaded on startup."""
import hashlib
import json
import os
import re
from dataclasses import replace
import tempfile
import stat
from contextlib import contextmanager
from pathlib import Path

from . import catalog_sources, files, queries, resources, web
from .voice.m1 import Hold

LOCALES = ("tr", "en", "de")


def empty():
    return {"endpoint": "", "llm": "", "stt": "", "voices": {}, "profile": "",
            "market_refs": {}, "allowed_hosts": [], "text_scale": "normal",
            "web_targets": [], "show_spoken_text": False, "research_root": "",
            "search_history": [], "resources": {"profile": "balance"}, "sources": []}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME")
    folder = Path(base) if base and Path(base).is_absolute() else Path.home() / ".config"
    return folder / "tihulu-jarvis" / "models.json"


def checked(obj):
    if not isinstance(obj, dict):
        raise Hold("invalid_local_settings")
    out = empty()
    for k in ("endpoint", "llm", "stt"):
        v = obj.get(k, "")
        if not isinstance(v, str) or len(v) > 4096:
            raise Hold("invalid_local_settings")
        out[k] = v
    if not isinstance(obj.get("profile", ""), str) or len(obj.get("profile", "")) > 64:
        raise Hold("invalid_local_settings")
    out["profile"] = obj.get("profile", "")
    refs = obj.get("market_refs", {})
    if not isinstance(refs, dict) or len(refs) > 256:
        raise Hold("invalid_local_settings")
    clean = {}
    for ref, path in refs.items():
        # Digest references are opaque; the paired paths stay on this machine.
        if not isinstance(ref, str) or not isinstance(path, str) or len(ref) > 64 or len(path) > 4096:
            raise Hold("invalid_local_settings")
        clean[ref] = path
    out["market_refs"] = clean
    # Omit empty optional metadata to preserve old recovery-journal digests.
    digests = obj.get("audio_digests", {})
    if not isinstance(digests, dict) or len(digests) > 256:
        raise Hold("invalid_local_settings")
    for ref, digest in digests.items():
        if (ref not in clean or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or ref != "sha256-" + digest[:32]):
            raise Hold("invalid_local_settings")
    if digests:
        out["audio_digests"] = dict(digests)
    sidecars = obj.get("voice_digests", {})
    if not isinstance(sidecars, dict) or len(sidecars) > 256:
        raise Hold("invalid_local_settings")
    for ref, digest in sidecars.items():
        if (ref not in digests or not clean[ref].lower().endswith(".onnx")
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise Hold("invalid_local_settings")
    if sidecars:
        out["voice_digests"] = dict(sidecars)
    hosts = obj.get("allowed_hosts", [])
    if not isinstance(hosts, list) or len(hosts) > 32:
        raise Hold("invalid_local_settings")
    clean_hosts = []
    for host in hosts:
        if not isinstance(host, str) or len(host) > 253:
            raise Hold("invalid_local_settings")
        clean_hosts.append(host)
    out["allowed_hosts"] = clean_hosts
    voices = obj.get("voices", {})
    if not isinstance(voices, dict) or any(k not in LOCALES or not isinstance(v, str) or len(v)>4096 for k,v in voices.items()):
        raise Hold("invalid_local_settings")
    out["voices"] = dict(voices)
    targets = obj.get("web_targets", [])
    if not isinstance(targets, list) or len(targets) > 32:
        raise Hold("invalid_local_settings")
    clean_targets = []
    for item in targets:
        # Each target is re-validated on read: a stored entry the policy would
        # refuse today is not silently loaded, and its submission right is
        # stored with it rather than inferred from the URL again.
        if (not isinstance(item, dict) or set(item) - {"url", "submit"}
                or not isinstance(item.get("url"), str) or len(item.get("url", "")) > 400):
            raise Hold("invalid_local_settings")
        target = web.from_entry(item)
        clean_targets.append(web.entry(target))
    out["web_targets"] = clean_targets
    history = obj.get("search_history", [])
    # A stored history is re-validated here, so a corrupt one is refused rather
    # than loaded as a promise of queries that cannot be run again.
    out["search_history"] = list(queries.checked(tuple(history)))
    stored = obj.get("resources", {"profile": "balance"})
    # The resource profile is validated on read: an unknown id, or a custom
    # profile carrying an impossible override, is refused rather than ignored.
    if not isinstance(stored, dict):
        raise Hold("invalid_local_settings")
    resources.checked(stored)
    out["resources"] = stored
    sources = obj.get("sources", [])
    # A stored source is re-validated on read: a host the policy would refuse
    # today is refused, not carried as a promise of a fetch that cannot happen.
    if not isinstance(sources, list) or len(sources) > 32:
        raise Hold("invalid_local_settings")
    out["sources"] = catalog_sources.store(catalog_sources.entries(sources))
    root = obj.get("research_root", "")
    # A stored folder is validated on read: a path that is no longer a folder
    # is a bounded reason later, never a silently empty listing.
    if not isinstance(root, str) or len(root) > 4096:
        raise Hold("invalid_local_settings")
    out["research_root"] = files.checked_root(root) if root else ""
    spoken = obj.get("show_spoken_text", False)
    # A stored preference must be a plain boolean, never a truthy guess.
    if not isinstance(spoken, bool):
        raise Hold("invalid_local_settings")
    out["show_spoken_text"] = spoken
    scale = obj.get("text_scale", "normal")
    # An unknown scale is refused here, not quietly swapped for a default:
    # a stored preference the client cannot honour must not be hidden.
    if scale not in ("normal", "large"):
        raise Hold("invalid_text_scale")
    out["text_scale"] = scale
    return out


def _read_text(p):
    """Bound actual reads; never follow a settings symlink or open a FIFO for waiting."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise Hold("settings_unreadable")
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 32768:
            raise Hold("invalid_local_settings")
        chunks = bytearray()
        while len(chunks) <= 32768:
            part = os.read(fd, min(8192, 32769 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                    info.st_ctime_ns, info.st_mode)
        try:
            current = p.lstat()
        except OSError:
            raise Hold("settings_changed") from None
        if (len(chunks) > 32768 or len(chunks) != before.st_size
                or identity(before) != identity(os.fstat(fd))
                or identity(before) != identity(current)):
            raise Hold("settings_changed")
        return chunks.decode("utf-8")
    finally:
        os.close(fd)


def _unique_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate settings key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs)


def _decode(text):
    return checked(_unique_json(text))


def load(path=None):
    p = config_path() if path is None else Path(path)
    try:
        return _decode(_read_text(p))
    except FileNotFoundError:
        return empty()
    except (ValueError, OSError, RecursionError):
        raise Hold("settings_unreadable") from None


def require_current_audio_selection(expected, path=None):
    """Refuse an old window after another writer changes persisted audio context.

    Read a bounded, validated settings snapshot. Unrelated UI preferences do
    not revoke audio authorization. The caller owns the live operation checks.
    """
    current = load(path)
    fields = ("profile", "stt", "voices", "market_refs", "resources")
    if (any(current[key] != expected[key] for key in fields)
            or current.get("audio_digests", {}) != expected.get("audio_digests", {})
            or current.get("voice_digests", {}) != expected.get("voice_digests", {})):
        raise Hold("audio_settings_changed_restart_required")


@contextmanager
def _write_guard(p):
    """Serialize cooperating settings writers, without waiting on the GTK thread."""
    try:
        import fcntl
        if not hasattr(os, "O_NOFOLLOW"):
            raise Hold("settings_lock_unavailable")
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(p.with_name(p.name + ".lock"),
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except (ImportError, OSError):
        raise Hold("settings_lock_unavailable") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise Hold("settings_lock_unavailable")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Hold("settings_busy") from None
        except OSError:
            raise Hold("settings_lock_unavailable") from None
        yield
    finally:
        os.close(fd)


def _choice_journal(p):
    return p.with_name(p.name + ".jan-choice-journal")


def choice_pending(path=None):
    p = config_path() if path is None else Path(path)
    try:
        _choice_journal(p).lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise Hold("llm_choice_recovery_required") from None


def audio_choice_pending(path=None):
    p = config_path() if path is None else Path(path)
    try:
        p.with_name(p.name + ".audio-choice-journal").lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise Hold("audio_choice_recovery_required") from None


def _require_no_choice(p):
    if choice_pending(p):
        raise Hold("llm_choice_recovery_required")
    if audio_choice_pending(p):
        raise Hold("audio_choice_recovery_required")


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _profile_digest(store, profile):
    return _digest(store._dump(profile))


def _jan_record(store, before, after, current, proposed):
    """Only a model preference/version change, never a policy transition."""
    if (before.state != "active"
            or after != replace(before, version=before.version + 1,
                                llm_model_id=proposed["llm"]).checked()
            or not proposed["llm"] or current["profile"] != before.id
            or proposed != {**current, "llm": proposed["llm"]}):
        raise Hold("invalid_jan_choice_transition")
    return {"version": 1, "profile": before.id,
            "old_model": current["llm"], "new_model": proposed["llm"],
            "old_settings": _digest(_serialize(current)),
            "new_settings": _digest(_serialize(proposed)),
            "old_profile": _profile_digest(store, before),
            "new_profile": _profile_digest(store, after)}


def _choice_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_nlink)


def _read_choice(p):
    try:
        path = _choice_journal(p)
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise Hold("llm_choice_recovery_required")
        raw = _read_text(path)
        record = _unique_json(raw)
        from .profiles import ID
        if (not isinstance(record, dict)
                or set(record) != {"version", "profile", "old_model", "new_model",
                                   "old_settings", "new_settings", "old_profile", "new_profile"}
                or type(record["version"]) is not int or record["version"] != 1
                or not isinstance(record["profile"], str) or not ID.fullmatch(record["profile"])
                or any(not isinstance(record[k], str) or len(record[k]) > 4096
                       for k in ("old_model", "new_model"))
                or not record["new_model"]
                or any(not isinstance(record[k], str) or not re.fullmatch(r"[0-9a-f]{64}", record[k])
                       for k in ("old_settings", "new_settings", "old_profile", "new_profile"))
                or record["old_profile"] == record["new_profile"]):
            raise Hold("llm_choice_recovery_required")
        stamp = _choice_identity(info)
        if _choice_identity(path.lstat()) != stamp:
            raise Hold("llm_choice_recovery_required")
        return record, raw, stamp
    except (Hold, OSError, ValueError, TypeError, RecursionError):
        raise Hold("llm_choice_recovery_required") from None


def _clear_choice(p, raw, stamp):
    journal = _choice_journal(p)
    try:
        _, current, identity = _read_choice(p)
        if current != raw or identity != stamp:
            raise Hold("llm_choice_recovery_required")
        journal.unlink()
        _sync_parent(journal)
    except (Hold, OSError, ValueError):
        raise Hold("llm_choice_recovery_required") from None


def recover_jan_choice(store, path=None):
    """Reconcile only an exact interrupted local preference transition.

    Never replay policy or registry writes. A committed profile with rolled
    back settings, or an unrelated generation, requires explicit review.
    Locks follow the production order: profile, then settings.
    """
    p = config_path() if path is None else Path(path)
    if not choice_pending(p):
        return False
    with store._mutation_lock(), _write_guard(p):
        if not choice_pending(p):
            return False
        if audio_choice_pending(p):
            raise Hold("llm_choice_recovery_required")
        record, raw, journal_stamp = _read_choice(p)
        try:
            settings_stamp = _choice_identity(p.lstat())
            current = load(p)
            if _choice_identity(p.lstat()) != settings_stamp:
                raise Hold("llm_choice_recovery_required")
            profile, stamp = store.snapshot(record["profile"])
            old = {**current, "llm": record["old_model"]}
            new = {**current, "llm": record["new_model"]}
            if (current["profile"] != record["profile"]
                    or _digest(_serialize(old)) != record["old_settings"]
                    or _digest(_serialize(new)) != record["new_settings"]
                    or current not in (old, new)):
                raise Hold("llm_choice_recovery_required")
            generation = _profile_digest(store, profile)
            if generation == record["old_profile"]:
                if current != old:
                    if (not store.is_current(profile.id, stamp)
                            or _read_choice(p) != (record, raw, journal_stamp)
                            or _choice_identity(p.lstat()) != settings_stamp):
                        raise Hold("llm_choice_recovery_required")
                    _replace_text(p, _serialize(old))
            elif generation == record["new_profile"] and current == new:
                # Repair only missing history of this exact active generation.
                if (not store.is_current(profile.id, stamp)
                        or _read_choice(p) != (record, raw, journal_stamp)
                        or _choice_identity(p.lstat()) != settings_stamp):
                    raise Hold("llm_choice_recovery_required")
                store._ensure_current_archive_locked(profile)
            else:
                raise Hold("llm_choice_recovery_required")
            if not store.is_current(profile.id, stamp):
                raise Hold("llm_choice_recovery_required")
            expected = old if generation == record["old_profile"] else new
            if (load(p) != expected
                    or (current == expected and _choice_identity(p.lstat()) != settings_stamp)):
                raise Hold("llm_choice_recovery_required")
            _clear_choice(p, raw, journal_stamp)
            return True
        except (Hold, OSError, ValueError, TypeError, RecursionError):
            raise Hold("llm_choice_recovery_required") from None


def _review_committed_locked(store, registry, p):
    record, raw, journal_stamp = _read_choice(p)
    settings_stamp = _choice_identity(p.lstat())
    current = load(p)
    profile, profile_stamp = store.snapshot(record["profile"])
    proposed = {**current, "llm": record["new_model"]}
    card = registry.card(record["new_model"])
    if (current["profile"] != record["profile"]
            or current["llm"] != record["old_model"]
            or _digest(_serialize(current)) != record["old_settings"]
            or _digest(_serialize(proposed)) != record["new_settings"]
            or _profile_digest(store, profile) != record["new_profile"]
            or profile.state != "active" or profile.llm_model_id != card.model_id
            or not registry._jan_inventory_card(card) or not card.registered
            or card.loaded
            or _choice_identity(p.lstat()) != settings_stamp
            or not store.is_current(profile.id, profile_stamp)):
        raise Hold("llm_choice_recovery_required")
    if _read_choice(p) != (record, raw, journal_stamp):
        raise Hold("llm_choice_recovery_required")
    # Immutable review proof; no settings contents or profile rules in the UI.
    return (card, raw, journal_stamp, settings_stamp, profile_stamp)


def review_committed_jan_choice(store, registry, path=None):
    """Review an exact committed profile with compensated old settings."""
    p = config_path() if path is None else Path(path)
    record, raw, stamp = _read_choice(p)
    with registry.transfer_lease(record["new_model"]), registry._write_guard(require_clean=True):
        with store._mutation_lock(), _write_guard(p):
            if audio_choice_pending(p) or _read_choice(p) != (record, raw, stamp):
                raise Hold("llm_choice_recovery_required")
            return _review_committed_locked(store, registry, p)


def resolve_committed_jan_choice(store, registry, *, expected, path=None):
    """Explicit owner-approved adoption of the committed local preference.

    Never restore permissions or activate/load a model. A fresh discovery
    is still required before connecting. The caller must display the reviewed
    model and obtain a distinct confirmation before invoking this operation.
    """
    p = config_path() if path is None else Path(path)
    card = expected[0]
    with registry.transfer_lease(card.model_id), registry._write_guard(require_clean=True):
        with store._mutation_lock(), _write_guard(p):
            if audio_choice_pending(p) or _review_committed_locked(store, registry, p) != expected:
                raise Hold("llm_choice_recovery_required")
            record, raw, stamp = _read_choice(p)
            profile = store.load(record["profile"])
            store._ensure_current_archive_locked(profile)
            if _review_committed_locked(store, registry, p) != expected:
                raise Hold("llm_choice_recovery_required")
            proposed = {**load(p), "llm": card.model_id}
            if (load(p) != {**proposed, "llm": record["old_model"]}
                    or _choice_identity(p.lstat()) != expected[3]
                    or _read_choice(p) != (record, raw, stamp)):
                raise Hold("llm_choice_recovery_required")
            _replace_text(p, _serialize(proposed))
            if (load(p) != proposed or registry.card(card.model_id) != card
                    or not store.is_current(profile.id, expected[4])):
                raise Hold("llm_choice_recovery_required")
            _clear_choice(p, raw, stamp)
            return proposed


def forget(path=None, *, expected=None):
    """Remove the stored choices, and say what was left alone.

    Only the local choices file is removed. Profiles, the audit trail and the
    market registry live elsewhere and are never touched by this call, so
    forgetting a preference can never erase a decision record.
    """
    p = config_path() if path is None else Path(path)
    if not p.parent.exists() and expected is None:
        return False
    with _write_guard(p):
        _require_no_choice(p)
        if expected is not None:
            try:
                current = _decode(_read_text(p))
            except FileNotFoundError:
                return False
            except (OSError, ValueError, RecursionError):
                raise Hold("settings_unreadable") from None
            if current != checked(expected):
                raise Hold("settings_changed")
        try:
            p.unlink()
        except FileNotFoundError:
            return False
        except OSError:
            raise Hold("settings_unwritable") from None
        try:
            _sync_parent(p)
        except OSError:
            raise Hold("settings_recovery_required") from None
        return True


def reset_choices(profile, *, expected, path=None):
    """Reset desktop preferences and retain only the active profile atomically.

    An already absent file is an idempotent reset, but a present newer file
    is never overwritten. No cooperating writer can enter between deletion
    and profile restoration because there is no delete/restore interval.
    """
    p = config_path() if path is None else Path(path)
    proposed = empty()
    proposed["profile"] = profile
    text = _serialize(proposed)
    with _write_guard(p):
        _require_no_choice(p)
        existed = True
        try:
            current = _decode(_read_text(p))
        except FileNotFoundError:
            existed = False
        except (OSError, ValueError, RecursionError):
            raise Hold("settings_unreadable") from None
        if existed and current != checked(expected):
            raise Hold("settings_changed")
        _replace_text(p, text)
    return existed, proposed


def _serialize(obj):
    text = json.dumps(checked(obj), ensure_ascii=False, sort_keys=True)
    if len(text.encode("utf-8")) > 32768:
        raise Hold("invalid_local_settings")
    return text


def save(obj, path=None, *, expected=None):
    p = config_path() if path is None else Path(path)
    text = _serialize(obj)
    with _write_guard(p):
        _require_no_choice(p)
        if expected is not None and load(p) != checked(expected):
            raise Hold("settings_changed")
        _replace_text(p, text)


@contextmanager
def choice_transaction(obj, path=None, *, expected=None, jan_transition=None):
    """Stage settings during a synchronous registry commit.

    Cooperating writers cannot interleave. Ordinary commit errors restore
    the exact old text (or absence); a failed restoration is explicitly held.
    This is compensation, not an atomic two-file crash-recovery protocol.
    """
    p = config_path() if path is None else Path(path)
    text = _serialize(obj)
    with _write_guard(p):
        _require_no_choice(p)
        try:
            previous = _read_text(p)
            current = _decode(previous)
        except FileNotFoundError:
            previous, current = None, empty()
        except (OSError, ValueError, RecursionError):
            raise Hold("settings_unreadable") from None
        if expected is not None and current != checked(expected):
            raise Hold("settings_changed")
        record = journal_text = journal_stamp = None
        if jan_transition is not None:
            store, before, after = jan_transition
            if store.load(before.id) != before:
                raise Hold("profile_changed")
            record = _jan_record(store, before, after, current, checked(obj))
            journal_text = json.dumps(record, sort_keys=True)
            try:
                _replace_text(_choice_journal(p), journal_text)
                observed, raw, journal_stamp = _read_choice(p)
                if observed != record or raw != journal_text:
                    raise Hold("llm_choice_recovery_required")
            except OSError:
                raise Hold("settings_unwritable") from None
        try:
            _replace_text(p, text)
        except OSError:
            # The durable journal is retained if staging fails.
            raise Hold("settings_unwritable") from None
        try:
            yield
        except BaseException:
            try:
                if _read_text(p) != text:
                    raise Hold("settings_recovery_required")
                if previous is None:
                    p.unlink()
                    _sync_parent(p)
                else:
                    _replace_text(p, previous)
            except (Hold, OSError, ValueError):
                raise Hold("settings_recovery_required") from None
            if record is not None:
                # Keep evidence if the active profile may have committed.
                try:
                    unchanged = store.load(before.id) == before
                except (Hold, OSError):
                    unchanged = False
                if unchanged:
                    _clear_choice(p, journal_text, journal_stamp)
            raise
        if record is not None:
            if store.load(after.id) != after or load(p) != checked(obj):
                raise Hold("llm_choice_recovery_required")
            _clear_choice(p, journal_text, journal_stamp)


def _sync_parent(p):
    fd = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace_text(p, text):
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=p.parent, delete=False) as f:
            tmp = f.name
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        tmp = None
        try:
            _sync_parent(p)
        except OSError:
            # The name may already contain the new generation. An ordinary
            # pre-commit error would misrepresent this durability failure.
            raise Hold("settings_recovery_required") from None
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)

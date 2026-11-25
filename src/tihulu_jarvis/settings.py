# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Desktop-owned local model choices. Nothing is discovered, loaded or downloaded on startup."""
import json
import os
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
        if (len(chunks) > 32768 or len(chunks) != before.st_size
                or identity(before) != identity(os.fstat(fd))
                or identity(before) != identity(p.lstat())):
            raise Hold("settings_changed")
        return chunks.decode("utf-8")
    finally:
        os.close(fd)


def _decode(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate settings key")
            result[key] = value
        return result
    return checked(json.loads(text, object_pairs_hook=pairs))


def load(path=None):
    p = config_path() if path is None else Path(path)
    try:
        return _decode(_read_text(p))
    except FileNotFoundError:
        return empty()
    except (ValueError, OSError, RecursionError):
        raise Hold("settings_unreadable") from None


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


def forget(path=None):
    """Remove the stored choices, and say what was left alone.

    Only the local choices file is removed. Profiles, the audit trail and the
    market registry live elsewhere and are never touched by this call, so
    forgetting a preference can never erase a decision record.
    """
    p = config_path() if path is None else Path(path)
    if not p.parent.exists():
        return False
    with _write_guard(p):
        try:
            p.unlink()
        except FileNotFoundError:
            return False
        except OSError:
            raise Hold("settings_unwritable") from None
        return True


def _serialize(obj):
    text = json.dumps(checked(obj), ensure_ascii=False, sort_keys=True)
    if len(text.encode("utf-8")) > 32768:
        raise Hold("invalid_local_settings")
    return text


def save(obj, path=None):
    p = config_path() if path is None else Path(path)
    text = _serialize(obj)
    with _write_guard(p):
        _replace_text(p, text)


@contextmanager
def choice_transaction(obj, path=None, *, expected=None):
    """Stage settings during a synchronous registry commit.

    Cooperating writers cannot interleave. Ordinary commit errors restore
    the exact old text (or absence); a failed restoration is explicitly held.
    This is compensation, not an atomic two-file crash-recovery protocol.
    """
    p = config_path() if path is None else Path(path)
    text = _serialize(obj)
    with _write_guard(p):
        try:
            previous = _read_text(p)
            current = _decode(previous)
        except FileNotFoundError:
            previous, current = None, empty()
        except (OSError, ValueError, RecursionError):
            raise Hold("settings_unreadable") from None
        if expected is not None and current != checked(expected):
            raise Hold("settings_changed")
        try:
            _replace_text(p, text)
        except OSError:
            raise Hold("settings_unwritable") from None
        try:
            yield
        except BaseException:
            try:
                if _read_text(p) != text:
                    raise Hold("settings_recovery_required")
                if previous is None:
                    p.unlink()
                else:
                    _replace_text(p, previous)
            except (Hold, OSError, ValueError):
                raise Hold("settings_recovery_required") from None
            raise


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
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)

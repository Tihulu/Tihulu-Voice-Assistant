# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Desktop-owned local model choices. Nothing is discovered, loaded or downloaded on startup."""
import json
import os
import tempfile
from pathlib import Path

from . import web
from .voice.m1 import Hold

LOCALES = ("tr", "en", "de")


def empty():
    return {"endpoint": "", "llm": "", "stt": "", "voices": {}, "profile": "",
            "market_refs": {}, "allowed_hosts": [], "text_scale": "normal",
            "web_targets": []}


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
    scale = obj.get("text_scale", "normal")
    # An unknown scale is refused here, not quietly swapped for a default:
    # a stored preference the client cannot honour must not be hidden.
    if scale not in ("normal", "large"):
        raise Hold("invalid_text_scale")
    out["text_scale"] = scale
    return out


def load(path=None):
    p = config_path() if path is None else Path(path)
    try:
        if p.stat().st_size > 32768:
            raise Hold("invalid_local_settings")
        return checked(json.loads(p.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return empty()
    except (ValueError, OSError):
        raise Hold("settings_unreadable") from None


def save(obj, path=None):
    p = config_path() if path is None else Path(path)
    text = json.dumps(checked(obj), ensure_ascii=False, sort_keys=True)
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

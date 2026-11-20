# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Desktop-owned local model choices. Nothing is discovered, loaded or downloaded on startup."""
import json
import os
import tempfile
from pathlib import Path

from .voice.m1 import Hold

LOCALES = ("tr", "en", "de")


def empty():
    return {"endpoint": "", "llm": "", "stt": "", "voices": {}, "profile": ""}


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
    voices = obj.get("voices", {})
    if not isinstance(voices, dict) or any(k not in LOCALES or not isinstance(v, str) or len(v)>4096 for k,v in voices.items()):
        raise Hold("invalid_local_settings")
    out["voices"] = dict(voices)
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

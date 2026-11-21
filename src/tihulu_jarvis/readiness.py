# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""What this machine can actually do, stated before a turn fails.

Each item is a fact about the configuration: the endpoint, the models, the
voices, the audio tools and the active profile. Nothing is probed that would
touch the network or a device, so an item is either ready or missing, and a
missing one says what is missing rather than leaving a control enabled that
would fail later.
"""
from dataclasses import dataclass
from pathlib import Path

from .settings import empty
from .voice.m1 import Hold

READY = "ready"
MISSING = "missing"
UNKNOWN = "unknown"

AUDIO_TOOLS = ("arecord", "whisper-cli", "piper", "aplay")


@dataclass(frozen=True)
class Item:
    token: str
    state: str
    detail: str = ""

    def checked(self):
        if self.state not in (READY, MISSING, UNKNOWN):
            raise Hold("invalid_readiness_state")
        if not isinstance(self.detail, str) or len(self.detail) > 120:
            raise Hold("invalid_local_settings")
        return self


def _endpoint(cfg):
    endpoint = cfg.get("endpoint", "")
    if not endpoint:
        return Item("endpoint", MISSING, "no_endpoint_configured")
    try:
        from .voice.jan import endpoint as parse
        parse(endpoint)
    except Hold:
        return Item("endpoint", MISSING, "endpoint_not_loopback")
    return Item("endpoint", READY, "")


def _model(key, cfg, present):
    chosen = cfg.get(key, "")
    if not chosen:
        return Item(key, MISSING, "no_model_selected")
    if not present:
        return Item(key, MISSING, "selected_file_not_found")
    return Item(key, READY, "")


def _voices(cfg, present):
    chosen = [lang for lang in ("tr", "en", "de") if cfg.get("voices", {}).get(lang)]
    if not chosen:
        return Item("voices", MISSING, "no_voice_selected")
    if not present:
        return Item("voices", MISSING, "selected_voice_file_not_found")
    return Item("voices", READY, ",".join(chosen))


def _inventory(inventory):
    if not inventory:
        return Item("local_models", UNKNOWN, "not_discovered_yet")
    return Item("local_models", READY, ",".join(inventory[:8]))


def _tools(ready):
    if ready is None:
        return Item("audio_tools", UNKNOWN, "not_checked_yet")
    if not ready:
        return Item("audio_tools", MISSING, "audio_backend_missing")
    return Item("audio_tools", READY, "")


def _profile(profile):
    if not profile:
        return Item("profile", MISSING, "no_active_profile")
    return Item("profile", READY, profile)


def report(cfg=None, inventory=(), tools_ready=None, profile=""):
    """The readiness of this configuration, as ordered items.

    ``inventory`` is what a discovery actually returned; without one, local
    models are *unknown* rather than missing, because nobody has looked.
    """
    cfg = empty() if cfg is None else cfg
    if not isinstance(cfg, dict):
        raise Hold("invalid_local_settings")
    stt_present = bool(cfg.get("stt")) and Path(cfg["stt"]).is_file()
    tts_present = all(Path(cfg["voices"][lang]).is_file()
                      for lang in cfg.get("voices", {}))
    return (
        _endpoint(cfg),
        _inventory(tuple(inventory or ())),
        _model("stt", cfg, stt_present),
        _voices(cfg, tts_present),
        _tools(tools_ready),
        _profile(profile if isinstance(profile, str) else ""),
    )


def summary(items):
    """One short English line: how many things are ready and what is not."""
    ready = [item.token for item in items if item.state == READY]
    missing = [item.token for item in items if item.state == MISSING]
    unknown = [item.token for item in items if item.state == UNKNOWN]
    parts = ["%d ready" % len(ready)]
    if missing:
        parts.append("%d missing: %s" % (len(missing), ", ".join(missing)))
    if unknown:
        parts.append("%d not checked: %s" % (len(unknown), ", ".join(unknown)))
    return " · ".join(parts)

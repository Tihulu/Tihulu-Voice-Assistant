# SPDX-License-Identifier: AGPL-3.0-only
"""Crash recovery of local STT/TTS preference and registry lifecycle.

Private local journal, no permission or profile mutation. Cooperating writers
use model -> registry -> settings locks. Recovery never rewrites registry.
"""
import json
import re
import os
import stat
from pathlib import Path

from . import market, settings
from .voice.m1 import Hold

REASON = "audio_choice_recovery_required"


def _journal(p):
    return p.with_name(p.name + ".audio-choice-journal")


def _value(cfg, slot):
    return cfg["stt"] if slot == "stt" else cfg["voices"].get(slot)


def _with_value(cfg, slot, value):
    cfg = settings.checked(cfg)
    if slot == "stt":
        cfg["stt"] = value
    elif value is None:
        cfg["voices"].pop(slot, None)
    else:
        cfg["voices"][slot] = value
    return settings.checked(cfg)


def _read(p):
    try:
        journal = _journal(p)
        info = journal.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise Hold(REASON)
        stamp = settings._choice_identity(info)
        raw = settings._read_text(journal)
        rec = settings._unique_json(raw)
        if (not isinstance(rec, dict)
                or set(rec) != {"version", "model", "slot", "old", "new",
                                "old_settings", "new_settings", "old_registry", "new_registry"}
                or type(rec["version"]) is not int or rec["version"] != 1
                or not isinstance(rec["model"], str)
                or not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", rec["model"])
                or rec["slot"] not in ("stt", "tr", "en", "de")
                or (rec["old"] is not None and
                    (not isinstance(rec["old"], str) or len(rec["old"]) > 4096))
                or (rec["slot"] == "stt" and rec["old"] is None)
                or not isinstance(rec["new"], str) or not rec["new"] or len(rec["new"]) > 4096
                or any(not isinstance(rec[k], str) or not re.fullmatch(r"[0-9a-f]{64}", rec[k])
                       for k in ("old_settings", "new_settings", "old_registry", "new_registry"))
                or settings._choice_identity(journal.lstat()) != stamp):
            raise Hold(REASON)
        return rec, raw, stamp
    except (Hold, OSError, ValueError, TypeError, RecursionError):
        raise Hold(REASON) from None


def _clear(p, proof):
    if _read(p) != proof:
        raise Hold(REASON)
    _journal(p).unlink()
    settings._sync_parent(p)


def _pair(p, rec):
    stamp = settings._choice_identity(p.lstat())
    current = settings.load(p)
    old = _with_value(current, rec["slot"], rec["old"])
    new = _with_value(current, rec["slot"], rec["new"])
    if (settings._choice_identity(p.lstat()) != stamp
            or current not in (old, new)
            or settings._digest(settings._serialize(old)) != rec["old_settings"]
            or settings._digest(settings._serialize(new)) != rec["new_settings"]):
        raise Hold(REASON)
    return current, old, new, stamp


def recover(registry, path=None):
    p = settings.config_path() if path is None else Path(path)
    if not settings.audio_choice_pending(p):
        return False
    proof = _read(p)
    rec = proof[0]
    try:
        with registry.transfer_lease(rec["model"]), registry._write_guard(), settings._write_guard(p):
            if settings.choice_pending(p) or _read(p) != proof:
                raise Hold(REASON)
            current, old, new, stamp = _pair(p, rec)
            generation = registry.snapshot()[0]
            if generation == rec["old_registry"] == rec["new_registry"]:
                desired = current
            elif generation == rec["old_registry"]:
                desired = old
            elif generation == rec["new_registry"]:
                desired = new
            else:
                raise Hold(REASON)
            if (registry.snapshot()[0] != generation or _read(p) != proof
                    or settings._choice_identity(p.lstat()) != stamp):
                raise Hold(REASON)
            # Ensure the observed registry generation is durable before
            # reconciling settings or dropping the journal.
            market._sync_directory(registry.root)
            if (registry.snapshot()[0] != generation or _read(p) != proof
                    or settings._choice_identity(p.lstat()) != stamp):
                raise Hold(REASON)
            if current != desired:
                settings._replace_text(p, settings._serialize(desired))
                stamp = settings._choice_identity(p.lstat())
            if (registry.snapshot()[0] != generation or settings.load(p) != desired
                    or settings._choice_identity(p.lstat()) != stamp):
                raise Hold(REASON)
            _clear(p, proof)
            return True
    except (Hold, OSError, ValueError, TypeError, RecursionError):
        raise Hold(REASON) from None


def commit(registry, expected_card, card, current, proposed, slot, path=None,
           *, checkpoint=lambda: None):
    """Publish one reviewed local audio preference and exact card transition."""
    p = settings.config_path() if path is None else Path(path)
    current, proposed = settings.checked(current), settings.checked(proposed)
    value = _value(proposed, slot)
    if (slot not in ("stt", "tr", "en", "de") or not value
            or proposed != _with_value(current, slot, value)
            or card.model_id != expected_card.model_id
            or card.category != ("stt" if slot == "stt" else "tts")
            or card.local_or_cloud != "local" or card.state != "ACTIVE"
            or card.storage_ref not in current["market_refs"]
            or current["market_refs"][card.storage_ref] != value
            or (slot != "stt" and card.languages != (slot,))):
        raise Hold("invalid_audio_choice_transition")
    wanted = expected_card
    if wanted.state == "REGISTERED":
        wanted = market.advance(wanted, "TESTABLE")
    if wanted.state == "TESTABLE":
        wanted = market.advance(wanted, "ACTIVE")
    if wanted != card:
        raise Hold("invalid_audio_choice_transition")
    with registry.transfer_lease(card.model_id), registry._write_guard(), settings._write_guard(p):
        settings._require_no_choice(p)
        checkpoint()
        if registry.card(card.model_id) != expected_card:
            raise Hold("registry_card_changed")
        if settings.load(p) != current:
            raise Hold("settings_changed")
        settings_stamp = settings._choice_identity(p.lstat())
        old_generation = registry.snapshot()[0]
        _, cards, selection, history = registry._prepared_card(card)
        new_generation = market._text_digest(registry._serialized(cards, selection, history=history))
        rec = {"version": 1, "model": card.model_id, "slot": slot,
               "old": _value(current, slot), "new": value,
               "old_settings": settings._digest(settings._serialize(current)),
               "new_settings": settings._digest(settings._serialize(proposed)),
               "old_registry": old_generation, "new_registry": new_generation}
        checkpoint()
        try:
            settings._replace_text(_journal(p), json.dumps(rec, sort_keys=True))
        except OSError:
            raise Hold("settings_unwritable") from None
        proof = _read(p)
        if (proof[0] != rec or registry.snapshot()[0] != old_generation
                or settings.load(p) != current or settings._choice_identity(p.lstat()) != settings_stamp):
            raise Hold(REASON)
        try:
            checkpoint()
        except Hold:
            # No setting or registry effect has occurred yet.
            _clear(p, proof)
            raise
        try:
            settings._replace_text(p, settings._serialize(proposed))
        except OSError:
            raise Hold("settings_unwritable") from None
        staged_stamp = settings._choice_identity(p.lstat())
        if (settings.load(p) != proposed or _read(p) != proof
                or registry.snapshot()[0] != old_generation):
            raise Hold(REASON)
        try:
            checkpoint()
            saved = registry.save_card(card, expected=expected_card)
        except (Hold, OSError):
            if (registry.snapshot()[0] != old_generation or _read(p) != proof
                    or settings.load(p) != proposed
                    or settings._choice_identity(p.lstat()) != staged_stamp):
                raise Hold(REASON) from None
            try:
                settings._replace_text(p, settings._serialize(current))
                _clear(p, proof)
            except (Hold, OSError):
                raise Hold(REASON) from None
            raise
        try:
            market._sync_directory(registry.root)
            if (registry.snapshot()[0] != new_generation or settings.load(p) != proposed
                    or settings._choice_identity(p.lstat()) != staged_stamp):
                raise Hold(REASON)
            _clear(p, proof)
        except (Hold, OSError):
            raise Hold(REASON) from None
        return saved

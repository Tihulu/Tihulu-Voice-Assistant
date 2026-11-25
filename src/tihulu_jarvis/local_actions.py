# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit user-composer intents for trusted local pickers.

No model-generated proposals, paths, shell commands, implicit execution,
or fuzzy matching. A match is only a request to present a native picker.
"""
from .voice.m1 import fold

COMMANDS = {
    "transcribe_recording": (
        "transcribe a recording", "transcribe this audio file",
        "bir ses kaydini yazıya cevir", "ses dosyasini yazıya cevir",
        "bir kaydi yazıya cevir", "transkribiere eine aufnahme",
        "transkribiere diese audiodatei",
    ),
    "discover_audio_models": (
        "find existing audio models", "find local audio models",
        "mevcut ses modellerini bul", "yerel ses modellerini bul",
        "finde vorhandene audiomodelle", "finde lokale audiomodelle",
    ),
}
_LOOKUP = {fold(command): action for action, commands in COMMANDS.items()
           for command in commands}


def direct(text):
    if not isinstance(text, str) or len(text) > 512:
        return None
    return _LOOKUP.get(fold(text.strip()).removesuffix(".").strip())

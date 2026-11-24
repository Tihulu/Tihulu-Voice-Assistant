# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded local task intent routing; never a general command interpreter."""
import json
import re
from dataclasses import dataclass

from .temperature_engine import COLORS
from .voice.m1 import Hold, Message, fold

@dataclass(frozen=True)
class Intent:
    action: str
    language: str
    color: str | None = None

# Exact, complete commands only. Quoted examples, questions and compound
# instructions must not be silently turned into side effects.
ANALYZE = {
    "en": ("analyze this file", "analyze the csv", "analyze this csv",
           "analyze this file and create a chart"),
    "tr": ("bu dosyayi analiz et", "csv dosyasini analiz et",
           "bu csv dosyasini analiz et", "bu dosyayi analiz et ve grafik olustur"),
    "de": ("analysiere diese datei", "analysiere die csv datei",
           "analysiere diese csv datei", "analysiere diese datei und erstelle ein diagramm"),
}
COLOR_NAMES = {
    "en": {"blue": "blue", "green": "green", "orange": "orange"},
    "tr": {"mavi": "blue", "yesil": "green", "turuncu": "orange"},
    "de": {"blau": "blue", "grun": "green", "orange": "orange"},
}
PREFIX = {"task": "en", "gorev": "tr", "aufgabe": "de"}


def direct(text):
    if not isinstance(text, str) or len(text) > 512:
        return None
    normalized = fold(text.strip()).removesuffix(".").strip()
    for language, commands in ANALYZE.items():
        if normalized in commands:
            return Intent("analyze", language)
    for language, names in COLOR_NAMES.items():
        for name, color in names.items():
            phrases = {
                "en": (f"make the chart {name}", f"change the chart to {name}"),
                "tr": (f"grafigi {name} yap", f"grafigin rengini {name} yap"),
                "de": (f"mach das diagramm {name}", f"farbe das diagramm {name}"),
            }[language]
            if normalized in phrases:
                return Intent("recolor", language, color)
    return None


def explicit_task(text):
    if not isinstance(text, str):
        return None
    name, separator, request = text.strip().partition(":")
    language = PREFIX.get(fold(name))
    if not separator or language is None:
        return None
    return language, request.strip()


def parse_proposal(raw, language):
    if language not in ANALYZE or not isinstance(raw, str) or len(raw) > 256:
        raise Hold("analysis_invalid_proposal")
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError
            out[key] = value
        return out
    try:
        data = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(data, dict):
            raise ValueError
        action = data.get("action")
        if action == "analyze" and set(data) == {"action"}:
            return Intent(action, language)
        if (action == "recolor" and set(data) == {"action", "color"}
                and isinstance(data["color"], str) and data["color"] in COLORS):
            return Intent(action, language, data["color"])
        if action == "unsupported" and set(data) == {"action"}:
            raise Hold("analysis_unsupported_task")
    except (ValueError, TypeError, RecursionError):
        pass
    raise Hold("analysis_invalid_proposal")


async def propose(text, jan):
    explicit = explicit_task(text)
    if explicit is None:
        raise Hold("analysis_explicit_task_required")
    language, request = explicit
    if not request or len(request) > 512:
        raise Hold("analysis_invalid_request")
    # Only the text explicitly submitted by the user. No conversation,
    # selected file, path discovered by Jarvis, artifact, or private summary.
    system = ('Classify the user request, never answer or execute it. Return only '
              'one JSON object: {"action":"analyze"} for temperature CSV analysis, '
              '{"action":"recolor","color":"blue"} (or green or orange) for changing '
              'the last chart color, or {"action":"unsupported"}. No other keys. '
              'Requests for other operations or multiple operations are unsupported.')
    raw = await jan((Message("system", system), Message("user", request)))
    return parse_proposal(raw, language)

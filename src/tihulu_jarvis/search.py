# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Search the local conversation.

Searching is local and bounded: the query is a plain substring, never a pattern,
so a search cannot be turned into an expression engine. Results are snippets,
not the whole message, and nothing is sent anywhere.
"""
import unicodedata

from .voice.m1 import Hold

MAX_QUERY = 120
MAX_RESULTS = 50
SNIPPET_WIDTH = 90


def _normal(text):
    """Casefold and drop combining marks.

    A search must match "kelimem" against "KELİMEM" without depending on the
    platform locale: casefold alone leaves the dotted capital İ with its
    combining dot, so the mark is removed rather than guessed at.
    """
    folded = "".join(ch for ch in text.casefold() if not unicodedata.combining(ch))
    return folded


def find(history, query, limit=MAX_RESULTS):
    """Matches as (index, role, snippet), newest first.

    ``history`` is the local session's messages. Anything that is not a
    (role, content) pair is skipped rather than guessed at, and an over-long or
    empty query is a bounded reason.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY:
        raise Hold("invalid_search_query")
    if not isinstance(limit, int) or limit < 1 or limit > MAX_RESULTS:
        raise Hold("invalid_search_limit")
    wanted = _normal(query.strip())
    out = []
    for index, entry in enumerate(history):
        role = getattr(entry, "role", None)
        content = getattr(entry, "content", None)
        if not isinstance(role, str) or not isinstance(content, str):
            continue
        if wanted in _normal(content):
            out.append((index, role, snippet(content, query)))
    return tuple(reversed(out[-limit:]))


def snippet(content, query, width=SNIPPET_WIDTH):
    """A bounded window around the first match, marked with ellipses.

    The window is clamped to the message, so a short message is returned whole
    rather than padded, and a match near an edge is still readable.
    """
    at = _normal(content).find(_normal(query.strip()))
    if at < 0:
        return ""
    half = max(8, width // 2)
    start = max(0, at - half)
    end = min(len(content), at + len(query) + half)
    body = content[start:end]
    return ("…" if start else "") + body + ("…" if end < len(content) else "")


def count(history, query):
    """How many local messages match; the number shown next to the field."""
    return len(find(history, query))

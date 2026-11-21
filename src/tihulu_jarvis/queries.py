# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""A short, local memory of what was searched for.

A remembered query is re-runnable and bounded: the newest first, no duplicates,
and at most a handful of them. It is stored on this machine, never sent
anywhere, and clearing it removes it from the store rather than hiding a row.
"""
from .search import MAX_QUERY
from .voice.m1 import Hold

MAX_QUERIES = 20


def checked(history):
    """A history of short, non-empty queries, or a bounded refusal."""
    if not isinstance(history, (list, tuple)):
        raise Hold("invalid_search_query")
    out = []
    for entry in history:
        if not isinstance(entry, str) or not entry.strip() or len(entry) > MAX_QUERY:
            raise Hold("invalid_search_query")
        query = entry.strip()
        if query not in out:
            out.append(query)
    if len(out) > MAX_QUERIES:
        out = out[:MAX_QUERIES]
    return tuple(out)


def remember(history, query):
    """Add a query as the newest, moving an existing one rather than duplicating."""
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY:
        raise Hold("invalid_search_query")
    return checked([query.strip()] + [q for q in checked(history) if q != query.strip()])


def forget(history, query):
    """Remove one query; forgetting an absent one is not an error."""
    if not isinstance(query, str):
        raise Hold("invalid_search_query")
    return tuple(q for q in checked(history) if q != query.strip())


def clear():
    """No queries at all, with nothing left behind to hide."""
    return ()

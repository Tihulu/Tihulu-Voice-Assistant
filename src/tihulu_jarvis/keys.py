# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Keyboard navigation as a small, stated table.

A keybinding is data: the widget it belongs to, the key, the modifiers and the
action it performs. The table is exposed so a test can assert what the client
binds without simulating a keyboard, and so nothing is bound silently.
"""
from .voice.m1 import Hold

# (name, description-key, widget, key, modifiers, action)
# ``action`` is a method name on the client; ``key`` is a GDK key value.
SEND = ("send", "Send the message", "entry", "<ctrl>Return", ())
FOCUS_SEARCH = ("focus_search", "Focus the conversation search", "search", "<ctrl>f", ())
CANCEL_TURN = ("cancel_turn", "Cancel the current turn", "window", "Escape", ())
HARD_STOP = ("hard_stop", "Stop everything", "window", "<ctrl>Escape", ())
ATTACH = ("attach", "Attach a file", "window", "<ctrl>o", ())

BINDINGS = (SEND, FOCUS_SEARCH, CANCEL_TURN, HARD_STOP, ATTACH)


def checked(binding):
    """A binding the client can honour: a known action and a known target."""
    name, caption, target, trigger, mods = binding
    if name not in {row[0] for row in BINDINGS}:
        raise Hold("unknown_key_action")
    if target not in ("entry", "search", "window"):
        raise Hold("unknown_key_target")
    if not isinstance(trigger, str) or not trigger:
        raise Hold("invalid_key_trigger")
    return binding


def names():
    """The bound action names, in the order the table states them."""
    return tuple(row[0] for row in BINDINGS)

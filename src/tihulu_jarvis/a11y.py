# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Display preferences that do not depend on eyesight or a mouse.

The interface is scalable in text, and it ships no motion at all: reduced
motion is the state the client is already in, not a switch that could promise
something the stylesheet does not do. A preference is stored as a short token
and applied to one stylesheet, so nothing here can quietly rewrite the
interface's look.
"""
from .voice.m1 import Hold

SCALES = ("normal", "large")
POINTS = {"normal": 1.0, "large": 1.25}

# The shipped look: gradients only where useful, no animation, no transition,
# no transform. A test asserts that this stays true.
MOTION_FREE = True


def checked(scale):
    """A stored scale, or the bounded refusal of an unknown one."""
    if scale not in (None, "", *SCALES):
        raise Hold("invalid_text_scale")
    return scale or "normal"


def css(scale, base=""):
    """One stylesheet for one scale, with no motion added to it."""
    factor = POINTS[checked(scale)]
    rules = [base, "* { font-size: %dpt; }" % max(10, round(11 * factor))]
    # Motion is absent, and stays absent: no transition, animation or transform
    # is written by any scale, including a large one.
    return "\n".join(r for r in rules if r)


def motion_words(text):
    """Motion keywords a stylesheet must not contain."""
    return tuple(word for word in ("transition", "animation", "transform", "@keyframes")
                 if word in text.lower())

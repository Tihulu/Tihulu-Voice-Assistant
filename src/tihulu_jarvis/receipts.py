# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Receipts for decisions, which are not conversation messages.

A chat bubble carries a sender and a time, and must never be mistaken for an
approval. A receipt is a different kind of row: it names the outcome, what was
decided and who decided it, in one short line, and it never claims to be a
message.
"""
KINDS = ("approved", "refused", "performed", "not_approved")


def approved(what, by="you"):
    """An owner's explicit approval of one thing, at one moment."""
    return "Approved by %s · %s" % (by, what)


def refused(what, by="the active profile"):
    """A refusal the policy produced, never an approval that was missed."""
    return "Refused by %s · %s" % (by, what)


def performed(what):
    """A bounded local action that ran, without any approval claim."""
    return "Performed on this computer · %s" % what


def not_approved(what):
    """A confirmation that was cancelled before anything changed."""
    return "Not approved · %s" % what


def line(kind, what):
    """One receipt line; an unknown kind is refused rather than invented."""
    if kind == "approved":
        return approved(what)
    if kind == "refused":
        return refused(what)
    if kind == "performed":
        return performed(what)
    if kind == "not_approved":
        return not_approved(what)
    raise ValueError("unknown receipt kind: %r" % (kind,))


def is_receipt(text):
    """Whether a row is a receipt, so a bubble can never be one by accident."""
    return isinstance(text, str) and text.startswith(("Approved by ", "Refused by ",
                                                      "Performed on this computer · ",
                                                      "Not approved · "))

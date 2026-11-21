# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Approved web targets, where reading and submitting are different rights.

A target is registered by the owner with an explicit scope: reading a page
(GET, a bounded preview) or submitting to it (a form, a POST — a separate
decision that must be granted on its own). Nothing is fetched that the owner
did not register, and a page is shown as bounded text without running,
storing or forwarding any script.
"""
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .voice.m1 import Hold

MAX_TARGETS = 32
MAX_PREVIEW = 4000
SCHEME = "https"


@dataclass(frozen=True)
class Target:
    host: str
    path: str = "/"
    submit: bool = False

    def checked(self):
        if not isinstance(self.host, str) or not self.host:
            raise Hold("invalid_local_settings")
        if len(self.host) > 253 or len(self.path) > 200:
            raise Hold("invalid_local_settings")
        try:
            ip = ipaddress.ip_address(self.host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", self.host) or ".." in self.host:
                raise Hold("invalid_local_settings") from None
            if "." not in self.host or self._is_local_name():
                # A local name or a bare label is a local service, not the web.
                raise Hold("target_not_public")
        else:
            # A private address is a local service, not a web target.
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                raise Hold("target_not_public")
        if not isinstance(self.submit, bool):
            raise Hold("invalid_local_settings")
        if not self.path.startswith("/") or not re.fullmatch(r"/[A-Za-z0-9._~/-]*", self.path) \
                or ".." in self.path.split("/"):
            raise Hold("invalid_local_settings")
        return self

    def _is_local_name(self):
        lower = self.host.lower()
        return (lower == "localhost" or lower.endswith((".localhost", ".local", ".home.arpa"))
                or lower.endswith(".internal"))

    @property
    def url(self):
        return "%s://%s%s" % (SCHEME, self.host, self.path)


def parse(raw):
    """A registered target from its own string form."""
    if not isinstance(raw, str) or not raw.startswith(SCHEME + "://"):
        raise Hold("invalid_local_settings")
    parts = urlsplit(raw)
    host = parts.hostname or ""
    if not host or not parts.path or parts.username or parts.password \
            or parts.query or parts.fragment:
        # A target states its path explicitly; a bare domain is not a target.
        raise Hold("invalid_local_settings")
    if any(c in raw for c in "?#@\"'\\ ") or not raw.isascii():
        raise Hold("invalid_local_settings")
    return Target(host, parts.path or "/", parts.path.endswith("/submit")).checked()


def entry(target):
    """One target as stored: its URL and its submission right, kept apart.

    A URL alone cannot carry the right, so the right is stored with it rather
    than inferred from the path a second time.
    """
    target = target.checked()
    return {"url": target.url, "submit": bool(target.submit)}


def from_entry(item):
    if not isinstance(item, dict) or not isinstance(item.get("url"), str):
        raise Hold("invalid_local_settings")
    target = parse(item["url"])
    return Target(target.host, target.path, bool(item.get("submit", False)))


def entries(items):
    return tuple(from_entry(item) for item in items)


def store(targets):
    return [entry(t) for t in targets]


def register(targets, target):
    """Add a target, replacing the same host and path rather than duplicating."""
    target = target.checked()
    rest = [t for t in targets if (t.host, t.path) != (target.host, target.path)]
    if len(rest) >= MAX_TARGETS:
        raise Hold("too_many_targets")
    return tuple(rest[:MAX_TARGETS - 1] + [target])


def forget(targets, target):
    """Remove one target by host and path; removing nothing is not an error."""
    return tuple(t for t in targets if (t.host, t.path) != (target.host, target.path))


def plan(target, submit=False):
    """What an action would do, or the bounded reason it may not happen.

    Reading is allowed for a registered target. Submitting is a separate right:
    without it, nothing is sent — not a form, not a token, not a redirect.
    """
    target = target.checked()
    if not submit:
        return "GET", target.url, "navigate_only"
    if not target.submit:
        raise Hold("submission_not_approved")
    return "POST", target.url, "submission_approved"


def text_preview(body, limit=MAX_PREVIEW):
    """Bounded readable text from a page, with nothing executable in it.

    Script and style content is dropped before tags are stripped, so what a
    reader sees cannot carry code; the result is whitespace-collapsed and cut
    at the limit, with the cut stated rather than hidden.
    """
    if not isinstance(body, str):
        raise Hold("invalid_local_settings")
    if len(body) > 262144:
        raise Hold("page_too_large")
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", body)
    text = re.sub(r"(?s)<[^>]*>", " ", text)
    text = re.sub(r"&", "&", text)
    text = re.sub(r"[ \t\r\n\f\v]+", " ", text).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + " …"
    return text

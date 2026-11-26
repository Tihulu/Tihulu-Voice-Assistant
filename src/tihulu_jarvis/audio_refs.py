# SPDX-License-Identifier: AGPL-3.0-only
"""Opaque references for local audio imports; not a filesystem locator."""
import hashlib
import re


def valid(ref):
    return isinstance(ref, str) and re.fullmatch(r"sha256-[0-9a-f]{32}(?:-[0-9a-f]{16})?", ref) is not None


def matches(ref, digest):
    return valid(ref) and ref[:39] == "sha256-" + digest[:32]


def scoped(digest, model_id):
    # Model IDs are already visible in the registry; never hash a private path
    # into exported metadata. Separate card references share a digest prefix.
    return "sha256-" + digest[:32] + "-" + hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:16]

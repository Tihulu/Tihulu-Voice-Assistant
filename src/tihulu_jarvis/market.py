# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Local AI Market: truthful cards for models and voices, and a lifecycle.

No network access exists in this module and none may be added here. A
download is described as an auditable state machine so that a future,
separately authorized transport can be reviewed on its own. Nothing is
downloaded, started or deleted without an explicit, bounded owner action,
and shared weights are never removed by this project.
"""
import asyncio
import hashlib
import hmac
import json
import os
import time
import re
import tempfile
import stat
import threading
from contextlib import contextmanager
from functools import wraps
from .owned_wait import await_authorized, finish_owned
from dataclasses import dataclass, field, replace
from pathlib import Path

from .voice.m1 import Hold

CATEGORIES = ("stt", "llm", "tts")
STATES = ("DISCOVERED", "REVIEWED", "APPROVED", "QUEUED", "DOWNLOADING", "VERIFIED",
          "REGISTERED", "TESTABLE", "ACTIVE", "CANCELLED", "HOLD", "FAILED")
TERMINAL = ("CANCELLED", "HOLD", "FAILED")
RETRYABLE_TRANSFER = ("REVIEWED", "APPROVED", "QUEUED", "DOWNLOADING", "VERIFIED",
                      "CANCELLED", "HOLD", "FAILED")
(DISCOVERED, REVIEWED, APPROVED, QUEUED, DOWNLOADING, VERIFIED, REGISTERED, TESTABLE,
 ACTIVE, CANCELLED, HOLD, FAILED) = STATES
ALLOWED_NEXT = {
    "DISCOVERED": ("REVIEWED", "CANCELLED", "HOLD", "FAILED"),
    "REVIEWED": ("APPROVED", "CANCELLED", "HOLD", "FAILED"),
    "APPROVED": ("QUEUED", "CANCELLED", "HOLD", "FAILED"),
    "QUEUED": ("DOWNLOADING", "CANCELLED", "HOLD", "FAILED"),
    "DOWNLOADING": ("VERIFIED", "CANCELLED", "FAILED"),
    # Verified staging is not yet an installed publication. It can be stopped.
    "VERIFIED": ("REGISTERED", "CANCELLED", "FAILED"),
    "REGISTERED": ("TESTABLE", "FAILED"),
    "TESTABLE": ("ACTIVE", "HOLD", "FAILED"),
    "ACTIVE": ("REGISTERED", "HOLD", "FAILED"),
    "CANCELLED": (), "HOLD": (), "FAILED": (),
}
TRUST = ("unknown", "owner", "curated", "vendor")
FORMATS = ("ggml", "gguf", "onnx", "ct2", "safetensors", "other")
# Format and engine compatibility is first-class; an unknown pair stays unknown.
ENGINE_FORMATS = {
    "whisper.cpp": ("ggml",),
    "piper": ("onnx",),
    "jan": ("gguf", "ggml"),
    "ctranslate2": ("ct2",),
    "faster-whisper": ("ct2",),
}
SUFFIX_FORMAT = {".gguf": "gguf", ".ggml": "ggml", ".bin": "ggml", ".onnx": "onnx",
                 ".ct2": "ct2", ".safetensors": "safetensors"}
UNKNOWN = "unknown/not tested"
DIGEST_LIMIT = 8 * 1024 ** 3


def market_dir():
    """Private local registry. Never shared with, or uploaded to, a provider."""
    base = os.environ.get("XDG_DATA_HOME")
    folder = Path(base) if base and Path(base).is_absolute() else Path.home() / ".local" / "share"
    return folder / "tihulu-jarvis" / "market"


def human_size(total):
    """Human-readable size. Unknown sizes are reported as unknown, not as 0."""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    if total <= 0:
        return "unknown/not tested"
    value = float(total)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return "%.1f %s" % (value, unit) if unit != "B" else "%d B" % int(value)
        value /= 1024
    return "%.1f TiB" % value


@dataclass(frozen=True)
class Usage:
    """Aggregate what the registry actually knows. Nothing is estimated here."""

    cards: int = 0
    by_category: tuple = ()
    by_state: tuple = ()
    disk_bytes: int = 0
    unknown_size: int = 0
    shared: int = 0
    owned: int = 0
    imported: int = 0
    requires_gpu: int = 0
    memory_bytes: int = 0
    unknown_memory: int = 0

    def report(self):
        return ("%d cards · %s · disk %s · shared %d · jarvis-owned %d · imported %d · "
                "gpu %d · memory %s" % (
                    self.cards, ", ".join("%s %d" % pair for pair in self.by_category),
                    human_size(self.disk_bytes) if self.disk_bytes else "unknown/not tested",
                    self.shared, self.owned, self.imported, self.requires_gpu,
                    human_size(self.memory_bytes) if self.memory_bytes else "unknown/not tested"))


def usage(cards):
    per_category, per_state, disk, gpu, memory = {}, {}, 0, 0, 0
    shared = owned = imported = unknown_size = unknown_memory = 0
    for card in cards:
        per_category[card.category] = per_category.get(card.category, 0) + 1
        per_state[card.state] = per_state.get(card.state, 0) + 1
        if card.disk_size_bytes:
            disk += card.disk_size_bytes
        else:
            unknown_size += 1
        if card.memory_estimate:
            memory += card.memory_estimate
        else:
            unknown_memory += 1
        if card.shared_storage_owner:
            shared += 1
        elif card.owned_by_jarvis:
            owned += 1
        else:
            imported += 1
        if card.requires_gpu:
            gpu += 1
    return Usage(len(cards), tuple(sorted(per_category.items())), tuple(sorted(per_state.items())),
                 disk, unknown_size, shared, owned, imported, gpu, memory, unknown_memory)


@dataclass(frozen=True)
class ModelCard:
    """A card describes what is actually known. Nothing is inferred."""

    model_id: str
    display_name: str
    category: str
    provider: str = "unknown"
    engine: str = "unknown"
    local_or_cloud: str = "local"
    version: str = "unknown"
    artifact_format: str = "other"
    languages: tuple = ()
    installed: bool = False
    registered: bool = False
    loaded: bool = False
    health_status: str = UNKNOWN
    compatibility_status: str = UNKNOWN
    trust_level: str = "unknown"
    shared_storage_owner: str = ""
    storage_ref: str = ""        # opaque reference, never a raw path in exports
    owned_by_jarvis: bool = False
    disk_size_bytes: int = 0        # 0 means the size is unknown, not zero
    memory_estimate: int = 0        # bytes; 0 means not estimated
    requires_gpu: bool = False
    license_id: str = "unknown"
    state: str = "DISCOVERED"
    last_tested_at: float = 0.0
    source_ref: str = ""        # approved download terms, opaque reference
    source_checksum: str = ""
    source_size: int = 0
    source_version: str = "unknown"
    network_approval: str = ""
    approval_expires_at: float = 0.0
    revision: int = 0          # registry-local sequence; 0 means never revised
    artifact_digest: str = ""  # full verified digest of owned bytes, not source terms

    def compatibility(self):
        if self.engine in ENGINE_FORMATS:
            if self.artifact_format in ENGINE_FORMATS[self.engine]:
                return "compatible"
            if self.artifact_format == "other":
                return UNKNOWN  # an unknown format is never marked incompatible
            return "incompatible_format"
        return UNKNOWN

    def approved_source(self):
        """True only when every required download term is present.

        A stated size is not required: an unstated size stays unknown and the
        transport's own limit still bounds the transfer.
        """
        return bool(self.source_ref and self.source_checksum and self.network_approval)

    def checked(self):
        if not isinstance(self.model_id, str) or not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", self.model_id):
            raise Hold("invalid_model_id")
        if self.category not in CATEGORIES:
            raise Hold("unknown_model_category")
        if self.state not in STATES:
            raise Hold("unknown_lifecycle_state")
        if self.artifact_format not in FORMATS or self.trust_level not in TRUST:
            raise Hold("invalid_card_field")
        if self.local_or_cloud not in ("local", "cloud"):
            raise Hold("invalid_card_field")
        if any(lang != lang.lower() or not lang.isalpha() for lang in self.languages):
            raise Hold("unsupported_language_tag")
        if not 0 <= self.disk_size_bytes <= DIGEST_LIMIT:
            raise Hold("invalid_size")
        if not 0 <= self.memory_estimate <= DIGEST_LIMIT:
            raise Hold("invalid_size")
        if not isinstance(self.artifact_digest, str) or (self.artifact_digest and (
                not re.fullmatch(r"[0-9a-f]{64}", self.artifact_digest)
                or not self.owned_by_jarvis or self.storage_ref != "sha256-" + self.artifact_digest[:32])):
            raise Hold("invalid_artifact_digest")
        if self.storage_ref and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.storage_ref):
            raise Hold("invalid_storage_reference")
        if len(self.display_name) > 120 or len(self.provider) > 64 or len(self.version) > 64:
            raise Hold("card_text_too_long")
        for key in ("source_ref", "source_checksum", "source_version"):
            if not isinstance(getattr(self, key), str) or len(getattr(self, key)) > 256:
                raise Hold("invalid_source_reference")
        if self.source_ref and not re.fullmatch(r"[A-Za-z0-9:._~/-]{1,256}", self.source_ref):
            raise Hold("invalid_source_reference")
        # An opaque token or an approved endpoint, never a workstation path.
        if (self.source_ref.startswith(("/", "~")) or "/home/" in self.source_ref
                or "/Users/" in self.source_ref or ".." in self.source_ref
                or self.source_ref.endswith("/")):
            raise Hold("invalid_source_reference")
        if self.source_checksum and not re.fullmatch(r"[0-9a-f]{64}", self.source_checksum):
            raise Hold("checksum_required_before_download")
        if not 0 <= self.source_size <= DIGEST_LIMIT:
            raise Hold("source_size_not_stated")
        if self.network_approval and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.network_approval):
            raise Hold("invalid_approval_reference")
        return replace(self, compatibility_status=self.compatibility())


def digest(path, limit=DIGEST_LIMIT):
    """Bounded read; returns the hex digest of at most `limit` bytes."""
    total = 0
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while total < limit:
            block = f.read(min(1 << 20, limit - total))
            if not block:
                break
            h.update(block)
            total += len(block)
    return h.hexdigest(), total


def from_file(path, category, model_id=None, engine="unknown", shared_storage_owner="",
              owned_by_jarvis=False, languages=(), display_name=None, *, checkpoint=lambda: None):
    """Register an existing owner-supplied file read-only."""
    card, _ = inspect_file(path, category, model_id, engine, shared_storage_owner,
                           owned_by_jarvis, languages, display_name, checkpoint=checkpoint)
    return card


def inspect_file(path, category, model_id=None, engine="unknown", shared_storage_owner="",
                 owned_by_jarvis=False, languages=(), display_name=None, *, checkpoint=lambda: None):
    """Hash one pinned regular file, returning a proof for later publication."""
    p = Path(path)
    checkpoint()
    if category not in CATEGORIES:
        raise Hold("unknown_model_category")
    if not p.is_absolute():
        raise Hold("model_file_not_found")
    try:
        info = p.lstat()
    except OSError:
        raise Hold("model_file_not_found") from None
    if not stat.S_ISREG(info.st_mode):
        raise Hold("model_file_not_found")
    size = info.st_size
    if not 0 < size <= DIGEST_LIMIT:
        raise Hold("model_size_out_of_range")
    steps = _artifact_hash_steps(p, size, checkpoint=checkpoint)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        proof = done.value
    finally:
        steps.close()
    if proof.identity != _artifact_identity(info):
        raise Hold("model_file_changed")
    require_import_proof(proof, checkpoint=checkpoint)
    checksum = proof.digest
    if shared_storage_owner and owned_by_jarvis:
        raise Hold("shared_and_owned_conflict")
    return ModelCard(
        model_id=model_id or p.name,
        display_name=display_name or p.name,
        category=category,
        engine=engine,
        artifact_format=SUFFIX_FORMAT.get(p.suffix.lower(), "other"),
        languages=tuple(languages),
        installed=True,
        trust_level="owner",
        shared_storage_owner=shared_storage_owner,
        storage_ref="sha256-" + checksum[:32],
        owned_by_jarvis=owned_by_jarvis,
        disk_size_bytes=size,
        state="REGISTERED",
    ).checked(), proof


def require_import_proof(proof, *, checkpoint=lambda: None):
    """Cheap final identity check; never rehash on the GTK thread."""
    checkpoint()
    try:
        if _artifact_identity(proof.path.lstat()) != proof.identity:
            raise Hold("model_file_changed")
    except OSError:
        raise Hold("model_file_changed") from None
    checkpoint()


async def from_models(client, languages=(), now=0.0):
    """List what a local Jan endpoint actually reports. No model guessing."""
    try:
        model_ids = tuple(await client.models())
    except Hold:
        raise
    except Exception:
        raise Hold("market_inventory_unavailable") from None
    cards = []
    for model_id in dict.fromkeys(model_ids):  # a registry key is unique
        if not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", model_id):
            continue
        cards.append(ModelCard(
            model_id=model_id, display_name=model_id, category="llm", provider="jan",
            engine="jan", artifact_format="other",
            languages=tuple(languages), installed=True, registered=False,
            trust_level="curated", state="DISCOVERED", last_tested_at=now).checked())
    if not cards:
        raise Hold("no_installed_chat_models")
    return tuple(cards)


def register_installed(card):
    """Register an item that already exists and is verified by its own endpoint.

    The download lifecycle stays linear; this never applies to an item that
    is still awaiting a transfer.
    """
    if card.state != DISCOVERED or not card.installed or card.local_or_cloud != "local":
        raise Hold("lifecycle_transition_rejected:" + card.state + "->REGISTERED")
    return replace(card, state=REGISTERED, registered=True)


def advance(card, state):
    """Lifecycle transitions are explicit and auditable."""
    if state not in ALLOWED_NEXT[card.state]:
        raise Hold("lifecycle_transition_rejected:" + card.state + "->" + state)
    return replace(card, state=state, installed=True,
                   registered=state in (REGISTERED, TESTABLE, ACTIVE))


@dataclass(frozen=True)
class Source:
    """An owner-approved download source. It never carries credentials."""

    reference: str = ""        # opaque reference, e.g. a curated catalog entry
    checksum: str = ""         # expected sha256; a transfer never starts without one
    size: int = 0              # expected bytes; 0 means the size is not stated
    version: str = "unknown"
    license_id: str = "unknown"
    network_approval: str = ""  # opaque approval reference
    approval_expires_at: float = 0.0

    def checked(self):
        if not re.fullmatch(r"[A-Za-z0-9:._~/-]{1,256}", self.reference):
            raise Hold("invalid_source_reference")
        if (self.reference.startswith(("/", "~")) or "/home/" in self.reference
                or "/Users/" in self.reference or ".." in self.reference
                or self.reference.endswith("/")):
            raise Hold("invalid_source_reference")
        if not re.fullmatch(r"[0-9a-f]{64}", self.checksum):
            raise Hold("checksum_required_before_download")
        if not 0 <= self.size <= DIGEST_LIMIT:
            # 0 means the source does not state a size; it stays unknown, not zero.
            raise Hold("source_size_not_stated")
        if self.network_approval and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.network_approval):
            raise Hold("invalid_approval_reference")
        if not 0 <= self.approval_expires_at <= 1e12:
            raise Hold("invalid_approval_expiry")
        return self

    def authorized(self, now):
        return bool(self.network_approval) and (not self.approval_expires_at
                                                or self.approval_expires_at > now)


CATALOG_LIMIT = 131072


@dataclass(frozen=True)
class Entry:
    """One curated catalog line. It carries terms, never credentials or paths."""

    model_id: str
    display_name: str
    category: str
    engine: str = "unknown"
    artifact_format: str = "other"
    languages: tuple = ()
    license_id: str = "unknown"
    source_ref: str = ""
    source_checksum: str = ""
    source_size: int = 0
    source_version: str = "unknown"
    requires_gpu: bool = False
    disk_size_bytes: int = 0
    trust_level: str = "curated"
    publisher: str = "unknown"

    def checked(self):
        if not re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", self.source_ref):
            raise Hold("invalid_source_reference")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_checksum):
            raise Hold("checksum_required_before_download")
        if not 0 < self.source_size <= DIGEST_LIMIT:
            raise Hold("source_size_not_stated")
        if any(lang != lang.lower() or not lang.isalpha() for lang in self.languages):
            raise Hold("unsupported_language_tag")
        return ModelCard(
            model_id=self.model_id, display_name=self.display_name, category=self.category,
            engine=self.engine, artifact_format=self.artifact_format, languages=self.languages,
            license_id=self.license_id, installed=False, registered=False,
            trust_level=self.trust_level, disk_size_bytes=self.disk_size_bytes,
            requires_gpu=self.requires_gpu, state="DISCOVERED", source_ref=self.source_ref,
            source_checksum=self.source_checksum, source_size=self.source_size,
            source_version=self.source_version, provider=self.publisher).checked()

    def terms(self, approval="", expires_at=0.0):
        """Published terms. The owner's approval is supplied separately."""
        return Source(self.source_ref, self.source_checksum, self.source_size,
                      self.source_version, self.license_id, approval, expires_at)


@dataclass(frozen=True)
class Catalog:
    """An owner-supplied curated catalog. It is never fetched.

    ``verified`` records whether this client could authenticate the document
    against an owner-managed key. An unverified catalog may be inspected, but
    its entries cannot be added as downloadable items.
    """

    entries: tuple = ()
    source_name: str = "unknown"
    verified: bool = False
    key_ref: str = ""
    signed_at: float = 0.0
    document: dict = field(default_factory=dict)
    signature: dict = field(default_factory=dict)

    def report(self):
        if not self.entries:
            return "empty"
        state = "verified" if self.verified else "unverified"
        extra = " · key " + self.key_ref if self.key_ref else ""
        return "%d entries · %s%s" % (len(self.entries), state, extra)

    def checked(self):
        seen = set()
        for entry in self.entries:
            entry.checked()
            if entry.model_id in seen:
                raise Hold("duplicate_catalog_entry")
            seen.add(entry.model_id)
        return self

    def search(self, category="", language="", engine="", text=""):
        """Filter by the fields the owner can see; nothing is ranked or guessed."""
        out = []
        for entry in self.entries:
            if category and entry.category != category:
                continue
            if language and language not in entry.languages:
                continue
            if engine and engine and entry.engine != engine:
                continue
            if text and not (text.casefold() in entry.display_name.casefold()
                             or text.casefold() in entry.model_id.casefold()):
                continue
            out.append(entry)
        return tuple(out)

    def card(self, model_id):
        for entry in self.entries:
            if entry.model_id == model_id:
                return entry.checked()
        raise Hold("catalog_entry_not_found")

    def cards(self):
        return tuple(entry.checked() for entry in self.entries)


def key_path(name):
    """Owner-managed catalog keys live beside the market data, mode 0600."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise Hold("invalid_key_reference")
    base = os.environ.get("XDG_DATA_HOME")
    folder = Path(base) if base and Path(base).is_absolute() else Path.home() / ".local" / "share"
    return folder / "tihulu-jarvis" / "market" / "keys" / (name + ".key")


def save_key(name, secret):
    """Store an owner-managed catalog key; the secret never leaves that file."""
    if not isinstance(secret, str) or not 16 <= len(secret) <= 256:
        raise Hold("invalid_key_material")
    path = key_path(name)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secret)


def load_key(name):
    """Read an owner-managed catalog key, or report why it cannot be used."""
    try:
        path = key_path(name)
        if path.stat().st_size > 512 or not path.is_file():
            raise Hold("invalid_key_material")
        secret = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        raise Hold("key_unreadable") from None
    if not 16 <= len(secret) <= 256:
        raise Hold("invalid_key_material")
    return secret


def sign_catalog(text, key_name, now=0.0):
    """Attach an owner-managed signature to a catalog document."""
    secret = load_key(key_name)
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        raise Hold("invalid_catalog") from None
    signed_at = now or time.time()
    payload = json.dumps({k: v for k, v in data.items() if k != "signature"},
                         ensure_ascii=False, sort_keys=True).encode("utf-8")
    data["signature"] = {"key_ref": key_name,
                         "mac": hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest(),
                         "signed_at": signed_at}
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def verify_catalog(catalog):
    """Authenticate a parsed catalog against its owner-managed key.

    Returns (verified, reason). It never mutates the document.
    """
    signature = catalog.signature if isinstance(catalog.signature, dict) else {}
    if not signature:
        return False, "catalog_signature_absent"
    try:
        secret = load_key(str(signature.get("key_ref", "")))
    except Hold as exc:
        return False, str(exc)
    payload = json.dumps({k: v for k, v in catalog.document.items() if k != "signature"},
                         ensure_ascii=False, sort_keys=True).encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(signature.get("mac", ""))):
        return False, "catalog_signature_invalid"
    return True, "catalog_signature_verified"


def parse_catalog(text, source_name="owner-supplied"):
    """Parse and validate a curated catalog document."""
    if len(text) > CATALOG_LIMIT:
        raise Hold("catalog_too_large")
    try:
        data = json.loads(text)
    except ValueError:
        raise Hold("invalid_catalog") from None
    raw = data.get("entries", []) if isinstance(data, dict) else None
    if not isinstance(raw, list) or len(raw) > 512:
        raise Hold("invalid_catalog")
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            raise Hold("invalid_catalog")
        try:
            entry = Entry(
                model_id=item["model_id"], display_name=item["display_name"],
                category=item["category"], engine=item.get("engine", "unknown"),
                artifact_format=item.get("artifact_format", "other"),
                languages=tuple(item.get("languages", ())), license_id=item.get("license_id", "unknown"),
                source_ref=item["source_ref"], source_checksum=item["source_checksum"],
                source_size=item.get("source_size", 0),
                source_version=item.get("source_version", "unknown"),
                requires_gpu=bool(item.get("requires_gpu", False)),
                disk_size_bytes=item.get("disk_size_bytes", 0),
                trust_level=item.get("trust_level", "curated"),
                publisher=item.get("publisher", "unknown"))
        except (KeyError, TypeError, ValueError):
            raise Hold("invalid_catalog") from None
        entry.checked()  # validate; the entry is stored, not its derived card
        entries.append(entry)
    signature = data.get("signature") if isinstance(data.get("signature"), dict) else {}
    catalog = Catalog(tuple(entries), str(data.get("source_name", source_name))[:64],
                      verified=False, key_ref=str(signature.get("key_ref", "")),
                      signed_at=float(signature.get("signed_at", 0.0) or 0.0),
                      document=data, signature=signature).checked()
    verified, _ = verify_catalog(catalog)
    return replace(catalog, verified=verified)


def catalog_path():
    """The catalog lives with the rest of the market data, under the owner's XDG dir."""
    return market_dir() / "catalog.json"


def source_of(card):
    """Build the approved download terms recorded on a card."""
    if not card.approved_source():
        raise Hold("download_terms_not_approved")
    return Source(card.source_ref, card.source_checksum, card.source_size, card.source_version,
                 card.license_id, card.network_approval, card.approval_expires_at).checked()


def _partial_binding(card, source):
    terms = {k: v for k, v in source.__dict__.items()
             if k not in ("network_approval", "approval_expires_at")}
    canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False)
    return {"version": 1, "model": card.model_id,
            "card": _text_digest(canonical(card.__dict__)),
            "source": _text_digest(canonical(terms))}


def _read_partial_record(metadata):
    try:
        record = _strict_registry_json(_bounded_registry_text(metadata, 4096))
        if (not isinstance(record, dict)
                or set(record) != {"version", "model", "card", "source", "size", "digest"}
                or type(record["version"]) is not int or record["version"] != 1
                or type(record["size"]) is not int or not 0 < record["size"] <= DIGEST_LIMIT
                or any(not isinstance(record[k], str)
                       or not re.fullmatch(r"[0-9a-f]{64}", record[k])
                       for k in ("card", "source", "digest"))):
            raise Hold("transfer_resume_identity_unverified")
        return record
    except (OSError, ValueError, TypeError, RecursionError, Hold):
        raise Hold("transfer_resume_identity_unverified") from None



def _staging_prefix(model_id):
    if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", model_id):
        raise Hold("invalid_model_id")
    return "tihulu-transfer-" + hashlib.sha256(model_id.encode("utf-8")).hexdigest() + "-"


def _directory_identity(path):
    """Check the actual directory, not a symlink to a different directory."""
    try:
        info = Path(path).lstat()
    except OSError:
        raise Hold("transfer_directory_untrusted") from None
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022):
        raise Hold("transfer_directory_untrusted")
    return info.st_dev, info.st_ino


def _staging_identity(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise Hold("staging_file_untrusted")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode)


def _partial_file_identity(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Hold("partial_file_untrusted")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode)


def resumable_partials(root):
    """Partial transfers on disk, so a restart can continue from them.

    The registry is the authority for what an item is; a partial file only
    says how far a transfer got.
    """
    transfers = Path(root) / "transfers"
    if not transfers.is_dir():
        return ()
    out = []
    for path in sorted(transfers.glob("partial-*")):
        model_id = path.name[len("partial-"):]
        if not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", model_id):
            continue
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and 0 < info.st_size <= DIGEST_LIMIT:
            out.append((model_id, info.st_size))
    return tuple(out)


def transport_for(reference):
    """Build a download transport for an approved source, if one is available.

    Only a numeric loopback http endpoint is supported here. Anything else has
    no transport in this build, which is the honest answer.
    """
    try:
        from .transfer_http import loopback_fetch
    except ImportError:
        return None
    try:
        return loopback_fetch(reference)
    except Hold:
        return None


def _registry_locked(method):
    """Serialize synchronous registry operations; never hold across an await."""
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._write_guard():
            return method(self, *args, **kwargs)
    return locked


def _transfer_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.registry._write_guard():
            return method(self, *args, **kwargs)
    return locked


def _model_leased(method):
    """Acquire model lease before registry lock for destructive operations."""
    @wraps(method)
    def leased(self, model_id, *args, **kwargs):
        with self.transfer_lease(model_id):
            return method(self, model_id, *args, **kwargs)
    return leased


def _leased_transfer(method):
    """Keep a per-model lease across await, not the global registry lock."""
    @wraps(method)
    async def leased(self, *args, **kwargs):
        with self.registry.transfer_lease(self.card.model_id):
            return await method(self, *args, **kwargs)
    return leased


class Transfer:
    """Cancellable, verified transfer into an isolated temporary directory.

    `fetch` is an explicitly authorized transport: an async callable that
    receives a writable path and a progress callback and returns the number of
    bytes it wrote. This layer owns cancellation, integrity and registration;
    it never opens a connection itself.
    """

    def __init__(self, registry, card, source, fetch, root=None, now=None, *, checkpoint=lambda: None):
        self.registry = registry
        self.card = card
        self.source = source.checked()
        self.fetch = fetch
        started = time.monotonic()
        self._clock = time.time if now is None else lambda: now + (time.monotonic() - started)
        self._checkpoint = checkpoint
        self.root = Path(root) if root else registry.root / "transfers"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._root_identity = _directory_identity(self.root)
        self.temp = None
        self._staging_proof = None
        self._publication_stop = threading.Event()
        self.received = 0
        self.state = card.state

    @classmethod
    def from_registry(cls, registry, model_id, *, checkpoint=lambda: None, transport=None,
                      expected=None):
        """Production entrypoint: derive transport and terms from one card.

        An explicitly injected transport is a trusted test/host adapter, not
        a caller-supplied network endpoint. The default transport is selected
        from the exact approved reference. No fallback to other endpoints.
        """
        card = registry.card(model_id)
        if expected is not None and card != expected:
            raise Hold("registry_card_changed")
        source = source_of(card)
        if not source.authorized(time.time()):
            raise Hold("network_not_authorized")
        fetch = transport if transport is not None else transport_for(source.reference)
        if fetch is None:
            raise Hold("download_transport_not_available")
        def bound_checkpoint():
            checkpoint()
            if source_of(registry.card(model_id)) != source:
                raise Hold("download_terms_changed")
        bound_checkpoint()
        return cls(registry, card, source, fetch, checkpoint=bound_checkpoint)

    def _assert_current(self):
        if self.registry.card(self.card.model_id) != self.card:
            raise Hold("transfer_card_changed")

    def _check_directory(self):
        if _directory_identity(self.root) != self._root_identity:
            raise Hold("transfer_directory_changed")

    def _check(self):
        if self._publication_stop.is_set():
            raise Hold("turn_cancelled")
        self._check_directory()
        self._checkpoint()
        if not self.source.authorized(self._clock()):
            raise Hold("network_not_authorized")
        self._assert_current()

    async def _fetch(self, progress, start=None):
        self._check()
        def note(count):
            self._check()
            self.received = count
            if progress:
                progress(count)
            self._check()
        async def run():
            self._check()
            if start is None:
                return await self.fetch(str(self.temp), note)
            return await self.fetch(str(self.temp), note, start)
        return await await_authorized(run, 300, self._check)

    async def _verified(self, written):
        self._check()
        if type(written) is not int or not 0 < written <= DIGEST_LIMIT:
            raise Hold("transfer_reported_invalid_length")
        if self.source.size and written != self.source.size:
            raise Hold("transfer_size_mismatch")
        proof = await _verified_artifact_async(self.temp, written, checkpoint=self._check,
                                               return_proof=True)
        if proof.digest != self.source.checksum:
            raise Hold("transfer_checksum_mismatch")
        self._check()
        self._staging_proof = proof
        return proof.digest

    @_transfer_locked
    def _publish(self, registered, *, replacing):
        self._check()
        if self.registry.card(self.card.model_id) != self.card:
            raise Hold("transfer_card_changed")
        target = self.registry.root / "artifacts" / self.card.model_id
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _directory_identity(target.parent)
        if _exists_entry(target):
            if (not replacing or not self.card.owned_by_jarvis
                    or self.card.shared_storage_owner or self.card.loaded
                    or not stat.S_ISREG(target.lstat().st_mode)):
                raise Hold("artifact_not_replaceable")
            if self.card.artifact_digest:
                self.registry.retain_artifact(self.card, target, checkpoint=self._check)
        saved = self.registry._publish_owned(self.card, registered, self.temp,
                                             checkpoint=self._check,
                                             staging_proof=self._staging_proof)
        self._staging_proof = None
        self.temp = None
        self.card, self.state = saved, saved.state
        return saved

    def request_publication_stop(self):
        """Signal the owned worker synchronously before async cancellation drains."""
        self._publication_stop.set()

    async def _publish_async(self, registered, *, replacing, threaded=False):
        """Opt-in worker for callers with a thread-safe authorization checkpoint.

        No transport/progress callback runs here. Keep the model lease and
        staging ownership until the worker and its recovery have drained.
        """
        if not threaded:
            return self._publish(registered, replacing=replacing)
        self._check()
        worker = asyncio.create_task(asyncio.to_thread(
            self._publish, registered, replacing=replacing))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            self.request_publication_stop()
            try:
                await finish_owned(asyncio.gather(worker, return_exceptions=True))
            except asyncio.CancelledError:
                # finish_owned raises only after its cleanup has completed.
                pass
            try:
                # A late stop cannot undo an already committed generation.
                return worker.result()
            except Hold as exc:
                if str(exc) == "turn_cancelled":
                    raise asyncio.CancelledError from None
                raise

    @_transfer_locked
    def _persist(self, state):
        self._assert_current()
        saved = self.registry.save_card(advance(self.card, state))
        self.card, self.state = saved, saved.state
        return saved

    def _terminal(self, state):
        # A stale operation must never overwrite a newer registry entry.
        try:
            if self.state not in TERMINAL:
                self._persist(state)
        except (Hold, OSError):
            self.state = state

    def _partial_paths(self):
        return (self.root / ("partial-" + self.card.model_id),
                self.root / (".partial-meta-" + self.card.model_id))

    def _partial_binding(self):
        return _partial_binding(self.card, self.source)

    def _read_partial_record(self):
        _, metadata = self._partial_paths()
        return _read_partial_record(metadata)

    def _save_partial(self, temp):
        self._assert_current()
        if not self.source.authorized(self._clock()):
            return
        partial, metadata = self._partial_paths()
        if _exists_entry(partial) or _exists_entry(metadata):
            return
        info = temp.lstat()
        if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size < DIGEST_LIMIT
                or (self.source.size and info.st_size >= self.source.size)):
            return
        identity = _artifact_identity(info)
        if info.st_nlink != 1:
            raise Hold("partial_file_untrusted")
        payload = self.registry._payload_record(temp)
        self._check_directory()
        self._assert_current()
        if not self.source.authorized(self._clock()):
            return
        if identity != _artifact_identity(temp.lstat()):
            raise Hold("partial_changed")
        record = {**self._partial_binding(), **payload}
        # A crash between the two publications leaves an untrusted partial.
        # Sync the payload name before removing its temporary alias.
        os.link(temp, partial, follow_symlinks=False)
        _sync_directory(self.root)
        linked = _artifact_identity(partial.lstat())
        if (linked[:4] != identity[:4] or linked[5] != identity[5]
                or linked[6] != 2 or linked != _artifact_identity(temp.lstat())):
            raise Hold("partial_changed")
        self._check_directory()
        temp.unlink()
        _sync_directory(self.root)
        published = _artifact_identity(partial.lstat())
        if (published[:4] != identity[:4] or published[5] != identity[5]
                or published[6] != 1):
            raise Hold("partial_changed")
        tmp = None
        tmp_owner = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=self.root, delete=False) as stream:
                tmp = Path(stream.name)
                tmp_owner = _artifact_identity(os.fstat(stream.fileno()))[:2]
                json.dump(record, stream, sort_keys=True)
                stream.flush()
                # Capture exact bytes before sync; fsync does not change identity.
                meta_identity = _artifact_identity(os.fstat(stream.fileno()))
                os.fsync(stream.fileno())
            self._check_directory()
            self._assert_current()
            if not self.source.authorized(self._clock()):
                return
            if (published != _artifact_identity(partial.lstat())
                    or meta_identity[6] != 1
                    or meta_identity != _artifact_identity(tmp.lstat())):
                raise Hold("partial_changed")
            os.link(tmp, metadata, follow_symlinks=False)
            _sync_directory(self.root)
            self._check_directory()
            linked_meta = _artifact_identity(metadata.lstat())
            if (linked_meta[:4] != meta_identity[:4]
                    or linked_meta[5] != meta_identity[5]
                    or linked_meta[6] != 2
                    or linked_meta != _artifact_identity(tmp.lstat())):
                raise Hold("partial_changed")
            tmp.unlink()
            tmp = None
            _sync_directory(self.root)
        finally:
            if tmp is not None and tmp_owner is not None:
                self._check_directory()
                try:
                    if _artifact_identity(tmp.lstat())[:2] == tmp_owner:
                        tmp.unlink()
                        _sync_directory(self.root)
                except FileNotFoundError:
                    pass

    def _finish_temp(self, *, keep):
        """Never overwrite another operation's partial or mask a cancellation."""
        if self.temp is None:
            return
        temp = Path(self.temp)
        try:
            self._check_directory()
            initial = temp.lstat()
        except (Hold, OSError):
            # The pathname is no longer ours. Preserve replacement directories.
            self.temp = None
            return
        try:
            if keep:
                with self.registry._write_guard():
                    self._save_partial(temp)
        except (Hold, OSError):
            pass
        finally:
            try:
                self._check_directory()
                actual = temp.lstat()
                if (actual.st_dev, actual.st_ino) == (initial.st_dev, initial.st_ino):
                    temp.unlink()
                    _sync_directory(self.root)
            except (Hold, OSError):
                pass
            self.temp = None

    async def _finish_temp_async(self, *, keep, threaded=False):
        """Drain cleanup before releasing the owning model lease.

        Cleanup must still run after cancellation. Unlike publication it
        cannot reuse a live-turn checkpoint that has already been revoked.
        Only the existing local partial-retention authority is used.
        """
        if self.temp is None:
            return
        if not threaded:
            self._finish_temp(keep=keep)
            return
        await finish_owned(asyncio.to_thread(self._finish_temp, keep=keep))

    def _consume_partial(self, identity, record=None, *, pair=None):
        """Remove only the exact partial consumed by this committed resume."""
        if identity is None:
            return
        try:
            with self.registry._write_guard():
                self._assert_current()
                self._check_directory()
                partial, metadata = self._partial_paths()
                info = partial.lstat()
                actual = (info.st_dev, info.st_ino, info.st_size,
                          info.st_mtime_ns, info.st_ctime_ns, info.st_mode)
                if actual == identity:
                    if pair is not None and self._partial_pair_identity() != pair:
                        return
                    if record is not None:
                        if self._read_partial_record() != record:
                            return
                        metadata.unlink()
                        _sync_directory(self.root)
                    self._check_directory()
                    if _partial_file_identity(partial) != identity:
                        return
                    partial.unlink()
                    _sync_directory(self.root)
        except (Hold, OSError):
            pass

    def _partial_pair_identity(self):
        return tuple(_partial_file_identity(path) for path in self._partial_paths())

    @_leased_transfer
    async def resume(self, progress=None, start=0, allow_installed=False, *, expected_pair=None,
                     threaded_publication=False):
        """Continue an interrupted transfer from a verified byte offset.

        The card's lifecycle is not advanced here: an interrupted item stays
        cancelled until the resumed transfer verifies and registers it.
        """
        if not allow_installed and self.card.state in ("REGISTERED", "TESTABLE", "ACTIVE"):
            raise Hold("model_already_registered")
        self._check()
        if type(start) is not int or not 0 <= start < DIGEST_LIMIT or (self.source.size and start >= self.source.size):
            raise Hold("transfer_resume_offset_unverified")
        if self.card.shared_storage_owner or self.card.loaded:
            raise Hold("artifact_not_replaceable")
        if expected_pair is not None and not start:
            raise Hold("partial_changed")
        # Resume copies the prefix into a separate full-size staging file.
        _require_free_space(self.root, self.source.size)
        fd, name = tempfile.mkstemp(prefix=_staging_prefix(self.card.model_id), dir=str(self.root))
        os.close(fd)
        self.temp = Path(name)
        identity = None
        record = None
        pair = None
        try:
            os.chmod(self.temp, 0o600)
            if start:
                partial, _ = self._partial_paths()
                from .selected_csv import selection_identity, copy_selected_to
                def authorized():
                    self._check()
                    return True
                with self.registry._write_guard():
                    self._check()
                    try:
                        info = partial.lstat()
                    except OSError:
                        raise Hold("transfer_resume_offset_unverified") from None
                    if not stat.S_ISREG(info.st_mode) or info.st_size != start:
                        raise Hold("transfer_resume_offset_unverified")
                    record = self._read_partial_record()
                    try:
                        pair = self._partial_pair_identity()
                    except (Hold, OSError):
                        raise Hold("transfer_resume_identity_unverified") from None
                    if expected_pair is not None and pair != expected_pair:
                        raise Hold("partial_changed")
                    if (record["size"] != start
                            or any(record[k] != v for k, v in self._partial_binding().items())):
                        raise Hold("transfer_resume_identity_unverified")
                    identity = selection_identity(str(partial.absolute()), authorized=authorized, max_bytes=start)
                    if identity[2] != start:
                        raise Hold("transfer_resume_offset_unverified")
                    if identity != pair[0] or self._partial_pair_identity() != pair:
                        raise Hold("partial_changed")
                self.temp.unlink()
                await copy_selected_to(str(partial.absolute()), self.temp, authorized=authorized,
                                       expected_identity=identity, max_bytes=start)
                if await _verified_artifact_async(self.temp, start, checkpoint=self._check) != record["digest"]:
                    raise Hold("transfer_resume_identity_unverified")
                with self.registry._write_guard():
                    self._check()
                    if self._partial_pair_identity() != pair:
                        raise Hold("partial_changed")
            written = await self._fetch(progress, start)
            checksum = await self._verified(written)
            if start and written <= start:
                raise Hold("transfer_resume_made_no_progress")
            # A successful update keeps an installed item installed.
            stays = self.card.state if self.card.state in ("TESTABLE", "ACTIVE") else "REGISTERED"
            registered = replace(self.card, state=stays, registered=True, installed=True,
                                 owned_by_jarvis=True, disk_size_bytes=written,
                                 storage_ref="sha256-" + checksum[:32],
                                 artifact_digest=checksum,
                                 version=self.source.version if self.source.version != "unknown" else self.card.version,
                                 source_version=self.source.version,
                                 license_id=self.source.license_id)
            result = await self._publish_async(registered, replacing=allow_installed,
                                               threaded=threaded_publication)
            # A cleanup failure must not turn a committed result into a failed update.
            self._consume_partial(identity, record, pair=pair)
            return result
        except asyncio.CancelledError:
            self.state = "CANCELLED"
            raise
        except (Hold, OSError, asyncio.TimeoutError):
            self.state = "FAILED"
            raise
        finally:
            await self._finish_temp_async(
                keep=self.state == "CANCELLED" and not allow_installed,
                threaded=threaded_publication)

    async def update(self, progress=None, *, threaded_publication=False):
        """Replace an installed item with its approved source, keeping history.

        The previous entry stays as a revision; the new one registers
        atomically after verification.
        """
        if self.card.state not in ("REGISTERED", "TESTABLE", "ACTIVE"):
            raise Hold("model_not_installed")
        return await self.resume(progress, start=0, allow_installed=True,
                                 threaded_publication=threaded_publication)

    @_leased_transfer
    async def run(self, progress=None, *, threaded_publication=False):
        if self.card.state != DISCOVERED:
            # Nothing already installed is ever overwritten by a transfer.
            raise Hold("model_already_registered")
        self._persist("REVIEWED")
        try:
            self._check()
            _require_free_space(self.root, self.source.size)
        except Hold:
            self._persist("HOLD")
            raise
        self._persist("APPROVED")
        self._persist("QUEUED")
        fd, name = tempfile.mkstemp(prefix=_staging_prefix(self.card.model_id), dir=str(self.root))
        os.close(fd)
        self.temp = Path(name)
        try:
            os.chmod(self.temp, 0o600)
            self._persist("DOWNLOADING")
            written = await self._fetch(progress)
            checksum = await self._verified(written)
            self._persist("VERIFIED")
            registered = replace(self.card, state="REGISTERED", registered=True, installed=True,
                                 owned_by_jarvis=True, disk_size_bytes=written,
                                 storage_ref="sha256-" + checksum[:32],
                                 artifact_digest=checksum,
                                 version=self.source.version if self.source.version != "unknown" else self.card.version,
                                 source_version=self.source.version,
                                  license_id=self.source.license_id)
            return await self._publish_async(registered, replacing=False,
                                             threaded=threaded_publication)
        except asyncio.CancelledError:
            self._terminal("CANCELLED")
            raise
        except (Hold, OSError, asyncio.TimeoutError):
            self._terminal("FAILED")
            raise
        finally:
            await self._finish_temp_async(keep=self.state == "CANCELLED",
                                          threaded=threaded_publication)

    def cancel(self):
        raise Hold("transfer_cancelled_by_owner")


def _require_free_space(directory, size):
    """Advisory preflight for known allocation, never a reservation.

    Concurrent writers and filesystem quotas can still cause ENOSPC.
    Unknown sizes stay unknown; no eviction or deletion is attempted.
    """
    if type(size) is not int or not 0 <= size <= DIGEST_LIMIT:
        raise Hold("invalid_size")
    if size == 0:
        return
    try:
        info = os.statvfs(directory)
        available = info.f_bavail * info.f_frsize
    except (OSError, AttributeError):
        raise Hold("storage_capacity_unavailable") from None
    if available < size:
        raise Hold("insufficient_storage")


def _artifact_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_nlink)


@dataclass(frozen=True)
class _ArtifactProof:
    path: Path
    identity: tuple
    digest: str


def _artifact_hash_steps(path, size, *, checkpoint=lambda: None):
    """Shared bounded hashing iterator; closes descriptor even when abandoned."""
    if type(size) is not int or not 0 < size <= DIGEST_LIMIT:
        raise Hold("transfer_length_mismatch")
    fd = None
    try:
        checkpoint()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
            raise Hold("transfer_length_mismatch")
        h = hashlib.sha256()
        total = 0
        while total < size:
            checkpoint()
            block = os.read(fd, min(1 << 20, size-total))
            if not block:
                raise Hold("transfer_length_mismatch")
            h.update(block)
            total += len(block)
            yield None
        checkpoint()
        after = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
        identity = _artifact_identity(before)
        if identity != _artifact_identity(after) or identity != _artifact_identity(current):
            raise Hold("transfer_artifact_changed")
        return _ArtifactProof(Path(path), identity, h.hexdigest())
    except OSError:
        raise Hold("transfer_artifact_unreadable") from None
    finally:
        if fd is not None:
            os.close(fd)


def _verified_artifact(path, size, *, checkpoint=lambda: None):
    steps = _artifact_hash_steps(path, size, checkpoint=checkpoint)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return done.value.digest
    finally:
        steps.close()


async def _verified_artifact_async(path, size, *, checkpoint=lambda: None, return_proof=False):
    """Yield between bounded reads on the owning event loop.

    No worker can outlive cancellation; permission callbacks remain on their
    original thread. Publication revalidates the exact verified file identity
    under the registry lock; restart recovery always hashes bytes independently.
    This bounds work per iteration, not filesystem I/O latency.
    """
    steps = _artifact_hash_steps(path, size, checkpoint=checkpoint)
    try:
        while True:
            try:
                next(steps)
            except StopIteration as done:
                return done.value if return_proof else done.value.digest
            await asyncio.sleep(0)
    finally:
        steps.close()


def _copy_verified_artifact(source, target, card, *, checkpoint=lambda: None):
    """Create an independent verified copy, never alias an active inode."""
    if not card.artifact_digest or not card.owned_by_jarvis:
        raise Hold("rollback_artifact_unverified")
    fd = None
    try:
        checkpoint()
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != card.disk_size_bytes:
            raise Hold("rollback_artifact_unverified")
        if not 0 < before.st_size <= DIGEST_LIMIT:
            raise Hold("rollback_artifact_unverified")
        h = hashlib.sha256()
        remaining = before.st_size
        with open(target, "xb") as out:
            os.fchmod(out.fileno(), 0o600)
            while remaining:
                checkpoint()
                block = os.read(fd, min(262144, remaining))
                if not block:
                    raise Hold("rollback_artifact_unverified")
                out.write(block)
                h.update(block)
                remaining -= len(block)
            out.flush()
            os.fsync(out.fileno())
            copied = os.fstat(out.fileno())
            copied_identity = _artifact_identity(copied)
        checkpoint()
        after = os.fstat(fd)
        if (_artifact_identity(before) != _artifact_identity(after)
                or _artifact_identity(before) != _artifact_identity(os.stat(source, follow_symlinks=False))
                or h.hexdigest() != card.artifact_digest
                or not stat.S_ISREG(copied.st_mode) or copied.st_nlink != 1
                or copied.st_size != before.st_size
                or copied_identity != _artifact_identity(Path(target).lstat())):
            raise Hold("rollback_artifact_unverified")
        return _ArtifactProof(Path(target), copied_identity, h.hexdigest())
    except OSError:
        raise Hold("rollback_artifact_unverified") from None
    finally:
        if fd is not None:
            os.close(fd)


def _normalized(items):
    """JSON turns tuples into lists; normalize before building cards."""
    out = []
    for item in items:
        row = dict(item)
        row["languages"] = tuple(row.get("languages", ()) or ())
        out.append(row)
    return out


def _bounded_registry_text(path, limit):
    """Read only a bounded regular file, never a symlink or blocking FIFO."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Hold("registry_unreadable")
        if info.st_size > limit:
            raise Hold("registry_too_large")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise Hold("registry_too_large")
        return raw.decode("utf-8")
    finally:
        os.close(fd)


def _strict_registry_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Hold("registry_unreadable")
            result[key] = value
        return result

    def nonfinite(value):
        raise Hold("registry_unreadable")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=nonfinite)


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _exists_entry(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


_UNCONDITIONAL = object()


class Registry:
    """Local registry with per-profile independent STT/LLM/TTS selection."""

    LIMIT = 262144
    SELECTION_LIMIT = 64
    HISTORY_LIMIT = 32
    HISTORY_BYTES = 262144

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "registry.json"
        self.removal_journal = self.root / ".uninstall-journal.json"
        self.publication_journal = self.root / ".publish-journal.json"
        self._guard_state = threading.local()
        # A directory of append-only revision logs; not the history() method.
        self.revisions = self.root / "revisions"
        self.revisions.mkdir(mode=0o700, parents=True, exist_ok=True)

    def recovery_pending(self):
        """Cheap presence check only; the locked recovery still validates everything."""
        return (_exists_entry(self.removal_journal)
                or _exists_entry(self.publication_journal))

    @_registry_locked
    def recovery_snapshot(self):
        """Recover and return local market/queue views under one registry guard.

        Suitable for an owned disk worker; never calls UI or network code.
        A queue-specific error must not hide an otherwise valid registry.
        """
        cards = self.cards()
        try:
            rows = self._pending_partials_for(cards)
        except (Hold, OSError) as exc:
            return cards, (), str(exc)
        return cards, rows, ""

    @contextmanager
    def _write_guard(self):
        """Cooperative nonblocking lock on a stable private inode.

        Never remove the lock file. This protects cooperating instances only,
        not an attacker with write access to the registry directory.
        """
        pid = os.getpid()
        if getattr(self._guard_state, "pid", None) == pid:
            yield
            return
        try:
            import fcntl
        except ImportError:
            raise Hold("registry_lock_unsupported") from None
        if not hasattr(os, "O_NOFOLLOW"):
            raise Hold("registry_lock_unsupported")
        fd = None
        try:
            fd = os.open(self.root / ".registry.lock",
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise Hold("registry_lock_unavailable")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if fd is not None:
                os.close(fd)
            raise Hold("registry_busy") from None
        except (OSError, Hold):
            if fd is not None:
                os.close(fd)
            raise Hold("registry_lock_unavailable") from None
        self._guard_state.pid = pid
        try:
            if _exists_entry(self.removal_journal) and _exists_entry(self.publication_journal):
                raise Hold("registry_recovery_conflict")
            self._recover_removal()
            self._recover_publication()
            yield
        finally:
            self._guard_state.pid = None
            os.close(fd)

    @staticmethod
    def _document(text):
        if len(text.encode("utf-8")) > Registry.LIMIT:
            raise Hold("registry_too_large")
        data = _strict_registry_json(text)
        if not isinstance(data, dict):
            raise Hold("registry_unreadable")
        return data

    @staticmethod
    def _parse(text):
        try:
            data = Registry._document(text)
            rows = data.get("cards", [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise Hold("registry_unreadable")
            cards = tuple(ModelCard(**item).checked() for item in _normalized(rows))
            if (len({card.model_id for card in cards}) != len(cards)
                    or any(type(card.revision) is not int or card.revision < 0 for card in cards)):
                raise Hold("registry_unreadable")
            choices_by_profile = data.get("selection", {})
            if not isinstance(choices_by_profile, dict):
                raise Hold("invalid_selection")
            if len(choices_by_profile) > Registry.SELECTION_LIMIT:
                raise Hold("selection_profile_limit")
            selection = {}
            for profile, choices in choices_by_profile.items():
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", profile) or not isinstance(choices, dict):
                    raise Hold("invalid_selection")
                clean = {}
                for category, model_id in choices.items():
                    if category not in CATEGORIES or not isinstance(model_id, str):
                        raise Hold("invalid_selection")
                    clean[category] = model_id
                selection[profile] = clean
            Registry._validate_history(data.get("history", {}))
            return cards, selection
        except (ValueError, TypeError, AttributeError, RecursionError, OverflowError):
            raise Hold("registry_unreadable") from None

    @staticmethod
    def _dump(cards, selection):
        return json.dumps({"cards": [card.__dict__ for card in cards],
                           "selection": selection}, ensure_ascii=False, sort_keys=True)

    def _read_document(self):
        return _bounded_registry_text(self.path, self.LIMIT)

    @_registry_locked
    def load(self):
        try:
            return self._parse(self._read_document())
        except FileNotFoundError:
            return (), {}
        except (ValueError, OSError):
            raise Hold("registry_unreadable") from None

    @staticmethod
    def _validate_history(history):
        if not isinstance(history, dict):
            raise Hold("registry_unreadable")
        for model, rows in history.items():
            if (not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", model)
                    or not isinstance(rows, list) or len(rows) > Registry.HISTORY_LIMIT):
                raise Hold("registry_unreadable")
            previous = -1
            for row in rows:
                if not isinstance(row, dict):
                    raise Hold("registry_unreadable")
                card = ModelCard(**_normalized([row])[0]).checked()
                if card.model_id != model or type(card.revision) is not int or card.revision <= previous:
                    raise Hold("registry_unreadable")
                previous = card.revision
        return history

    def _inline_history(self):
        try:
            raw = self._read_document()
            return self._validate_history(self._document(raw).get("history", {}))
        except FileNotFoundError:
            return {}
        except (ValueError, OSError, TypeError, AttributeError, RecursionError, OverflowError):
            raise Hold("registry_unreadable") from None

    def _serialized(self, cards, selection, *, history=None):
        document = json.loads(self._dump(cards, selection))
        document["history"] = self._inline_history() if history is None else history
        text = json.dumps(document, ensure_ascii=False, sort_keys=True)
        if len(text.encode("utf-8")) > self.LIMIT:
            raise Hold("registry_too_large")
        self._parse(text)
        if len(selection) > self.SELECTION_LIMIT:
            raise Hold("selection_profile_limit")
        return text

    @_registry_locked
    def store(self, cards, selection, *, history=None):
        text = self._serialized(cards, selection, history=history)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(self.root), delete=False) as f:
                tmp = f.name
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            tmp = None
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)

    def cards(self):
        return self.load()[0]

    def lookup_card(self, model_id):
        return next((card for card in self.cards() if card.model_id == model_id), None)

    def card(self, model_id):
        card = self.lookup_card(model_id)
        if card is None:
            raise Hold("model_not_registered")
        return card

    @_registry_locked
    def snapshot(self):
        """Return an exact generation token with the decoded registry."""
        try:
            raw = self._read_document()
        except FileNotFoundError:
            return None, (), {}
        cards, selection = self._parse(raw)
        return _text_digest(raw), cards, selection

    @_registry_locked
    def store_if_current(self, generation, cards, selection):
        current, _, _ = self.snapshot()
        if current != generation:
            raise Hold("registry_generation_changed")
        self.store(cards, selection)

    def _prepared_card(self, card):
        card = card.checked()
        cards, selection = self.load()
        previous = None
        for known in cards:
            if known.model_id == card.model_id:
                previous = known
        history = self._inline_history()
        if previous is not None and previous != card:
            card = replace(card, revision=previous.revision + 1)
            rows = list(self.history(previous.model_id))
            if rows and rows[-1].revision >= previous.revision:
                raise Hold("registry_history_conflict")
            rows.append(previous)
            history[previous.model_id] = [row.__dict__ for row in rows[-self.HISTORY_LIMIT:]]
        elif previous is None:
            rows = self.history(card.model_id)
            if rows:
                card = replace(card, revision=rows[-1].revision + 1)
        kept = tuple(c for c in cards if c.model_id != card.model_id)
        # Current card and its revision history share one atomic JSON replace.
        # Legacy jsonl files remain read-only fallback; no destructive migration.
        return card, kept + (card,), selection, history

    @_registry_locked
    def save_card(self, card, *, expected=_UNCONDITIONAL):
        """Persist with optional exact previous-card precondition.

        None means the model must not exist; the legacy unconditional API
        remains available to explicitly trusted internal callers.
        """
        if expected is not _UNCONDITIONAL and self.lookup_card(card.model_id) != expected:
            raise Hold("registry_card_changed")
        card, cards, selection, history = self._prepared_card(card)
        self.store(cards, selection, history=history)
        return card

    @staticmethod
    def _jan_inventory_card(card):
        return (card.category == "llm" and card.provider == "jan"
                and card.engine == "jan" and card.local_or_cloud == "local"
                and card.installed and not card.owned_by_jarvis
                and not card.shared_storage_owner and not card.storage_ref
                and not card.artifact_digest and not card.source_ref
                and not card.source_checksum and not card.network_approval
                and card.state in ("DISCOVERED", "REGISTERED", "TESTABLE", "ACTIVE"))

    @_registry_locked
    def record_jan_inventory(self, cards):
        """Preflight all IDs before refreshing only compatible Jan records."""
        incoming = tuple(card.checked() for card in cards)
        if len({card.model_id for card in incoming}) != len(incoming):
            raise Hold("registry_discovery_conflict")
        current, selection = self.load()
        known = {card.model_id: card for card in current}
        additions = []
        for card in incoming:
            previous = known.get(card.model_id)
            if (not self._jan_inventory_card(card) or card.state != "DISCOVERED"
                    or card.registered or card.loaded
                    or (previous is not None and not self._jan_inventory_card(previous))):
                raise Hold("registry_discovery_conflict")
            if previous is None:
                rows = self.history(card.model_id)
                if rows:
                    # A forgotten foreign identity must not inherit Jan provenance.
                    if any(not self._jan_inventory_card(row) for row in rows):
                        raise Hold("registry_discovery_conflict")
                    card = replace(card, revision=rows[-1].revision + 1)
                additions.append(card)
            # Keep existing Jan metadata, state, revision and health.
        if additions:
            # Publish the entire validated inventory in one registry generation.
            self.store(current + tuple(additions), selection)
        return tuple(card.model_id for card in incoming)

    @_registry_locked
    def record_discovered(self, card):
        """Discovery cannot overwrite installed or already approved records."""
        card = card.checked()
        if card.state != "DISCOVERED" or card.installed or card.registered or card.owned_by_jarvis:
            raise Hold("invalid_discovery_record")
        card = replace(card, network_approval="", approval_expires_at=0.0)
        previous = self.lookup_card(card.model_id)
        if previous is not None:
            if (previous.state != "DISCOVERED" or previous.installed or previous.registered
                    or previous.owned_by_jarvis or previous.loaded):
                raise Hold("registry_discovery_conflict")
            comparable = replace(previous, revision=0, network_approval="", approval_expires_at=0.0)
            if comparable == replace(card, revision=0):
                return previous
            if previous.network_approval:
                raise Hold("registry_discovery_conflict")
        return self.save_card(card, expected=previous)

    @_registry_locked
    def approve_catalog_entry(self, entry, approval, expires_at):
        """Approve only the exact discovered card represented by the entry.

        The caller must already have authenticated the selected catalog.
        """
        selected = entry.checked()
        terms = entry.terms(approval, expires_at).checked()
        if not terms.authorized(time.time()):
            raise Hold("network_not_authorized")
        known = self.lookup_card(selected.model_id)
        if known is None:
            known = self.save_card(selected, expected=None)
        if known.state != "DISCOVERED":
            raise Hold("lifecycle_transition_rejected:" + known.state + "->REVIEWED")
        comparable = replace(known, revision=0, network_approval="", approval_expires_at=0.0)
        if comparable != replace(selected, revision=0):
            raise Hold("catalog_entry_changed")
        return self.save_card(replace(known, network_approval=approval,
                                      approval_expires_at=expires_at), expected=known)

    @_registry_locked
    def history(self, model_id):
        """Previous revisions of one item, oldest first. Never invented."""
        if not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", model_id):
            raise Hold("invalid_model_id")
        inline = self._inline_history()
        if model_id in inline:
            return tuple(ModelCard(**row).checked() for row in _normalized(inline[model_id]))
        path = self.revisions / (model_id + ".jsonl")
        out = []
        try:
            for line in _bounded_registry_text(path, self.HISTORY_BYTES).splitlines():
                if line:
                    item = _strict_registry_json(line)
                    if not isinstance(item, dict):
                        raise Hold("registry_unreadable")
                    row = ModelCard(**_normalized([item])[0]).checked()
                    if (row.model_id != model_id or type(row.revision) is not int or row.revision < 0
                            or (out and out[-1].revision >= row.revision)
                            or len(out) >= self.HISTORY_LIMIT):
                        raise Hold("registry_history_conflict")
                    out.append(row)
        except FileNotFoundError:
            return ()
        except (ValueError, OSError, TypeError, AttributeError, RecursionError, OverflowError):
            raise Hold("registry_unreadable") from None
        return tuple(out)

    def _archive_path(self, card):
        card.checked()
        if not card.artifact_digest or not card.owned_by_jarvis:
            raise Hold("rollback_artifact_unverified")
        base = self.root / "artifact-revisions"
        folder = base / card.model_id
        for path in (base, folder):
            path.mkdir(mode=0o700, exist_ok=True)
            try:
                _directory_identity(path)
            except Hold:
                raise Hold("artifact_archive_unavailable") from None
        return folder / card.artifact_digest

    def _archive_staging_directory(self, model_id):
        _staging_prefix(model_id)  # Validate the model ID before building a path.
        base = self.root / "artifact-revisions"
        folder = base / model_id
        for path in (base, folder):
            try:
                info = path.lstat()
            except FileNotFoundError:
                return None
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077):
                raise Hold("artifact_archive_unavailable")
        return folder

    @_model_leased
    @_registry_locked
    def archive_staging_candidates(self, model_id):
        """Review only single-link, private, pre-publication archive staging.

        Digest-named revisions and interrupted post-link pairs are excluded.
        No directory is created by inspection; no bytes are changed.
        """
        self.card(model_id)
        folder = self._archive_staging_directory(model_id)
        if folder is None:
            return ()
        directory = _directory_identity(folder)
        candidates = []
        with os.scandir(folder) as entries:
            for count, entry in enumerate(entries, 1):
                if count > 4096:
                    raise Hold("staging_scan_limit")
                if not re.fullmatch(r"\.archive-[a-z0-9_]{8}", entry.name):
                    continue
                try:
                    identity = _staging_identity(Path(entry.path))
                except (Hold, OSError):
                    continue
                candidates.append((entry.name, identity, directory))
                if len(candidates) > 128:
                    raise Hold("staging_candidate_limit")
        if directory != _directory_identity(folder):
            raise Hold("staging_changed")
        return tuple(sorted(candidates))

    @_model_leased
    @_registry_locked
    def discard_archive_staging(self, model_id, name, expected_identity, expected_directory):
        """Explicitly delete only the reviewed archive temporary inode."""
        self.card(model_id)
        if not isinstance(name, str) or not re.fullmatch(r"\.archive-[a-z0-9_]{8}", name):
            raise Hold("staging_name_untrusted")
        folder = self._archive_staging_directory(model_id)
        if folder is None or _directory_identity(folder) != expected_directory:
            raise Hold("staging_changed")
        path = folder / name
        try:
            actual = _staging_identity(path)
        except (Hold, OSError):
            raise Hold("staging_changed") from None
        if actual != expected_identity:
            raise Hold("staging_changed")
        path.unlink()
        _sync_directory(folder)

    def _recover_archive_link(self, card, source, target, archive_info, *, checkpoint):
        """Finish only an exact, verified post-link interrupted archive copy.

        The registry lock excludes cooperating writers. Unknown or incomplete
        staging files are preserved for explicit review, not guessed at.
        """
        identity = _artifact_identity(archive_info)
        source_identity = _artifact_identity(Path(source).lstat())
        if (archive_info.st_nlink != 2 or archive_info.st_uid != os.geteuid()
                or archive_info.st_mode & 0o077
                or source_identity[:2] == identity[:2]):
            raise Hold("rollback_artifact_unverified")
        matches = []
        with os.scandir(target.parent) as entries:
            for count, entry in enumerate(entries, 1):
                checkpoint()
                if count > 4096:
                    raise Hold("artifact_archive_unavailable")
                if not re.fullmatch(r"\.archive-[a-z0-9_]{8}", entry.name):
                    continue
                candidate = Path(entry.path)
                info = candidate.lstat()
                if _artifact_identity(info) == identity:
                    matches.append(candidate)
        if len(matches) != 1:
            raise Hold("rollback_artifact_unverified")
        temporary = matches[0]
        if _verified_artifact(target, card.disk_size_bytes,
                              checkpoint=checkpoint) != card.artifact_digest:
            raise Hold("rollback_artifact_unverified")
        # Establish durable publication before removing the redundant name.
        _sync_directory(target.parent)
        _sync_directory(target.parent.parent)
        _sync_directory(self.root)
        checkpoint()
        if (identity != _artifact_identity(target.lstat())
                or identity != _artifact_identity(temporary.lstat())
                or source_identity != _artifact_identity(Path(source).lstat())):
            raise Hold("rollback_artifact_unverified")
        temporary.unlink()
        _sync_directory(target.parent)

    @_registry_locked
    def retain_artifact(self, card, source, *, checkpoint=lambda: None):
        if card.shared_storage_owner or card.loaded:
            raise Hold("artifact_not_replaceable")
        target = self._archive_path(card)
        if _exists_entry(target):
            # A retained revision must not alias the active file. Reuse only
            # after checking both contents and making the archive durable.
            try:
                source_info, archive_info = Path(source).lstat(), target.lstat()
                if (not stat.S_ISREG(archive_info.st_mode)
                        or (source_info.st_dev, source_info.st_ino)
                        == (archive_info.st_dev, archive_info.st_ino)):
                    raise Hold("rollback_artifact_unverified")
                if archive_info.st_nlink == 2:
                    self._recover_archive_link(card, source, target, archive_info,
                                               checkpoint=checkpoint)
                    archive_info = target.lstat()
                if archive_info.st_nlink != 1:
                    raise Hold("rollback_artifact_unverified")
                expected = {"size": card.disk_size_bytes, "digest": card.artifact_digest}
                if self._payload_record(target, checkpoint=checkpoint) != expected:
                    raise Hold("rollback_artifact_unverified")
                if _verified_artifact(source, card.disk_size_bytes, checkpoint=checkpoint) != card.artifact_digest:
                    raise Hold("rollback_artifact_unverified")
                _sync_directory(target.parent)
                _sync_directory(target.parent.parent)
                _sync_directory(self.root)
                if (_artifact_identity(archive_info) != _artifact_identity(target.lstat())
                        or _artifact_identity(source_info) != _artifact_identity(Path(source).lstat())):
                    raise Hold("rollback_artifact_unverified")
                return target
            except OSError:
                raise Hold("rollback_artifact_unverified") from None
        # Bounded retention, no automatic deletion or eviction of older bytes.
        count = 0
        total = 0
        for entry in target.parent.iterdir():
            checkpoint()
            info = entry.lstat()
            if not stat.S_ISREG(info.st_mode) or not re.fullmatch(r"[0-9a-f]{64}", entry.name):
                raise Hold("artifact_archive_unavailable")
            count += 1
            total += info.st_size
            if count >= self.HISTORY_LIMIT or total + card.disk_size_bytes > 2 * DIGEST_LIMIT:
                raise Hold("artifact_archive_limit")
        _require_free_space(target.parent, card.disk_size_bytes)
        fd, name = tempfile.mkstemp(prefix=".archive-", dir=target.parent)
        os.close(fd)
        temp = Path(name)
        temp.unlink()
        try:
            source_identity = _artifact_identity(Path(source).lstat())
            proof = _copy_verified_artifact(source, temp, card, checkpoint=checkpoint)
            checkpoint()
            if (proof.identity != _artifact_identity(temp.lstat())
                    or source_identity != _artifact_identity(Path(source).lstat())):
                raise Hold("rollback_artifact_unverified")
            # No overwrite, even if another operation published the same digest.
            os.link(temp, target, follow_symlinks=False)
            _sync_directory(target.parent)
            _sync_directory(target.parent.parent)
            _sync_directory(self.root)
            temp.unlink()
            _sync_directory(target.parent)
            return target
        except FileExistsError:
            raise Hold("artifact_archive_changed") from None
        finally:
            temp.unlink(missing_ok=True)

    def _restore_owned(self, current, candidate, restored, *, checkpoint=lambda: None):
        checkpoint()
        if (not current.owned_by_jarvis or not candidate.owned_by_jarvis
                or current.shared_storage_owner or candidate.shared_storage_owner
                or current.loaded or candidate.loaded or not current.artifact_digest
                or not candidate.artifact_digest):
            raise Hold("rollback_artifact_unverified")
        target = self.root / "artifacts" / current.model_id
        try:
            _directory_identity(target.parent)
        except Hold:
            raise Hold("rollback_artifact_unverified") from None
        if current.artifact_digest == candidate.artifact_digest:
            identity = _artifact_identity(target.lstat())
            if _verified_artifact(target, candidate.disk_size_bytes,
                                  checkpoint=checkpoint) != candidate.artifact_digest:
                raise Hold("rollback_artifact_unverified")
            checkpoint()
            if identity != _artifact_identity(target.lstat()):
                raise Hold("rollback_artifact_unverified")
            return self.save_card(restored)
        archive = self._archive_path(candidate)
        try:
            info = archive.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise Hold("rollback_artifact_unverified")
            if info.st_nlink == 2:
                self._recover_archive_link(candidate, target, archive, info,
                                           checkpoint=checkpoint)
                info = archive.lstat()
            if info.st_nlink != 1:
                raise Hold("rollback_artifact_unverified")
        except OSError:
            raise Hold("rollback_artifact_unverified") from None
        if _verified_artifact(archive, candidate.disk_size_bytes,
                              checkpoint=checkpoint) != candidate.artifact_digest:
            raise Hold("rollback_artifact_unverified")
        self.retain_artifact(current, target, checkpoint=checkpoint)
        checkpoint()
        _require_free_space(target.parent, candidate.disk_size_bytes)
        fd, name = tempfile.mkstemp(prefix=".rollback-", dir=target.parent)
        os.close(fd)
        temp = Path(name)
        temp.unlink()
        try:
            proof = _copy_verified_artifact(archive, temp, candidate, checkpoint=checkpoint)
            return self._publish_owned(current, restored, temp, checkpoint=checkpoint,
                                       staging_proof=proof)
        finally:
            temp.unlink(missing_ok=True)

    @_model_leased
    @_registry_locked
    def rollback(self, model_id, revision, *, expected=None, checkpoint=lambda: None):
        """Restore a previous revision as current, as a new revision.

        Check cancellation before publication; once a generation commits,
        independent recovery completes without cancellation checkpoints.
        """
        checkpoint()
        current = self.card(model_id)
        if expected is not None and current != expected:
            raise Hold("registry_card_changed")
        if current.loaded:
            raise Hold("model_loaded")
        for candidate in self.history(model_id):
            if candidate.revision == revision:
                if candidate.model_id != model_id:
                    raise Hold("rollback_artifact_unverified")
                restored = replace(current, **{k: v for k, v in candidate.__dict__.items()
                                              if k not in ("revision", "state")})
                if current.owned_by_jarvis or candidate.owned_by_jarvis:
                    # Historical approvals are never restored with a revision.
                    restored = replace(restored, network_approval="", approval_expires_at=0.0,
                                       loaded=False, health_status=UNKNOWN, last_tested_at=0.0,
                                       state="REGISTERED", installed=True, registered=True)
                    return self._restore_owned(current, candidate, restored,
                                               checkpoint=checkpoint)
                checkpoint()
                return self.save_card(restored)
        raise Hold("revision_not_found")

    @_registry_locked
    def select(self, profile, category, model_id):
        """Independent STT/LLM/TTS choice per profile, applied only when ACTIVE."""
        if category not in CATEGORIES or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", profile):
            raise Hold("invalid_selection")
        card = self.card(model_id)
        if card.category != category or card.state != "ACTIVE":
            raise Hold("model_not_selectable")
        current = dict(self.load()[1].get(profile, {}))
        current[category] = model_id
        cards, selection = self.load()
        self.store(cards, {**selection, profile: current})
        return current

    def selection(self, profile):
        return dict(self.load()[1].get(profile, {}))

    @contextmanager
    def transfer_lease(self, model_id):
        """Nonblocking, cooperative per-model lease, released on process exit.

        Stable lock files are intentionally never removed. A registry lock
        must not be held while awaiting network operations.
        """
        if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", model_id):
            raise Hold("invalid_model_id")
        try:
            import fcntl
        except ImportError:
            raise Hold("transfer_lock_unsupported") from None
        if not hasattr(os, "O_NOFOLLOW"):
            raise Hold("transfer_lock_unsupported")
        folder = self.root / ".transfer-locks"
        fd = None
        try:
            folder.mkdir(mode=0o700, exist_ok=True)
            info = folder.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077):
                raise Hold("transfer_lock_unavailable")
            fd = os.open(folder / (model_id + ".lock"),
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise Hold("transfer_lock_unavailable")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if fd is not None:
                os.close(fd)
            raise Hold("transfer_busy") from None
        except (OSError, Hold):
            if fd is not None:
                os.close(fd)
            raise Hold("transfer_lock_unavailable") from None
        try:
            yield
        finally:
            os.close(fd)

    def _staging_directory(self):
        folder = self.root / "transfers"
        try:
            info = folder.lstat()
        except FileNotFoundError:
            return None
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            raise Hold("staging_directory_untrusted")
        return folder

    @_model_leased
    @_registry_locked
    def staging_candidates(self, model_id):
        """Inspect model-bound abandoned staging files; never delete implicitly.

        The lease excludes cooperating live transfers. Legacy names without
        a model binding and custom staging roots are deliberately excluded.
        """
        prefix = _staging_prefix(model_id)
        folder = self._staging_directory()
        if folder is None:
            return ()
        candidates = []
        # Bound total entries, including unrelated names; do not materialize
        # an unbounded directory listing while holding the registry lock.
        with os.scandir(folder) as entries:
            for count, entry in enumerate(entries, 1):
                if count > 4096:
                    raise Hold("staging_scan_limit")
                if not re.fullmatch(re.escape(prefix) + r"[a-z0-9_]{8}", entry.name):
                    continue
                try:
                    identity = _staging_identity(Path(entry.path))
                except (Hold, OSError):
                    continue
                candidates.append((entry.name, identity))
                if len(candidates) > 128:
                    raise Hold("staging_candidate_limit")
        return tuple(sorted(candidates))

    @_model_leased
    @_registry_locked
    def discard_staging(self, model_id, name, expected_identity):
        """Explicitly remove one exact, privately owned, inactive staging file."""
        prefix = _staging_prefix(model_id)
        if not isinstance(name, str) or not re.fullmatch(re.escape(prefix) + r"[a-z0-9_]{8}", name):
            raise Hold("staging_name_untrusted")
        folder = self._staging_directory()
        if folder is None:
            raise Hold("staging_changed")
        path = folder / name
        try:
            actual = _staging_identity(path)
        except FileNotFoundError:
            raise Hold("staging_changed") from None
        if actual != expected_identity:
            raise Hold("staging_changed")
        path.unlink()
        _sync_directory(folder)

    def _queue_partial_paths(self, model_id):
        if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", model_id):
            raise Hold("invalid_model_id")
        folder = self.root / "transfers"
        try:
            folder.lstat()
        except FileNotFoundError:
            pass
        else:
            try:
                _directory_identity(folder)
            except Hold:
                raise Hold("partial_file_untrusted") from None
        return folder / ("partial-" + model_id), folder / (".partial-meta-" + model_id)

    @_registry_locked
    def partial_status(self, model_id):
        """Cheap metadata-only inspection. Resume verifies the actual prefix."""
        return self._partial_status_for(model_id, self.lookup_card(model_id))

    def _partial_status_for(self, model_id, card):
        payload, metadata = self._queue_partial_paths(model_id)
        identity = (_partial_file_identity(payload), _partial_file_identity(metadata))
        if identity == (None, None):
            return None
        size = identity[0][2] if identity[0] else 0
        state = "UNVERIFIED"
        try:
            if card is None:
                raise Hold("model_not_registered")
            source = source_of(card)
            record = _read_partial_record(metadata)
            if (identity[0] is not None and 0 < size < DIGEST_LIMIT
                    and (not source.size or size < source.size)
                    and card.state not in ("REGISTERED", "TESTABLE", "ACTIVE")
                    and not card.loaded and not card.shared_storage_owner
                    and source.authorized(time.time())
                    and record["size"] == size
                    and all(record[k] == v for k, v in _partial_binding(card, source).items())):
                state = "RESUMABLE"
        except Hold:
            pass
        # A metadata read is not a stable snapshot if either name changed.
        self._queue_partial_paths(model_id)
        if identity != (_partial_file_identity(payload), _partial_file_identity(metadata)):
            raise Hold("partial_changed")
        return {"state": state, "size": size, "identity": identity}

    @_registry_locked
    def pending_partials(self):
        """Inspect known registry IDs only, without enumerating the directory.

        A registry snapshot bounds work and avoids re-reading it per card.
        Untrusted entries are visible but cannot be resumed or removed.
        """
        return self._pending_partials_for(self.cards())

    def _pending_partials_for(self, cards):
        folder = self.root / "transfers"
        try:
            folder.lstat()
        except FileNotFoundError:
            return ()
        _directory_identity(folder)
        out = []
        for card in sorted(cards, key=lambda card: card.model_id):
            if card.state in ("REGISTERED", "TESTABLE", "ACTIVE"):
                continue
            try:
                status = self._partial_status_for(card.model_id, card)
            except (Hold, OSError):
                status = {"state": "UNVERIFIED", "size": 0, "identity": None}
            if status is not None:
                out.append((card.model_id, card.source_size, status))
        return tuple(out)

    @_model_leased
    @_registry_locked
    def retry_transfer(self, model_id, expected_card):
        """Owner-requested restart of an inactive, uninstalled transfer.

        Journal recovery precedes the exact card check. Never infer that
        abandoned bytes were installed; never delete or reuse them here.
        Existing approval is neither extended nor recreated.
        """
        current = self.card(model_id)
        if current != expected_card:
            raise Hold("registry_card_changed")
        if (current.state not in RETRYABLE_TRANSFER or current.registered
                or current.loaded or current.owned_by_jarvis
                or current.shared_storage_owner or current.storage_ref
                or current.artifact_digest or current.local_or_cloud != "local"
                or not current.source_ref or not current.source_checksum):
            raise Hold("transfer_retry_not_available")
        target = self.root / "artifacts" / model_id
        if _exists_entry(target):
            raise Hold("transfer_retry_artifact_exists")
        restarted = replace(current, state=DISCOVERED, installed=False, registered=False)
        return self.save_card(restarted, expected=current)

    @_model_leased
    @_registry_locked
    def discard_partial(self, model_id, expected_identity):
        """Explicitly discard the two exact files observed by the owner."""
        payload, metadata = self._queue_partial_paths(model_id)
        actual = (_partial_file_identity(payload), _partial_file_identity(metadata))
        if actual != expected_identity or actual == (None, None):
            raise Hold("partial_changed")
        directory = _directory_identity(payload.parent)
        # Remove the binding first: interruption cannot leave trusted bytes.
        if actual[1] is not None:
            metadata.unlink()
            _sync_directory(metadata.parent)
        if actual[0] is not None:
            if (directory != _directory_identity(payload.parent)
                    or actual[0] != _partial_file_identity(payload)):
                raise Hold("partial_changed")
            payload.unlink()
            _sync_directory(payload.parent)

    def _without_card(self, model_id):
        cards, selection = self.load()
        kept = tuple(c for c in cards if c.model_id != model_id)
        if len(kept) == len(cards):
            raise Hold("model_not_registered")
        cleaned = {p: {k: v for k, v in choices.items() if v != model_id}
                   for p, choices in selection.items()}
        cleaned = {p: v for p, v in cleaned.items() if v}
        return kept, cleaned

    @_model_leased
    @_registry_locked
    def unregister(self, model_id, *, expected=None):
        """Remove a registry entry. This never deletes a file by itself."""
        card = self.card(model_id)
        if expected is not None and card != expected:
            raise Hold("registry_card_changed")
        if card.loaded:
            raise Hold("model_loaded")
        self.store(*self._without_card(model_id))

    def _write_removal_journal(self, record):
        self._write_journal(self.removal_journal, record)

    def _write_journal(self, path, record):
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=self.root, delete=False) as stream:
                temp = Path(stream.name)
                json.dump(record, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temp, path, follow_symlinks=False)
            _sync_directory(self.root)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    @staticmethod
    def _payload_record(path, *, checkpoint=lambda: None, proof=None):
        path = Path(path)
        checkpoint()
        info = path.lstat()
        if proof is None:
            digest = _verified_artifact(path, info.st_size, checkpoint=checkpoint)
        else:
            # Internal, ephemeral evidence from the completed async hash;
            # never accept a persisted or caller-supplied digest as proof.
            if (not isinstance(proof, _ArtifactProof) or proof.path != path
                    or proof.identity != _artifact_identity(info)
                    or info.st_nlink != 1):
                raise Hold("transfer_artifact_changed")
            digest = proof.digest
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            identity = _artifact_identity(info)
            if not stat.S_ISREG(info.st_mode) or identity != _artifact_identity(os.fstat(fd)):
                raise Hold("artifact_not_replaceable")
            os.fsync(fd)
            checkpoint()
            if (identity != _artifact_identity(os.fstat(fd))
                    or identity != _artifact_identity(path.lstat())):
                raise Hold("transfer_artifact_changed")
        finally:
            os.close(fd)
        return {"size": info.st_size, "digest": digest}

    def _publish_owned(self, current, candidate, staged, *, checkpoint=lambda: None, staging_proof=None):
        """Publish verified bytes with a recovery record; caller holds lock."""
        if self.card(current.model_id) != current or current.model_id != candidate.model_id:
            raise Hold("transfer_card_changed")
        if not candidate.owned_by_jarvis or not candidate.artifact_digest:
            raise Hold("artifact_not_replaceable")
        target = self.root / "artifacts" / current.model_id
        backup = target.parent / ".publish-backup"
        try:
            _directory_identity(target.parent)
        except Hold:
            raise Hold("publication_recovery_required") from None
        if _exists_entry(backup):
            raise Hold("publication_recovery_required")
        staged = Path(staged)
        incoming = self._payload_record(staged, checkpoint=checkpoint, proof=staging_proof)
        if incoming != {"size": candidate.disk_size_bytes, "digest": candidate.artifact_digest}:
            raise Hold("transfer_checksum_mismatch")
        previous = None
        if _exists_entry(target):
            if not current.owned_by_jarvis or current.shared_storage_owner or current.loaded:
                raise Hold("artifact_not_replaceable")
            previous = self._payload_record(target, checkpoint=checkpoint)
            if current.artifact_digest and previous != {
                    "size": current.disk_size_bytes, "digest": current.artifact_digest}:
                raise Hold("artifact_not_replaceable")
        elif current.artifact_digest:
            raise Hold("artifact_not_replaceable")
        old = self._read_document()
        prepared, cards, selection, history = self._prepared_card(candidate)
        new = self._serialized(cards, selection, history=history)
        checkpoint()
        if old == new:
            if previous != incoming:
                raise Hold("publication_recovery_required")
            staged.unlink()
            return prepared
        record = {"version": 1, "model": current.model_id, "old": _text_digest(old),
                  "new": _text_digest(new), "previous": previous, "incoming": incoming}
        _sync_directory(target.parent)
        _sync_directory(self.root)
        self._write_journal(self.publication_journal, record)
        try:
            if previous is not None:
                os.link(target, backup, follow_symlinks=False)
                _sync_directory(target.parent)
            checkpoint()
            if staging_proof is not None and staging_proof.identity != _artifact_identity(staged.lstat()):
                raise Hold("transfer_artifact_changed")
            os.replace(staged, target)
            _sync_directory(target.parent)
            checkpoint()
            saved = self.save_card(candidate)
        except BaseException:
            self._recover_publication()
            raise
        self._recover_publication()
        return saved

    @staticmethod
    def _recovery_identity(path):
        try:
            return _artifact_identity(path.lstat())
        except FileNotFoundError:
            return None

    def _recovery_binding(self, journal, journal_identity, registry_identity, folder, directory):
        """Bind a recovery decision to its exact journal, registry and directory."""
        if (self._recovery_identity(journal) != journal_identity
                or self._recovery_identity(self.path) != registry_identity
                or _directory_identity(folder) != directory):
            raise Hold("registry_recovery_changed")

    def _recover_publication(self):
        """Restore the old artifact or finalize the exact committed generation."""
        try:
            journal_identity = self._recovery_identity(self.publication_journal)
            raw = _bounded_registry_text(self.publication_journal, 4096)
        except FileNotFoundError:
            return
        except (OSError, ValueError, Hold):
            raise Hold("publication_recovery_required") from None
        try:
            record = _strict_registry_json(raw)
            def valid_payload(value):
                return (isinstance(value, dict) and set(value) == {"size", "digest"}
                        and type(value["size"]) is int and 0 < value["size"] <= DIGEST_LIMIT
                        and isinstance(value["digest"], str)
                        and re.fullmatch(r"[0-9a-f]{64}", value["digest"]))
            if (not isinstance(record, dict)
                    or set(record) != {"version", "model", "old", "new", "previous", "incoming"}
                    or type(record["version"]) is not int or record["version"] != 1
                    or not isinstance(record["model"], str)
                    or not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", record["model"])
                    or any(not isinstance(record[k], str)
                           or not re.fullmatch(r"[0-9a-f]{64}", record[k]) for k in ("old", "new"))
                    or record["old"] == record["new"]
                    or not valid_payload(record["incoming"])
                    or (record["previous"] is not None and not valid_payload(record["previous"]))):
                raise Hold("publication_recovery_required")
            registry_identity = self._recovery_identity(self.path)
            raw_registry = self._read_document()
            generation = _text_digest(raw_registry)
            cards, _ = self._parse(raw_registry)
            known = next((c for c in cards if c.model_id == record["model"]), None)
            target = self.root / "artifacts" / record["model"]
            backup = target.parent / ".publish-backup"
            directory = _directory_identity(target.parent)
            if known is None:
                raise Hold("publication_recovery_required")
            previous, incoming = record["previous"], record["incoming"]
            target_identity = self._recovery_identity(target)
            backup_identity = self._recovery_identity(backup)

            def bound():
                self._recovery_binding(self.publication_journal, journal_identity,
                                       registry_identity, target.parent, directory)
                if (self._recovery_identity(target) != target_identity
                        or self._recovery_identity(backup) != backup_identity):
                    raise Hold("publication_recovery_required")

            def observed(path, identity):
                if identity is None:
                    return None
                size = identity[2]
                return {"size": size, "digest": _verified_artifact(path, size)}

            actual, retained = observed(target, target_identity), observed(backup, backup_identity)
            bound()
            if retained is not None and (previous is None or retained != previous):
                raise Hold("publication_recovery_required")
            if generation == record["old"]:
                if (known.artifact_digest and previous !=
                        {"size": known.disk_size_bytes, "digest": known.artifact_digest}):
                    raise Hold("publication_recovery_required")
                if previous is not None and (not known.owned_by_jarvis or known.shared_storage_owner):
                    raise Hold("publication_recovery_required")
                if previous is None:
                    if actual is not None:
                        if actual != incoming:
                            raise Hold("publication_recovery_required")
                        target.unlink()
                elif actual == previous:
                    if retained is not None:
                        backup.unlink()
                elif actual == incoming and retained == previous:
                    os.replace(backup, target)
                else:
                    raise Hold("publication_recovery_required")
            elif generation == record["new"]:
                if (not known.owned_by_jarvis or known.shared_storage_owner
                        or incoming != {"size": known.disk_size_bytes, "digest": known.artifact_digest}
                        or actual != incoming):
                    raise Hold("publication_recovery_required")
                _sync_directory(self.root)
                bound()
                if retained is not None:
                    backup.unlink()
            else:
                raise Hold("publication_recovery_required")
            # The intended unlink/replace may change link count and ctime.
            # Capture the resulting namespace and check it after durability sync.
            target_identity = self._recovery_identity(target)
            backup_identity = self._recovery_identity(backup)
            _sync_directory(target.parent)
            bound()
            self.publication_journal.unlink()
            _sync_directory(self.root)
        except (OSError, ValueError, TypeError, RecursionError, Hold):
            raise Hold("publication_recovery_required") from None

    def _recover_removal(self):
        """Recover an interrupted removal under the cooperative lock.

        Exact registry generations and verified payload are required.
        Unexpected states are preserved and fail closed. Private local
        state is assumed; this is not a hostile same-UID sandbox.
        """
        try:
            journal_identity = self._recovery_identity(self.removal_journal)
            raw = _bounded_registry_text(self.removal_journal, 4096)
        except FileNotFoundError:
            return
        except (OSError, ValueError, Hold):
            raise Hold("uninstall_recovery_required") from None
        try:
            record = _strict_registry_json(raw)
            if (not isinstance(record, dict)
                    or set(record) != {"version", "model", "size", "digest", "old", "new"}
                    or type(record["version"]) is not int or record["version"] != 1
                    or not isinstance(record["model"], str)
                    or not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", record["model"])
                    or type(record["size"]) is not int
                    or not 0 < record["size"] <= DIGEST_LIMIT
                    or any(not isinstance(record[key], str)
                           or not re.fullmatch(r"[0-9a-f]{64}", record[key])
                           for key in ("digest", "old", "new"))
                    or record["old"] == record["new"]):
                raise Hold("uninstall_recovery_required")
            registry_identity = self._recovery_identity(self.path)
            registry_text = self._read_document()
            generation = _text_digest(registry_text)
            cards, _ = self._parse(registry_text)
            known = next((card for card in cards if card.model_id == record["model"]), None)
            target = self.root / "artifacts" / record["model"]
            backup = target.parent / ".uninstall-recovery"
            directory = _directory_identity(target.parent)
            target_identity = self._recovery_identity(target)
            backup_identity = self._recovery_identity(backup)
            has_target, has_backup = target_identity is not None, backup_identity is not None

            def bound():
                self._recovery_binding(self.removal_journal, journal_identity,
                                       registry_identity, target.parent, directory)
                if (self._recovery_identity(target) != target_identity
                        or self._recovery_identity(backup) != backup_identity):
                    raise Hold("uninstall_recovery_required")

            def verify(path):
                if _verified_artifact(path, record["size"]) != record["digest"]:
                    raise Hold("uninstall_recovery_required")

            if generation == record["old"]:
                if (known is None or not known.owned_by_jarvis or known.shared_storage_owner
                        or (known.artifact_digest and
                            (known.artifact_digest != record["digest"]
                             or known.disk_size_bytes != record["size"]))):
                    raise Hold("uninstall_recovery_required")
                if (_text_digest(self._serialized(*self._without_card(record["model"])))
                        != record["new"] or has_target == has_backup):
                    raise Hold("uninstall_recovery_required")
                if has_backup:
                    verify(backup)
                    bound()
                    os.replace(backup, target)
                else:
                    verify(target)
                    bound()
            elif generation == record["new"]:
                if known is not None or has_target:
                    raise Hold("uninstall_recovery_required")
                # The new registry must be durable before removing old bytes.
                _sync_directory(self.root)
                if has_backup:
                    verify(backup)
                bound()
                if has_backup:
                    backup.unlink()
            else:
                raise Hold("uninstall_recovery_required")
            target_identity = self._recovery_identity(target)
            backup_identity = self._recovery_identity(backup)
            _sync_directory(target.parent)
            bound()
            self.removal_journal.unlink()
            _sync_directory(self.root)
        except (OSError, ValueError, TypeError, RecursionError, Hold):
            raise Hold("uninstall_recovery_required") from None

    @_model_leased
    @_registry_locked
    def uninstall(self, model_id, approval="", *, expected=None):
        """Delete JARVIS-owned files only. Shared weights are never removed."""
        card = self.card(model_id)
        if expected is not None and card != expected:
            raise Hold("registry_card_changed")
        if card.loaded:
            raise Hold("model_loaded")
        if card.shared_storage_owner:
            raise Hold("shared_weights_are_not_removable")
        if card.owned_by_jarvis:
            if not approval:
                raise Hold("uninstall_requires_approval")
            target = self.root / "artifacts" / model_id
            if _exists_entry(target.parent):
                try:
                    _directory_identity(target.parent)
                except Hold:
                    raise Hold("artifact_not_replaceable") from None
            try:
                info = target.lstat()
            except FileNotFoundError:
                self.store(*self._without_card(model_id))
                return
            if not stat.S_ISREG(info.st_mode):
                raise Hold("artifact_not_replaceable")
            digest = _verified_artifact(target, info.st_size)
            if card.artifact_digest and (digest != card.artifact_digest
                                         or card.disk_size_bytes != info.st_size):
                raise Hold("artifact_not_replaceable")
            backup = target.parent / ".uninstall-recovery"
            if _exists_entry(backup):
                raise Hold("uninstall_recovery_required")
            old = self._read_document()
            remaining = self._without_card(model_id)
            new = self._serialized(*remaining)
            record = {"version": 1, "model": model_id, "size": info.st_size,
                      "digest": digest, "old": _text_digest(old),
                      "new": _text_digest(new)}
            # Bind the journal before moving bytes. Recovery observes either
            # exact registry generation, never a guessed transaction phase.
            # Flush the old payload as well as its directory entry before
            # allowing recovery to depend on its continued existence.
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                verified = os.fstat(fd)
                if (not stat.S_ISREG(verified.st_mode)
                        or (verified.st_dev, verified.st_ino, verified.st_size,
                            verified.st_mtime_ns, verified.st_ctime_ns)
                        != (info.st_dev, info.st_ino, info.st_size,
                            info.st_mtime_ns, info.st_ctime_ns)):
                    raise Hold("artifact_not_replaceable")
                os.fsync(fd)
            finally:
                os.close(fd)
            _sync_directory(target.parent)
            _sync_directory(self.root)
            self._write_removal_journal(record)
            try:
                os.replace(target, backup)
                _sync_directory(target.parent)
                self.store(*remaining)
            except BaseException:
                self._recover_removal()
                raise
            self._recover_removal()
            return
        elif not approval:
            raise Hold("unregister_only_without_approval")
        self.store(*self._without_card(model_id))

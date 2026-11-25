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
from .owned_wait import await_authorized
from dataclasses import dataclass, field, replace
from pathlib import Path

from .voice.m1 import Hold

CATEGORIES = ("stt", "llm", "tts")
STATES = ("DISCOVERED", "REVIEWED", "APPROVED", "QUEUED", "DOWNLOADING", "VERIFIED",
          "REGISTERED", "TESTABLE", "ACTIVE", "CANCELLED", "HOLD", "FAILED")
TERMINAL = ("CANCELLED", "HOLD", "FAILED")
(DISCOVERED, REVIEWED, APPROVED, QUEUED, DOWNLOADING, VERIFIED, REGISTERED, TESTABLE,
 ACTIVE, CANCELLED, HOLD, FAILED) = STATES
ALLOWED_NEXT = {
    "DISCOVERED": ("REVIEWED", "CANCELLED", "HOLD", "FAILED"),
    "REVIEWED": ("APPROVED", "CANCELLED", "HOLD", "FAILED"),
    "APPROVED": ("QUEUED", "CANCELLED", "HOLD", "FAILED"),
    "QUEUED": ("DOWNLOADING", "CANCELLED", "HOLD", "FAILED"),
    "DOWNLOADING": ("VERIFIED", "CANCELLED", "FAILED"),
    "VERIFIED": ("REGISTERED", "FAILED"),
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
        if self.artifact_digest and (not isinstance(self.artifact_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.artifact_digest)
                or not self.owned_by_jarvis or self.storage_ref != "sha256-" + self.artifact_digest[:32]):
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
              owned_by_jarvis=False, languages=(), display_name=None):
    """Register an existing, owner-supplied file read-only.

    Import never copies weights: it records provenance, size and digest.
    """
    p = Path(path)
    if category not in CATEGORIES:
        raise Hold("unknown_model_category")
    if not p.is_absolute() or not p.is_file():
        raise Hold("model_file_not_found")
    size = p.stat().st_size
    if not 0 < size <= DIGEST_LIMIT:
        raise Hold("model_size_out_of_range")
    checksum, read_bytes = digest(p)
    if read_bytes != size:
        raise Hold("digest_incomplete")
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
    ).checked()


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
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > 0:
            out.append((path.name[len("partial-"):], size))
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
        self.temp = None
        self.received = 0
        self.state = card.state

    def _assert_current(self):
        if self.registry.card(self.card.model_id) != self.card:
            raise Hold("transfer_card_changed")

    def _check(self):
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

    def _verified(self, written):
        self._check()
        if type(written) is not int or not 0 < written <= DIGEST_LIMIT:
            raise Hold("transfer_reported_invalid_length")
        if self.source.size and written != self.source.size:
            raise Hold("transfer_size_mismatch")
        checksum = _verified_artifact(self.temp, written, checkpoint=self._check)
        if checksum != self.source.checksum:
            raise Hold("transfer_checksum_mismatch")
        self._check()
        return checksum

    def _publish(self, registered, *, replacing):
        self._check()
        if self.registry.card(self.card.model_id) != self.card:
            raise Hold("transfer_card_changed")
        target = self.registry.root / "artifacts" / self.card.model_id
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Preserve the previous inode across ordinary registry write failure.
        # This private-directory protocol is NOT crash-durable or a sandbox.
        backup = None
        replaced = False
        try:
            if target.exists() or target.is_symlink():
                if (not replacing or not self.card.owned_by_jarvis
                        or self.card.shared_storage_owner or self.card.loaded
                        or not stat.S_ISREG(target.lstat().st_mode)):
                    raise Hold("artifact_not_replaceable")
                if self.card.artifact_digest:
                    self.registry.retain_artifact(self.card, target, checkpoint=self._check)
                fd, name = tempfile.mkstemp(prefix=".previous-", dir=target.parent)
                os.close(fd)
                backup = Path(name)
                backup.unlink()
                os.link(target, backup, follow_symlinks=False)
            self._check()
            os.replace(self.temp, target)
            self.temp = None
            replaced = True
            try:
                self._check()
                if self.registry.card(self.card.model_id) != self.card:
                    raise Hold("transfer_card_changed")
                saved = self.registry.save_card(registered)
            except BaseException:
                # If recovery itself fails, leave the backup intact for review.
                if backup is not None:
                    os.replace(backup, target)
                    backup = None
                else:
                    target.unlink(missing_ok=True)
                replaced = False
                raise
            self.card, self.state = saved, saved.state
            replaced = False
            return saved
        finally:
            if backup is not None and not replaced:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    # Do not report a committed update as failed because an
                    # old backup could not be removed.
                    pass

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

    def _current_for_partial(self):
        try:
            self._assert_current()
            return True
        except (Hold, OSError):
            return False

    async def resume(self, progress=None, start=0, allow_installed=False):
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
        fd, name = tempfile.mkstemp(prefix="tihulu-transfer-", dir=str(self.root))
        os.close(fd)
        self.temp = Path(name)
        try:
            os.chmod(self.temp, 0o600)
            if start:
                partial = self.root / ("partial-" + self.card.model_id)
                if not partial.is_file() or partial.stat().st_size != start:
                    raise Hold("transfer_resume_offset_unverified")
                from .selected_csv import selection_identity, copy_selected_to
                def authorized():
                    self._check()
                    return True
                identity = selection_identity(str(partial.absolute()), authorized=authorized, max_bytes=start)
                if identity[2] != start:
                    raise Hold("transfer_resume_offset_unverified")
                self.temp.unlink()
                await copy_selected_to(str(partial.absolute()), self.temp, authorized=authorized,
                                       expected_identity=identity, max_bytes=start)
            written = await self._fetch(progress, start)
            checksum = self._verified(written)
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
            result = self._publish(registered, replacing=allow_installed)
            # A cleanup failure must not turn a committed result into a failed update.
            try:
                (self.root / ("partial-" + self.card.model_id)).unlink(missing_ok=True)
            except OSError:
                pass
            return result
        except asyncio.CancelledError:
            self.state = "CANCELLED"
            raise
        except (Hold, OSError, asyncio.TimeoutError):
            self.state = "FAILED"
            raise
        finally:
            if self.temp is not None:
                # Keep a cancelled partial so a later resume can continue from it.
                if (self.state == "CANCELLED" and not allow_installed and self._current_for_partial()
                        and self.temp.is_file() and not self.temp.is_symlink() and self.temp.stat().st_size > 0):
                    self.temp.replace(self.root / ("partial-" + self.card.model_id))
                else:
                    Path(self.temp).unlink(missing_ok=True)

    async def update(self, progress=None):
        """Replace an installed item with its approved source, keeping history.

        The previous entry stays as a revision; the new one registers
        atomically after verification.
        """
        if self.card.state not in ("REGISTERED", "TESTABLE", "ACTIVE"):
            raise Hold("model_not_installed")
        return await self.resume(progress, start=0, allow_installed=True)

    async def run(self, progress=None):
        if self.card.state != DISCOVERED:
            # Nothing already installed is ever overwritten by a transfer.
            raise Hold("model_already_registered")
        self._persist("REVIEWED")
        try:
            self._check()
        except Hold:
            self._persist("HOLD")
            raise
        self._persist("APPROVED")
        self._persist("QUEUED")
        fd, name = tempfile.mkstemp(prefix="tihulu-transfer-", dir=str(self.root))
        os.close(fd)
        self.temp = Path(name)
        try:
            os.chmod(self.temp, 0o600)
            self._persist("DOWNLOADING")
            written = await self._fetch(progress)
            checksum = self._verified(written)
            self._persist("VERIFIED")
            registered = replace(self.card, state="REGISTERED", registered=True, installed=True,
                                 owned_by_jarvis=True, disk_size_bytes=written,
                                 storage_ref="sha256-" + checksum[:32],
                                 artifact_digest=checksum,
                                 version=self.source.version if self.source.version != "unknown" else self.card.version,
                                 source_version=self.source.version,
                                  license_id=self.source.license_id)
            return self._publish(registered, replacing=False)
        except asyncio.CancelledError:
            self._terminal("CANCELLED")
            raise
        except (Hold, OSError, asyncio.TimeoutError):
            self._terminal("FAILED")
            raise
        finally:
            if self.temp is not None:
                if (self.state == "CANCELLED" and self._current_for_partial()
                        and self.temp.is_file() and not self.temp.is_symlink() and self.temp.stat().st_size > 0):
                    # Keep the partial so a later resume can continue from it.
                    self.temp.replace(self.root / ("partial-" + self.card.model_id))
                else:
                    Path(self.temp).unlink(missing_ok=True)

    def cancel(self):
        raise Hold("transfer_cancelled_by_owner")


def _verified_artifact(path, size, *, checkpoint=lambda: None):
    """Hash an exact bounded regular file; refuse symlink/size changes."""
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
        after = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
        identity = lambda x: (x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns,x.st_mode)
        if identity(before) != identity(after) or identity(before) != identity(current):
            raise Hold("transfer_artifact_changed")
        checkpoint()
        return h.hexdigest()
    except OSError:
        raise Hold("transfer_artifact_unreadable") from None
    finally:
        if fd is not None:
            os.close(fd)


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
        after = os.fstat(fd)
        identity = lambda x: (x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns,x.st_mode)
        if (identity(before) != identity(after)
                or identity(before) != identity(os.stat(source, follow_symlinks=False))
                or h.hexdigest() != card.artifact_digest):
            raise Hold("rollback_artifact_unverified")
        checkpoint()
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
        # A directory of append-only revision logs; not the history() method.
        self.revisions = self.root / "revisions"
        self.revisions.mkdir(mode=0o700, parents=True, exist_ok=True)

    @staticmethod
    def _parse(text):
        if len(text) > Registry.LIMIT:
            raise Hold("registry_too_large")
        data = json.loads(text)
        cards = tuple(ModelCard(**item).checked() for item in _normalized(data.get("cards", [])))
        selection = {}
        for profile, choices in (data.get("selection", {}) or {}).items():
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", profile) or not isinstance(choices, dict):
                raise Hold("invalid_selection")
            clean = {}
            for category, model_id in choices.items():
                if category not in CATEGORIES or not isinstance(model_id, str):
                    raise Hold("invalid_selection")
                clean[category] = model_id
            selection[profile] = clean
        return cards, selection

    @staticmethod
    def _dump(cards, selection):
        return json.dumps({"cards": [card.__dict__ for card in cards],
                           "selection": selection}, ensure_ascii=False, sort_keys=True)

    def load(self):
        try:
            return self._parse(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return (), {}
        except (ValueError, OSError):
            raise Hold("registry_unreadable") from None

    def store(self, cards, selection):
        text = self._dump(cards, selection)
        if len(text) > self.LIMIT:
            raise Hold("registry_too_large")
        if len(selection) > self.SELECTION_LIMIT:
            raise Hold("selection_profile_limit")
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

    def card(self, model_id):
        for card in self.cards():
            if card.model_id == model_id:
                return card
        raise Hold("model_not_registered")

    def save_card(self, card):
        """Persist a card, keeping the previous entry as a revision."""
        card = card.checked()
        cards, selection = self.load()
        previous = None
        for known in cards:
            if known.model_id == card.model_id:
                previous = known
        if previous is not None and previous != card:
            replacement = replace(card, revision=previous.revision + 1)
            if replacement == previous:
                return previous
            card = replacement
            self.append_history(previous)
        kept = tuple(c for c in cards if c.model_id != card.model_id)
        self.store(kept + (card,), selection)
        return card

    def append_history(self, card):
        """Keep a bounded, append-only revision log for one item."""
        path = self.revisions / (card.model_id + ".jsonl")
        rows = []
        if path.is_file():
            try:
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
            except (ValueError, OSError):
                raise Hold("registry_unreadable") from None
        if len(rows) > self.HISTORY_LIMIT - 1:
            rows = rows[-(self.HISTORY_LIMIT - 1):]
        rows.append(card.__dict__)
        text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
        if len(text) > self.HISTORY_BYTES:
            raise Hold("registry_too_large")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(self.revisions), delete=False) as f:
            tmp = f.name
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def history(self, model_id):
        """Previous revisions of one item, oldest first. Never invented."""
        if not re.fullmatch(r"[A-Za-z0-9][\w.:-]{0,127}", model_id):
            raise Hold("invalid_model_id")
        path = self.revisions / (model_id + ".jsonl")
        if not path.is_file():
            return ()
        out = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line:
                    out.append(ModelCard(**_normalized([json.loads(line)])[0]).checked())
        except (ValueError, OSError):
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
            if path.is_symlink() or not path.is_dir():
                raise Hold("artifact_archive_unavailable")
        return folder / card.artifact_digest

    def retain_artifact(self, card, source, *, checkpoint=lambda: None):
        if card.shared_storage_owner or card.loaded:
            raise Hold("artifact_not_replaceable")
        target = self._archive_path(card)
        if target.exists() or target.is_symlink():
            if _verified_artifact(target, card.disk_size_bytes, checkpoint=checkpoint) != card.artifact_digest:
                raise Hold("rollback_artifact_unverified")
            if _verified_artifact(source, card.disk_size_bytes, checkpoint=checkpoint) != card.artifact_digest:
                raise Hold("rollback_artifact_unverified")
            return target
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
        fd, name = tempfile.mkstemp(prefix=".archive-", dir=target.parent)
        os.close(fd)
        temp = Path(name)
        temp.unlink()
        try:
            _copy_verified_artifact(source, temp, card, checkpoint=checkpoint)
            checkpoint()
            # No overwrite, even if another operation published the same digest.
            os.link(temp, target, follow_symlinks=False)
            return target
        except FileExistsError:
            raise Hold("artifact_archive_changed") from None
        finally:
            temp.unlink(missing_ok=True)

    def _restore_owned(self, current, candidate, restored):
        if (not current.owned_by_jarvis or not candidate.owned_by_jarvis
                or current.shared_storage_owner or candidate.shared_storage_owner
                or current.loaded or candidate.loaded or not current.artifact_digest
                or not candidate.artifact_digest):
            raise Hold("rollback_artifact_unverified")
        target = self.root / "artifacts" / current.model_id
        if current.artifact_digest == candidate.artifact_digest:
            if _verified_artifact(target, candidate.disk_size_bytes) != candidate.artifact_digest:
                raise Hold("rollback_artifact_unverified")
            return self.save_card(restored)
        archive = self._archive_path(candidate)
        if _verified_artifact(archive, candidate.disk_size_bytes) != candidate.artifact_digest:
            raise Hold("rollback_artifact_unverified")
        self.retain_artifact(current, target)
        fd, name = tempfile.mkstemp(prefix=".rollback-", dir=target.parent)
        os.close(fd)
        temp = Path(name)
        temp.unlink()
        fd, name = tempfile.mkstemp(prefix=".previous-", dir=target.parent)
        os.close(fd)
        backup = Path(name)
        backup.unlink()
        replaced = False
        try:
            _copy_verified_artifact(archive, temp, candidate)
            if self.card(current.model_id) != current:
                raise Hold("transfer_card_changed")
            if _verified_artifact(target, current.disk_size_bytes) != current.artifact_digest:
                raise Hold("rollback_artifact_unverified")
            os.link(target, backup, follow_symlinks=False)
            os.replace(temp, target)
            replaced = True
            try:
                saved = self.save_card(restored)
            except BaseException:
                os.replace(backup, target)
                replaced = False
                raise
            replaced = False
            return saved
        finally:
            temp.unlink(missing_ok=True)
            if not replaced:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    pass

    def rollback(self, model_id, revision):
        """Restore a previous revision as current, as a new revision."""
        current = self.card(model_id)
        for candidate in self.history(model_id):
            if candidate.revision == revision:
                if candidate.model_id != model_id:
                    raise Hold("rollback_artifact_unverified")
                restored = replace(current, **{k: v for k, v in candidate.__dict__.items()
                                              if k not in ("revision", "state")})
                if current.owned_by_jarvis or candidate.owned_by_jarvis:
                    # Historical approvals are never restored with a revision.
                    restored = replace(restored, network_approval="", approval_expires_at=0.0,
                                       loaded=False, health_status=UNKNOWN, last_tested_at=0.0)
                    return self._restore_owned(current, candidate, restored)
                return self.save_card(restored)
        raise Hold("revision_not_found")

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

    def unregister(self, model_id):
        """Remove a registry entry. This never deletes a file by itself."""
        cards, selection = self.load()
        kept = tuple(c for c in cards if c.model_id != model_id)
        if len(kept) == len(cards):
            raise Hold("model_not_registered")
        cleaned = {p: {k: v for k, v in choices.items() if v != model_id}
                   for p, choices in selection.items()}
        cleaned = {p: v for p, v in cleaned.items() if v}
        self.store(kept, cleaned)

    def uninstall(self, model_id, approval=""):
        """Delete JARVIS-owned files only. Shared weights are never removed."""
        card = self.card(model_id)
        if card.shared_storage_owner:
            raise Hold("shared_weights_are_not_removable")
        if card.owned_by_jarvis:
            if not approval:
                raise Hold("uninstall_requires_approval")
            # A JARVIS-owned artifact lives only under its own install root.
            target = self.root / "artifacts" / model_id
            if target.is_file():
                target.unlink()
        elif not approval:
            raise Hold("unregister_only_without_approval")
        self.unregister(model_id)

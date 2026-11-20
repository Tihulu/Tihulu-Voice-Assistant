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
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
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
    """An owner-supplied curated catalog. It is never fetched."""

    entries: tuple = ()
    source_name: str = "unknown"

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
    return Catalog(tuple(entries), str(data.get("source_name", source_name))[:64]).checked()


def catalog_path():
    """The catalog lives with the rest of the market data, under the owner's XDG dir."""
    return market_dir() / "catalog.json"


def source_of(card):
    """Build the approved download terms recorded on a card."""
    if not card.approved_source():
        raise Hold("download_terms_not_approved")
    return Source(card.source_ref, card.source_checksum, card.source_size, card.source_version,
                 card.license_id, card.network_approval, card.approval_expires_at).checked()


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

    def __init__(self, registry, card, source, fetch, root=None, now=0.0):
        self.registry = registry
        self.card = card
        self.source = source.checked()
        self.fetch = fetch
        self.now = now
        self.root = Path(root) if root else registry.root / "transfers"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.temp = None
        self.received = 0
        self.state = card.state

    def _persist(self, state):
        self.card = advance(self.card, state)
        self.state = self.card.state
        return self.registry.save_card(self.card)

    async def resume(self, progress=None, start=0):
        """Continue an interrupted transfer from a verified byte offset.

        The card's lifecycle is not advanced here: an interrupted item stays
        cancelled until the resumed transfer verifies and registers it.
        """
        if self.card.state in ("REGISTERED", "TESTABLE", "ACTIVE"):
            raise Hold("model_already_registered")
        fd, name = tempfile.mkstemp(prefix="tihulu-transfer-", dir=str(self.root))
        os.close(fd)
        self.temp = Path(name)
        try:
            os.chmod(self.temp, 0o600)
            if start:
                partial = self.root / ("partial-" + self.card.model_id)
                if not partial.is_file() or partial.stat().st_size != start:
                    raise Hold("transfer_resume_offset_unverified")
                self.temp.write_bytes(partial.read_bytes())
            written = await self.fetch(str(self.temp), lambda count: progress(count) if progress else None, start)
            if start and written <= start:
                raise Hold("transfer_resume_made_no_progress")
            checksum, _ = digest(self.temp)
            if checksum != self.source.checksum:
                self.card = replace(self.card, state="FAILED")
                self.state = self.card.state
                raise Hold("transfer_checksum_mismatch")
            # A resumed transfer registers directly; the interrupted item's
            # lifecycle was not advanced for the retry itself.
            target = self.registry.root / "artifacts" / self.card.model_id
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(self.temp, target)
            self.temp = None
            os.chmod(target, 0o600)
            # The resumed bytes are the artifact now; the partial is no longer needed.
            (self.root / ("partial-" + self.card.model_id)).unlink(missing_ok=True)
            registered = replace(self.card, state="REGISTERED", registered=True, installed=True,
                                 owned_by_jarvis=True, disk_size_bytes=written,
                                 storage_ref="sha256-" + checksum[:32],
                                 version=self.source.version if self.source.version != "unknown" else self.card.version,
                                 license_id=self.source.license_id, trust_level="curated")
            self.registry.save_card(registered)
            self.card = registered
            self.state = registered.state
            return registered
        except asyncio.CancelledError:
            if self.state not in TERMINAL:
                self._persist("CANCELLED")
            raise
        except Hold:
            if self.state not in TERMINAL:
                self._persist("FAILED")
            raise
        finally:
            if self.temp is not None:
                # Keep a cancelled partial so a later resume can continue from it.
                if self.state == "CANCELLED" and self.temp.stat().st_size > 0:
                    self.temp.replace(self.root / ("partial-" + self.card.model_id))
                else:
                    Path(self.temp).unlink(missing_ok=True)

    async def run(self, progress=None):
        if self.card.state != DISCOVERED:
            # Nothing already installed is ever overwritten by a transfer.
            raise Hold("model_already_registered")
        self._persist("REVIEWED")
        if not self.source.authorized(self.now):
            self._persist("HOLD")
            raise Hold("network_not_authorized")
        self._persist("APPROVED")
        self._persist("QUEUED")
        fd, name = tempfile.mkstemp(prefix="tihulu-transfer-", dir=str(self.root))
        os.close(fd)
        self.temp = Path(name)
        try:
            os.chmod(self.temp, 0o600)
            self._persist("DOWNLOADING")
            def note(count):
                self.received = count
                if progress:
                    progress(count)
            written = await self.fetch(str(self.temp), note)
            if not isinstance(written, int) or written <= 0 or written > DIGEST_LIMIT:
                raise Hold("transfer_reported_invalid_length")
            if self.temp.stat().st_size != written:
                raise Hold("transfer_length_mismatch")
            if self.source.size and written != self.source.size:
                raise Hold("transfer_size_mismatch")
            checksum, _ = digest(self.temp)
            if checksum != self.source.checksum:
                self._persist("FAILED")
                raise Hold("transfer_checksum_mismatch")
            self._persist("VERIFIED")
            target = self.registry.root / "artifacts" / self.card.model_id
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(self.temp, target)
            self.temp = None
            os.chmod(target, 0o600)
            registered = replace(self.card, state="REGISTERED", registered=True, installed=True,
                                 owned_by_jarvis=True, disk_size_bytes=written,
                                 storage_ref="sha256-" + checksum[:32],
                                 version=self.source.version if self.source.version != "unknown" else self.card.version,
                                 license_id=self.source.license_id, trust_level="curated")
            self.registry.save_card(registered)
            self.card = registered
            self.state = registered.state
            return registered
        except asyncio.CancelledError:
            if self.state not in TERMINAL:
                self._persist("CANCELLED")
            raise
        except Hold:
            if self.state not in TERMINAL:
                self._persist("FAILED")
            raise
        finally:
            if self.temp is not None:
                if self.state == "CANCELLED" and self.temp.stat().st_size > 0:
                    # Keep the partial so a later resume can continue from it.
                    self.temp.replace(self.root / ("partial-" + self.card.model_id))
                else:
                    Path(self.temp).unlink(missing_ok=True)

    def cancel(self):
        raise Hold("transfer_cancelled_by_owner")


class Registry:
    """Local registry with per-profile independent STT/LLM/TTS selection."""

    LIMIT = 262144
    SELECTION_LIMIT = 64

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "registry.json"

    @staticmethod
    def _parse(text):
        if len(text) > Registry.LIMIT:
            raise Hold("registry_too_large")
        data = json.loads(text)
        cards = tuple(ModelCard(**item).checked() for item in data.get("cards", []))
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
        card = card.checked()
        cards, selection = self.load()
        kept = tuple(c for c in cards if c.model_id != card.model_id)
        self.store(kept + (card,), selection)
        return card

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

# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Local AI Market: truthful cards for models and voices, and a lifecycle.

No network access exists in this module and none may be added here. A
download is described as an auditable state machine so that a future,
separately authorized transport can be reviewed on its own. Nothing is
downloaded, started or deleted without an explicit, bounded owner action,
and shared weights are never removed by this project.
"""
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
    disk_size_bytes: int = 0
    requires_gpu: bool = False
    license_id: str = "unknown"
    state: str = "DISCOVERED"
    last_tested_at: float = 0.0

    def compatibility(self):
        if self.engine in ENGINE_FORMATS:
            if self.artifact_format in ENGINE_FORMATS[self.engine]:
                return "compatible"
            if self.artifact_format == "other":
                return UNKNOWN  # an unknown format is never marked incompatible
            return "incompatible_format"
        return UNKNOWN

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
        if self.disk_size_bytes < 0 or self.disk_size_bytes > DIGEST_LIMIT:
            raise Hold("invalid_size")
        if self.storage_ref and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.storage_ref):
            raise Hold("invalid_storage_reference")
        if len(self.display_name) > 120 or len(self.provider) > 64 or len(self.version) > 64:
            raise Hold("card_text_too_long")
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


def advance(card, state):
    """Lifecycle transitions are explicit and auditable."""
    if state not in ALLOWED_NEXT[card.state]:
        raise Hold("lifecycle_transition_rejected:" + card.state + "->" + state)
    return replace(card, state=state, installed=True,
                   registered=state in (REGISTERED, TESTABLE, ACTIVE))


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

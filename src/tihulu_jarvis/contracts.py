# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""TIHULU JARVIS interface contracts.

Reviewable interface definitions ONLY. No implementation, enforcement or
I/O here. These lock the boundaries between the M1 conversation core, the
future frontend, the AI Market registry and the mode/profile layer so
those components can be built and tested independently.
"""

from dataclasses import dataclass, field
from enum import Enum


class Mode(str, Enum):
    LOCAL_ONLY = "local_only"
    CLOUD_CHAT = "cloud_chat"
    SECURE_HYBRID = "secure_hybrid"
    CUSTOM = "custom"


class ModelCategory(str, Enum):
    STT = "stt"
    LLM = "llm"
    TTS = "tts"


class VoiceState(str, Enum):
    READY = "ready"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    ERROR = "error"


class VoiceControl(str, Enum):
    TALK = "talk"
    TOGGLE_RECORD = "toggle_record"
    STOP_SPEAKING = "stop_speaking"
    CANCEL_TURN = "cancel_turn"
    END_SESSION = "end_session"
    HARD_STOP = "hard_stop"
    MUTE = "mute"


@dataclass(frozen=True)
class ModelCard:
    model_id: str
    category: ModelCategory
    provider: str
    runtime: str            # e.g. "jan", "hex", "piper"
    languages: tuple[str, ...]
    local: bool
    installed: bool
    compatibility: str      # "verified", "untested", "incompatible"


@dataclass(frozen=True)
class ModelSelection:
    stt: str | None = None
    llm: str | None = None
    tts: str | None = None
    voices: dict = field(default_factory=dict)  # lang -> voice id


class Permission(str, Enum):
    DENY = "deny"
    ASK = "ask"
    SCOPED_ALLOW = "scoped_allow"


@dataclass(frozen=True)
class Profile:
    profile_id: str
    name: str
    mode: Mode
    selection: ModelSelection
    permissions: dict = field(default_factory=dict)
    version: int = 1

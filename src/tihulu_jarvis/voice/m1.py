# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""TIHULU JARVIS M1 conversation-routing core (reviewed revision).

Pure Python 3.10+. No filesystem, network, Shell, audio or model access.

Review changes vs. the Astra draft:
  * quoted foreign text is stripped before BOTH instruction parsing and
    language identification;
  * identification uses a strict-plurality rule (>=2 cues or anchor, and
    strictly more than any rival) instead of a fixed 0.80 strength
    threshold, so a Turkish sentence containing one English technical
    word is still Turkish;
  * response validation rejects only CONTRARY evidence, so short informal
    and purely numeric answers are no longer rejected for lacking cues.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping
import html
import re
import unicodedata


LANGUAGES = {"tr": "Turkish", "en": "English", "de": "German"}


class Hold(Exception):
    """Bounded reason code. Never carries raw backend diagnostics."""


@dataclass(frozen=True)
class Evidence:
    language: str | None
    strength: float  # lexical evidence score, not a calibrated probability
    reason: str


@dataclass(frozen=True)
class LanguageContext:
    input_language: str | None = None
    response_language: str | None = None
    persistent_response_language: str | None = None


@dataclass(frozen=True)
class Decision:
    evidence: Evidence
    response_language: str
    next_context: LanguageContext


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class Voice:
    id: str
    language: str


@dataclass(frozen=True)
class SpeechRequest:
    text: str
    language: str
    voice_id: str


@dataclass(frozen=True)
class CompletedTurn:
    display_text: str
    speech: SpeechRequest
    history: tuple[Message, ...]
    context: LanguageContext


Identifier = Callable[[str], Evidence]
Chat = Callable[[tuple[Message, ...]], Awaitable[str]]


def fold(text: str) -> str:
    """Case/diacritic-insensitive comparison form. Never mutates the text."""
    text = unicodedata.normalize("NFKD", text.casefold().replace("ı", "i"))
    return "".join(c for c in text if not unicodedata.combining(c))


_QUOTE_PATTERNS = (
    r'"[^"]*"',
    r"“[^”]*”",
    r"„[^“]*“",
    r"(?<!\w)'[^'\n]*'(?!\w)",
    r"`[^`\n]*`",
)


def strip_quoted(text: str) -> str:
    """Remove quoted spans so foreign quoted text cannot masquerade as an
    instruction or as language evidence."""
    for pattern in _QUOTE_PATTERNS:
        text = re.sub(pattern, " ", text)
    return text


_WORDS = {
    "tr": set("""
        merhaba selam naber nasilsin nasil ne yapiyorsun neden nerede
        hangi benim senin evet hayir lutfen tesekkurler tamam gunaydin
        iyi hatirla hatirliyor hatirliyorum hatirlayacagim musun bunu
        kelime kelimem kelimen kelimemi kelimenin peki hala cevap ver yanit
        yanitla konus bundan sonra dilimi takip et
    """.split()),
    "en": set("""
        hello hi how are you what where why when which was is the
        your my word remember remembered please answer respond reply
        speak yes no thanks thank good morning evening doing can
        could does mean from now on match language use summarize this note
    """.split()),
    "de": set("""
        hallo guten morgen abend wie geht es dir was machst du
        danke schon tschuss ja nein dein testwort war ich erinnere
        mich an mein bitte antworte sprich auf gut schlecht noch
        kannst erinnern ab jetzt folge meiner sprache
    """.split()),
}
_ANCHORS = {
    "tr": {"merhaba", "naber", "nasilsin", "evet", "hayir", "gunaydin"},
    "en": {"hello", "hi", "yes", "no", "thanks"},
    "de": {"hallo", "tschuss", "ja", "nein", "danke"},
}
# Words shared by multiple languages must not decide between them.
_CUES = {
    lang: words - set().union(
        *(other for key, other in _WORDS.items() if key != lang)
    )
    for lang, words in _WORDS.items()
}


def lexical_identifier(text: str) -> Evidence:
    """Limited offline fallback. Returns None on insufficient or mixed
    evidence; it never forces a language."""
    prose = strip_quoted(text)
    tokens = set(re.findall(r"[a-z]+", fold(prose)))
    counts = {lang: len(tokens & cues) for lang, cues in _CUES.items()}
    ordered = sorted(counts, key=counts.get, reverse=True)
    winner, runner_up = ordered[:2]
    total = sum(counts.values())
    strength = counts[winner] / total if total else 0.0
    enough = counts[winner] >= 2 or bool(tokens & _ANCHORS[winner])
    if not enough or counts[winner] <= counts[runner_up]:
        return Evidence(None, strength, "insufficient_or_mixed_evidence")
    return Evidence(winner, strength, "lexical_evidence")


_ALIASES = {
    "turkish": "tr", "turkce": "tr", "turkisch": "tr",
    "english": "en", "ingilizce": "en", "englisch": "en",
    "german": "de", "almanca": "de", "deutsch": "de",
}
_INSTRUCTIONS = (
    r"(?:please )?(?:answer|reply|respond|speak) (?:in )?"
    r"(turkish|english|german)(?: please)?",
    r"(?:lutfen )?(turkce|ingilizce|almanca)(?: olarak)? "
    r"(?:cevap ver|yanit ver|yanitla|konus)(?: lutfen)?",
    r"(?:bitte )?(?:antworte|sprich) (?:auf )?"
    r"(turkisch|englisch|deutsch)(?: bitte)?",
)
_AUTO = {"match my language", "use auto language",
         "dilimi takip et", "folge meiner sprache"}


def explicit_request(text: str) -> tuple[str | None, str] | None:
    """Only standalone instructions count. Quoted examples and negated
    sentences do not."""
    text = strip_quoted(text)
    requests = []
    for clause in re.split(r"[.!?;\n]+", fold(text)):
        clause = re.sub(r"[,:\s]+", " ", clause).strip()
        scope = "turn"
        for prefix in ("from now on ", "bundan sonra ", "ab jetzt "):
            if clause.startswith(prefix):
                clause, scope = clause[len(prefix):], "persistent"
                break
        if clause in _AUTO:
            requests.append((None, "auto"))
            continue
        for pattern in _INSTRUCTIONS:
            match = re.fullmatch(pattern, clause)
            if match:
                requests.append((_ALIASES[match[1]], scope))
                break
    if len(set(requests)) > 1:
        raise Hold("conflicting_language_instructions")
    return requests[0] if requests else None


def route_language(
    text: str,
    context: LanguageContext,
    identify: Identifier = lexical_identifier,
) -> Decision:
    if not isinstance(text, str) or len(text) > 16000:
        raise Hold("invalid_input")
    if not any(c.isalnum() for c in text):
        raise Hold("empty_input")

    evidence = identify(text)
    request = explicit_request(text)
    pinned = context.persistent_response_language
    requested = None

    if request:
        requested, scope = request
        if scope == "auto":
            pinned = None
        elif scope == "persistent":
            pinned = requested

    target = (requested or pinned or evidence.language
              or context.response_language or context.input_language)
    if target not in LANGUAGES:
        raise Hold("response_language_unknown")
    if evidence.language not in (*LANGUAGES, None):
        raise Hold("unsupported_input_language")

    return Decision(
        evidence, target,
        LanguageContext(
            evidence.language or context.input_language, target, pinned
        ),
    )


_EMOJI = re.compile(
    r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF"
    r"\u2600-\u27BF\uFE0F\u200D]"
)
_EMPHASIS = re.compile(
    r"(?<!\w)(\*\*|__|\*|_)(?=\S)(.+?)\1(?!\w)"
)


def speech_text(display_text: str) -> str:
    """Normalize a conservative Markdown-prose subset for Piper.

    Unsupported rich content requires a separate spoken summary instead of
    silent corruption. DISPLAY_TEXT is never modified.
    """
    if not isinstance(display_text, str) or not display_text.strip():
        raise Hold("empty_response")
    if len(display_text) > 32000:
        raise Hold("response_too_long")

    text = html.unescape(display_text)
    if re.search(r"</?(?:think|analysis)\b", text, re.I):
        raise Hold("nonfinal_model_output")
    if re.search(r"```|~~~|!\[|\|", text):
        raise Hold("spoken_summary_required")
    if re.search(r"^(?: {4}|\t)\S", text, re.M):
        raise Hold("spoken_summary_required")

    # Supported block formatting only; meaningful punctuation remains.
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.M)
    text = re.sub(r"^\s{0,3}[-+*]\s+", "", text, flags=re.M)
    text = re.sub(
        r"\[([^\[\]\n]+)\]\(https?://[^()\s]+\)", r"\1", text
    )

    parts = []
    for part in re.split(r"(`[^`\n]+`)", text):
        if part.startswith("`") and part.endswith("`"):
            token = part[1:-1]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+#-]*", token):
                raise Hold("spoken_summary_required")
            parts.append(token)
            continue

        for _ in range(3):
            part = _EMPHASIS.sub(r"\2", part)
        if re.search(r"[*`~\[\]<>]|(?<!\w)_|_(?!\w)|https?://", part):
            raise Hold("unsupported_speech_markup")
        parts.append(part)

    lines = []
    for line in "".join(parts).splitlines():
        # Do not silently turn "I ❤️ you" into "I you".
        for match in _EMOJI.finditer(line):
            if (re.search(r"\w", line[:match.start()])
                    and re.search(r"\w", line[match.end():])):
                raise Hold("semantic_emoji_requires_summary")
        line = _EMOJI.sub(" ", line)
        lines.append(re.sub(r"[ \t]+", " ", line).strip())

    result = "\n".join(lines).strip()
    if not any(c.isalnum() for c in result):
        raise Hold("empty_speech")
    return result


def valid_response(text: str, expected: str, identify: Identifier) -> bool:
    """Reject only on contrary evidence.

    A limited lexical identifier must not reject short, informal or
    numeric-but-correct answers merely for lacking cue words.
    """
    pieces = [
        p.strip() for p in re.split(r"(?<=[.!?;])\s+|\n+", text)
        if any(c.isalpha() for c in p)
    ]
    if not pieces:
        # Language-neutral content (numbers, symbols, quoted words).
        return True
    whole = identify(text).language
    if whole is not None and whole != expected:
        return False
    for piece in pieces:
        detected = identify(piece).language
        if detected is not None and detected != expected:
            return False
    normalized = [" ".join(fold(p).split()) for p in pieces]
    return not any(
        a == b and len(a) > 12
        for a, b in zip(normalized, normalized[1:])
    )


async def complete_turn(
    user_text: str,
    context: LanguageContext,
    history: tuple[Message, ...],
    chat: Chat,
    voices: Mapping[str, Voice],
    identify: Identifier = lexical_identifier,
    cancelled: Callable[[], bool] = lambda: False,
) -> CompletedTurn:
    """chat must be an authorized adapter, not a blocking GUI-thread call.

    history contains user/assistant messages; the host owns its policy
    prompt. Commit this result only if its task/epoch is still current.
    Retry changes only the request instruction, never stored history.
    """
    if any(m.role not in {"user", "assistant"} for m in history):
        raise Hold("unsupported_history_role")
    decision = route_language(user_text, context, identify)
    expected = decision.response_language
    voice = voices.get(expected)
    if not voice or not voice.id or voice.language != expected:
        raise Hold("matching_voice_unavailable")

    user_message = Message("user", user_text)
    for attempt in range(2):
        if cancelled():
            raise Hold("turn_cancelled")
        instruction = (
            f"Reply naturally in {LANGUAGES[expected]}. "
            "Preserve facts and quoted words from the conversation. "
            "Do not invent additional rules. Return a concise final "
            "answer, without analysis tags or repetition."
        )
        if attempt:
            instruction += (
                " The previous draft failed validation. Produce a fresh "
                "answer in the requested language using plain sentences."
            )

        # Retry changes only this request's instruction, not stored history.
        request = (
            Message("system", instruction),
            *history,
            user_message,
        )
        display = await chat(request)
        if cancelled():
            raise Hold("turn_cancelled")
        try:
            spoken = speech_text(display)
        except Hold:
            continue
        if not valid_response(spoken, expected, identify):
            continue
        if cancelled():
            raise Hold("turn_cancelled")

        return CompletedTurn(
            display,
            SpeechRequest(spoken, expected, voice.id),
            (*history, user_message, Message("assistant", display)),
            decision.next_context,
        )
    raise Hold("response_rejected_after_one_retry")

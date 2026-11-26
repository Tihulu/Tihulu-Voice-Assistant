# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Explicit, on-demand local capture, Whisper transcription and Piper playback."""
import asyncio
import json
import math
import os
import shutil
import signal
import stat
import struct
import tempfile
import wave
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event

from .. import resources
from ..owned_wait import OperationDeadline, await_with_deadline, finish_owned
from ..owned_wait import await_authorized as owned_wait
from ..selected_csv import selection_identity, copy_selected_to
from .m1 import Hold, Voice


PROFILE = None


def configure(profile=None):
    """Hold the resource profile the local engines are started with.

    The profile decides how many threads a local engine gets and which device it
    prefers; it never selects the executable, the flags or the model. Calling it
    with nothing restores the balance defaults.
    """
    global PROFILE
    PROFILE = profile if profile is not None else None
    return PROFILE


def spawn_environment():
    """The environment a local engine is spawned with, as a plain dict.

    An empty dict is the honest answer when no profile is configured: the engine
    then uses its own defaults, and nothing is set on the user's behalf.
    """
    if PROFILE is None:
        return {}
    return resources.environment(PROFILE)


def selected_file(value, suffix):
    if not isinstance(value, str) or not value:
        raise Hold("select_local_model")
    p = Path(value)
    if not p.is_absolute() or p.suffix.lower() != suffix or not p.is_file():
        raise Hold("incompatible_model_file")
    return p


def _voice_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_nlink)


def inspect_voice(value, lang, *, checkpoint=lambda: None):
    """Pin and bound the Piper sidecar; never wait on a FIFO or follow a symlink."""
    p = selected_file(value, ".onnx")
    config = p.with_name(p.name + ".json")
    fd = None
    try:
        checkpoint()
        fd = os.open(config, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 1048576:
            raise ValueError
        raw = bytearray()
        while len(raw) <= before.st_size:
            checkpoint()
            block = os.read(fd, min(65536, before.st_size + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        identity = _voice_identity(before)
        if (len(raw) != before.st_size or _voice_identity(os.fstat(fd)) != identity
                or _voice_identity(config.lstat()) != identity):
            raise ValueError
        def unique(items):
            out = {}
            for key, value in items:
                if key in out:
                    raise ValueError
                out[key] = value
            return out
        locale = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)["language"]["code"]
        if not isinstance(locale, str) or locale.replace("-", "_").split("_")[0].lower() != lang:
            raise ValueError
    except (KeyError, ValueError, TypeError, OSError, RecursionError):
        raise Hold("voice_locale_unverified") from None
    finally:
        if fd is not None:
            os.close(fd)
    proof = (config, identity)
    require_voice_proof(proof, checkpoint=checkpoint)
    return Voice(str(p), lang), proof


def require_voice_proof(proof, *, checkpoint=lambda: None):
    checkpoint()
    config, identity = proof
    try:
        if _voice_identity(config.lstat()) != identity:
            raise Hold("voice_locale_unverified")
    except OSError:
        raise Hold("voice_locale_unverified") from None
    checkpoint()


def voice_for(value, lang):
    return inspect_voice(value, lang)[0]


class SelectedVoices(Mapping):
    """Snapshot selections, validating only the requested response locale."""

    def __init__(self, choices):
        self._choices = dict(choices)

    def __getitem__(self, lang):
        return voice_for(self._choices[lang], lang)

    def __iter__(self):
        return iter(self._choices)

    def __len__(self):
        return len(self._choices)


def tools_ready(*names):
    return all(shutil.which(n) for n in names)


async def terminate(p):
    if p.returncode is None:
        try:
            p.terminate()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(p.wait(), 1)
    except asyncio.TimeoutError:
        try:
            p.kill()
        except ProcessLookupError:
            pass
        await p.wait()


@asynccontextmanager
async def child(name, *args, stdin=False, checkpoint=lambda: None):
    # A desktop user chooses model data only. Executable names below are fixed;
    # neither transcripts nor model replies can select executables or flags.
    if name not in ("arecord","whisper-cli","piper","aplay"):
        raise Hold("unknown_audio_backend")
    binary = shutil.which(name)
    if not binary:
        raise Hold("missing_" + name)
    checkpoint()
    async def start():
        # This runs in the scheduled spawn task, not merely before queuing it.
        argv = tuple(map(str, args))
        env = {**os.environ, **spawn_environment()}
        checkpoint()
        return await asyncio.create_subprocess_exec(binary, *argv,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            env=env)
    spawn = asyncio.create_task(start())
    try:
        p = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        async def reclaim_spawn():
            await terminate(await spawn)
        await finish_owned(reclaim_spawn())
        raise
    except OSError:
        raise Hold("audio_start_failed") from None
    try:
        checkpoint()
        yield p
    finally:
        await finish_owned(terminate(p))


def has_signal(path, *, checkpoint=lambda: None):
    checkpoint()
    try:
        with wave.open(str(path),"rb") as w:
            if (w.getnchannels(),w.getsampwidth(),w.getframerate()) != (1,2,16000):
                return False
            # Require at least 100 ms of non-silent frames; this is not a VAD
            # quality guarantee. Never transcribe a silent/truncated capture.
            hits = 0
            for _ in range(1550):
                checkpoint()
                block = w.readframes(320)
                checkpoint()
                if len(block) < 640:
                    break
                rms = math.sqrt(sum(x*x for (x,) in struct.iter_unpack("<h", block)) / 320)
                hits += rms >= 90
                if hits >= 5:
                    checkpoint()
                    return True
    except (OSError, EOFError, wave.Error, struct.error):
        pass
    checkpoint()
    return False


# Only active audio operations poll. The deadline includes polling time.
AUTH_POLL = .25


async def validate_signal(path, *, checkpoint):
    """Retain the private WAV and task ownership until the worker drains.

    The owner checks live authorization on the event loop; the worker only
    receives a thread-safe stop signal, never GTK or mutable UI callbacks.
    Blocking filesystem calls cannot be forcibly interrupted by cancellation.
    """
    checkpoint()
    stop = Event()
    def worker_check():
        if stop.is_set():
            raise Hold("turn_cancelled")
    def work():
        worker_check()
        result = has_signal(path, checkpoint=worker_check)
        worker_check()
        return result
    worker = asyncio.create_task(asyncio.to_thread(work))
    try:
        while True:
            checkpoint()
            done, _ = await asyncio.wait((worker,), timeout=AUTH_POLL)
            checkpoint()
            if done:
                result = worker.result()
                checkpoint()
                return result
    finally:
        stop.set()
        # Never cancel the to_thread task: doing so would hide a live thread.
        await finish_owned(asyncio.gather(worker, return_exceptions=True))


async def await_authorized(factory, timeout, checkpoint):
    return await owned_wait(factory, timeout, checkpoint, interval=AUTH_POLL)


async def _capture(wav, finish, publish, *, checkpoint):
    # Include queued spawn and capture in the existing 33-second budget.
    # Startup is observable only when the child returns and WAV data is seen.
    deadline = OperationDeadline(33, checkpoint)
    heard_start = False

    def check():
        deadline.check()
        if not heard_start and deadline.remaining() <= 29:
            raise Hold("capture_timeout")

    async def run():
        nonlocal heard_start
        async with child("arecord", "-q", "-t", "wav", "-f", "S16_LE", "-r",
                         "16000", "-c", "1", "-d", "30", wav, checkpoint=check) as p:
            check()
            while p.returncode is None:
                check()
                if not heard_start and wav.exists() and wav.stat().st_size > 44:
                    # Recheck after filesystem work and before publishing.
                    check()
                    heard_start = True
                    publish("listening")
                if finish.is_set():
                    check()
                    try:
                        p.send_signal(signal.SIGINT)
                    except ProcessLookupError:
                        pass
                    break
                await asyncio.sleep(.04)
            check()
            # Keep the existing short finish wait, bounded by the stage.
            await await_authorized(p.wait, min(2, deadline.remaining()), check)
            check()

    try:
        await await_with_deadline(run, deadline, interval=AUTH_POLL)
    except asyncio.TimeoutError:
        raise Hold("capture_timeout") from None


async def listen(model, finish, publish, *, checkpoint):
    checkpoint()
    model = selected_file(model, ".bin")
    if not tools_ready("arecord","whisper-cli"):
        raise Hold("capture_backend_missing")
    with tempfile.TemporaryDirectory(prefix="tihulu-capture-") as tmp:
        wav, stem = Path(tmp)/"input.wav", Path(tmp)/"transcript"
        await _capture(wav, finish, publish, checkpoint=checkpoint)
        checkpoint()
        if not await validate_signal(wav, checkpoint=checkpoint):
            raise Hold("no_speech_signal")
        return await _transcription_stage(model, wav, stem, None, publish,
                                          checkpoint=checkpoint)


MAX_TRANSCRIPT = 64000


MAX_RECORDING = 256 * 1024 * 1024


def recording_identity(recording, *, checkpoint):
    checkpoint()
    if not isinstance(recording, str) or not Path(recording).is_absolute():
        raise Hold("recording_not_found")
    if Path(recording).suffix.lower() != ".wav":
        raise Hold("unsupported_recording")
    def authorized():
        checkpoint()
        return True
    try:
        return selection_identity(recording, authorized=authorized, max_bytes=MAX_RECORDING)
    except Hold as exc:
        if str(exc) == "csv_selection_unreadable":
            raise Hold("recording_not_found") from None
        if str(exc) in ("csv_invalid_size", "csv_not_regular_file", "csv_invalid_selection"):
            raise Hold("unsupported_recording") from None
        raise


async def transcribe(model, recording, runner=None, publish=None, *, checkpoint,
                     expected_identity=None):
    """Transcribe a bounded private copy of an explicitly selected recording."""
    checkpoint()
    model = selected_file(model, ".bin")
    identity = recording_identity(recording, checkpoint=checkpoint)
    if expected_identity is not None and identity != expected_identity:
        raise Hold("recording_changed")
    def authorized():
        checkpoint()
        return True
    with tempfile.TemporaryDirectory(prefix="tihulu-transcribe-") as tmp:
        stem = Path(tmp) / "transcript"
        snapshot = Path(tmp) / "selected.wav"
        try:
            await copy_selected_to(recording, snapshot, authorized=authorized,
                                   expected_identity=identity, max_bytes=MAX_RECORDING)
        except Hold as exc:
            if str(exc) in ("csv_selection_changed", "csv_changed_during_read"):
                raise Hold("recording_changed") from None
            raise
        checkpoint()
        if not await validate_signal(snapshot, checkpoint=checkpoint):
            raise Hold("no_speech_signal")
        return await _transcription_stage(model, snapshot, stem, runner, publish,
                                          checkpoint=checkpoint)


async def _transcription_stage(model, wav, stem, runner, publish, *, checkpoint):
    # Start the budget before status publication and queued process creation.
    deadline = OperationDeadline(120, checkpoint)

    async def run():
        deadline.check()
        if publish:
            publish("transcribing")
        deadline.check()
        if runner is None:
            await _whisper(model, wav, stem, checkpoint=deadline.check)
        else:
            await runner(model, wav, stem)
        deadline.check()
        output = stem.with_suffix(".txt")
        if not output.is_file() or output.stat().st_size > MAX_TRANSCRIPT:
            raise Hold("invalid_transcript")
        text = output.read_text(encoding="utf-8").strip()
        if not text or len(text) > 16000 or not any(ch.isalnum() for ch in text):
            raise Hold("empty_transcript")
        deadline.check()
        return text

    return await await_with_deadline(run, deadline, interval=AUTH_POLL)


async def _whisper(model, wav, stem, *, checkpoint):
    checkpoint()
    if not tools_ready("whisper-cli"):
        raise Hold("transcription_backend_missing")
    async with child("whisper-cli", "-m", model, "-f", wav, "-l", "auto",
                     "-otxt", "-of", stem, checkpoint=checkpoint) as p:
        checkpoint()
        if await p.wait():
            raise Hold("transcription_failed")
        checkpoint()


async def speak(speech, publish, *, checkpoint):
    checkpoint()
    model = voice_for(speech.voice_id, speech.language)
    if not tools_ready("piper","aplay"):
        raise Hold("playback_backend_missing")
    with tempfile.TemporaryDirectory(prefix="tihulu-playback-") as tmp:
        wav = Path(tmp)/"reply.wav"
        synthesis = OperationDeadline(90, checkpoint)

        async def synthesize():
            synthesis.check()
            publish("synthesizing")
            synthesis.check()
            async with child("piper", "--model", model.id, "--output_file", wav,
                             stdin=True, checkpoint=synthesis.check) as p:
                payload = (speech.text + "\n").encode("utf-8")
                synthesis.check()
                await p.communicate(payload)
                synthesis.check()
                if p.returncode or not wav.is_file() or wav.stat().st_size <= 44:
                    raise Hold("synthesis_failed")
                synthesis.check()

        await await_with_deadline(synthesize, synthesis, interval=AUTH_POLL)
        playback = OperationDeadline(180, checkpoint)

        async def play():
            async with child("aplay", "-q", wav, checkpoint=playback.check) as p:
                playback.check()
                publish("speaking")
                playback.check()
                if await p.wait():
                    raise Hold("playback_failed")
                playback.check()

        await await_with_deadline(play, playback, interval=AUTH_POLL)
        checkpoint()

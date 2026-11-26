# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Explicit, on-demand local capture, Whisper transcription and Piper playback."""
import asyncio
import fcntl
import json
import hashlib
import hmac
import math
import os
import shutil
import signal
import stat
import struct
import tempfile
import wave
from collections.abc import Mapping
from contextlib import asynccontextmanager, contextmanager, nullcontext
from pathlib import Path
from threading import Event

from .. import resources
from ..owned_wait import OperationDeadline, await_with_deadline, finish_owned
from ..owned_wait import await_authorized as owned_wait
from ..selected_csv import selection_identity, copy_selected_to
from .m1 import Hold, Voice, safe_text


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
    if not p.is_absolute() or p.suffix.lower() != suffix:
        raise Hold("incompatible_model_file")
    try:
        if not stat.S_ISREG(p.lstat().st_mode):
            raise OSError
    except OSError:
        raise Hold("incompatible_model_file") from None
    return p


def _voice_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_nlink)


def _runtime_identity(path):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise OSError
        return _voice_identity(info)
    except OSError:
        raise Hold("model_file_changed") from None


def _bind_runtime_model(path, checkpoint, *, expected_identity=None):
    """Bind the current regular inode to the lifetime of an audio operation.

    This is a metadata guard, not a persisted digest or an open-file handoff
    to the engine. Never claim it closes the engine pathname-open race.
    """
    checkpoint()
    identity = _runtime_identity(path)
    if expected_identity is not None and identity != expected_identity:
        raise Hold("model_file_changed")

    def check():
        checkpoint()
        if _runtime_identity(path) != identity:
            raise Hold("model_file_changed")
        checkpoint()

    return check


def voice_config_identity(value):
    p = selected_file(value, ".onnx")
    try:
        info = p.with_name(p.name + ".json").lstat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 1048576:
            raise ValueError
        return _voice_identity(info)
    except (OSError, ValueError):
        raise Hold("voice_locale_unverified") from None


def inspect_voice(value, lang, *, checkpoint=lambda: None, expected_identity=None):
    """Pin and bound the Piper sidecar; never wait on a FIFO or follow a symlink."""
    p = selected_file(value, ".onnx")
    config = p.with_name(p.name + ".json")
    fd = None
    try:
        checkpoint()
        fd = os.open(config, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 1048576
                or (expected_identity is not None and _voice_identity(before) != expected_identity)):
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
    proof = (config, identity, hashlib.sha256(raw).hexdigest())
    require_voice_proof(proof, checkpoint=checkpoint)
    return Voice(str(p), lang), proof


def require_voice_proof(proof, *, checkpoint=lambda: None):
    checkpoint()
    config, identity, _ = proof
    try:
        if _voice_identity(config.lstat()) != identity:
            raise Hold("voice_locale_unverified")
    except OSError:
        raise Hold("voice_locale_unverified") from None
    checkpoint()


def bind_voice_digest(value, lang, expected, *, checkpoint):
    """Validate the same bounded JSON bytes used for locale verification."""
    checkpoint()
    if expected is None:
        return checkpoint
    _, proof = inspect_voice(value, lang, checkpoint=checkpoint)
    if not hmac.compare_digest(proof[2], expected):
        raise Hold("voice_digest_mismatch")

    def bound():
        require_voice_proof(proof, checkpoint=checkpoint)

    bound()
    return bound


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


def _sealed_sidecar(fd, identity, checkpoint, *, expected_digest=None):
    """Copy only the bounded Piper JSON into an immutable Linux memfd."""
    if not 0 < identity[2] <= 1048576:
        raise Hold("voice_locale_unverified")
    sealed = None
    try:
        sealed = os.memfd_create("tihulu-piper-config", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        remaining = identity[2]
        digest = hashlib.sha256()
        while remaining:
            checkpoint()
            block = os.read(fd, min(65536, remaining))
            if not block:
                raise Hold("model_file_changed")
            remaining -= len(block)
            digest.update(block)
            view = memoryview(block)
            while view:
                written = os.write(sealed, view)
                if written <= 0:
                    raise Hold("audio_model_descriptor_unavailable")
                view = view[written:]
        if os.read(fd, 1) or _voice_identity(os.fstat(fd)) != identity:
            raise Hold("model_file_changed")
        if expected_digest is not None and not hmac.compare_digest(digest.hexdigest(), expected_digest):
            raise Hold("voice_digest_mismatch")
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        fcntl.fcntl(sealed, fcntl.F_ADD_SEALS, seals)
        if fcntl.fcntl(sealed, fcntl.F_GET_SEALS) & seals != seals:
            raise Hold("audio_model_descriptor_unavailable")
        os.lseek(sealed, 0, os.SEEK_SET)
        checkpoint()
        result, sealed = sealed, None
        return result
    except (OSError, AttributeError):
        raise Hold("audio_model_descriptor_unavailable") from None
    finally:
        if sealed is not None:
            os.close(sealed)


@contextmanager
def _pinned_model_arguments(name, args, checkpoint, *, model_identity=None, voice_proof=None,
                            alias_dir=None):
    """Hand inherited read-only model descriptors to Linux audio engines.

    Weights are pinned against pathname replacement, not in-place writes.
    The small Piper JSON is copied into a sealed immutable memfd.
    Existing identity/authorization checkpoints still apply.
    No original-path fallback is permitted if descriptor handoff is unavailable.
    """
    argv = list(map(str, args))
    descriptors = []
    originals = []
    inherited = ()
    try:
        if name in ("whisper-cli", "piper"):
            flag = "-m" if name == "whisper-cli" else "--model"
            if argv.count(flag) != 1 or argv.index(flag) + 1 >= len(argv):
                raise Hold("invalid_audio_model_arguments")
            index = argv.index(flag) + 1
            model = Path(argv[index])
            if not model.is_absolute():
                raise Hold("invalid_audio_model_arguments")

            def pin(path, *, seal=False, expected=None, digest=None):
                checkpoint()
                identity = _runtime_identity(path)
                if expected is not None and identity != expected:
                    raise Hold("model_file_changed")
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
                    descriptors.append(fd)
                    alias = "/proc/self/fd/%d" % fd
                    if (_voice_identity(os.fstat(fd)) != identity
                            or _voice_identity(os.stat(alias)) != identity
                            or _runtime_identity(path) != identity):
                        raise Hold("model_file_changed")
                except OSError:
                    raise Hold("audio_model_descriptor_unavailable") from None
                originals.append((path, identity))
                checkpoint()
                if seal:
                    immutable = _sealed_sidecar(fd, identity, checkpoint, expected_digest=digest)
                    descriptors.append(immutable)
                    return "/proc/self/fd/%d" % immutable
                return alias

            argv[index] = pin(model, expected=model_identity)
            if name == "piper":
                if "--config" in argv or "-c" in argv:
                    raise Hold("invalid_audio_model_arguments")
                config = model.with_name(model.name + ".json")
                if voice_proof is not None and voice_proof[0] != config:
                    raise Hold("voice_locale_unverified")
                config_fd_path = pin(config, seal=True,
                    expected=voice_proof[1] if voice_proof is not None else None,
                    digest=voice_proof[2] if voice_proof is not None else None)
                # Some Piper CLI versions ignore --config and infer model + ".json".
                # Both spellings must resolve to the same inherited, sealed bytes.
                if alias_dir is None:
                    raise Hold("audio_model_descriptor_unavailable")
                weights_fd_path = argv[index]
                inherited = tuple(fd for fd in descriptors
                                  if "/proc/self/fd/%d" % fd in (weights_fd_path, config_fd_path))
                alias = Path(alias_dir) / "voice.onnx"
                sidecar = alias.with_name("voice.onnx.json")
                try:
                    alias.symlink_to(weights_fd_path)
                    sidecar.symlink_to(config_fd_path)
                except OSError:
                    raise Hold("audio_model_descriptor_unavailable") from None
                argv[index] = str(alias)
                argv[index + 1:index + 1] = ["--config", str(sidecar)]
            else:
                inherited = tuple(fd for fd in descriptors if "/proc/self/fd/%d" % fd in argv)
            checkpoint()
            for path, identity in originals:
                if _runtime_identity(path) != identity:
                    raise Hold("model_file_changed")
        # Keep source FDs until spawn, but inherit only the exact engine FDs.
        yield tuple(argv), inherited
    finally:
        for fd in descriptors:
            os.close(fd)


@asynccontextmanager
async def child(name, *args, stdin=False, checkpoint=lambda: None,
                model_identity=None, voice_proof=None):
    # A desktop user chooses model data only. Executable names below are fixed;
    # neither transcripts nor model replies can select executables or flags.
    if name not in ("arecord","whisper-cli","piper","aplay"):
        raise Hold("unknown_audio_backend")
    binary = shutil.which(name)
    if not binary:
        raise Hold("missing_" + name)
    # The private Piper alias directory must outlive process execution, not
    # merely spawn: the CLI may open its inferred sidecar much later.
    with (tempfile.TemporaryDirectory(prefix="tihulu-piper-fds-")
          if name == "piper" else nullcontext()) as alias_dir:
        checkpoint()
        async def start():
            # This runs in the scheduled spawn task, not merely before queuing it.
            env = {**os.environ, **spawn_environment()}
            checkpoint()
            with _pinned_model_arguments(name, args, checkpoint, model_identity=model_identity,
                                         voice_proof=voice_proof, alias_dir=alias_dir) as (argv, descriptors):
                checkpoint()
                extra = {"pass_fds": descriptors} if descriptors else {}
                return await asyncio.create_subprocess_exec(binary, *argv,
                    stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    env=env, **extra)
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
            # quality guarantee. A selected recording can be much longer
            # than the 30-second microphone capture. Scan up to the existing
            # selected-file byte limit, not just its initial 31 seconds.
            hits = 0
            silence = bytes(640)
            for _ in range(MAX_RECORDING // 640):
                checkpoint()
                block = w.readframes(320)
                checkpoint()
                if len(block) < 640:
                    break
                if block == silence:
                    continue
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
        interrupted = False
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
                        interrupted = True
                    except ProcessLookupError:
                        pass
                    break
                await asyncio.sleep(.04)
            check()
            # Keep the existing short finish wait, bounded by the stage.
            status = await await_authorized(p.wait, min(2, deadline.remaining()), check)
            check()
            # arecord can report 1 or -SIGINT when the owner finishes early.
            # A spontaneous nonzero exit must never reach transcription, even
            # when the failed recorder left a seemingly usable partial WAV.
            if status != 0 and not (interrupted and status in (1, -signal.SIGINT)):
                raise Hold("capture_failed")

    try:
        await await_with_deadline(run, deadline, interval=AUTH_POLL)
    except asyncio.TimeoutError:
        raise Hold("capture_timeout") from None


async def listen(model, finish, publish, *, checkpoint):
    checkpoint()
    model = selected_file(model, ".bin")
    model_identity = _runtime_identity(model)
    checkpoint = _bind_runtime_model(model, checkpoint, expected_identity=model_identity)
    if not tools_ready("arecord","whisper-cli"):
        raise Hold("capture_backend_missing")
    with tempfile.TemporaryDirectory(prefix="tihulu-capture-") as tmp:
        wav, stem = Path(tmp)/"input.wav", Path(tmp)/"transcript"
        await _capture(wav, finish, publish, checkpoint=checkpoint)
        checkpoint()
        if not await validate_signal(wav, checkpoint=checkpoint):
            raise Hold("no_speech_signal")
        return await _transcription_stage(model, wav, stem, None, publish,
                                          checkpoint=checkpoint, model_identity=model_identity)


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
    model_identity = _runtime_identity(model)
    checkpoint = _bind_runtime_model(model, checkpoint, expected_identity=model_identity)
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
                                          checkpoint=checkpoint, model_identity=model_identity)


async def _transcription_stage(model, wav, stem, runner, publish, *, checkpoint, model_identity=None):
    # Start the budget before status publication and queued process creation.
    deadline = OperationDeadline(120, checkpoint)

    async def run():
        deadline.check()
        if publish:
            publish("transcribing")
        deadline.check()
        if runner is None:
            await _whisper(model, wav, stem, checkpoint=deadline.check, model_identity=model_identity)
        else:
            await runner(model, wav, stem)
        deadline.check()
        output = stem.with_suffix(".txt")
        try:
            if not output.is_file():
                raise Hold("invalid_transcript")
            with output.open("rb") as stream:
                payload = stream.read(MAX_TRANSCRIPT + 1)
            if len(payload) > MAX_TRANSCRIPT:
                raise Hold("invalid_transcript")
            # Match the universal-newline behavior of the former text read.
            text = payload.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()
        except (OSError, UnicodeError):
            raise Hold("invalid_transcript") from None
        if not safe_text(text):
            raise Hold("invalid_transcript")
        if not text or len(text) > 16000 or not any(ch.isalnum() for ch in text):
            raise Hold("empty_transcript")
        deadline.check()
        return text

    return await await_with_deadline(run, deadline, interval=AUTH_POLL)


def whisper_resource_args(profile, cores_count=None):
    """Fixed CLI options only; a GPU backend preference is not proof of use."""
    if profile is None:
        return ()
    profile = profile.checked()
    count = resources.cores() if cores_count is None else cores_count
    args = ("-t", str(profile.threads_for(count)))
    if profile.gpu == "cpu":
        args += ("-ng",)
    return args


async def _whisper(model, wav, stem, *, checkpoint, model_identity=None):
    checkpoint()
    if not tools_ready("whisper-cli"):
        raise Hold("transcription_backend_missing")
    options = whisper_resource_args(PROFILE)
    checkpoint()
    async with child("whisper-cli", "-m", model, "-f", wav, "-l", "auto",
                     *options, "-otxt", "-of", stem, checkpoint=checkpoint, model_identity=model_identity) as p:
        checkpoint()
        if await p.wait():
            raise Hold("transcription_failed")
        checkpoint()


def require_playback_wav(path, *, checkpoint):
    """Validate bounded PCM output before letting a playback child open it."""
    checkpoint()
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 44 < info.st_size <= MAX_RECORDING:
            raise Hold("synthesis_failed")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            with wave.open(stream, "rb") as wav:
                frames = wav.getnframes()
                channels, width = wav.getnchannels(), wav.getsampwidth()
                if (channels != 1 or width != 2 or not 8000 <= wav.getframerate() <= 192000
                        or frames <= 0 or frames * channels * width > info.st_size):
                    raise Hold("synthesis_failed")
                # Check the last advertised frame too, not just the header.
                wav.setpos(frames - 1)
                if len(wav.readframes(1)) != channels * width:
                    raise Hold("synthesis_failed")
            if _voice_identity(os.fstat(stream.fileno())) != _voice_identity(info):
                raise Hold("synthesis_failed")
        if _voice_identity(path.lstat()) != _voice_identity(info):
            raise Hold("synthesis_failed")
    except (OSError, EOFError, wave.Error, ValueError, OverflowError, struct.error):
        raise Hold("synthesis_failed") from None
    finally:
        if fd is not None:
            os.close(fd)
    checkpoint()


async def speak(speech, publish, *, checkpoint):
    checkpoint()
    if not safe_text(speech.text) or not speech.text.strip() or len(speech.text) > 32000:
        raise Hold("invalid_speech_text")
    model_path = selected_file(speech.voice_id, ".onnx")
    model_identity = _runtime_identity(model_path)
    model_check = _bind_runtime_model(model_path, checkpoint, expected_identity=model_identity)
    model, voice_proof = inspect_voice(speech.voice_id, speech.language, checkpoint=model_check)

    def synthesis_check():
        model_check()
        require_voice_proof(voice_proof, checkpoint=model_check)

    synthesis_check()
    if not tools_ready("piper","aplay"):
        raise Hold("playback_backend_missing")
    with tempfile.TemporaryDirectory(prefix="tihulu-playback-") as tmp:
        wav = Path(tmp)/"reply.wav"
        synthesis = OperationDeadline(90, synthesis_check)

        async def synthesize():
            synthesis.check()
            publish("synthesizing")
            synthesis.check()
            async with child("piper", "--model", model.id, "--output_file", wav,
                             stdin=True, checkpoint=synthesis.check, model_identity=model_identity,
                             voice_proof=voice_proof) as p:
                payload = (speech.text + "\n").encode("utf-8")
                synthesis.check()
                await p.communicate(payload)
                synthesis.check()
                if p.returncode:
                    raise Hold("synthesis_failed")
                synthesis.check()
            require_playback_wav(wav, checkpoint=synthesis.check)

        await await_with_deadline(synthesize, synthesis, interval=AUTH_POLL)
        synthesis_check()
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

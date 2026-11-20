# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Explicit, on-demand local capture, Whisper transcription and Piper playback."""
import asyncio
import json
import math
import shutil
import signal
import struct
import tempfile
import wave
from contextlib import asynccontextmanager
from pathlib import Path

from .m1 import Hold, Voice


def selected_file(value, suffix):
    if not isinstance(value, str) or not value:
        raise Hold("select_local_model")
    p = Path(value)
    if not p.is_absolute() or p.suffix.lower() != suffix or not p.is_file():
        raise Hold("incompatible_model_file")
    return p


def voice_for(value, lang):
    p = selected_file(value, ".onnx")
    config = p.with_name(p.name + ".json")
    try:
        if config.stat().st_size > 1048576:
            raise ValueError
        locale = json.loads(config.read_text(encoding="utf-8"))["language"]["code"]
        if not isinstance(locale, str) or locale.replace("-", "_").split("_")[0].lower() != lang:
            raise ValueError
    except (KeyError, ValueError, TypeError, OSError):
        raise Hold("voice_locale_unverified") from None
    return Voice(str(p), lang)


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
async def child(name, *args, stdin=False):
    # A desktop user chooses model data only. Executable names below are fixed;
    # neither transcripts nor model replies can select executables or flags.
    if name not in ("arecord","whisper-cli","piper","aplay"):
        raise Hold("unknown_audio_backend")
    binary = shutil.which(name)
    if not binary:
        raise Hold("missing_" + name)
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(binary, *map(str,args),
        stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL))
    try:
        p = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        try:
            await terminate(await spawn)
        finally:
            raise
    except OSError:
        raise Hold("audio_start_failed") from None
    try:
        yield p
    finally:
        await terminate(p)


def has_signal(path):
    try:
        with wave.open(str(path),"rb") as w:
            if (w.getnchannels(),w.getsampwidth(),w.getframerate()) != (1,2,16000):
                return False
            # Require at least 100 ms of non-silent frames; this is not a VAD
            # quality guarantee. Never transcribe a silent/truncated capture.
            hits = 0
            for _ in range(1550):
                block = w.readframes(320)
                if len(block) < 640:
                    break
                rms = math.sqrt(sum(x*x for (x,) in struct.iter_unpack("<h", block)) / 320)
                hits += rms >= 90
                if hits >= 5:
                    return True
    except (OSError, EOFError, wave.Error, struct.error):
        pass
    return False


async def listen(model, finish, publish):
    model = selected_file(model, ".bin")
    if not tools_ready("arecord","whisper-cli"):
        raise Hold("capture_backend_missing")
    with tempfile.TemporaryDirectory(prefix="tihulu-capture-") as tmp:
        wav, stem = Path(tmp)/"input.wav", Path(tmp)/"transcript"
        async with child("arecord","-q","-t","wav","-f","S16_LE","-r","16000","-c","1","-d","30",wav) as p:
            heard_start = False
            began = asyncio.get_running_loop().time()
            while p.returncode is None:
                if not heard_start and wav.exists() and wav.stat().st_size > 44:
                    heard_start = True
                    publish("listening")
                if finish.is_set():
                    try:
                        p.send_signal(signal.SIGINT)
                    except ProcessLookupError:
                        pass
                    break
                age = asyncio.get_running_loop().time() - began
                if (not heard_start and age > 4) or age > 33:
                    raise Hold("capture_timeout")
                await asyncio.sleep(.04)
            await asyncio.wait_for(p.wait(), 2)
        if not await asyncio.to_thread(has_signal, wav):
            raise Hold("no_speech_signal")
        publish("transcribing")
        async with child("whisper-cli","-m",model,"-f",wav,"-l","auto","-otxt","-of",stem) as p:
            if await asyncio.wait_for(p.wait(), 120):
                raise Hold("transcription_failed")
        output = stem.with_suffix(".txt")
        if not output.is_file() or output.stat().st_size > 64000:
            raise Hold("invalid_transcript")
        text = output.read_text(encoding="utf-8").strip()
        if not text or len(text)>16000 or not any(ch.isalnum() for ch in text):
            raise Hold("empty_transcript")
        return text


async def speak(speech, publish):
    model = voice_for(speech.voice_id, speech.language)
    if not tools_ready("piper","aplay"):
        raise Hold("playback_backend_missing")
    with tempfile.TemporaryDirectory(prefix="tihulu-playback-") as tmp:
        wav = Path(tmp)/"reply.wav"
        publish("synthesizing")
        async with child("piper","--model",model.id,"--output_file",wav, stdin=True) as p:
            await asyncio.wait_for(p.communicate((speech.text+"\n").encode("utf-8")), 90)
            if p.returncode or not wav.is_file() or wav.stat().st_size <= 44:
                raise Hold("synthesis_failed")
        async with child("aplay","-q",wav) as p:
            publish("speaking")
            if await asyncio.wait_for(p.wait(),180):
                raise Hold("playback_failed")

# SPDX-License-Identifier: AGPL-3.0-only
"""Real-engine acceptance using only known public/synthetic audio.

No microphone, no Jan, no physical playback. ALSA null sink is explicit.
The production audio adapters are exercised, including inherited model FDs.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import math
import random
import struct
import shutil
import subprocess
import tempfile
import time
import unicodedata
import wave
from contextlib import asynccontextmanager
from unittest.mock import patch

from tihulu_jarvis import resources
from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.m1 import Hold, SpeechRequest

CASES = {
    "en": "Hello, how are you today? The sky is blue and the sun is shining.",
    "de": "Guten Morgen. Wie geht es dir heute? Der Himmel ist blau und die Sonne scheint.",
    "tr": "Merhaba, bugün nasılsın? Gökyüzü mavi ve güneş parlıyor.",
}
HUMAN = "And so my fellow Americans ask not what your country can do for you ask what you can do for your country"
COMMANDS = {
    "tr": "Tarayıcıyı aç ve hava durumunu göster.",
    "en": "Open the browser and show the weather.",
    "de": "Öffne den Browser und zeige das Wetter.",
}


def words(text):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text).casefold())


def wer(expected, actual):
    a, b = words(expected), words(actual)
    if not a:
        raise ValueError("empty reference")
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        nxt = [i]
        for j, y in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j-1] + (x != y)))
        row = nxt
    return row[-1] / len(a)


def language_recall(references, actual):
    """Exact in-order word retention per reference block; no reused matches.

    A global WER may conceal the complete loss of one language. Use an LCS
    alignment across the complete reference, then count matches by block.
    Insertions/substitutions remain penalized separately by WER.
    """
    groups = [(lang, words(text)) for lang, text in references]
    if not groups or any(not tokens for _, tokens in groups):
        raise ValueError("empty reference")
    ref = [(lang, word) for lang, tokens in groups for word in tokens]
    observed = words(actual)
    table = [[0] * (len(observed) + 1) for _ in range(len(ref) + 1)]
    for i, (_, token) in enumerate(ref, 1):
        for j, value in enumerate(observed, 1):
            table[i][j] = (table[i-1][j-1] + 1 if token == value
                           else max(table[i-1][j], table[i][j-1]))
    counts, totals = {}, {}
    for lang, tokens in groups:
        totals[lang] = totals.get(lang, 0) + len(tokens)
        counts[lang] = 0
    i, j = len(ref), len(observed)
    while i and j:
        if ref[i-1][1] == observed[j-1]:
            counts[ref[i-1][0]] += 1
            i, j = i-1, j-1
        elif table[i-1][j] >= table[i][j-1]:
            i -= 1
        else:
            j -= 1
    return {lang: counts[lang] / total for lang, total in totals.items()}


def concatenate_clips(paths, target):
    with wave.open(str(target), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        for path in paths:
            with wave.open(str(path), "rb") as clip:
                if (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) != (1, 2, 16000):
                    raise ValueError("incompatible fixture")
                out.writeframes(clip.readframes(clip.getnframes()))
            out.writeframes(bytes(16000))


def noisy_pcm(data, *, snr_db=15, seed=270):
    """Add reproducible uniform noise at the requested pre-clipping SNR."""
    if not data or len(data) % 2 or not 0 <= snr_db <= 60:
        raise ValueError("invalid PCM or SNR")
    samples = [v for (v,) in struct.iter_unpack("<h", data)]
    rms = math.sqrt(sum(v*v for v in samples) / len(samples))
    if rms == 0:
        raise ValueError("silent PCM")
    rng = random.Random(seed)
    noise = [rng.uniform(-1, 1) for _ in samples]
    noise_rms = math.sqrt(sum(v*v for v in noise) / len(noise))
    gain = rms / (10 ** (snr_db / 20) * noise_rms)
    values = [max(-32768, min(32767, round(v+n*gain))) for v,n in zip(samples, noise)]
    return struct.pack("<%dh" % len(values), *values)


def noise_clip(src, dst):
    with wave.open(str(src), "rb") as inp:
        if (inp.getnchannels(), inp.getsampwidth(), inp.getframerate()) != (1, 2, 16000):
            raise ValueError("incompatible fixture")
        payload = inp.readframes(inp.getnframes())
    with wave.open(str(dst), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        out.writeframes(noisy_pcm(payload))


def convert(src, dst):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src),
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
                   check=True, timeout=30)


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            sha.update(block)
    return sha.hexdigest()


async def run(args):
    audio.configure(resources.effective({"profile": "custom", "gpu": "cpu", "threads": 4}))
    results = []
    with tempfile.TemporaryDirectory(prefix="jarvis-wav-qa-") as tmp:
        root = Path(tmp)
        def report(case, status, **detail):
            row = dict(case=case, status=status, **detail)
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

        async def transcribe(case, path, expected, threshold, language="auto", references=None):
            started = time.monotonic()
            stages = []
            try:
                actual = await audio.transcribe(str(args.model), str(path), publish=stages.append,
                                                checkpoint=lambda: None, language=language)
                error = wer(expected, actual)
                recall = language_recall(references, actual) if references else {}
                passed = (error <= threshold and stages == ["transcribing"]
                          and all(v >= .5 for v in recall.values()))
                report(case, "PASS" if passed else "FAIL",
                       expected=expected, actual=actual, wer=round(error, 4),
                       language_recall={k: round(v, 4) for k, v in recall.items()},
                       threshold=threshold, seconds=round(time.monotonic()-started, 2),
                       wav_sha256=digest(path), stages=stages)
            except (Hold, asyncio.TimeoutError) as exc:
                report(case, "FAIL", reason=str(exc) or type(exc).__name__, stages=stages)

        human = root / "human.wav"
        convert(args.human_wav, human)
        await transcribe("human-en", human, HUMAN, .25)
        clips = {}
        for lang, expected in CASES.items():
            raw, wav = root / (lang + "-raw.wav"), root / (lang + ".wav")
            subprocess.run(["espeak-ng", "-v", lang, "-s", "145", "-w", str(raw), expected],
                           check=True, timeout=30)
            convert(raw, wav)
            clips[lang] = wav
            await transcribe("espeak-" + lang, wav, expected, .5)
            await transcribe("espeak-forced-" + lang, wav, expected, .5, language=lang)
            noisy = root / (lang + "-noise.wav")
            noise_clip(wav, noisy)
            await transcribe("noise-15db-" + lang, noisy, expected, .5)
            fast_raw, fast = root / (lang + "-fast-raw.wav"), root / (lang + "-fast.wav")
            subprocess.run(["espeak-ng", "-v", lang, "-s", "190", "-w", str(fast_raw), expected],
                           check=True, timeout=30)
            convert(fast_raw, fast)
            await transcribe("fast-190-" + lang, fast, expected, .5)
            command_raw = root / (lang + "-command-raw.wav")
            command = root / (lang + "-command.wav")
            subprocess.run(["espeak-ng", "-v", lang, "-s", "145", "-w", str(command_raw), COMMANDS[lang]],
                           check=True, timeout=30)
            convert(command_raw, command)
            # Only recognition: no command dispatch or browser action.
            await transcribe("command-" + lang, command, COMMANDS[lang], .25)

        # These are deliberately separate stress criteria, not claims that
        # automatic single-language detection handles code switching.
        mixed = root / "mixed.wav"
        delayed = root / "delayed.wav"
        references = [(lang, CASES[lang]) for lang in ("tr", "en", "de")]
        mixed_text = " ".join(text for _, text in references)
        concatenate_clips([clips[lang] for lang, _ in references], mixed)
        with wave.open(str(delayed), "wb") as out:
            out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            out.writeframes(bytes(32 * 32000))
            with wave.open(str(clips["en"]), "rb") as clip:
                out.writeframes(clip.readframes(clip.getnframes()))
        await transcribe("mixed-tr-en-de", mixed, mixed_text, .5, references=references)
        await transcribe("mixed-segmented-tr-en-de", mixed, mixed_text, .5,
                         language="segmented", references=references)
        await transcribe("speech-after-32s", delayed, CASES["en"], .5)

        silence = root / "silence.wav"
        with wave.open(str(silence), "wb") as w:
            w.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            w.writeframes(bytes(64000))
        stages = []
        try:
            await audio.transcribe(str(args.model), str(silence), publish=stages.append,
                                   checkpoint=lambda: None)
        except Hold as exc:
            report("silence", "PASS" if str(exc) == "no_speech_signal" and not stages else "FAIL",
                   reason=str(exc), stages=stages)
        else:
            report("silence", "FAIL", reason="unexpected_transcript")

        # ALSA's file PCM accepts raw audio as a virtual microphone. Run the
        # production arecord -> WAV -> Whisper path with a padded known clip.
        # No actual microphone, system-wide ALSA change or fake recorder.
        capture_config = root / "capture.conf"
        raw_input = root / "capture.raw"
        for lang, source in clips.items():
            with wave.open(str(source), "rb") as clip:
                data = clip.readframes(clip.getnframes())
            if len(data) > 30 * 32000:
                report("virtual-mic-" + lang, "FAIL", reason="fixture_too_long")
                continue
            raw_input.write_bytes(data + bytes(30 * 32000 - len(data)))
            capture_config.write_text(
                'pcm.null { type null }\n'
                'pcm.!default { type file slave.pcm "null" file "/dev/null" '
                'infile "' + str(raw_input) + '" format "raw" }\n', encoding="utf-8")
            started, stages = time.monotonic(), []
            try:
                with patch.dict(os.environ, {"ALSA_CONFIG_PATH": str(capture_config)}):
                    actual = await audio.listen(str(args.model), asyncio.Event(), stages.append,
                                                checkpoint=lambda: None)
                error = wer(CASES[lang], actual)
                # A null-backed source may finish before the 40ms capture poll.
                # That is not a physical listening-state/timing acceptance.
                report("virtual-mic-" + lang,
                       "PASS" if error <= .5 and stages in (["transcribing"], ["listening", "transcribing"]) else "FAIL",
                       expected=CASES[lang], actual=actual, wer=round(error, 4),
                       threshold=.5, stages=stages, seconds=round(time.monotonic()-started, 2),
                       pcm_sha256=digest(raw_input))
            except (Hold, asyncio.TimeoutError, OSError) as exc:
                report("virtual-mic-" + lang, "FAIL", reason=str(exc) or type(exc).__name__, stages=stages)

        # A real aplay process sends audio to a deliberately configured null
        # device. Observe/copy only the private output at the playback boundary.
        config = root / "alsa.conf"
        config.write_text("pcm.!default { type null }\n", encoding="utf-8")
        original_child = audio.child
        piper_clips = {}
        for lang, voice in (("en", args.voice), ("tr", args.voice_tr), ("de", args.voice_de)):
            output, roundtrip = root / (lang + "-piper.wav"), root / (lang + "-roundtrip.wav")
            children, stages = [], []

            @asynccontextmanager
            async def observe(name, *argv, **kwargs):
                children.append(name)
                if name == "aplay":
                    shutil.copyfile(argv[-1], output)
                async with original_child(name, *argv, **kwargs) as proc:
                    yield proc

            try:
                with patch.object(audio, "child", observe), patch.dict(os.environ, {"ALSA_CONFIG_PATH": str(config)}):
                    await audio.speak(SpeechRequest(CASES[lang], lang, str(voice)),
                                      stages.append, checkpoint=lambda: None)
                report("piper-null-playback-" + lang,
                       "PASS" if stages == ["synthesizing", "speaking"]
                       and children == ["piper", "aplay"] and output.is_file() else "FAIL",
                       children=children, stages=stages, wav_sha256=digest(output))
            except (Hold, asyncio.TimeoutError, OSError) as exc:
                report("piper-null-playback-" + lang, "FAIL",
                       reason=str(exc) or type(exc).__name__, children=children, stages=stages)
                report("piper-roundtrip-" + lang, "HOLD", reason="synthesis_or_playback_failed")
                continue
            try:
                convert(output, roundtrip)
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                report("piper-roundtrip-" + lang, "FAIL", reason="conversion_failed")
                continue
            piper_clips[lang] = roundtrip
            await transcribe("piper-roundtrip-" + lang, roundtrip, CASES[lang], .5)
        if len(piper_clips) == 3:
            mixed_piper = root / "mixed-piper.wav"
            concatenate_clips([piper_clips[lang] for lang, _ in references], mixed_piper)
            await transcribe("mixed-piper-auto", mixed_piper, mixed_text, .5, references=references)
            await transcribe("mixed-piper-segmented", mixed_piper, mixed_text, .5,
                             language="segmented", references=references)
        else:
            for case in ("mixed-piper-auto", "mixed-piper-segmented"):
                report(case, "HOLD", reason="missing_piper_fixture")
    audio.configure(None)
    return results


def main():
    parser = argparse.ArgumentParser()
    for name in ("model", "voice", "voice-tr", "voice-de", "human-wav", "report"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    for attr in ("model", "voice", "voice_tr", "voice_de", "human_wav"):
        setattr(args, attr, getattr(args, attr).resolve(strict=True))
    results = asyncio.run(run(args))
    args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if len(results) == 31 and all(x["status"] == "PASS" for x in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

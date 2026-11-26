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


def convert(src, dst):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src),
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
                   check=True, timeout=30)


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


async def run(args):
    audio.configure(resources.effective({"profile": "custom", "gpu": "cpu", "threads": 4}))
    results = []
    with tempfile.TemporaryDirectory(prefix="jarvis-wav-qa-") as tmp:
        root = Path(tmp)
        def report(case, status, **detail):
            row = dict(case=case, status=status, **detail)
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

        async def transcribe(case, path, expected, threshold):
            started = time.monotonic()
            stages = []
            try:
                actual = await audio.transcribe(str(args.model), str(path), publish=stages.append,
                                                checkpoint=lambda: None)
                error = wer(expected, actual)
                report(case, "PASS" if error <= threshold and stages == ["transcribing"] else "FAIL",
                       expected=expected, actual=actual, wer=round(error, 4),
                       threshold=threshold, seconds=round(time.monotonic()-started, 2),
                       wav_sha256=digest(path), stages=stages)
            except (Hold, asyncio.TimeoutError) as exc:
                report(case, "FAIL", reason=str(exc) or type(exc).__name__, stages=stages)

        human = root / "human.wav"
        convert(args.human_wav, human)
        await transcribe("human-en", human, HUMAN, .25)
        for lang, expected in CASES.items():
            raw, wav = root / (lang + "-raw.wav"), root / (lang + ".wav")
            subprocess.run(["espeak-ng", "-v", lang, "-s", "145", "-w", str(raw), expected],
                           check=True, timeout=30)
            convert(raw, wav)
            await transcribe("espeak-" + lang, wav, expected, .5)

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

        # A real aplay process sends audio to a deliberately configured null
        # device. Observe/copy only the private output at the playback boundary.
        output, roundtrip = root / "piper.wav", root / "roundtrip.wav"
        config = root / "alsa.conf"
        config.write_text("pcm.!default { type null }\n", encoding="utf-8")
        original_child = audio.child
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
                await audio.speak(SpeechRequest(CASES["en"], "en", str(args.voice)),
                                  stages.append, checkpoint=lambda: None)
            report("piper-null-playback", "PASS" if stages == ["synthesizing", "speaking"]
                   and children == ["piper", "aplay"] and output.is_file() else "FAIL",
                   children=children, stages=stages)
            convert(output, roundtrip)
            await transcribe("piper-roundtrip-en", roundtrip, CASES["en"], .5)
        except (Hold, asyncio.TimeoutError, OSError, subprocess.CalledProcessError) as exc:
            report("piper-null-playback", "FAIL", reason=str(exc) or type(exc).__name__,
                   children=children, stages=stages)
            report("piper-roundtrip-en", "HOLD", reason="synthesis_or_playback_failed")
    audio.configure(None)
    return results


def main():
    parser = argparse.ArgumentParser()
    for name in ("model", "voice", "human-wav", "report"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    for attr in ("model", "voice", "human_wav"):
        setattr(args, attr, getattr(args, attr).resolve(strict=True))
    results = asyncio.run(run(args))
    args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if len(results) == 7 and all(x["status"] == "PASS" for x in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

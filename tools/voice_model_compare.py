# SPDX-License-Identifier: AGPL-3.0-only
"""Like-for-like CPU comparison; no expected words/languages sent in Auto."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import time

from voice_wav_acceptance import CASES, convert, digest, wer, language_recall, concatenate_clips
from tihulu_jarvis import resources
from tihulu_jarvis.voice import audio
from tihulu_jarvis.voice.m1 import Hold


async def run(model):
    results = []
    audio.configure(resources.effective({"profile": "custom", "gpu": "cpu", "threads": 4}))
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-model-compare-") as tmp:
            root = Path(tmp)
            clips = {}
            for lang, text in CASES.items():
                raw, wav = root / (lang + "-raw.wav"), root / (lang + ".wav")
                subprocess.run(["espeak-ng", "-v", lang, "-s", "145", "-w", str(raw), text],
                               check=True, timeout=30)
                convert(raw, wav)
                clips[lang] = wav
            mixed = root / "mixed.wav"
            references = [(lang, CASES[lang]) for lang in ("tr", "en", "de")]
            concatenate_clips([clips[lang] for lang, _ in references], mixed)
            mixed_text = " ".join(text for _, text in references)
            cases = [
                ("en-auto", clips["en"], CASES["en"], "auto"),
                ("de-auto", clips["de"], CASES["de"], "auto"),
                ("tr-auto", clips["tr"], CASES["tr"], "auto"),
                ("tr-forced", clips["tr"], CASES["tr"], "tr"),
                ("mixed-auto", mixed, mixed_text, "auto"),
                ("mixed-segmented", mixed, mixed_text, "segmented"),
            ]
            for name, path, expected, lang in cases:
                started = time.monotonic()
                row = dict(case=name, model=model.name, input_language=lang,
                           wav_sha256=digest(path), expected=expected, threshold=.5)
                try:
                    actual = await audio.transcribe(str(model), str(path), checkpoint=lambda: None,
                                                    language=lang)
                    error = wer(expected, actual)
                    recall = language_recall(references, actual) if name.startswith("mixed-") else {}
                    row.update(actual=actual, wer=round(error, 4),
                               language_recall={k: round(v, 4) for k, v in recall.items()},
                               status="PASS" if error <= .5 and all(v >= .5 for v in recall.values()) else "FAIL")
                except (Hold, asyncio.TimeoutError) as exc:
                    row.update(status="FAIL", reason=str(exc) or type(exc).__name__)
                row["seconds"] = round(time.monotonic() - started, 2)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                results.append(row)
    finally:
        audio.configure(None)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    model = args.model.resolve(strict=True)
    results = asyncio.run(run(model))
    args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return 0 if len(results) == 6 and all(row["status"] == "PASS" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

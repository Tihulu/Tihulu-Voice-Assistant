# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable upstream source experiment; never used by shipped code."""
import sys
from pathlib import Path

ANCHOR = "    // auto-detect language if not specified\n"
INSERT = """    // TIHULU disposable detection-only experiment; explicit 10s context.
    if (params.detect_language && params.audio_ctx == 500) {
        if (n_samples <= 0 || n_samples > 8 * WHISPER_SAMPLE_RATE ||
                params.audio_ctx > whisper_n_audio_ctx(ctx)) {
            return -5;
        }
        state->exp_n_audio_ctx = params.audio_ctx;
    }

"""

INSERT_300 = INSERT.replace("explicit 10s context", "explicit 6s context").replace("== 500", "== 300").replace("> 8 *", "> 5 *")

def patched_source(source,context=500):
    if type(context) is not int or context not in (300,500):
        raise ValueError("unsupported context")
    insertion = INSERT if context==500 else INSERT_300
    if source.count(ANCHOR) != 1 or INSERT in source:
        raise ValueError("unexpected or already patched upstream source")
    later = "state->exp_n_audio_ctx = params.audio_ctx;"
    if source.count(later) != 1 or source.index(later) < source.index(ANCHOR):
        raise ValueError("unexpected upstream context initialization")
    return source.replace(ANCHOR, insertion + ANCHOR, 1)

if __name__ == "__main__":
    if len(sys.argv) not in (2,3) or (len(sys.argv)==3 and sys.argv[2]!="300"):
        raise SystemExit("expected pinned upstream source path and optional context 300")
    path = Path(sys.argv[1])
    if path.name != "whisper.cpp" or path.is_symlink() or not path.is_file():
        raise SystemExit("unexpected source path")
    source = path.read_text(encoding="utf-8")
    path.write_text(patched_source(source,300 if len(sys.argv)==3 else 500), encoding="utf-8")

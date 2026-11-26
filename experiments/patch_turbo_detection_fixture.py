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

def patched_source(source):
    if source.count(ANCHOR) != 1 or INSERT in source:
        raise ValueError("unexpected or already patched upstream source")
    later = "state->exp_n_audio_ctx = params.audio_ctx;"
    if source.count(later) != 1 or source.index(later) < source.index(ANCHOR):
        raise ValueError("unexpected upstream context initialization")
    return source.replace(ANCHOR, INSERT + ANCHOR, 1)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("expected pinned upstream source path")
    path = Path(sys.argv[1])
    if path.name != "whisper.cpp" or path.is_symlink() or not path.is_file():
        raise SystemExit("unexpected source path")
    source = path.read_text(encoding="utf-8")
    path.write_text(patched_source(source), encoding="utf-8")

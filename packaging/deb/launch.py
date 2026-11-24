# SPDX-License-Identifier: AGPL-3.0-only
"""Entrypoint installed beside the pure-Python application modules.

The wrapper uses /usr/bin/python3 -I -B. No build-machine interpreter,
venv relocation, PYTHONPATH, or user-site package injection is needed.
"""
from pathlib import Path
import sys


def main():
    runtime = Path(__file__).resolve().parent / "python"
    sys.path.insert(0, str(runtime))
    import tihulu_jarvis
    if Path(tihulu_jarvis.__file__).resolve().parent != runtime / "tihulu_jarvis":
        raise SystemExit("Jarvis package runtime mismatch")
    from tihulu_jarvis.desktop import main as desktop_main
    if sys.argv[1:] == ["--check-runtime"]:
        # No user configuration, recording, model loading or network.
        from tihulu_jarvis.desktop import _gtk
        from tihulu_jarvis.analysis_ui import AnalysisView
        from tihulu_jarvis.temperature_engine import analyze_temperature, render_svg
        _gtk()
        svg = render_svg(analyze_temperature(b"time_s,temperature_c\n0,20\n1,22\n"))
        texture = AnalysisView._texture(svg)
        if (texture.get_width(), texture.get_height()) != (640, 320):
            raise SystemExit("Jarvis native chart runtime mismatch")
        print("Jarvis installed runtime: GTK and SVG texture OK")
        return 0
    return desktop_main()


if __name__ == "__main__":
    raise SystemExit(main())

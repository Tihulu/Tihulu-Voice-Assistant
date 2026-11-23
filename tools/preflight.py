# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Is this machine ready for physical acceptance, and what is it missing?

A pre-flight answers the questions a checklist asks before a person starts
tapping buttons: is GTK 4 with PyGObject importable, are the audio tools this
build calls present on PATH, is an endpoint and are models chosen, and is the
stored configuration readable. Nothing is downloaded, started or recorded here.
"""
import shutil
import sys
from pathlib import Path

READY = "ready"
MISSING = "missing"
UNKNOWN = "unknown"

AUDIO_TOOLS = ("arecord", "whisper-cli", "piper", "aplay")


def which(name):
    """Where a program lives on PATH, or None. Injectable for tests."""
    return shutil.which(name)


def gtk_available(importer=None):
    """Whether GTK 4 with PyGObject can be imported here."""
    importer = importer or _import_gtk
    try:
        version = importer()
    except Exception:
        return None
    return version


def _import_gtk():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    return (Gtk.get_major_version(), Gtk.get_minor_version())


def configuration(cfg=None, loader=None):
    """The stored configuration, or None when it cannot be read.

    The reason is kept by the caller: a missing import and an unreadable file
    are different problems, and a pre-flight that collapses them helps nobody.
    """
    if cfg is not None:
        return cfg
    loader = loader or _load_settings
    return loader()


def _load_settings():
    from tihulu_jarvis import settings
    return settings.load()


def _probe(tool, exe):
    """A device tool's presence on PATH, as a (state, detail) pair."""
    path = exe(tool)
    if path:
        return READY, path
    return MISSING, "not found on PATH"


def device_rows(exe=None):
    """The two devices this build can prefer, as questions a pre-flight asks.

    Presence is not capability: a driver being installed does not mean the
    device works, and a missing tool does not mean the machine has no GPU. The
    pre-flight states what it can see and stops there.
    """
    exe = exe or which
    rows = []
    for token, tool in (("cuda_tools", "nvidia-smi"), ("vulkan_tools", "vulkaninfo")):
        state, detail = _probe(tool, exe)
        rows.append((token, state, detail))
    return tuple(rows)


def rows(exe=None, importer=None, cfg=None, loader=None, interpreter=None):
    """(token, state, detail) for every pre-flight question.

    ``interpreter`` is stated in the verdict, because the answer is about the
    interpreter that runs this check — not about every Python on the machine.
    """
    exe = exe or which
    out = []
    version = gtk_available(importer)
    out.append(("gtk", READY if version else MISSING,
                "GTK %s.%s" % version if version else "PyGObject or GTK 4 is missing"))
    for tool in AUDIO_TOOLS:
        path = exe(tool)
        out.append((tool, READY if path else MISSING,
                    path if path else "not found on PATH"))
    out.extend(device_rows(exe))
    try:
        cfg = configuration(cfg, loader)
    except Exception as exc:
        out.append(("settings", MISSING, "cannot read stored choices: %s" % exc))
        cfg = None
    if cfg is not None:
        out.append(("settings", READY, "stored choices read"))
        endpoint = cfg.get("endpoint", "")
        out.append(("endpoint", READY if endpoint else MISSING,
                    endpoint if endpoint else "no Jan endpoint chosen"))
        stt = cfg.get("stt", "")
        out.append(("stt", READY if stt and Path(stt).is_file() else MISSING,
                    stt if stt and Path(stt).is_file() else "no STT model file chosen"))
        voices = [lang for lang in ("tr", "en", "de") if cfg.get("voices", {}).get(lang)]
        present = [lang for lang in voices if Path(cfg["voices"][lang]).is_file()]
        out.append(("voices", READY if present else MISSING,
                    ",".join(present) if present else "no voice file chosen"))
    return tuple(out)


def verdict(items, out=None, interpreter=None):
    """One line per question and an exit code a person can act on."""
    out = out or sys.stdout
    print("preflight interpreter: %s" % (interpreter or sys.executable), file=out)
    bad = [token for token, state, _ in items if state == MISSING]
    for token, state, detail in items:
        print("%-12s %-8s %s" % (token, state, detail), file=out)
    print("preflight: %d questions, %d missing"
          % (len(items), len(bad)), file=out)
    return 1 if bad else 0


def main(argv):
    items = rows()
    return verdict(items)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

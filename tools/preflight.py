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
import os
import stat
from pathlib import Path

READY = "ready"
MISSING = "missing"
UNKNOWN = "unknown"
OPTIONAL = frozenset(("cuda_tools", "vulkan_tools"))

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
        return READY, "found on PATH"
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


def _selected_model(value, suffix):
    """Metadata-only check; no model weights opened or parsed."""
    if not isinstance(value, str) or not value:
        return False
    try:
        path = Path(value)
        info = path.stat()
        return (path.is_absolute() and path.suffix.lower() == suffix
                and stat.S_ISREG(info.st_mode) and info.st_size > 0
                and os.access(path, os.R_OK))
    except (OSError, ValueError):
        return False


def _voice_metadata(value):
    """Check the selected voice and presence of its regular bounded sidecar.

    Do not parse metadata here: this is a read-only readiness hint, not proof
    that Piper accepts the model or that its language matches.
    """
    if not _selected_model(value, ".onnx"):
        return False
    try:
        path = Path(value)
        sidecar = path.with_name(path.name + ".json")
        info = sidecar.stat()
        return (stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 1048576
                and os.access(sidecar, os.R_OK))
    except (OSError, ValueError):
        return False


def rows(exe=None, importer=None, cfg=None, loader=None, interpreter=None):
    """Redacted metadata-only readiness, not hardware acceptance.

    No endpoint connection, program launch, model contents or settings writes.
    Required checks cover the full TR/EN/DE owner protocol; GPU tools are optional.
    """
    from tihulu_jarvis.voice.jan import endpoint as parse_endpoint, model_id

    exe = exe or which
    out = []
    version = gtk_available(importer)
    out.append(("gtk", READY if version else MISSING,
                "GTK %s.%s" % version if version else "PyGObject or GTK 4 is missing"))
    for tool in AUDIO_TOOLS:
        state, detail = _probe(tool, exe)
        out.append((tool, state, detail))
    out.extend(device_rows(exe))
    try:
        cfg = configuration(cfg, loader)
        if not isinstance(cfg, dict):
            raise ValueError
    except Exception:
        out.append(("settings", MISSING, "cannot read stored choices (details withheld)"))
        return tuple(out)
    out.append(("settings", READY, "stored choices read"))
    raw = cfg.get("endpoint", "")
    try:
        parse_endpoint(raw)
        valid = True
    except Exception:
        valid = False
    out.append(("endpoint", READY if valid else MISSING,
                "numeric loopback syntax valid; service untested" if valid else
                "no Jan endpoint chosen" if not raw else "invalid or non-loopback Jan endpoint"))
    llm = model_id(cfg.get("llm"))
    out.append(("llm", READY if llm else MISSING,
                "model ID selected; discovery untested" if llm else "no valid chat model selected"))
    stt = _selected_model(cfg.get("stt"), ".bin")
    out.append(("stt", READY if stt else MISSING,
                "nonempty readable .bin file; inference untested" if stt else
                "no compatible readable STT model file chosen"))
    voices = cfg.get("voices", {})
    if not isinstance(voices, dict):
        voices = {}
    present = [lang for lang in ("tr", "en", "de") if _voice_metadata(voices.get(lang))]
    absent = [lang for lang in ("tr", "en", "de") if lang not in present]
    out.append(("voices", READY if not absent else MISSING,
                "TR/EN/DE file and sidecar metadata present; language and speech untested" if not absent else
                "missing compatible model/sidecar: " + ",".join(absent)))
    return tuple(out)


def verdict(items, out=None, interpreter=None):
    """Report fixed categories, not private paths, addresses, or raw errors."""
    out = out or sys.stdout
    # The basename and version identify the interpreter without a home path.
    name = Path(interpreter or sys.executable).name
    if not name or not name.isascii() or not all(c.isalnum() or c in "._-" for c in name):
        name = "python"
    print("preflight interpreter: %s (%s)" % (name, sys.version.split()[0]), file=out)
    items = tuple(items)
    bad = [token for token, state, _ in items if token not in OPTIONAL and state != READY]
    optional = [token for token, state, _ in items if token in OPTIONAL and state != READY]
    for token, state, detail in items:
        print("%-12s %-8s %s" % (token, state, detail), file=out)
    print("preflight: %d questions, %d required missing/unverified, %d optional missing"
          % (len(items), len(bad), len(optional)), file=out)
    print("Metadata readiness only; physical acceptance and Jan inference are untested.", file=out)
    return 1 if bad else 0


def main(argv):
    items = rows()
    return verdict(items)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

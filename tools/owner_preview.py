# SPDX-License-Identifier: AGPL-3.0-only
"""Verify an extracted developer-built Debian preview; never install or launch it.

This compares the application source and launcher with a specific Git tree.
It is not verification of an arbitrary hostile archive, the OS or the
interpreter. Run only on an owner-controlled fresh checkout and extraction.
"""
import argparse
import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path

APP = "tihulu_jarvis"
SOURCE = "src/" + APP + "/"
RUNTIME = "usr/share/tihulu-jarvis/python/" + APP + "/"
EXTRA = {
    "packaging/deb/launch.py": "usr/share/tihulu-jarvis/launch.py",
    "data/io.github.Tihulu.Jarvis.desktop": "usr/share/applications/io.github.Tihulu.Jarvis.desktop",
    "data/io.github.Tihulu.Jarvis.svg": "usr/share/icons/hicolor/scalable/apps/io.github.Tihulu.Jarvis.svg",
}
WRAPPER = b'''#!/bin/sh
set -eu
launcher=$(readlink -f -- "$0")
exec /usr/bin/python3 -I -B "$(dirname -- "$launcher")/../share/tihulu-jarvis/launch.py" "$@"
'''
SCENARIOS = ("native_layout", "picker_cancel_reopen", "jan_text_only",
             "whisper_piper_tr", "whisper_piper_en", "whisper_piper_de",
             "cancel_and_recovery", "csv_chart_table_speech", "four_turn_memory",
             "recolor_and_four_exports", "local_file_search", "owner_ux")
MAX_MODULES = 512
MAX_FILE = 4 * 1024 * 1024


class Refusal(Exception):
    pass


def git(repo, *args):
    try:
        result = subprocess.run(["git", "-C", str(repo), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                check=True, timeout=30)
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        raise Refusal("git_evidence_unavailable") from None


def read_regular(root, relative):
    # Refuse symlinks on each component, including the extracted root itself.
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise Refusal("unexpected_symlink")
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
            raise Refusal("invalid_package_file")
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise Refusal("invalid_package_file")
        return data
    except OSError:
        raise Refusal("package_file_missing") from None


def verify(repo, root, commit):
    repo, root = Path(repo).absolute(), Path(root).absolute()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Refusal("exact_commit_required")
    if git(repo, "rev-parse", "HEAD").decode("ascii").strip() != commit:
        raise Refusal("checkout_commit_mismatch")
    # Source files come from Git objects, not mutable worktree files.
    tree = git(repo, "rev-parse", commit + "^{tree}").decode("ascii").strip()
    raw = git(repo, "ls-tree", "-rz", commit, "--", SOURCE)
    entries = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, sep, name = record.partition(b"\t")
        mode, kind, oid = meta.decode("ascii").split()
        name = name.decode("utf-8")
        if not sep or not name.startswith(SOURCE) or kind != "blob" or mode not in ("100644", "100755"):
            raise Refusal("unexpected_source_entry")
        if not name.endswith(".py"):
            raise Refusal("unexpected_source_entry")
        entries[name] = oid
        if len(entries) > MAX_MODULES:
            raise Refusal("module_limit")
    if not entries:
        raise Refusal("source_modules_missing")
    # The extraction is a local, trusted private directory, not a sandbox.
    if not root.is_dir() or root.is_symlink() or any(p.is_symlink() for p in root.parents):
        raise Refusal("invalid_extracted_root")
    expected = {RUNTIME + name[len(SOURCE):] for name in entries}
    for name, oid in entries.items():
        data = read_regular(root, RUNTIME + name[len(SOURCE):])
        if data != git(repo, "cat-file", "blob", oid):
            raise Refusal("module_content_mismatch")
    module_root = root / RUNTIME
    found = set()
    # Bound unexpected entries as well as expected modules.
    for directory, dirs, files in os.walk(module_root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink():
                raise Refusal("unexpected_symlink")
        for name in files:
            found.add((Path(directory) / name).relative_to(root).as_posix())
            if len(found) > MAX_MODULES:
                raise Refusal("module_limit")
    if found != expected:
        raise Refusal("unexpected_runtime_files")
    for source, target in EXTRA.items():
        if read_regular(root, target) != git(repo, "show", commit + ":" + source):
            raise Refusal("launcher_or_metadata_mismatch")
    if read_regular(root, "usr/bin/tihulu-jarvis") != WRAPPER:
        raise Refusal("wrapper_mismatch")
    if not os.access(root / "usr/bin/tihulu-jarvis", os.X_OK):
        raise Refusal("wrapper_not_executable")
    return {"commit": commit, "tree": tree, "modules": len(entries),
            "source_content": "PASS", "physical_acceptance": "UNKNOWN"}


def prepare(root, evidence):
    """Create only new disposable state and synthetic files, not an installation."""
    base = Path(tempfile.mkdtemp(prefix="jarvis-owner-preview-"))
    env = {}
    for name in ("config", "data", "cache", "state"):
        path = base / name
        path.mkdir(mode=0o700)
        env["XDG_" + name.upper() + "_HOME"] = str(path)
    (base / "temperature.csv").write_text(
        "time_s,temperature_c\n0,20\n10,22\n20,24\n30,26\n40,28\n", encoding="utf-8")
    research = base / "research"
    research.mkdir(mode=0o700)
    (research / "notes.txt").write_text("Synthetic local note\nmavi\n", encoding="utf-8")
    report = dict(evidence, scenarios={name: "UNKNOWN" for name in SCENARIOS},
                  independent_review="NOT_CLAIMED", clean_install="NOT_RUN")
    (base / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    launcher = str(Path(root).absolute() / "usr/bin/tihulu-jarvis")
    command = shlex.join(["env", *(key + "=" + value for key, value in env.items()), launcher])
    print("Disposable preview state:", base)
    print("Close the stable Jarvis normally before launching. No program was started.")
    print("Runtime check:", shlex.join([launcher, "--check-runtime"]))
    print("Launch after runtime check:", command)
    print("Synthetic inputs and UNKNOWN-only acceptance.json are in the new state directory.")
    return base


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--root", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args(argv)
    try:
        evidence = verify(args.repo, args.root, args.commit)
        print(json.dumps(evidence, sort_keys=True))
        if args.prepare:
            prepare(args.root, evidence)
    except (Refusal, OSError, ValueError) as exc:
        print("Preview HOLD:", str(exc) if isinstance(exc, Refusal) else "local_evidence_error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

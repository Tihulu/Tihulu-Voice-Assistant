import re
import subprocess
import sys
from pathlib import Path

WORKFLOW = ".github/workflows/ci.yml"
# A word boundary: `some-branches:` is not this workflow's trigger list.
BLOCK = re.compile(r"(?<![A-Za-z-])branches: \[(?P<body>.*?)\]", re.S)
# A name is quoted or bare: `main` is bare in this workflow, so a pattern that
# only reads quoted names silently drops it and reports it missing forever.
NAME = re.compile(r"'([^']+)'|([A-Za-z0-9._/-]+)")
MAIN = "main"
WIDTH = 96


def _block(text):
    match = BLOCK.search(text)
    if match is None:
        raise SystemExit("the workflow has no branches list")
    return match


def current(text):
    """The branch names the workflow lists, in the order it lists them."""
    names = []
    for quoted, bare in NAME.findall(_block(text).group("body")):
        if quoted:
            names.append(quoted)
        elif bare:
            names.append(bare)
    return tuple(names)


def remote_branches(remote="origin"):
    """Every branch the remote has, so a new branch is visible immediately."""
    proc = subprocess.run(["git", "ls-remote", "--heads", remote],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("git ls-remote failed: %s" % proc.stderr.strip())
    return tuple(sorted(line.split("refs/heads/", 1)[1].strip()
                        for line in proc.stdout.splitlines() if "refs/heads/" in line))


def rewrite(text, names, indent="               "):
    """Replace the list with a valid YAML flow list, wrapped for readability.

    A flow list needs a comma after every item, including the one that ends a
    wrapped line; the wrapper keeps the file readable and the list valid.
    """
    match = _block(text)
    # `main` is always kept: it is the branch every pull request may target.
    names = list(names) + [MAIN]
    keep = [name for name in current(text) if name in names]
    added = [name for name in names if name not in keep]
    ordered = keep + added
    items = ["'%s'" % name for name in ordered]
    lines = ["branches: ["]
    line = ""
    for item in items:
        piece = item + ","
        if line and len(indent) + len(line) + len(piece) + 1 > WIDTH:
            lines.append(indent + line)
            line = ""
        line += ("" if not line else " ") + piece
    lines.append(indent + line[:-1])          # no comma before the closing bracket
    block = "\n".join(lines) + "]"
    return text[:match.start()] + block + text[match.end():]


def check(path=WORKFLOW, remote="origin"):
    """(missing, stale): branches the workflow should list, and stale names."""
    text = Path(path).read_text(encoding="utf-8")
    listed = set(current(text))
    live = set(remote_branches(remote))
    # `main` is the default branch and is never reported stale, whatever the
    # remote happens to list; it is still reported when it is missing.
    stale = listed - live - {MAIN}
    return tuple(sorted(live - listed)), tuple(sorted(stale))


def main(argv):
    args = [name for name in argv[1:] if not name.startswith("-")]
    remote = args[0] if args else "origin"
    missing, stale = check(WORKFLOW, remote)
    if "--check" in argv:
        print("ci triggers: %d missing, %d stale" % (len(missing), len(stale)))
        for name in missing:
            print("  missing: %s" % name, file=sys.stderr)
        for name in stale:
            print("  stale: %s" % name, file=sys.stderr)
        return 1 if missing or stale else 0
    if missing or stale:
        Path(WORKFLOW).write_text(
            rewrite(Path(WORKFLOW).read_text(encoding="utf-8"),
                    remote_branches(remote)), encoding="utf-8")
        print("ci triggers: wrote %d names" % len(current(Path(WORKFLOW).read_text())))
    else:
        print("ci triggers: current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

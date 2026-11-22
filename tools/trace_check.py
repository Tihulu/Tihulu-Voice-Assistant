# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Is this document still true?

Every row of `docs/TRACEABILITY.md` names code and test classes. This reads the
document, the source tree and the test suite, and reports any row whose code or
test class no longer exists — so a traceability table cannot quietly become a
claim. It is read-only.
"""
import re
import sys
from pathlib import Path

ROW = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$", re.M)
CODE = re.compile(r"`([^`]+)`")


def modules(root):
    """Every module name that exists, in any of the source trees."""
    out = {path.stem for path in (root / "src" / "tihulu_jarvis").glob("*.py")}
    out |= {path.stem for path in (root / "src" / "tihulu_jarvis" / "voice").glob("*.py")}
    return out


def test_classes(root):
    names = set()
    for path in list((root / "tests").glob("*.py")) + list((root / "tests" / "gtk").glob("*.py")):
        for match in re.finditer(r"^class (\w+)\(", path.read_text(encoding="utf-8"), re.M):
            names.add(match.group(1))
    return names


def resolve(root, path):
    """Where a named code path lives, in any of this repository's trees."""
    for base in ("src/tihulu_jarvis", "src/tihulu_jarvis/voice", "tools", "packaging",
                 ".", "docs"):
        candidate = root / base / path
        if candidate.exists():
            return candidate
    return root / path


def check(root, document="docs/TRACEABILITY.md"):
    """(rows, problems): every named code path and test class must exist."""
    text = (root / document).read_text(encoding="utf-8")
    known_code = modules(root)
    known_tests = test_classes(root)
    rows, problems = 0, []
    for match in ROW.finditer(text):
        requirement, code, tests = (part.strip() for part in match.groups())
        if requirement in ("Requirement", "---"):
            continue
        rows += 1
        for path in CODE.findall(code):
            stem = path.split("/")[-1].removesuffix(".py")
            # A script, a shell file or a data file is evidence too; only a
            # path that exists nowhere is a problem.
            if stem not in known_code and not resolve(root, path).exists():
                problems.append("unknown code %r in %r" % (path, requirement))
            if not resolve(root, path).exists():
                problems.append("missing path %r for %r" % (path, requirement))
        for name in CODE.findall(tests):
            stem = name.split(":")[-1].removesuffix("()")
            # A suite, a job, a document or a tool is evidence too; a name that
            # looks like a class (`FooTests`) must exist somewhere.
            if stem in known_tests or stem in ("CI", "tools/smoke.py", "tests/gtk"):
                continue
            if stem.islower() or "." in stem:
                continue
            problems.append("unknown test class %r for %r" % (name, requirement))
    return rows, problems


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent
    rows, problems = check(root)
    print("trace check: %d rows, %d problems" % (rows, len(problems)))
    for problem in problems:
        print("  problem: %s" % problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

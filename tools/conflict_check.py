# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Would the next merge step apply cleanly? Rehearse it before merging it.

`stack_check.py` answers whether the *topology* is right; this answers the
question that actually blocks a merge — does this branch's change still apply to
what is below it. It runs `git merge-tree` in-memory for each branch against its
base, so nothing is checked out, merged or pushed, and a conflict is reported
before the button is pressed rather than after.

A clean rehearsal is not a guarantee: it proves the trees do not overlap, not
that CI will pass. A conflicted one is proof that it will not.
"""
import re
import subprocess
import sys

CONFLICT = re.compile(r"^\\+<<<<<<< \\.")
INFO = re.compile(r"^\\+=======$")


def merge_tree(base, head):
    """The in-memory merge result for `head` onto `base`.

    Returns `(clean, files)`, where ``files`` names the paths that would carry
    conflict markers. A missing ref is reported, not silently treated as clean.
    `git merge-tree --write-tree --name-only` prints the tree oid, then the
    conflicted paths, then a blank line and the messages; only the path list is
    reported, so a message never reads as a path.
    """
    proc = subprocess.run(["git", "merge-tree", "--write-tree", "--name-only",
                           base, head], capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        return None, ["git merge-tree failed: %s" % proc.stderr.strip()]
    lines = proc.stdout.split("\n")
    conflicted = []
    for line in lines[1:]:
        if line == "":
            break
        conflicted.append(line)
    return proc.returncode == 0, conflicted


def contained(base, head):
    """How many of ``head``'s own commits the base already carries.

    A merge pull request — one that carries a whole forked line — replays
    history the base already has when rebased. That is not a conflict to
    resolve: it is the wrong operation, and the tool should say so instead of
    telling the operator to rebase a branch that must be merged.
    """
    proc = subprocess.run(["git", "cherry", base, head], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return sum(1 for line in proc.stdout.splitlines()
               if line.startswith("-") and line[1:].strip())


def local_branches():
    """Every branch this clone has, so an unpushed branch can be checked too."""
    proc = subprocess.run(["git", "for-each-ref", "--format=%(refname:short)",
                           "refs/heads/"], capture_output=True, text=True, check=True)
    return [line for line in proc.stdout.split() if line]


def report(plan, out=None, err=None):
    """One line per branch in the merge order, and a verdict."""
    out, err = out or sys.stdout, err or sys.stderr
    problems, checked = [], 0
    if not plan:
        problems.append("no branches to rehearse")
    for entry in plan:
        number, head, base = entry.get("number"), entry.get("head"), entry.get("base")
        if not head or not base:
            problems.append("#%s has no head or base to rehearse" % number)
            continue
        if base not in local_branches():
            problems.append("#%s: base missing locally (%s)" % (number, base))
            print("  #%s %-32s base missing locally (%s)" % (number, head, base), file=out)
            continue
        if head not in local_branches():
            problems.append("#%s: head missing locally (%s)" % (number, head))
            print("  #%s %-32s head missing locally (%s)" % (number, head, head), file=out)
            continue
        already = contained(base, head)
        checked += 1
        if already is None:
            problems.append("#%s: commits cannot be compared" % number)
            print("  #%s %-32s could not be rehearsed" % (number, head), file=out)
            continue
        if already:
            # A merge PR carries a whole line: rebasing it replays what the base
            # already has, so the answer is to merge it, not to rebase it.
            print("  #%s %-32s merge it, do not rebase (%d commits already in base)"
                  % (number, head, already), file=out)
            continue
        clean, files = merge_tree(base, head)
        if clean is None:
            problems.append("#%s: %s" % (number, files[0] if files else "unknown"))
            print("  #%s %-32s could not be rehearsed" % (number, head), file=out)
            continue
        if clean:
            print("  #%s %-32s applies cleanly" % (number, head), file=out)
            continue
        problems.append("#%s (%s) conflicts in %s" % (number, head, ", ".join(files)))
        print("  #%s %-32s CONFLICT in %s" % (number, head, ", ".join(files)), file=err)
    for problem in problems:
        print("  problem: %s" % problem, file=err)
    print("conflict rehearsal: %d branches checked, %d problems" % (checked, len(problems)),
          file=out)
    return 1 if problems else 0


def main(argv):
    """Read a JSON plan (list of {number, head, base}) or take one branch."""
    if len(argv) >= 3 and argv[1] == "--from-file":
        import json
        plan = json.loads(open(argv[2], encoding="utf-8").read())
    elif len(argv) >= 3 and argv[1] == "--step":
        plan = [{"number": 0, "head": argv[2], "base": argv[3]}]
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return report(plan)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

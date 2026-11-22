# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Is this stack actually mergeable, in the order it is written down?

A deep stack is a risk: a branch based on the wrong parent, a PR that lost its
green run, or a merge that touched `main` would break the order the release
depends on. This reads the real pull requests — from a JSON file or from `gh` —
and states, with evidence, whether the chain holds.

Nothing is merged, pushed or created here: this is a read-only check.
"""
import json
import subprocess
import sys

MAIN = "main"
KINDS = ("open", "draft", "green")


def chain(prs, order=None):
    """(merge_order, problems) for a list of pull requests.

    ``order`` is the desired merge sequence by head branch; without it the
    sequence is derived from the bases, so a branch that is not based on its
    predecessor is reported rather than silently re-ordered.
    """
    problems = []
    by_head = {}
    for pr in prs:
        head = pr.get("headRefName")
        if not head or head == MAIN:
            problems.append("a pull request targets the main branch: %r" % (pr.get("number"),))
            continue
        if head in by_head:
            problems.append("two pull requests use the same head branch: %s" % head)
            continue
        if pr.get("state") not in (None, "OPEN"):
            problems.append("pull request %s is not open" % pr.get("number"))
        by_head[head] = pr
    sequence = order if order is not None else [pr.get("headRefName") for pr in prs]
    merge_order = []
    for index, head in enumerate(sequence):
        if not head or head == MAIN:
            continue  # already reported above; never part of the chain
        pr = by_head.get(head)
        if pr is None:
            problems.append("no open pull request for %s" % head)
            continue
        merge_order.append(pr)
        expected = MAIN if index == 0 else sequence[index - 1]
        if pr.get("baseRefName") != expected:
            problems.append("#%s (%s) is based on %s, expected %s"
                            % (pr.get("number"), head, pr.get("baseRefName"), expected))
        if pr.get("isDraft") is False:
            problems.append("#%s is not a draft" % pr.get("number"))
        if pr.get("conclusion") not in (None, "SUCCESS"):
            problems.append("#%s has no green run (%s)" % (pr.get("number"),
                                                           pr.get("conclusion")))
    return merge_order, problems


def from_gh(repo, limit=100):
    """The live pull requests of a repository, through the GitHub CLI."""
    fields = "number title headRefName baseRefName isDraft state"
    query = ("query($owner: String!, $name: String!, $limit: Int!) {"
             " repository(owner: $owner, name: $name) {"
             " pullRequests(first: $limit, states: OPEN) {"
             " nodes {%s commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }"
             " } } } }" % fields)
    proc = subprocess.run(["gh", "api", "graphql", "-f", "query=" + query,
                           "-F", "owner=" + repo.split("/")[0],
                           "-F", "name=" + repo.split("/")[1],
                           "-F", "limit=%d" % limit],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("gh api graphql failed: %s" % proc.stderr.strip())
    nodes = json.loads(proc.stdout)["data"]["repository"]["pullRequests"]["nodes"]
    prs = []
    for node in nodes:
        # A pull request with no checks yet reports PENDING, not a success.
        rollup = ((node.get("commits") or {}).get("nodes") or [{}])[0].get(
            "commit", {}).get("statusCheckRollup") or {}
        prs.append({"number": node["number"], "title": node["title"],
                    "headRefName": node["headRefName"], "baseRefName": node["baseRefName"],
                    "isDraft": node["isDraft"],
                    "conclusion": rollup.get("state", "PENDING")})
    return prs


def report(prs, order=None, out=None, err=None):
    """One verdict, printed with the evidence behind it."""
    out, err = out or sys.stdout, err or sys.stderr
    merge_order, problems = chain(prs, order)
    print("stack check: %d pull requests, %d mergeable in order, %d problems"
          % (len(prs), len(merge_order), len(problems)), file=out)
    for pr in merge_order:
        print("  #%s %-28s -> %s" % (pr["number"], pr["headRefName"], pr["baseRefName"]),
              file=out)
    for problem in problems:
        print("  problem: %s" % problem, file=err)
    return 1 if problems else 0


def main(argv):
    if len(argv) >= 3 and argv[1] == "--from-gh":
        prs = from_gh(argv[2])
    elif len(argv) >= 3 and argv[1] == "--from-file":
        prs = json.loads(open(argv[2], encoding="utf-8").read())
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    order = [name for name in argv[3:] if name != "--"] or None
    return report(prs, order)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

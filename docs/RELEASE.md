
## Merge log

With the owner's approval, the documented order in `docs/STACK.md` is being
executed one pull request at a time; nothing merges automatically and `main` is
never overwritten or force-pushed (only feature branches are force-pushed, with
approval, to publish a rebase).

| Step | PR | Branch | Result | Verified by |
| --- | --- | --- | --- | --- |
| 1 | #4 | `feat/m1-portable-core` | fast-forward into `main` at `2011643` | `tests` 39/39 OK on the merged `main`; CI green on the PR |
| 2 | #8 | `feat/modes-and-profiles` | fast-forward into `main` at `6a21bf7`; PR closed (head already in `main`) | `tests` 58/58 OK on the merged `main`; CI green on the PR |
| 3 | #9 | `feat/profile-ui` | rebased onto the updated `main` (its previous base's rebuild was dropped, not duplicated), then fast-forward into `main` at `6273094`; PR closed | `tests` 64/64 OK and `tests/gtk` 17/17 OK on the merged `main`; CI `test (3.10)`, `test (3.12)`, `gtk` green on the branch (run 45065806032) |

`main` now carries the portable core, the mode/profile policy core, the
profiles page with the native GTK cancellation tests, and the AI Market page
with the verified download flow (`151b100`, a real merge commit: `main` had
taken one documentation commit forward in the meantime, so a fast-forward was no
longer possible and nothing was rewritten). Steps 1–5 are closed: #4 MERGED,
#8, #9 and #12 CLOSED with the evidence recorded above, and #73 MERGED. The
remaining stack stands on `main` in the documented order, each step rebased
onto the updated `main` and verified before the next merge; the next merge is
#19, which the rehearsal identifies as a merge pull request (merge commit, not a
rebase).

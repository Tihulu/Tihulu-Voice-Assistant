# Stack map and merge procedure

The work is delivered as stacked draft pull requests so each increment stays reviewable
on its own. Merge in the order below; none of them merges automatically.

```
main (d548f7c…)
└── #4  feat/m1-portable-core         portable core + Jan + GTK client + CI
    ├── #8  feat/modes-and-profiles   modes, profiles, lifecycle, import/export
    │   └── #9  feat/profile-ui       profiles page, policy-gated controls
    │       └── #12 feat/market-ui    market page, download flow (+ #11 files)
    │           └── #13 feat/release-packaging  build/install verification
    ├── #6  feat/native-voice-controls  cancellation, awaited cleanup, GTK tests
    └── #11 feat/ai-market            market cards, lifecycle, registry

## Consolidated market line

`feat/market-consolidated` merges the branches that had grown below `feat/market-ui`:
the catalog line (#14) and the enforcement/transport/queue line (#15 → #16 → #17 →
#18), plus the repository hygiene and release documentation from #13. After it lands,
that whole line can be rebased onto it, keeping the stack one feature per branch. Its
own commit is a merge commit with no source change of its own.
```

## Procedure

1. Merge #4 into `main`, then rebase #8, #6 and #11 onto the updated `main`.
2. Merge #8, then rebase #9 onto it; merge #6.
3. #9 already carries #6's files; after #6 merges, rebase #9 and drop the duplicates.
4. Merge #11 into the branch that now contains the core; then rebase #12, which carries
   #11's module and should drop it after #11 lands.
5. Merge #12, then merge #13. After each step re-run CI and do not merge a red branch.

## Why the branches look larger than their own change

A stacked PR shows the accumulated diff of its unmerged dependencies. That is expected;
the per-PR "Files changed" list starts with its own files and then carries the unmerged
work below it. Nothing here overwrites `main` or force-pushes.

## Checking before merge

- The PR's own CI is green for its head commit (not an ancestor).
- The published tree matches what was verified locally; pushes, not file uploads, are the
  norm since the release branch.
- No credentials, recordings, model weights or workstation paths appear in the diff.

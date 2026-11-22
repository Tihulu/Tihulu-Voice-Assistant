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

## The current chain, verified

`tools/stack_check.py --from-gh Tihulu/Tihulu-Voice-Assistant` reads the live pull
requests and states, with evidence, whether the chain holds: each branch based on
its predecessor, every PR a draft, and every head commit's CI run green. It is
read-only — nothing is merged, pushed or created.

The verified merge order at the time of writing is 42 branches, `0 problems`:

```
#4  feat/m1-portable-core      -> main
#8  feat/modes-and-profiles    -> #4
#9  feat/profile-ui            -> #8
#12 feat/market-ui             -> #9
#19 feat/market-consolidated   -> #12
#20 feat/transfer-resume → #21 feat/market-history → #22 feat/update-flow
    → #23 feat/queue-persistence → #24 feat/catalog-trust → #25 feat/deb-packaging
#26 feat/remote-catalog        -> #25
#27 feat/audit-trail           -> #26
#28 feat/escalation-consent    -> #27
#29 feat/transcribe-file       -> #28
#30 feat/route-policy          -> #29
#31 feat/resource-locks        -> #30
#32 feat/privacy-disclosure    -> #31
#33 feat/state-labels          -> #32
#34 feat/chat-search           -> #33
#35 feat/attachments           -> #34
#36 feat/routing-table         -> #35
#37 feat/audit-filters         -> #36
#38 feat/streaming             -> #37
#39 feat/a11y-scale            -> #38
#40 feat/keyboard-nav          -> #39
#41 feat/receipts              -> #40
#42 feat/web-targets           -> #41
#43 feat/activity-board        -> #42
#44 feat/spoken-text           -> #43
#45 feat/files-research        -> #44
#46 feat/readiness              -> #45
#47 feat/search-history        -> #46
#48 feat/topbar                -> #47
#49 feat/quick-stop            -> #48
#50 feat/queue-summary         -> #49
#51 feat/settings-page         -> #50
#52 feat/transcript-export     -> #51
#53 feat/mute                  -> #52
#54 feat/keys-help             -> #53
#55 feat/agents-gates          -> #54
#56 feat/clean-install         -> #55
```

Re-run the check before every merge step; a branch that lost its green run or was
rebased onto the wrong parent is reported, not silently re-ordered. The branches
that forked below `feat/market-ui` (`#6`, `#11`, `#14`–`#18`) are carried by the
consolidation PR, which is why they are not part of this chain.

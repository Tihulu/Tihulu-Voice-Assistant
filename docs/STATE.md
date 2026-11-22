# Development checkpoint

Last updated at the top of the stack, `feat/traceability`. The numbers below are
the runs recorded on the newest PR; older PRs keep their own.

## Stack and merge order

Each branch is stacked on the one below it; nothing is merged automatically. Merge in
this order and rebase the branch above after each merge. The table is generated from
the live pull requests with the read-only check, so a stale order fails loudly instead
of quietly rearranging the stack:

```sh
python3 tools/stack_check.py --from-gh Tihulu/Tihulu-Voice-Assistant --markdown -- <order below>
```

| # | PR | Branch | Based on | Contents |
| --- | --- | --- | --- | --- |
| 1 | #4 | `feat/m1-portable-core` | `main` | Add portable multilingual core and local desktop chat and voice |
| 2 | #8 | `feat/modes-and-profiles` | `feat/m1-portable-core` | Enforce four operating modes and saved profile policy |
| 3 | #9 | `feat/profile-ui` | `feat/modes-and-profiles` | Bind operating modes and saved profiles into the desktop client |
| 4 | #12 | `feat/market-ui` | `feat/profile-ui` | Show truthful AI Market cards and apply selections in the client |
| 5 | #19 | `feat/market-consolidated` | `feat/market-ui` | Consolidate the catalog, enforcement, transport and queue lines |
| 6 | #20 | `feat/transfer-resume` | `feat/market-consolidated` | Resume an interrupted download from its verified partial |
| 7 | #21 | `feat/market-history` | `feat/transfer-resume` | Keep item revisions and roll back to a previous one |
| 8 | #22 | `feat/update-flow` | `feat/market-history` | Update an installed item from its approved source |
| 9 | #23 | `feat/queue-persistence` | `feat/update-flow` | Restore the download queue from disk after a restart |
| 10 | #24 | `feat/catalog-trust` | `feat/queue-persistence` | Verify curated catalogs with an owner-managed key |
| 11 | #25 | `feat/deb-packaging` | `feat/catalog-trust` | Assemble and verify a Debian package |
| 12 | #26 | `feat/remote-catalog` | `feat/deb-packaging` | Fetch curated catalogs over an owner-scoped HTTPS allowlist |
| 13 | #27 | `feat/audit-trail` | `feat/remote-catalog` | Record policy decisions in a bounded local audit trail |
| 14 | #28 | `feat/escalation-consent` | `feat/audit-trail` | Require trusted-UI consent for any privilege escalation |
| 15 | #29 | `feat/transcribe-file` | `feat/escalation-consent` | Transcribe an owner-supplied recording without the microphone |
| 16 | #30 | `feat/route-policy` | `feat/transcribe-file` | Route models explicitly under the operating mode |
| 17 | #31 | `feat/resource-locks` | `feat/route-policy` | Lease exclusive hardware resources per turn |
| 18 | #32 | `feat/privacy-disclosure` | `feat/resource-locks` | State the privacy boundary before content is written |
| 19 | #33 | `feat/state-labels` | `feat/privacy-disclosure` | State the live condition in words, not colour alone |
| 20 | #34 | `feat/chat-search` | `feat/state-labels` | Search the local conversation with bounded snippets |
| 21 | #35 | `feat/attachments` | `feat/chat-search` | Attach a local text file behind the filesystem policy |
| 22 | #36 | `feat/routing-table` | `feat/attachments` | State every task's route on the routing page |
| 23 | #37 | `feat/audit-filters` | `feat/routing-table` | Filter the local audit view by action and outcome |
| 24 | #38 | `feat/streaming` | `feat/audit-filters` | Show a typed reply as the server streams it |
| 25 | #39 | `feat/a11y-scale` | `feat/streaming` | One stored text scale, applied at once, with no motion |
| 26 | #40 | `feat/keyboard-nav` | `feat/a11y-scale` | Keyboard navigation with a stated binding table |
| 27 | #41 | `feat/receipts` | `feat/keyboard-nav` | Record decisions in the conversation as receipts, not bubbles |
| 28 | #42 | `feat/web-targets` | `feat/receipts` | Approved web targets: reading and submitting are separate rights |
| 29 | #43 | `feat/activity-board` | `feat/web-targets` | An activity board that states tasks without guessing progress |
| 30 | #44 | `feat/spoken-text` | `feat/activity-board` | Show the exact text that will be spoken, when asked |
| 31 | #45 | `feat/files-research` | `feat/spoken-text` | Read a chosen local folder behind the filesystem policy |
| 32 | #46 | `feat/readiness` | `feat/files-research` | State what this machine can do, before a turn fails |
| 33 | #47 | `feat/search-history` | `feat/readiness` | Remember searches locally, bounded and re-runnable |
| 34 | #48 | `feat/topbar` | `feat/search-history` | Name the active mode, profile, model and boundary in the topbar |
| 35 | #49 | `feat/quick-stop` | `feat/topbar` | A hard stop in the topbar that is the same stop |
| 36 | #50 | `feat/queue-summary` | `feat/quick-stop` | Count the queue from the board, with the leases it holds |
| 37 | #51 | `feat/settings-page` | `feat/queue-summary` | A settings page, and forgetting that only forgets preferences |
| 38 | #52 | `feat/transcript-export` | `feat/settings-page` | Export a conversation as bounded local text |
| 39 | #53 | `feat/mute` | `feat/transcript-export` | Mute suppresses audio output, never the answer |
| 40 | #54 | `feat/keys-help` | `feat/mute` | List the bound shortcuts on the settings page |
| 41 | #55 | `feat/agents-gates` | `feat/keys-help` | State the capabilities this build does not ship |
| 42 | #56 | `feat/clean-install` | `feat/agents-gates` | Verify a clean install with a smoke test outside the source tree |
| 43 | #57 | `feat/stack-check` | `feat/clean-install` | Verify the stack is mergeable with a read-only check |
| 44 | #58 | `feat/preflight` | `feat/stack-check` | A read-only pre-flight that states what the machine is missing |
| 45 | #59 | `feat/traceability` | `feat/preflight` | A traceability document that checks itself |

## Why a stacked PR looks larger than its own change

A stacked PR shows the accumulated diff of its unmerged dependencies; the per-PR "Files
changed" list starts with its own files and then carries the unmerged work below it.
Nothing here overwrites `main` or force-pushes.

## Executed results (latest green runs)

At the top of the stack (`feat/traceability`, run
[44987654028](https://github.com/Tihulu/Tihulu-Voice-Assistant/actions/runs/44987654028)):

- Core suite: **309/309** per interpreter (Python 3.10 and 3.12).
- Native GTK suite: **480/480** under Xvfb/D-Bus with `G_DEBUG=fatal-criticals`, under
  both `tr_TR` and `C` locales.
- Package job: sdist + wheel built (37 entries verified, no leaks), the wheel installed
  into a clean venv, the deterministic suite run against the **installed** artefact
  (309/309), `tools/smoke.py` **21/21 checks**, the Debian package built, extracted and
  imported, and the smoke run again against the deb root.
- Read-only checks: `tools/stack_check.py` → 44 branches mergeable in order, 0 problems;
  `tools/trace_check.py` → 38 rows, 0 problems; `tools/preflight.py` states 3 missing
  choices on the development machine (endpoint, STT file, voice file).

Each PR keeps its own run; verify the head on the PR rather than trusting this list.

## Verification limits (unchanged)

Physical microphone and output quality, model inference quality, COSMIC/Wayland input,
clean-machine install and rollback, remote transport and catalog trust are **not**
established by the above. `docs/NATIVE_ACCEPTANCE.md` is the physical checklist and
`docs/RELEASE.md` the release process; both remain unexecuted on a target machine.

## Next increments

- **Approved remote catalog sources** — `docs/CATALOG_SOURCES.md`. Hugging Face Hub's
  public `/api/models` (including the `ggml-format` Whisper models used by whisper.cpp
  and the Piper voice repos), the local Jan inventory, and the owner-signed curated
  catalog, as registered sources with an allowlist and digest-gated install. The gate is
  nothing: it is next in line.
- The remaining gates are physical (network transport, devices, model quality) and
  unbuilt integrations (OpenCode, provider sign-in, real browser); both are listed in
  `docs/CATALOG_SOURCES.md` and `docs/RELEASE.md`.

## Recovery

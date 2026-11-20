# Development checkpoint

Last updated for the top of the stack, `feat/release-packaging`.

## Stack and merge order

Each branch is stacked on the one below it; nothing is merged automatically. Merge in
this order and rebase the branch above after each merge:

| # | PR | Branch | Based on | Contents |
| --- | --- | --- | --- | --- |
| 1 | #1 | `docs/initial-public-scaffold` | `main` | Documentation foundation |
| 4 | #4 | `feat/m1-portable-core` | `main` | Portable AGPL core, loopback Jan client, GTK client, model selection, CI |
| 8 | #8 | `feat/modes-and-profiles` | `feat/m1-portable-core` | Four operating modes, enforceable profiles, lifecycle, import/export, rollback |
| 6 | #6 | `feat/native-voice-controls` | `feat/m1-portable-core` | Deterministic cancellation, awaited cleanup, native GTK control tests |
| 9 | #9 | `feat/profile-ui` | `feat/modes-and-profiles` (+ #6 files) | Profiles page, policy-gated controls, truthful mode disclosure |
| 11 | #11 | `feat/ai-market` | `feat/m1-portable-core` | Market cards, lifecycle, import, registry, per-profile selection |
| 12 | #12 | `feat/market-ui` | `feat/profile-ui` (+ #11 files) | Market page, Jan discovery, preview/test chat, storage, approved-source download |
| 13 | #13 | `feat/release-packaging` | `feat/market-ui` | Build/install verification CI job, `docs/RELEASE.md`, repository hygiene |

PR #11's module is carried by PR #12's branch; after #11 merges, rebase #12 to drop the
duplication. PR #9 carries PR #6's files until #6 merges; the same applies to #12.

## Executed results (latest green runs)

- Core suite: 97 tests per interpreter (Python 3.10 and 3.12).
- Native GTK suite: 38 cases under Xvfb/D-Bus with `G_DEBUG=fatal-criticals`.
- Package job: sdist + wheel built, wheel content verified (19 entries, no leaks), wheel
  installed into a clean venv and the suite ran 97/97 against the installed artefact.
- Runs: 44664461595 (core), 44674465300 and 44674935117 (native), 44680350527 (policy),
  44693729992 and 44697746284-family (profile UI), 44697825330 (market core),
  44702974062, 44707798395, 44712982487, 44715770391, 44722949289 (market UI),
  44762595116 (packaging). Verify the exact head on each PR; this list is a pointer, not a
  substitute for the PR's checks.

## Verification limits (unchanged)

Physical microphone and output quality, model inference quality, COSMIC/Wayland input,
clean-machine install and rollback, remote transport and catalog trust are **not**
established by the above. `docs/NATIVE_ACCEPTANCE.md` is the physical checklist and
`docs/RELEASE.md` the release process; both remain unexecuted on a target machine.

## Recovery

Everything lives on feature branches. Discard or revert any branch independently; shared
models, private configuration and the legacy application are never modified.

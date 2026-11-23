
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

## Build, install and recovery (development release process)

This document describes how the development artefact is produced and how it is
removed again. It does not claim a verified end-user installer.

## What this builds

- An sdist and a wheel of the portable Python package (`tihulu_jarvis`), AGPL-3.0-only, with no runtime dependencies.
- The wheel contains the package modules, the LICENSE, the console script `tihulu-jarvis` and the desktop metadata under `share/`.
- The wheel does not contain tests, documentation, developer notes, credentials, recordings, model weights or any machine-specific configuration.

## Reproduce the build

```sh
python -m venv .buildvenv
.buildvenv/bin/pip install build
.buildvenv/bin/python -m build --sdist --wheel --outdir dist
```

CI performs the same steps and additionally:
1. verifies the wheel contains every shipped module, the LICENSE, the entry point and the two `share/` metadata files;
2. asserts that no `tests/`, `docs/` or `data/` path leaked into the wheel;
3. installs the wheel into a clean virtual environment and runs the whole deterministic suite against the **installed** artefact, so a missing module or entry point fails the job;
4. imports the installed package and the desktop entry module.

## Install into an isolated prefix

```sh
python -m venv .runvenv
.runvenv/bin/pip install dist/tihulu_jarvis-*.whl
.runvenv/bin/tihulu-jarvis        # requires GTK 4 with PyGObject and a display
```

Configuration, profiles and the market registry stay under the XDG directories of the
installing user. No service, firewall rule, autostart entry or system model is created
by installation.

## Uninstall and rollback

```sh
.runvenv/bin/pip uninstall tihulu-jarvis
rm -rf .runvenv .buildvenv dist
```

Project-owned files, if any download artefact was ever created, live only under
`$XDG_DATA_HOME/tihulu-jarvis/market/artifacts`; removing the environment and that
directory leaves every shared model, voice and other application untouched. The legacy
application and its installation are never modified by this package.

## Honest limits

A green package job proves that the artefact builds, installs, imports and passes the
deterministic suite. It does not prove a clean-machine install on a target desktop,
desktop integration on COSMIC or Wayland, physical microphone or output, or model
inference quality. Those gates are tracked separately and require a real machine and
operator-chosen models.

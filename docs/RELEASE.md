# Build, install and recovery (development release process)

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
4. imports the installed package and the desktop entry module;
5. runs `tools/smoke.py` from outside the source tree against the installed wheel — 42 checks, one per shipped capability: preferences round-trip and refuse, profiles created and escalated, the audit trail recording, every vocabulary (state, disclosure, receipts, keys, sources, routing, search, locks), the resource profiles handed to the voice layer, the measured process usage, an approved source's cards and refusals, and the readiness report's mixed truth.

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

## Debian package

The portable wheel alone is not an end-user install. The Debian package
stages only its pure Python modules and license metadata in
`/usr/share/tihulu-jarvis/python`. The native wrapper invokes
`/usr/bin/python3 -I -B` with the packaged launch module, so the target's
distribution PyGObject/GTK libraries are visible without bundling an
interpreter or relocating a build-machine virtual environment. No user
Python path or user-site packages are required. The build environment
must have pip and a prebuilt wheel. Runtime dependencies include GTK 4,
PyGObject, GdkPixbuf and the SVG loader.

```sh
PATH="$PWD/.buildvenv/bin:$PATH" sh packaging/deb/build.sh .release-work/deb dist
# Non-installing runtime rehearsal from the extracted package:
dpkg-deb -x .release-work/tihulu-jarvis.deb .release-work/preview-root
.release-work/preview-root/usr/bin/tihulu-jarvis --check-runtime
```

The check requires a real display (Xvfb in CI is sufficient). It calls the
actual wrapper, imports the packaged runtime and initializes GTK, then
decodes a real synthetic SVG into a native texture. CI also runs the
deterministic suite from the installed wheel and extracted Debian modules;
any failed suite must fail the job.

Only after the owner approves installation:

```sh
sudo dpkg -i .release-work/tihulu-jarvis.deb
sudo dpkg -r tihulu-jarvis
```

The package has no service, no root-owned user data and no autostart entry.
Existing XDG configuration, profiles, market registry and shared models
are not removed by package removal. Do not install over a working version
without a separately verified rollback package and owner approval.
An extracted-root check is not a clean-machine install, COSMIC/Wayland
acceptance or physical audio test. See R1_ARTIFACT_EXPORT.md for the
remaining owner checks.

## The gates before a release, in order

A release is not a green job; it is the sequence of gates that produces it. Run
them in this order, and stop at the first red one.

1. **The stack is mergeable.** `python3 tools/stack_check.py --from-gh
   <owner>/<repo> [branch order]` — every branch based on its predecessor,
   every PR a draft, every head commit's run green. A branch rebased onto the
   wrong parent or a lost green run is reported, not re-ordered.
2. **Every increment is verified.** The CI matrix runs the deterministic suite
   on 3.10 and 3.12, the GTK suite, and the package job. A merge step never
   leaves the stack on a red branch.
3. **The extracted package works.** Run the actual Debian launcher with
   `--check-runtime` and `tools/smoke.py` outside the source tree. A true
   clean-machine install and rollback are separate owner gates.
4. **The requirements map is true.** `python3 tools/trace_check.py` — 38 rows
   pointing at code and test classes that all exist. A dangling reference fails
   the release.
5. **The target machine says what it is missing.**
   `PYTHONPATH=src /usr/bin/python3 tools/preflight.py` — GTK 4 with
   PyGObject, the audio tools, a chosen endpoint, an STT model file and a voice
   file. Exit code 1 means the physical acceptance cannot start yet.
6. **Physical acceptance.** The owner's checklist in `docs/NATIVE_ACCEPTANCE.md`,
   on the owner's machine, with the real artifacts. This is the only gate that
   can certify devices, integration and model quality, and it cannot be run
   here.

Gates 1–4 are automated and repeatable; 5 is automated but machine-specific; 6
is not automated and is never claimed to be.

## Rehearse the merge before making it

`stack_check.py` answers whether the topology is right. `conflict_check.py`
answers the question that actually blocks a merge — does this branch's change
still apply to what is below it — by running `git merge-tree --write-tree` in
memory for every branch in the order, so nothing is checked out, merged or
pushed:

```sh
# one step, or the whole verified order from the stack check
python3 tools/conflict_check.py --step <head> <base>
python3 tools/conflict_check.py --from-file chain.json     # from --markdown output
```

A clean rehearsal is not a guarantee: it proves the trees do not overlap, not
that CI will pass. A conflicted one is proof that it will not.

**Rehearsal result after the fix:** `conflict rehearsal: 51 branches checked, 0
problems`. Two branches were found conflicting and were fixed (with the owner's
approval to force-push feature branches): `#9 feat/profile-ui` and
`#12 feat/market-ui` both carried tracked `__pycache__` from before the hygiene
pass, which collided add/add with the pass that removed it. Both were rebased
onto their current bases, the bytecode was dropped, `.gitignore` now excludes it,
and CI is green on both (`97` and `64` core cases, `38` and `17` GTK cases).

The rehearsal also had to learn one thing the hard way: a branch that carries a
whole forked line — `#19 feat/market-consolidated`, a merge pull request — reports
"conflicts" on a rebase because rebasing it replays history the base already has.
That is the wrong operation, not a conflict: the tool now says "merge it, do not
rebase (6 commits already in base)", which is what the merge procedure in
`docs/STACK.md` has always said. A clean rehearsal is not a guarantee: it proves
the trees do not overlap, not that CI will pass.


## Merge log — continuation after the initial stack

| # | PR | What | How it landed | Evidence |
|---|----|------|---------------|----------|
| 8 | chain root | the feature line below the open stack (`feat/web-targets`, 23 features) | rebased onto the updated `main` at `19e0134`, landed as a fast-forward at `c540b4c`; two accepted test fixes landed with it (the condition-based wait budget, the sticky `chat_entered` flag) | 245 core / 268 GTK green locally; CI green on the published head |
| 9 | #43 – #72 | the open stack, one increment at a time | each increment replayed onto the updated `main` (its own commits, or its whole tree diff where its tip carried CI scaffolding), verified locally, published on its branch, then fast-forwarded into `main` once its own CI was green; #68 and #69 were rebuilt from their tree diffs after placeholder commits had marked them merged | see the section below; the stack completes at `939a567` with 356 core and 602 native GTK tests, every CI job green |

## The stack landing (the continuation after #19)

With the owner's approval, the documented order continued after #19 with the chain root below the open stack and then open pull requests #43 upward, one verified increment at a time; `main` was never overwritten or force-pushed, and every merge waited for its own CI.

1. The chain root: the feature line the open stack stands on (transfer resume, item revisions, update flow, queue persistence, catalog trust, Debian packaging, owner-scoped HTTPS catalog fetch, audit trail, escalation consent, file transcription, explicit routing, resource leases, privacy disclosure, state labels, conversation search, attachments, routing page, audit filters, streaming replies, text scale, keyboard navigation, receipts, approved web targets) was rebased onto `main` and landed as a fast-forward.
2. #43 (activity board) through #59 (traceability), then #60 (release runbook and gates), #61 (approved remote catalog sources), #62 (resource profiles and idle wakeups), #63 (repository language policy), #64 (voice-layer resource hand-off), #65 (measured process usage), #66 (approved catalog sources with truthful cards) and #67 (the merge rehearsal, `tools/conflict_check.py` and its tests): each increment was replayed onto the updated `main`, verified locally, published on its branch, and landed as a fast-forward once its own CI was green.
3. #68 and #69 had been marked merged by placeholder commits during one batch; the real increments were rebuilt from the branches' tree diffs and landed with the correction recorded on both pull requests.
4. #70 (CI trigger automation: `tools/ci_triggers.py`, its tests, the regenerated trigger list and the CI `triggers` job), #71 (pre-flight GPU probes) and #72 (record-card provenance) completed the stack. `main` now carries the whole stack: 356 core tests and 602 native GTK tests pass and every CI job is green.

What remains: physical acceptance on the owner's machine (`docs/NATIVE_ACCEPTANCE.md`) and the real-network transport gate. Neither is claimed here.

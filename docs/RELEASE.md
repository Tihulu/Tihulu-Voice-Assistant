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

The portable wheel alone is not an end-user install. A Debian package is
assembled from the built wheel with `packaging/deb/build.sh`, which needs no
network: it stages the wheel into `/usr/share/tihulu-jarvis/venv` with `pip
--no-index --find-links`, adds a wrapper at `/usr/bin/tihulu-jarvis`, installs
the desktop entry and icon into `/usr/share`, and records the runtime
dependencies (python3, PyGObject, GTK 4, Pango, cairo) in `control`. No
service, no root-owned data and no autostart entry are added.

```sh
sh packaging/deb/build.sh          # produces .release-work/tihulu-jarvis.deb
sudo dpkg -i .release-work/tihulu-jarvis.deb
sudo dpkg -r tihulu-jarvis
```

CI builds this package and verifies its contents: every shipped module and
metadata file is present, the file list carries no workstation path, credential
material or tests, and the package is extracted into an isolated root where the
package imports and the entry module load. Removal deletes only what the
package owns; the user's XDG configuration, profiles, market registry and
every shared model remain.

What a green package job does not prove: installation on a real desktop,
launch under COSMIC, physical audio, or clean-machine installation on a
different machine. Those remain separate gates and require a target system.

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
3. **A clean-machine install works.** `tools/smoke.py` runs from outside the
   source tree against the installed wheel and against the Debian root, so the
   artefact is exercised rather than only imported.
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

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

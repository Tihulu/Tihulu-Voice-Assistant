#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Owner-side, offline build preparation. Run as ordinary user, not sudo.
set -eu
umask 077
commit=d4b756351b1552aa5806c89e39eda3b5b82aa337
if [ "$#" -ne 2 ]; then
    echo "Usage: sh prepare-owner-preview.sh LOCAL_REPOSITORY BUILD_VENV" >&2
    exit 2
fi
if [ "$(id -u)" -eq 0 ]; then
    echo "HOLD: run as ordinary owner, not root" >&2
    exit 2
fi
source=$(cd "$1" && pwd -P)
builder=$(cd "$2" && pwd -P)
test -x "$builder/bin/python3" || { echo "HOLD: build venv missing python3" >&2; exit 2; }
test -f "$builder/pyvenv.cfg" || { echo "HOLD: existing build venv required" >&2; exit 2; }
test "$(git -C "$source" rev-parse --verify "$commit^{commit}")" = "$commit" ||
    { echo "HOLD: exact candidate not present locally" >&2; exit 2; }
command -v dpkg-deb >/dev/null || { echo "HOLD: dpkg-deb missing" >&2; exit 2; }
unset PYTHONPATH PYTHONHOME
export PATH="$builder/bin:$PATH"
export PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_CONFIG_FILE=/dev/null
export PYTHONDONTWRITEBYTECODE=1
# Do not install prerequisites or resolve them over the network.
python3 -m build --version >/dev/null || { echo "HOLD: build module missing" >&2; exit 2; }
python3 -m pip --version >/dev/null || { echo "HOLD: pip missing" >&2; exit 2; }
work=$(mktemp -d /tmp/jarvis-owner-build-XXXXXXXX)
chmod 0700 "$work"
printf 'Private build directory: %s\n' "$work"
# Local clone leaves the owner's original checkout and Git refs unchanged.
git clone --local --no-hardlinks --no-checkout -- "$source" "$work/source"
cd "$work/source"
git checkout --detach "$commit"
test "$(git rev-parse HEAD)" = "$commit"
test -z "$(git status --porcelain --untracked-files=all)"
python3 -m build --wheel --no-isolation --outdir "$work/wheels"
set -- "$work/wheels/"*.whl
test "$#" -eq 1 && test -f "$1"
# Debian control directory requires mode 0755; all staged files remain
# inside the enclosing owner-only 0700 mktemp directory.
(umask 022; sh packaging/deb/build.sh .release-work/deb "$work/wheels")
mkdir -m 0700 "$work/extracted"
dpkg-deb -x .release-work/tihulu-jarvis.deb "$work/extracted"
# Compare with the exact Git objects before any runtime import or launch.
TMPDIR="$work" python3 tools/owner_preview.py --root "$work/extracted" --commit "$commit" --prepare
printf '%s\n' 'Preparation complete. No app launch, system installation or physical PASS claimed.'

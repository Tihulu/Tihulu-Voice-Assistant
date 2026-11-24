#!/bin/sh
# Assemble a Debian package from the built wheel. No network access is used.
set -eu
root=$(cd "$(dirname "$0")/../.." && pwd)
work=${1:-"$root/.release-work/deb"}
wheels=${2:-"$root/.release-work/dist"}
if [ ! -d "$wheels" ]; then
    echo "no wheel directory: $wheels" >&2
    exit 2
fi
# Never delete or reuse a caller-supplied staging directory.
# mkdir (without -p for the leaf) also refuses an existing symlink.
mkdir -p -- "$(dirname -- "$work")"
if ! mkdir -m 0700 -- "$work"; then
    echo "staging directory must not exist; choose a fresh path" >&2
    exit 2
fi
mkdir -p "$work/DEBIAN" "$work/usr/share/tihulu-jarvis"
cp "$root/packaging/deb/control" "$work/DEBIAN/control"
cp "$root/packaging/deb/conffiles" "$work/DEBIAN/conffiles"
cp "$root/packaging/deb/postinst" "$work/DEBIAN/postinst"
cp "$root/packaging/deb/prerm" "$work/DEBIAN/prerm"
chmod 0755 "$work/DEBIAN/postinst" "$work/DEBIAN/prerm"
# Stage only the portable wheel, never a relocatable-in-name-only venv.
runtime="$work/usr/share/tihulu-jarvis/python"
mkdir -p "$runtime"
python3 -m pip install --no-index --no-deps --no-compile --target "$runtime" \
    --find-links "$wheels" tihulu-jarvis
# The wheel's generated entry point embeds the build interpreter; the Debian
# package has its own launcher. Desktop metadata is installed below.
rm -rf "$runtime/bin" "$runtime/share"
install -m 0644 "$root/packaging/deb/launch.py" "$work/usr/share/tihulu-jarvis/launch.py"
install -d "$work/usr/bin" "$work/usr/share/applications" "$work/usr/share/icons/hicolor/scalable/apps"
cat > "$work/usr/bin/tihulu-jarvis" <<'WRAP'
#!/bin/sh
set -eu
launcher=$(readlink -f -- "$0")
exec /usr/bin/python3 -I -B "$(dirname -- "$launcher")/../share/tihulu-jarvis/launch.py" "$@"
WRAP
chmod 0755 "$work/usr/bin/tihulu-jarvis"
install -m 0644 "$root/data/io.github.Tihulu.Jarvis.desktop" \
    "$work/usr/share/applications/io.github.Tihulu.Jarvis.desktop"
install -m 0644 "$root/data/io.github.Tihulu.Jarvis.svg" \
    "$work/usr/share/icons/hicolor/scalable/apps/io.github.Tihulu.Jarvis.svg"
dpkg-deb --root-owner-group --build "$work" "$root/.release-work/tihulu-jarvis.deb"

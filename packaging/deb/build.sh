#!/bin/sh
# Assemble a Debian package from the built wheel. No network access is used.
set -eu
root=$(cd "$(dirname "$0")/../.." && pwd)
work=${1:-"$root/.release-work/deb"}
rm -rf "$work"
mkdir -p "$work/DEBIAN" "$work/usr/share/tihulu-jarvis"
cp "$root/packaging/deb/control" "$work/DEBIAN/control"
cp "$root/packaging/deb/conffiles" "$work/DEBIAN/conffiles"
cp "$root/packaging/deb/postinst" "$work/DEBIAN/postinst"
cp "$root/packaging/deb/prerm" "$work/DEBIAN/prerm"
chmod 0755 "$work/DEBIAN/postinst" "$work/DEBIAN/prerm"
python3 -m venv "$work/.venv"
"$work/.venv/bin/pip" install -q --no-index --find-links "$root/.release-work/dist" tihulu-jarvis
"$work/.venv/bin/pip" uninstall -q -y pip setuptools 2>/dev/null || true
rm -rf "$work/.venv/lib/python"*/site-packages/*.dist-info/RECORD.jws || true
mv "$work/.venv" "$work/usr/share/tihulu-jarvis/venv"
install -d "$work/usr/bin" "$work/usr/share/applications" "$work/usr/share/icons/hicolor/scalable/apps"
cat > "$work/usr/bin/tihulu-jarvis" <<'WRAP'
#!/bin/sh
exec /usr/share/tihulu-jarvis/venv/bin/tihulu-jarvis "$@"
WRAP
chmod 0755 "$work/usr/bin/tihulu-jarvis"
install -m 0644 "$root/data/io.github.Tihulu.Jarvis.desktop" \
    "$work/usr/share/applications/io.github.Tihulu.Jarvis.desktop"
install -m 0644 "$root/data/io.github.Tihulu.Jarvis.svg" \
    "$work/usr/share/icons/hicolor/scalable/apps/io.github.Tihulu.Jarvis.svg"
# The wheel's own data-files land under the venv prefix; mirror them to /usr.
if [ -d "$work/usr/share/tihulu-jarvis/venv/share/applications" ]; then
    cp -a "$work/usr/share/tihulu-jarvis/venv/share/applications/." \
        "$work/usr/share/applications/" 2>/dev/null || true
fi
dpkg-deb --root-owner-group --build "$work" "$root/.release-work/tihulu-jarvis.deb"

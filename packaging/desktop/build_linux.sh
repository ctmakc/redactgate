#!/usr/bin/env bash
# Build Linux AppImage + .deb in Docker. Output: dist-desktop/linux/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/dist-desktop/linux"
VERSION="$(python3 -c "import re;print(re.search(r'__version__ = \"(.+?)\"', open('$ROOT/app/desktop/__init__.py').read())[1])")"
mkdir -p "$OUT"
docker build -q -t redactgate-desktop-linuxbuild -f "$ROOT/packaging/desktop/Dockerfile.linux" "$ROOT/packaging/desktop" >/dev/null
docker run --rm -e VERSION="$VERSION" -e APPIMAGE_EXTRACT_AND_RUN=1 -v "$ROOT":/src:ro -v "$OUT":/out redactgate-desktop-linuxbuild bash -c '
set -e
rm -rf /tmp/w && mkdir -p /tmp/w && cp -r /src/app /src/packaging /src/LICENSE /tmp/w/ && cd /tmp/w
pyinstaller packaging/desktop/redactgate-desktop.spec --noconfirm --clean > pyi.log 2>&1 || { tail -40 pyi.log; exit 1; }
B=/tmp/w/dist/redactgate-desktop
# ---- AppImage
A=/tmp/w/AppDir; mkdir -p $A/usr/lib $A/usr/share/applications $A/usr/share/icons/hicolor/256x256/apps
cp -r $B $A/usr/lib/redactgate-desktop
cp packaging/desktop/icons/icon-256.png $A/usr/share/icons/hicolor/256x256/apps/redactgate-desktop.png
cp packaging/desktop/icons/icon-256.png $A/redactgate-desktop.png
cat > $A/redactgate-desktop.desktop <<D
[Desktop Entry]
Type=Application
Name=RedactGate Desktop
Comment=Offline document anonymizer
Exec=redactgate-desktop
Icon=redactgate-desktop
Categories=Office;Utility;
Terminal=false
D
cp $A/redactgate-desktop.desktop $A/usr/share/applications/
cat > $A/AppRun <<R
#!/bin/sh
HERE="\$(dirname "\$(readlink -f "\$0")")"
exec "\$HERE/usr/lib/redactgate-desktop/redactgate-desktop" "\$@"
R
chmod +x $A/AppRun
ARCH=x86_64 appimagetool --no-appstream $A /out/RedactGate-Desktop-$VERSION-x86_64.AppImage > appimage.log 2>&1 || { tail -20 appimage.log; exit 1; }
# ---- .deb
D=/tmp/w/deb; mkdir -p $D/DEBIAN $D/opt $D/usr/bin $D/usr/share/applications $D/usr/share/icons/hicolor/256x256/apps
cp -r $B $D/opt/redactgate-desktop
ln -s /opt/redactgate-desktop/redactgate-desktop $D/usr/bin/redactgate-desktop
sed "s|^Exec=.*|Exec=/opt/redactgate-desktop/redactgate-desktop|" $A/redactgate-desktop.desktop > $D/usr/share/applications/redactgate-desktop.desktop
cp packaging/desktop/icons/icon-256.png $D/usr/share/icons/hicolor/256x256/apps/redactgate-desktop.png
SIZE=$(du -sk $D/opt | cut -f1)
cat > $D/DEBIAN/control <<C
Package: redactgate-desktop
Version: $VERSION
Section: utils
Priority: optional
Architecture: amd64
Installed-Size: $SIZE
Recommends: tesseract-ocr, tesseract-ocr-ukr, tesseract-ocr-rus, chromium | google-chrome-stable | microsoft-edge-stable
Maintainer: INNOVA CONSULT LTD <noreply@mmix.biz>
Homepage: https://github.com/ctmakc/redactgate
Description: RedactGate Desktop - offline document anonymizer
 Replaces people, companies, accounts, tax codes, addresses, phones and emails in
 PDF, DOCX, XLSX, CSV and TXT with reversible labels (Person-1, Company-1) and puts
 the real data back into AI answers. Works fully offline; the label dictionary is
 encrypted on this computer.
C
dpkg-deb --root-owner-group --build $D /out/redactgate-desktop_${VERSION}_amd64.deb > /dev/null
cp pyi.log /out/pyinstaller.log
chown -R '"$(id -u):$(id -g)"' /out
'
ls -la "$OUT"

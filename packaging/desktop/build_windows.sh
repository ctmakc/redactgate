#!/usr/bin/env bash
# Build the Windows installer under Wine in Docker. Output: dist-desktop/windows/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/dist-desktop/windows"
VERSION="$(python3 -c "import re;print(re.search(r'__version__ = \"(.+?)\"', open('$ROOT/app/desktop/__init__.py').read())[1])")"
mkdir -p "$OUT"
docker build -q -t redactgate-desktop-winbuild -f "$ROOT/packaging/desktop/Dockerfile.windows" "$ROOT/packaging/desktop" >/dev/null
docker run --rm -v "$ROOT":/src:ro -v "$OUT":/out redactgate-desktop-winbuild bash -lc "
  set -e
  rm -rf /tmp/w && mkdir -p /tmp/w && cp -r /src/app /src/packaging /src/LICENSE /tmp/w/
  cd /tmp/w
  wine pyinstaller packaging/desktop/redactgate-desktop.spec --noconfirm --clean --distpath Z:\\\\tmp\\\\w\\\\dist --workpath Z:\\\\tmp\\\\w\\\\build > /tmp/w/pyi.log 2>&1 || { tail -40 /tmp/w/pyi.log; exit 1; }
  cd packaging/desktop
  makensis -V2 -DVERSION=$VERSION -DSRCDIR=/tmp/w/dist/redactgate-desktop -DOUTDIR=/out -DLICENSEFILE=/tmp/w/LICENSE installer.nsi
  cp /tmp/w/pyi.log /out/pyinstaller.log
  chown -R $(id -u):$(id -g) /out
"
(cd "$OUT" && rm -rf portable && mkdir portable)
docker run --rm -v "$OUT":/out redactgate-desktop-winbuild true
ls -la "$OUT"

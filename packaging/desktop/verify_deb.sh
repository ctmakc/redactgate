#!/usr/bin/env bash
# Install the .deb into a clean Debian 12 container with NO network, run it, run the black-box e2e.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
docker run --rm --network none -v "$ROOT":/src:ro -v "$ROOT/dist-desktop/linux":/out redactgate-desktop-linuxbuild bash -c '
set -u
echo "== interfaces: $(ls /sys/class/net | tr "\n" " ")"
dpkg -i /out/redactgate-desktop_*_amd64.deb >/dev/null && echo "deb installed: $(dpkg -s redactgate-desktop | grep Version)"
ls -la /usr/bin/redactgate-desktop
redactgate-desktop --no-browser --port 18803 --idle-exit 0 --data-dir /tmp/rgd > /tmp/app.out 2>&1 &
for i in $(seq 1 30); do sleep 1; [ -f /tmp/rgd/instance.json ] && break; done
cd /tmp && cp -r /src/eval /src/app /tmp/ 2>/dev/null
python3 /tmp/eval/desktop/api_roundtrip.py --base http://127.0.0.1:18803 --data-dir /tmp/rgd --case /src/dist-desktop/verify-case --out /tmp/verify-deb 2>&1 | tail -3
mkdir -p /out/verify-deb && cp /tmp/verify-deb/summary.json /out/verify-deb/ && chown -R '"$(id -u):$(id -g)"' /out/verify-deb
'

#!/usr/bin/env bash
# Install the Windows build into a fresh Wine prefix with NO network, launch it, run the auditor demo
# round trip through the local API, and save evidence to dist-desktop/windows/verify/.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/dist-desktop/windows"
SETUP="$(ls "$OUT"/RedactGate-Desktop-Setup-*.exe | head -1)"
mkdir -p "$OUT/verify"
docker run --rm --network none -v "$ROOT":/src:ro -v "$OUT":/out redactgate-desktop-winbuild bash -lc '
set -u
exec > >(tee /out/verify/wine-run.log) 2>&1
echo "== network inside container:"; cat /proc/net/route | wc -l; ls /sys/class/net
echo "== silent install"; wine "/out/'"$(basename "$SETUP")"'" /S; sleep 3
APPDIR="$(winepath -u "C:\\users\\root\\AppData\\Local\\Programs\\RedactGate Desktop" 2>/dev/null)"
ls "$APPDIR" | head; test -f "$APPDIR/RedactGate Desktop.exe" || { echo "NOT INSTALLED"; exit 1; }
echo "== launch"
wine "$APPDIR/RedactGate Desktop.exe" --no-browser --port 18800 --idle-exit 0 --data-dir "C:\\rgdata" &
for i in $(seq 1 60); do sleep 2; curl -s -o /dev/null -H "Host: 127.0.0.1:18800" http://127.0.0.1:18800/ && break; done
DATA="$(winepath -u "C:\\rgdata")"; cat "$DATA/instance.json"; echo
TOK=$(sed -E "s/.*\"token\": \"([^\"]+)\".*/\1/" "$DATA/instance.json")
H=(-s -H "Host: 127.0.0.1:18800" -H "X-RG-Token: $TOK" -H "Content-Type: application/json")
echo "== GET / (UI)"; curl -s -o /dev/null -w "%{http_code} %{size_download}\n" -H "Host: 127.0.0.1:18800" http://127.0.0.1:18800/
echo "== state"; curl "${H[@]}" http://127.0.0.1:18800/api/state | head -c 300; echo
echo "== init vault"; curl "${H[@]}" -X POST -d "{\"password\":\"wine-test-1\"}" http://127.0.0.1:18800/api/vault/init; echo
echo "== demo"; curl "${H[@]}" -X POST http://127.0.0.1:18800/api/demo > /out/verify/demo.json; head -c 600 /out/verify/demo.json; echo
python_out=$(wine python -c "import json;d=json.load(open(r\"Z:\\out\\verify\\demo.json\",encoding=\"utf-8\"));print(len(d[\"results\"]),sum(1 for r in d[\"results\"] if \"error\" in r));open(r\"Z:\\out\\verify\\answer.json\",\"w\",encoding=\"utf-8\").write(json.dumps({\"text\":d[\"sample_answer\"]},ensure_ascii=False));open(r\"Z:\\out\\verify\\pid.txt\",\"w\").write(d[\"project_id\"])" 2>/dev/null); echo "files, errors: $python_out"
PID=$(cat /out/verify/pid.txt)
echo "== restore sample answer"; curl "${H[@]}" -X POST --data-binary @/out/verify/answer.json http://127.0.0.1:18800/api/projects/$PID/restore-text > /out/verify/restored.json; head -c 900 /out/verify/restored.json; echo
echo "== selfcheck"; curl "${H[@]}" http://127.0.0.1:18800/api/selfcheck; echo
echo "== download first anonymized file + round trip"
RID=$(sed -E "s/.*\"id\": \"([0-9a-f]{16})\".*/\1/;q" /out/verify/demo.json); echo rid=$RID
curl -s -H "Host: 127.0.0.1:18800" "http://127.0.0.1:18800/api/results/$RID?t=$TOK" -o /out/verify/first.anon.bin; ls -la /out/verify/first.anon.bin
echo "== black-box e2e (synthetic case, seed 99) with Windows Python against the Windows build"
wine python -X utf8 "Z:\\src\\eval\\desktop\\api_roundtrip.py" --base http://127.0.0.1:18800 --data-dir "C:\\rgdata" --case "Z:\\src\\dist-desktop\\verify-case" --out "Z:\\out\\verify\\rt" --password wine-test-1 2>/dev/null | tail -3
echo "== quit"; curl "${H[@]}" -X POST http://127.0.0.1:18800/api/quit; echo; sleep 3
echo "== app.log"; cat "$DATA/app.log" | tail -5
'

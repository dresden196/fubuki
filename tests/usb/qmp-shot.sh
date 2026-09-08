#!/usr/bin/env bash
# Take a PNG screenshot of a running QEMU over its QMP socket.
#   tests/usb/qmp-shot.sh out/qmp-x.sock out/shot.png
set -euo pipefail
python3 - "${1:?qmp socket}" "${2:?png}" <<'PY'
import json, socket, sys
sock, shot = sys.argv[1], sys.argv[2]
s = socket.socket(socket.AF_UNIX); s.connect(sock); f = s.makefile("rw")
def cmd(name, **args):
    f.write(json.dumps({"execute": name, "arguments": args} if args else {"execute": name}) + "\n"); f.flush()
    while True:
        r = json.loads(f.readline())
        if "return" in r or "error" in r: return r
f.readline(); cmd("qmp_capabilities")
r = cmd("screendump", filename=shot, format="png")
sys.exit(0 if "return" in r else 1)
PY

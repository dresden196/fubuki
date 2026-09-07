#!/usr/bin/env bash
# Boot the stick image written by the test VM, under BIOS or UEFI, and
# take a screenshot after a while. Exits 0 when qemu stayed up.
#
#   tests/usb/boot-stick.sh bios 40 out/shot.png
#   tests/usb/boot-stick.sh uefi 60 out/shot.png
#   SAKURA_STICK_PERSIST=1 ...   keep what the guest writes (to read its logs afterwards)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$REPO_ROOT/out"
MODE="${1:-bios}"
WAIT="${2:-40}"
SHOT="${3:-$OUT/stick-$MODE.png}"
STICK="${SAKURA_STICK:-$OUT/usb-stick.img}"
QMP="$OUT/qmp-stickboot.sock"
LOG="$OUT/console-stickboot-$MODE.log"
rm -f "$QMP" "$LOG"

SNAPSHOT=",snapshot=on"
[[ -n "${SAKURA_STICK_PERSIST:-}" ]] && SNAPSHOT=""
fw_args=()
if [[ "$MODE" == "uefi" ]]; then
    VARS="$OUT/OVMF_VARS-stickboot.fd"
    cp /usr/share/edk2/x64/OVMF_VARS.4m.fd "$VARS"
    fw_args=(-drive if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd
             -drive if=pflash,format=raw,unit=1,file="$VARS")
fi
qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 4 -m 4G \
    "${fw_args[@]}" \
    -drive file="$STICK",if=none,id=stick,format=raw,file.locking=off$SNAPSHOT \
    -device qemu-xhci,id=xhci -device usb-storage,bus=xhci.0,drive=stick,removable=on \
    -boot menu=off \
    -netdev user,id=net0 -device e1000,netdev=net0 \
    -qmp "unix:$QMP,server,nowait" \
    -serial "file:$LOG" \
    -display none -device VGA &
QPID=$!
trap 'kill $QPID 2>/dev/null || true' EXIT
sleep "$WAIT"
if ! kill -0 $QPID 2>/dev/null; then echo "qemu exited early" >&2; exit 1; fi
python3 - "$QMP" "$SHOT" <<'PY'
import json, socket, sys
sock, shot = sys.argv[1], sys.argv[2]
s = socket.socket(socket.AF_UNIX); s.connect(sock); f = s.makefile("rw")
def cmd(name, **args):
    f.write(json.dumps({"execute": name, "arguments": args} if args else {"execute": name}) + "\n"); f.flush()
    while True:
        r = json.loads(f.readline())
        if "return" in r or "error" in r: return r
f.readline(); cmd("qmp_capabilities")
print(cmd("screendump", filename=shot, format="png"))
PY
echo "screenshot: $SHOT"

#!/usr/bin/env bash
# Boot a written Windows stick against an empty SATA disk under OVMF and let
# it install: with the silent-install option the whole run is hands-off. No
# TPM is attached, so the media's bypass keys are what lets Setup proceed.
# Screenshots every 90 s into tests/usb/out/install-NN.png.
#
#   tests/usb/install-vm.sh out/native-silent.img 40    (minutes)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/out"
STICK="${1:?stick image}"; MINUTES="${2:-40}"
DISK="$OUT/install-target.qcow2"; VARS="$OUT/OVMF_VARS-install.fd"; QMP="$OUT/qmp-install.sock"
OVMF="${OVMF_DIR:-/usr/share/edk2/x64}"
rm -f "$DISK" "$QMP" "$OUT"/install-*.png; qemu-img create -f qcow2 "$DISK" 40G >/dev/null; cp "$OVMF/OVMF_VARS.4m.fd" "$VARS"
qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 4 -m "${FUBUKI_BOOT_MEM:-4G}" \
    -drive if=pflash,format=raw,unit=0,readonly=on,file="$OVMF/OVMF_CODE.4m.fd" \
    -drive if=pflash,format=raw,unit=1,file="$VARS" \
    -drive file="$DISK",if=none,id=hd0,format=qcow2 -device ide-hd,drive=hd0,bootindex=1 \
    -drive file="$STICK",if=none,id=stick,format=raw,file.locking=off -device qemu-xhci,id=xhci \
    -device usb-storage,bus=xhci.0,drive=stick,removable=on,bootindex=0 -device usb-tablet \
    -netdev user,id=net0 -device e1000,netdev=net0 -qmp "unix:$QMP,server,nowait" \
    -serial "file:$OUT/console-install.log" -display none -device VGA &
QPID=$!
trap 'kill $QPID 2>/dev/null || true' EXIT
for i in $(seq 1 $(( MINUTES * 60 / 90 ))); do
    sleep 90
    kill -0 $QPID 2>/dev/null || { echo "qemu exited"; break; }
    "$HERE/qmp-shot.sh" "$QMP" "$OUT/install-$(printf %02d $i).png" >/dev/null 2>&1 || true
done
echo "done after $MINUTES minutes"

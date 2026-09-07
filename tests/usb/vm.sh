#!/usr/bin/env bash
# Boot the SakuraOS live ISO in QEMU with an emulated USB stick, for testing
# sakura-usb end to end without touching a real drive.
#
# The guest sees: a removable USB mass-storage device backed by
# out/usb-stick.img (raw), the repository at /mnt/repo (9p, read-only), the
# ISOs directory at /mnt/isos (9p, read-only), and a scratch virtio disk for
# temporary files. It is driven over the guest agent, so build/guest-run.sh
# works with SAKURA_VM=usb.
#
#   tests/usb/vm.sh                 boot (headless), 32 GB stick
#   tests/usb/vm.sh --stick 8G      a smaller stick
#   tests/usb/vm.sh --reset         recreate the stick and scratch disk
#   tests/usb/vm.sh --gui           show a window
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$REPO_ROOT/out"
VM="${SAKURA_VM:-usb}"
ISOS_DIR="${SAKURA_ISOS:-$HOME/Downloads}"
STICK="$OUT/usb-stick.img"
SCRATCH="$OUT/usb-scratch.qcow2"
NVRAM="$OUT/OVMF_VARS-$VM.fd"
QMP_SOCK="$OUT/qmp-$VM.sock"
QGA_SOCK="$OUT/qga-$VM.sock"
OVMF_DIR=/usr/share/edk2/x64

stick_size=32G
reset=0
gui=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --stick) stick_size="$2"; shift ;;
        --reset) reset=1 ;;
        --gui) gui=1 ;;
        *) echo "unknown flag: $1" >&2; exit 1 ;;
    esac
    shift
done

ISO="${SAKURA_ISO:-$(ls -t "$OUT"/sakura-*.iso 2>/dev/null | head -1 || true)}"
[[ -n "$ISO" ]] || { echo "no SakuraOS ISO in out/" >&2; exit 1; }

mkdir -p "$OUT"
if (( reset )); then rm -f "$STICK" "$SCRATCH" "$NVRAM"; fi
[[ -f "$STICK" ]] || truncate -s "$stick_size" "$STICK"
[[ -f "$SCRATCH" ]] || qemu-img create -f qcow2 "$SCRATCH" 40G >/dev/null
[[ -f "$NVRAM" ]] || cp "$OVMF_DIR/OVMF_VARS.4m.fd" "$NVRAM"
rm -f "$QMP_SOCK" "$QGA_SOCK"

display_args=(-display none -device virtio-vga)
(( gui )) && display_args=(-display gtk,show-cursor=on -device virtio-vga)

echo ">> booting $(basename "$ISO") as \"$VM\" with a $stick_size USB stick ($STICK)"
exec qemu-system-x86_64 \
    -enable-kvm -machine q35 -cpu host -smp 8 -m 8G \
    -drive if=pflash,format=raw,unit=0,readonly=on,file="$OVMF_DIR/OVMF_CODE.4m.fd" \
    -drive if=pflash,format=raw,unit=1,file="$NVRAM" \
    -drive file="$ISO",media=cdrom,readonly=on -boot order=d \
    -drive file="$SCRATCH",if=virtio,format=qcow2 \
    -drive file="$STICK",if=none,id=stick,format=raw,cache=writeback \
    -device qemu-xhci,id=xhci \
    -device usb-storage,bus=xhci.0,drive=stick,removable=on,serial=SAKURATEST01 \
    -device usb-tablet \
    -virtfs local,path="$REPO_ROOT",mount_tag=repo,security_model=none,readonly=on \
    -virtfs local,path="$ISOS_DIR",mount_tag=isos,security_model=none,readonly=on \
    -netdev user,id=net0 -device virtio-net,netdev=net0 \
    -qmp "unix:$QMP_SOCK,server,nowait" \
    -chardev "socket,path=$QGA_SOCK,server=on,wait=off,id=qga0" \
    -device virtio-serial -device virtserialport,chardev=qga0,name=org.qemu.guest_agent.0 \
    -serial "file:$OUT/console-$VM.log" \
    "${display_args[@]}"

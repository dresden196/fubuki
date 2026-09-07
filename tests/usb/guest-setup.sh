#!/usr/bin/env bash
# Prepare the live guest started by tests/usb/vm.sh: mount the shares,
# install the engine's dependencies, give it scratch space.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export SAKURA_VM="${SAKURA_VM:-usb}"
RUN="$REPO_ROOT/build/guest-run.sh"

echo ">> waiting for the guest agent"
for _ in $(seq 1 120); do
    if "$RUN" 'true' >/dev/null 2>&1; then break; fi
    sleep 2
done
"$RUN" 'true' >/dev/null || { echo "guest agent never answered" >&2; exit 1; }

SAKURA_GUEST_TIMEOUT=600 "$RUN" '
set -e
mkdir -p /mnt/repo /mnt/isos /var/tmp/sakura
mountpoint -q /mnt/repo || mount -t 9p -o trans=virtio,version=9p2000.L,msize=262144 repo /mnt/repo
mountpoint -q /mnt/isos || mount -t 9p -o trans=virtio,version=9p2000.L,msize=262144 isos /mnt/isos
if ! mountpoint -q /var/tmp/sakura; then
    blkid /dev/vda >/dev/null 2>&1 || mkfs.ext4 -q -F /dev/vda
    mount /dev/vda /var/tmp/sakura
fi
missing=""
for p in wimlib hivex python-pyudev; do pacman -Q $p >/dev/null 2>&1 || missing="$missing $p"; done
if [ -n "$missing" ]; then
    # Everything needed is in the Arch repos. The live image also lists the
    # sakura-core repo, whose signing key may not be in the keyring yet this
    # early in the boot, and one unusable repo makes -Sy fail as a whole.
    sed "/\\[sakura-core\\]/,/^$/d" /etc/pacman.conf > /tmp/pacman-arch.conf
    for i in 1 2 3; do pacman --config /tmp/pacman-arch.conf -Sy --noconfirm --needed $missing && break; sleep 5; done
fi
lsblk -o NAME,SIZE,TRAN,RM,MODEL | grep -E "NAME|sd"
echo "guest ready"
'

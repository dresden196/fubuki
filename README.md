# Fubuki

Japanese: 桜吹雪, *sakura fubuki*: a blizzard of cherry petals. Fubuki is a bootable-USB
writer for Linux, doing what Rufus does on Windows: Windows install media on
NTFS with UEFI:NTFS, the Windows 11 hardware-check bypass and answer files,
Windows To Go, Linux ISOs with persistence, FreeDOS, and plain DD writes with
verification. It is a KDE application (Qt 6, Kirigami) with a command line
engine that works on its own.

- `fubuki/` — the engine (Python) and its package. See its README and `PROTOCOL.md`.
- `fubuki-ui/` — the window.
- `tests/usb/` — the QEMU bench: `native.sh` writes a loop-backed image and
  boots it under BIOS and UEFI; `vm.sh` does the same inside a live guest with
  an emulated USB stick.

Verified this way, by screenshot: Ubuntu ISO mode with persistence (BIOS and
UEFI), Ubuntu DD with verify, Windows 11 on NTFS via UEFI:NTFS and on FAT32
with a split install.wim (BIOS and UEFI), Windows To Go (BIOS and UEFI, to
OOBE), an archiso-based live ISO through Syslinux (BIOS and UEFI), FreeDOS.

Both are packaged for Arch Linux (`makepkg` in each directory). Fubuki ships
with SakuraOS but does not depend on it.

Design and boot payloads derive from [Rufus](https://github.com/pbatard/rufus)
by Pete Batard, GPLv3. This project is GPL-3.0-or-later.

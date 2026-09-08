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
with a split install.wim (BIOS and UEFI), a silent Windows 11 install carried
through to the desktop on a machine with no TPM, Windows To Go on MBR (BIOS
and UEFI) and on GPT with an ESP (UEFI), an archiso-based live ISO through
Syslinux (BIOS and UEFI), FreeDOS, KolibriOS (to its desktop), a Grub4DOS
stick (to the grub> prompt). ReactOS 0.4.15's BootCD boots FreeLoader and the
kernel through Syslinux's mboot chain, the same way Rufus does, and then stops
with 0x7B: that image cannot mount a disk partition as its boot device, on USB
or SATA alike. On a physical 64 GB stick handed to QEMU
over USB passthrough (`tests/usb/boot-real.sh`): Windows 11 on NTFS boots
under UEFI and under enforcing Secure Boot with Microsoft's certificates
enrolled; an archiso live image written in ISO mode boots under BIOS
(Syslinux) and UEFI. `tests/usb/test-cancel.sh` covers cancel
mid-write, running out of temporary space, and refused devices.

Both are packaged for Arch Linux (`makepkg` in each directory). Fubuki ships
with SakuraOS but does not depend on it.

Design and boot payloads derive from [Rufus](https://github.com/pbatard/rufus)
by Pete Batard, GPLv3. This project is GPL-3.0-or-later.

# Sakura USB Writer

A bootable-USB writer for SakuraOS, doing on Linux what Rufus does on
Windows: Windows install media on NTFS with UEFI:NTFS, the Windows 11
hardware-check bypass and answer files, Windows To Go, Linux ISOs with
persistence, FreeDOS, and plain DD writes with verification.

- `sakura-usb/` — the engine (Python) and its package. See its README and `PROTOCOL.md`.
- `sakura-usb-ui/` — the Qt/QML window (KDE Plasma).
- `tests/usb/` — the QEMU bench: a live guest with an emulated USB stick, and
  BIOS/UEFI boot checks of what was written.

Design and boot payloads derive from [Rufus](https://github.com/pbatard/rufus)
by Pete Batard, GPLv3. This project is GPL-3.0-or-later.

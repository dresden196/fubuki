# fubuki (engine)

Makes bootable USB drives from ISO and disk images, the way Rufus does on
Windows. Works on its own from the command line; `fubuki-ui` is the window.

    fubuki devices                       # drives it is willing to write
    fubuki probe some.iso                # what the image is and how it boots
    sudo fubuki write -d /dev/sdb -i some.iso            # defaults from the probe
    sudo fubuki write -d /dev/sdb -i win11.iso --scheme gpt --target uefi --fs ntfs \
         --windows-option bypass_requirements --windows-option no_online_account
    sudo fubuki write -d /dev/sdb -i ubuntu.iso --persistence 4G
    sudo fubuki write -d /dev/sdb -i win11.iso --wintogo 1 --scheme gpt --target uefi --fs ntfs
    sudo fubuki write -d /dev/sdb -i image.img.xz --mode dd --verify
    sudo fubuki write -d /dev/sdb --boot-type freedos

The window (`fubuki-ui`) runs `fubuki serve` under pkexec and talks
the protocol in `PROTOCOL.md`.

## What it does that a plain `dd` cannot

- Windows install media on NTFS or FAT32, booting on UEFI through the
  UEFI:NTFS loader and on BIOS through Microsoft's own boot code.
- Windows 11 hardware-check bypass written straight into `boot.wim`'s
  registry (hivex + wimlib), the in-place-upgrade wrapper for 24H2, answer
  files for local account / no online account / no telemetry / no BitLocker.
- `install.wim` over 4 GB split for FAT32.
- Windows To Go: `install.wim` applied to NTFS, boot files and a BCD built
  from the media, internal drives set offline.
- Linux ISOs copied as files with config patches (labels, persistence,
  Red Hat `inst.repo`), BIOS boot through GRUB or Syslinux, persistent
  partition for Ubuntu and Debian live media.
- Raw images, fixed VHD, and gzip/xz/bzip2/zstd compressed images, with
  optional read-back verification.

## Layout

| module | job |
|---|---|
| `iso9660.py`, `udf.py` | read ISO images without mounting them (UDF for Windows media) |
| `image.py` | the probe: what boots how, Windows version and editions, recommended settings |
| `layout.py` | partition plan and `sfdisk` |
| `bootrec.py`, `bootcode.py` | MBR / volume boot records (byte arrays ported from ms-sys) |
| `fs.py`, `mountctl.py` | mkfs, labels, mounting, keeping udisks away |
| `extract.py` | file copy with the config fixes |
| `linux.py` | Syslinux, GRUB, persistence, FreeDOS |
| `unattend.py`, `windows.py`, `wim.py` | Windows customization and Windows To Go |
| `writer.py` | DD mode |
| `job.py` | the sequence |
| `cli.py` | commands and the `serve` protocol |

`payload/` holds `uefi-ntfs.img`, FreeDOS and the setup wrapper, taken
from Rufus (GPLv3, Pete Batard). `tools/port-ms-sys.py` regenerates
`bootcode.py` from a Rufus checkout.

## Testing

`tests/usb/native.sh` writes a loop-backed disk image on the host and boots
it under BIOS and UEFI in QEMU; that is the quick check. `tests/usb/vm.sh`
boots an Arch-based live ISO in QEMU with an emulated
USB stick and runs the engine inside it, then boots the result under BIOS
and UEFI. See `tests/usb/run.sh`.

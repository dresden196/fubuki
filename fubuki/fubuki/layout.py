"""Partition layout: the rules from Rufus's CreatePartition(), applied with sfdisk.

The order and sizes are not arbitrary. A UEFI:NTFS partition must be a plain
data partition on GPT, because Windows Setup refuses to install when the
media carries a second ESP. An ESP for Windows To Go goes first because
Microsoft says so and some firmware agrees. Everything is aligned to 1 MB
unless the user asked for old-BIOS fixes, which aligns to a fake track.
"""

import os
import random
import subprocess
import time
import uuid

from .util import MB, KB, align_up, align_down, run, UsbError

from .i18n import _

# GPT type GUIDs
GPT_MS_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
GPT_ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
GPT_MSR = "E3C9E316-0B5C-4DB8-817D-F92DF00215AE"
GPT_LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
# Bit 63 of the GPT attributes: "no drive letter" for Windows.
GPT_ATTR_NO_DRIVE_LETTER = 63

# MBR partition types
MBR_FAT16_LBA = 0x0E
MBR_FAT32_LBA = 0x0C
MBR_NTFS = 0x07          # also exFAT and UDF
MBR_LINUX = 0x83
MBR_ESP = 0xEF
MBR_EXTRA_COMPAT = 0xEA  # Rufus's "BIOS Compatibility" filler partition

# Rufus writes this MBR disk id when the drive was made MBR+UEFI, so the
# scheme can be recognised again later.
MBR_UEFI_MARKER = 0x49464555  # "UEFI"

ESP_SIZE = 260 * MB
MSR_SIZE = 128 * MB
MAX_PARTITIONS = 16


class Partition:
    def __init__(self, name, role):
        self.name = name        # GPT name / log label
        self.role = role        # main | esp | msr | uefi_ntfs | persistence | compat
        self.offset = 0
        self.size = 0
        self.mbr_type = 0
        self.gpt_type = GPT_MS_DATA
        self.bootable = False
        self.attrs = []
        self.device = None      # /dev/sdX<n> once created
        self.number = 0

    def __repr__(self):
        return f"<{self.name} @{self.offset} +{self.size}>"


def plan(disk_size, sector_size, scheme, fs, bootable=True, extras=(), persistence_size=0,
         old_bios_fixes=False, cluster_size=0, uefi_ntfs_size=1 * MB, write_as_esp=False):
    """Return a list of Partition in disk order. `extras` is a set of roles
    among {'uefi_ntfs','esp','msr','persistence','compat'}."""
    extras = set(extras)
    if scheme not in ("mbr", "gpt"):
        raise UsbError(_("unknown partition scheme %s") % scheme)
    cluster = cluster_size or 512
    # Linux has no real geometry for USB; BIOSes assume 63 sectors/track.
    bytes_per_track = 63 * sector_size
    parts = []

    if scheme == "gpt" or not old_bios_fixes:
        first = 1 * MB
    else:
        # Align to a cylinder that is itself aligned to the cluster, doubled so
        # GRUB2's core.img still fits in the gap (Rufus does the same).
        first = align_up(bytes_per_track, cluster) * 2

    offset = first
    if "esp" in extras and scheme == "gpt":
        p = Partition("EFI System Partition", "esp")
        p.offset, p.size = offset, ESP_SIZE
        p.gpt_type = GPT_ESP
        parts.append(p)
        offset = align_up(offset + p.size, bytes_per_track)
        if cluster % sector_size == 0:
            offset = align_down(offset, cluster)
        extras.discard("esp")
    if "msr" in extras:
        if scheme != "gpt":
            raise UsbError(_("an MSR partition needs GPT"))
        p = Partition("Microsoft Reserved Partition", "msr")
        p.offset, p.size = offset, MSR_SIZE
        p.gpt_type = GPT_MSR
        parts.append(p)
        offset = align_up(offset + p.size, bytes_per_track)
        if cluster % sector_size == 0:
            offset = align_down(offset, cluster)
        extras.discard("msr")

    main = Partition("EFI System Partition" if write_as_esp else "Main Data Partition", "main")
    main.offset = offset
    main.bootable = bootable
    parts.append(main)
    tail = []
    if "persistence" in extras:
        if persistence_size <= 0:
            raise UsbError(_("persistence requested with no size"))
        p = Partition("Linux Persistence", "persistence")
        p.size = align_up(persistence_size, bytes_per_track)
        p.gpt_type = GPT_LINUX
        p.mbr_type = MBR_LINUX
        tail.append(p)
    if "esp" in extras:
        p = Partition("EFI System Partition", "esp")
        p.size = align_up(ESP_SIZE, bytes_per_track)
        p.gpt_type = GPT_ESP
        p.mbr_type = MBR_ESP
        tail.append(p)
    elif "uefi_ntfs" in extras:
        p = Partition("UEFI:NTFS", "uefi_ntfs")
        p.size = align_up(uefi_ntfs_size, bytes_per_track)
        # Deliberately a data partition on GPT (see module docstring) and
        # hidden from Windows.
        p.gpt_type = GPT_MS_DATA
        p.attrs = [GPT_ATTR_NO_DRIVE_LETTER]
        p.mbr_type = MBR_ESP
        tail.append(p)
    elif "compat" in extras:
        p = Partition("BIOS Compatibility", "compat")
        p.size = bytes_per_track
        p.mbr_type = MBR_EXTRA_COMPAT
        tail.append(p)
    parts.extend(tail)
    if len(parts) > MAX_PARTITIONS:
        raise UsbError(_("too many partitions"))

    # Extra partitions are packed at the end of the disk, track-aligned.
    last = disk_size
    if scheme == "gpt":
        last -= 33 * sector_size
    for p in reversed(tail):
        if p.size >= last:
            raise UsbError(_("%s does not fit on this drive") % p.name)
        p.offset = align_down(last - p.size, bytes_per_track)
        last = p.offset
    if last <= main.offset:
        raise UsbError(_("drive is too small for this layout"))
    main.size = align_down(last - main.offset, bytes_per_track)
    if cluster % sector_size == 0:
        main.size = align_down(main.size, cluster)
    if main.size <= 0:
        raise UsbError(_("drive is too small for this layout"))

    main.mbr_type = {
        "fat16": MBR_FAT16_LBA, "fat32": MBR_FAT32_LBA,
        "ntfs": MBR_NTFS, "exfat": MBR_NTFS, "udf": MBR_NTFS,
        "ext2": MBR_LINUX, "ext3": MBR_LINUX, "ext4": MBR_LINUX,
    }.get(fs)
    if main.mbr_type is None:
        raise UsbError(_("unsupported file system %s") % fs)
    if write_as_esp:
        main.mbr_type = MBR_ESP
        main.gpt_type = GPT_ESP
    return parts


def sfdisk_script(parts, scheme, sector_size, mbr_uefi_marker=False, disk_id=None):
    lines = [f"label: {'dos' if scheme == 'mbr' else 'gpt'}", "unit: sectors"]
    if scheme == "mbr":
        sig = MBR_UEFI_MARKER if mbr_uefi_marker else (disk_id or (int(time.time() * 1000) & 0xFFFFFFFF) or 1)
        lines.append(f"label-id: 0x{sig:08x}")
    else:
        lines.append(f"label-id: {disk_id or str(uuid.uuid4()).upper()}")
        lines.append("first-lba: 34")
    for p in parts:
        start = p.offset // sector_size
        size = p.size // sector_size
        if scheme == "mbr":
            fields = [f"start={start}", f"size={size}", f"type={p.mbr_type:02x}"]
            if p.bootable:
                fields.append("bootable")
        else:
            fields = [f"start={start}", f"size={size}", f"type={p.gpt_type}",
                      f'name="{p.name}"', f"uuid={str(uuid.uuid4()).upper()}"]
            if p.attrs:
                fields.append("attrs=\"" + " ".join(f"GUID:{a}" for a in p.attrs) + "\"")
        lines.append(", ".join(fields))
    return "\n".join(lines) + "\n"


def clear_partition_starts(dev, parts, sector_size):
    """Zero the first sectors of each partition so stale superblocks can't
    confuse the kernel, udev, or mkfs."""
    zero = bytes(min(128 * KB, sector_size * 256))
    fd = os.open(dev, os.O_WRONLY)
    try:
        for p in parts:
            n = min(len(zero), p.size)
            os.pwrite(fd, zero[:n], p.offset)
        os.fsync(fd)
    finally:
        os.close(fd)


def wipe_disk_signatures(dev, disk_size, sector_size, log=None):
    """Rufus's ClearMBRGPT: zero the first and last MB, MBR and both GPTs."""
    fd = os.open(dev, os.O_WRONLY)
    try:
        zero = bytes(1 * MB)
        os.pwrite(fd, zero, 0)
        os.pwrite(fd, zero[:min(1 * MB, disk_size)], max(0, disk_size - 1 * MB))
        os.fsync(fd)
    finally:
        os.close(fd)
    subprocess.run(["wipefs", "-a", "-q", dev], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply(dev, parts, scheme, sector_size, mbr_uefi_marker=False, log=None):
    """Write the partition table and wait for the kernel to see the nodes."""
    script = sfdisk_script(parts, scheme, sector_size, mbr_uefi_marker)
    if log:
        for line in script.rstrip().splitlines():
            log("  sfdisk: " + line)
    # 255 heads / 63 sectors is what every BIOS assumes and what Windows
    # writes; the kernel's fake geometry for a USB stick (64/32) makes the
    # CHS fields in the table disagree with the BIOS, and NT-era boot
    # sectors, which read by CHS, then read the wrong sectors and hang.
    run(["sfdisk", "-q", "-w", "always", "-W", "always", dev], input=script, log=None)
    if scheme == "mbr":
        from .bootrec import fix_mbr_chs
        fix_mbr_chs(dev, sector_size, log=log)
    reread(dev)
    # Resolve /dev nodes for each partition.
    for i, p in enumerate(parts, 1):
        p.number = i
        p.device = partition_device(dev, i)
    for p in parts:
        for _try in range(50):
            if os.path.exists(p.device):
                break
            time.sleep(0.1)
        else:
            raise UsbError(_("%s did not appear after partitioning") % p.device)
    return parts


def reread(dev):
    subprocess.run(["partprobe", dev], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["blockdev", "--rereadpt", dev], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["udevadm", "settle", "--timeout=10"], check=False)


def partition_device(dev, number):
    base = os.path.basename(os.path.realpath(dev))
    sep = "p" if base[-1].isdigit() else ""
    return f"/dev/{base}{sep}{number}"


def read_layout(dev):
    """Current table as sfdisk sees it (for logging 'what was there before')."""
    r = run(["sfdisk", "-J", dev], check=False)
    if r.returncode != 0:
        return None
    import json
    try:
        return json.loads(r.stdout).get("partitiontable")
    except ValueError:
        return None

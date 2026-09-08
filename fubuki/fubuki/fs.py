"""Formatting partitions and validating labels."""

import os
import re
import subprocess

from .util import run, UsbError, human_size, MB

FS_LABEL_MAX = {"fat16": 11, "fat32": 11, "exfat": 11, "ntfs": 32,
                "ext2": 16, "ext3": 16, "ext4": 16, "udf": 126}


def valid_label(label, fs, disk_size=0):
    """Rufus's ToValidLabel: strip what the file system refuses, upper-case
    FAT labels, and fall back to a size label if what remains is mostly
    underscores."""
    fat = fs in ("fat16", "fat32", "exfat")
    unauthorized = set('*?,;:/\\|+=<>[]"')
    out = []
    for ch in label:
        if fat:
            if ch in unauthorized:
                continue
            if ord(ch) >= 0x80:
                out.append("_")
                continue
        if ch in "\t.":
            out.append("_")
            continue
        out.append(ch.upper() if fat else ch)
    result = "".join(out)
    if fat:
        result = result[:11]
        if result and len(result) < 2 * result.count("_"):
            result = human_size(disk_size, binary=False).replace(".", "_")[:11].upper()
    else:
        result = result[:FS_LABEL_MAX.get(fs, 32)]
    return result


def zero_partition(part_dev, size, emitter=None, cancel=None):
    """Full (non-quick) format: overwrite the whole partition with zeros first."""
    chunk = bytes(8 * MB)
    done = 0
    fd = os.open(part_dev, os.O_WRONLY)
    try:
        while done < size:
            if cancel:
                cancel.check()
            n = min(len(chunk), size - done)
            os.write(fd, chunk[:n])
            done += n
            if emitter:
                emitter.progress("format", done / size)
        os.fsync(fd)
    finally:
        os.close(fd)


def mkfs(part_dev, fs, label="", cluster_size=0, quick=True, sector_size=512,
         size=0, emitter=None, cancel=None, log=None, compression=False):
    """Create the file system. `cluster_size` in bytes, 0 for the default."""
    if not quick and size:
        if log:
            log(f"Zeroing {human_size(size)} before formatting (full format)")
        zero_partition(part_dev, size, emitter, cancel)
    if emitter:
        emitter.progress("format", None)
    if fs in ("fat32", "fat16"):
        cmd = ["mkfs.fat", "-F", "32" if fs == "fat32" else "16", "-I"]
        if label:
            cmd += ["-n", label]
        if cluster_size:
            cmd += ["-s", str(max(1, cluster_size // sector_size))]
        if sector_size != 512:
            cmd += ["-S", str(sector_size)]
        cmd.append(part_dev)
    elif fs == "ntfs":
        # mkntfs -f is the quick format; without it mkntfs zeroes the volume,
        # which zero_partition() already did when a full format was asked for.
        cmd = ["mkntfs", "-F", "-q", "-f"]
        if label:
            cmd += ["-L", label]
        if cluster_size:
            cmd += ["-c", str(cluster_size)]
        if compression:
            cmd.append("-C")
        cmd.append(part_dev)
    elif fs == "exfat":
        cmd = ["mkfs.exfat"]
        if label:
            cmd += ["-L", label]
        if cluster_size:
            cmd += ["-c", str(cluster_size)]
        cmd.append(part_dev)
    elif fs in ("ext2", "ext3", "ext4"):
        cmd = [f"mkfs.{fs}", "-F", "-q"]
        if label:
            cmd += ["-L", label]
        if cluster_size and cluster_size in (1024, 2048, 4096):
            cmd += ["-b", str(cluster_size)]
        # Older live kernels (Debian persistence) choke on metadata_csum_seed
        # and orphan_file; leave the flashy features off, this is a USB stick.
        if fs == "ext4":
            cmd += ["-O", "^metadata_csum_seed,^orphan_file,^64bit"]
        cmd.append(part_dev)
    else:
        raise UsbError(f"unsupported file system {fs}")
    run(cmd, log=log, cancel=cancel)
    subprocess.run(["udevadm", "settle", "--timeout=10"], check=False)
    if log:
        log(f"Formatted {part_dev} as {fs.upper()}" + (f" '{label}'" if label else ""))


def read_label(part_dev):
    r = run(["blkid", "-o", "value", "-s", "LABEL", part_dev], check=False)
    return r.stdout.strip()


def set_label(part_dev, fs, label):
    """Change the label of an existing volume (used for the UEFI:NTFS partition)."""
    if fs in ("fat32", "fat16"):
        run(["fatlabel", part_dev, label], check=False)
    elif fs == "ntfs":
        run(["ntfslabel", part_dev, label], check=False)
    elif fs == "exfat":
        run(["exfatlabel", part_dev, label], check=False)
    elif fs.startswith("ext"):
        run(["e2label", part_dev, label], check=False)


def default_cluster_sizes(fs, volume_size):
    """The list Rufus offers, with the default first. Values in bytes."""
    if fs in ("fat32", "fat16"):
        if fs == "fat32":
            if volume_size <= 8 * 1024 * MB:
                default = 4096
            elif volume_size <= 16 * 1024 * MB:
                default = 8192
            elif volume_size <= 32 * 1024 * MB:
                default = 16384
            else:
                default = 32768
        else:
            default = 32768 if volume_size > 1024 * MB else 16384
        choices = [512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]
    elif fs == "ntfs":
        default = 4096
        choices = [512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]
    elif fs == "exfat":
        default = 32768 if volume_size <= 32 * 1024 * MB else 131072
        choices = [4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576]
    else:
        default = 4096
        choices = [1024, 2048, 4096]
    return default, choices

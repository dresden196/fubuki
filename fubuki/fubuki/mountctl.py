"""Mounting the target and keeping the desktop's hands off it while we work.

The moment a new partition table lands, udisks (and so Plasma) may mount
the fresh volume and start writing .Trash and thumbnail folders to it. A
temporary udev rule marks the drive UDISKS_IGNORE for the duration.
"""

import os
import shutil
import subprocess
import time

from .util import run, UsbError

from .i18n import _

RULES_DIR = "/run/udev/rules.d"
MOUNT_BASE = "/run/fubuki"


def _reload_udev(dev):
    subprocess.run(["udevadm", "control", "--reload"], check=False)
    subprocess.run(["udevadm", "trigger", "--action=change", "--subsystem-match=block", "--sysname-match", os.path.basename(dev) + "*"],
                   check=False)
    subprocess.run(["udevadm", "settle", "--timeout=10"], check=False)


class Inhibit:
    """Context manager: hide the drive from udisks while we write it."""

    def __init__(self, dev):
        self.dev = dev
        name = os.path.basename(os.path.realpath(dev))
        self.rule = os.path.join(RULES_DIR, f"89-fubuki-{name}.rules")
        self.name = name

    def __enter__(self):
        try:
            os.makedirs(RULES_DIR, exist_ok=True)
            with open(self.rule, "w") as f:
                f.write(f'KERNEL=="{self.name}*", ENV{{UDISKS_IGNORE}}="1", ENV{{UDISKS_AUTO}}="0", ENV{{UDISKS_PRESENTATION_HIDE}}="1"\n')
            _reload_udev(self.dev)
        except OSError:
            pass
        return self

    def __exit__(self, *a):
        try:
            os.remove(self.rule)
        except OSError:
            pass
        _reload_udev(self.dev)


def unmount_all(dev, log=None):
    """Unmount every partition (and the whole disk) of dev. Lazy unmount as
    a last resort: a file manager sitting in the folder must not stop us."""
    name = os.path.basename(os.path.realpath(dev))
    targets = set()
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                parts = line.split()
                dash = parts.index("-")
                src = parts[dash + 2]
                if src.startswith("/dev/") and os.path.basename(os.path.realpath(src)).startswith(name):
                    targets.add((src, parts[4].replace("\\040", " ")))
    except (OSError, ValueError):
        pass
    for src, mp in sorted(targets, key=lambda t: -len(t[1])):
        if log:
            log(f"Unmounting {src} from {mp}")
        r = subprocess.run(["umount", mp], capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(["umount", "-l", mp], capture_output=True, text=True)
    # swap?
    try:
        with open("/proc/swaps") as f:
            for line in f.readlines()[1:]:
                s = line.split()[0]
                if os.path.basename(os.path.realpath(s)).startswith(name):
                    raise UsbError(_("%s is active swap; deactivate it first") % s)
    except OSError:
        pass


def open_exclusive(dev):
    """O_EXCL on the whole disk: fails if anything (a mount, another
    writer) holds it, which is exactly the check we want."""
    try:
        return os.open(dev, os.O_RDWR | os.O_EXCL)
    except OSError as e:
        raise UsbError(_("%s is in use (%s); close anything using it and try again") % (dev, e.strerror))


def mount_dir_for(dev, tag="main"):
    d = os.path.join(MOUNT_BASE, os.path.basename(os.path.realpath(dev)) + "-" + tag)
    os.makedirs(d, exist_ok=True)
    return d


def mount(part_dev, fs, mount_dir, log=None):
    """Mount a freshly made file system read-write for file copying."""
    os.makedirs(mount_dir, exist_ok=True)
    attempts = []
    if fs in ("fat16", "fat32"):
        # No `flush`: it syncs after every close, and an XP tree is thousands
        # of small files; the job syncs the whole drive before unmounting.
        attempts.append(["mount", "-t", "vfat", "-o", "rw,umask=000,shortname=mixed,utf8=1", part_dev, mount_dir])
    elif fs == "exfat":
        attempts.append(["mount", "-t", "exfat", "-o", "rw,umask=000", part_dev, mount_dir])
    elif fs == "ntfs":
        attempts.append(["mount", "-t", "ntfs3", "-o", "rw,windows_names,force", part_dev, mount_dir])
        attempts.append(["ntfs-3g", "-o", "rw,windows_names,big_writes", part_dev, mount_dir])
        attempts.append(["mount", "-t", "ntfs-3g", "-o", "rw,windows_names", part_dev, mount_dir])
    elif fs.startswith("ext"):
        attempts.append(["mount", "-t", fs, "-o", "rw", part_dev, mount_dir])
    else:
        attempts.append(["mount", part_dev, mount_dir])
    errors = []
    for cmd in attempts:
        if not shutil.which(cmd[0]):
            continue
        r = run(cmd, check=False, log=log)
        if r.returncode == 0:
            return
        errors.append((r.stderr or r.stdout).strip())
    raise UsbError(_("could not mount %s (%s): %s") % (part_dev, fs, errors[-1] if errors else _("no mount helper")))


def unmount(mount_dir, log=None):
    os.sync()
    for _ in range(10):
        r = subprocess.run(["umount", mount_dir], capture_output=True, text=True)
        if r.returncode == 0:
            break
        time.sleep(0.5)
    else:
        subprocess.run(["umount", "-l", mount_dir], capture_output=True, text=True)
    try:
        os.rmdir(mount_dir)
    except OSError:
        pass

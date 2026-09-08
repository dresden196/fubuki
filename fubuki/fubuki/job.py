"""The write job: Rufus's FormatThread() in the order that works.

Nothing here is clever. The value is in the order and in the conditions,
each of which exists because some firmware or some installer needs it.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time

from . import bootrec, devices, extract, fs as fsmod, image, layout, linux, mountctl, unattend as ua, windows, wim as wimmod, writer
from .udf import open_image
from .util import (UsbError, Cancelled, human_size, payload_path, sync_device, GB, MB, KB, write_at, read_at)

BOOT_TYPES = ("image", "none", "freedos", "syslinux", "grub2", "uefi_ntfs")
FS_TYPES = ("fat16", "fat32", "ntfs", "exfat", "ext2", "ext3", "ext4")

# Text for the "you booted UEFI-only media in BIOS mode"
# message MBR. Colour codes as in Rufus's msg.S: \0N sets the attribute.
PROTECTIVE_MESSAGE = (
    "\x07             \x70\xc9" + "\xcd" * 48 + "\xbb \x07\r\n"
    "             \x70\xba" + " " * 48 + "\xba \x07\r\n"
    "             \x70\xba   \x74ERROR: BIOS/LEGACY BOOT OF UEFI-ONLY MEDIA\x70   \xba \x07\r\n"
    "             \x70\xba" + " " * 48 + "\xba \x07\r\n"
    "             \x70\xc8" + "\xcd" * 48 + "\xbc \x07\r\n\r\n"
    "             This drive was created by Fubuki.\r\n\r\n"
    "             It can boot in \x04UEFI mode only\x07 but you are trying to\r\n"
    "             boot it in BIOS/Legacy mode. THIS WILL NOT WORK!\r\n\r\n"
    "             To remove this message you need to do \x02ONE\x07 of the following:\r\n"
    "             o If this computer supports UEFI, go to your UEFI settings\r\n"
    "               and lower or disable the priority of \x09CSM/Legacy mode\x07.\r\n"
    "             o \x02OR\x07 Recreate the drive and use:\r\n"
    "               * \x09Partition scheme\x07 -> \x09MBR\x07.\r\n"
    "               * \x09Target system\x07 -> \x09BIOS (...)\x07\r\n"
    "             o \x02OR\x07 Erase the whole drive by selecting:\r\n"
    "               * \x09Boot Type\x07 -> \x09Non bootable\x07\r\n\r\n"
    "             Note: You may also see this message if you installed a new\r\n"
    "             OS and your computer is unable to boot that OS in UEFI mode.\r\n"
)


class Progress:
    """Maps per-phase progress onto one overall bar."""

    def __init__(self, emitter):
        self.emitter = emitter
        self.ranges = {}
        self.current = None

    def plan(self, phases):
        """phases: list of (name, weight)."""
        total = sum(w for _, w in phases) or 1
        pos = 0.0
        self.ranges = {}
        for name, w in phases:
            self.ranges[name] = (pos / total, (pos + w) / total)
            pos += w

    def phase(self, name, status=None):
        self.current = name
        if status:
            self.emitter.status(status)
        self.update(None)

    def update(self, value, message=None):
        lo, hi = self.ranges.get(self.current, (0.0, 1.0))
        overall = None if value is None else lo + (hi - lo) * max(0.0, min(1.0, value))
        self.emitter.progress(self.current or "work", value, message)
        if overall is not None:
            self.emitter.event(event="overall", value=overall)

    # emitter-compatible shim for modules that take an `emitter`
    def progress(self, phase, value, message=None):
        self.update(value, message)

    def status(self, text):
        self.emitter.status(text)

    def log(self, text):
        self.emitter.log(text)

    def event(self, **kw):
        self.emitter.event(**kw)


def normalize(job):
    """Fill defaults and validate the job dictionary."""
    j = dict(job)
    j.setdefault("boot_type", "image" if j.get("image") else "none")
    if j["boot_type"] not in BOOT_TYPES:
        raise UsbError(f"unknown boot type {j['boot_type']}")
    j.setdefault("mode", "iso")
    j.setdefault("wintogo", False)
    j.setdefault("wintogo_index", 1)
    j.setdefault("scheme", "mbr")
    j.setdefault("target", "bios")
    j.setdefault("fs", "fat32")
    j.setdefault("cluster_size", 0)
    j.setdefault("label", "")
    j.setdefault("quick_format", True)
    j.setdefault("bad_blocks", 0)
    j.setdefault("extended_label", False)
    j.setdefault("old_bios_fixes", False)
    j.setdefault("rufus_mbr", False)
    j.setdefault("persistence_size", 0)
    j.setdefault("windows_options", [])
    j.setdefault("username", "")
    j.setdefault("edition_index", 1)
    j.setdefault("verify", False)
    j.setdefault("zero_full", False)
    j.setdefault("allow_internal", False)
    j.setdefault("allow_loop", False)
    j.setdefault("temp_dir", None)
    if j["scheme"] not in ("mbr", "gpt"):
        raise UsbError("scheme must be mbr or gpt")
    if j["target"] not in ("bios", "uefi", "dual"):
        raise UsbError("target must be bios, uefi or dual")
    if j["fs"] not in FS_TYPES:
        raise UsbError(f"unsupported file system {j['fs']}")
    if j["boot_type"] == "image" and not j.get("image"):
        raise UsbError("no image given")
    if not j.get("device"):
        raise UsbError("no device given")
    if j["scheme"] == "gpt" and j["target"] == "bios":
        raise UsbError("GPT needs a UEFI target")
    if j["boot_type"] == "uefi_ntfs" and j["fs"] not in ("ntfs", "exfat"):
        raise UsbError("UEFI:NTFS needs an NTFS or exFAT main partition")
    return j


def run_job(job, emitter, cancel=None):
    j = normalize(job)
    prog = Progress(emitter)
    log = emitter.log
    dev = os.path.realpath(j["device"])
    disk = devices.find_disk(dev)
    if disk is None:
        if os.path.exists(dev) and devices.is_system_disk(dev):
            raise UsbError(f"{dev} holds the running system; refusing to write it")
        raise UsbError(f"{dev} is not a disk")
    if disk["kind"] == "internal" and not j["allow_internal"]:
        raise UsbError(f"{dev} is an internal drive; refusing to write it")
    if disk["kind"] == "loop" and not j["allow_loop"]:
        raise UsbError(f"{dev} is a loop device; refusing to write it")
    disk_size, sector = disk["size"], disk["sector_size"]
    log(f"Device: {disk['display']} ({dev}), {human_size(disk_size)}, {sector}-byte sectors, {disk['kind']}")
    if disk["kind"] == "internal":
        log("WARNING: writing an internal drive because allow_internal was set")

    report = None
    reader = None
    if j["boot_type"] == "image":
        prog.phase("scan", "Scanning image...")
        report = image.probe(j["image"], log=log)
        log(f"Image: {report['name']} ({report['size_human']}), label '{report['label']}'")
        if j["mode"] == "dd" and not report["is_bootable_img"] and report["is_iso"]:
            raise UsbError("this ISO is not a hybrid image and cannot be written in DD mode")
        if j["mode"] == "iso" and not report["is_iso"]:
            j["mode"] = "dd"
        if j["wintogo"] and not report["wininst"]:
            raise UsbError("Windows To Go needs an image with sources/install.wim")
        if j["wintogo"] and j["scheme"] != "mbr":
            # Boot files live on the NTFS partition (UEFI:NTFS chains into
            # them), so there is no ESP; on GPT sysprep then finds no system
            # partition and specialize fails with 0xC0000452. On MBR the
            # active partition is the system partition, and that is NTFS.
            raise UsbError("Windows To Go currently needs the MBR partition scheme (target 'BIOS or UEFI')")
        if j["mode"] == "iso" and report["needs_ntfs"] and j["fs"] in ("fat16", "fat32"):
            raise UsbError("this image has files over 4 GB that cannot be split; use NTFS or exFAT")
        if j["persistence_size"] and not report["supports_persistence"]:
            raise UsbError("this image does not support a persistent partition")
        if j["mode"] == "iso" and j["fs"] in ("fat16", "fat32") and report["has_4gb_file"] and not report["wininst"]:
            raise UsbError("this image has files over 4 GB; FAT32 cannot hold them, use NTFS or exFAT")
        if j["mode"] == "iso" and report["projected_size"] > disk_size:
            raise UsbError(f"the image needs {human_size(report['projected_size'])} but the drive has {human_size(disk_size)}")

    dd_mode = j["boot_type"] == "image" and j["mode"] == "dd"
    bootable = j["boot_type"] != "none"
    is_windows = bool(report and report["is_windows"])
    efi_bootable = bool(report and (report["recommended"]["boots_uefi"]))
    needs_masquerading = bool(report and report["winpe"] and not report["uses_minint"])

    extras = set()
    if not dd_mode:
        if j["persistence_size"]:
            extras.add("persistence")
        if (j["boot_type"] == "image" and efi_bootable and j["fs"] in ("ntfs", "exfat")) or j["boot_type"] == "uefi_ntfs":
            extras.add("uefi_ntfs")
            if j["boot_type"] == "image" and report["has_bootmgr"] and not j["wintogo"] and j["target"] == "bios":
                extras.discard("uefi_ntfs")
        if j["old_bios_fixes"] and j["scheme"] == "mbr":
            extras.add("compat")
    plan_phases = []
    if j["bad_blocks"]:
        plan_phases.append(("badblocks", 25))
    if dd_mode:
        plan_phases += [("write", 70), ("verify", 20 if j["verify"] else 0), ("finalize", 2)]
    elif j["boot_type"] == "none" and j["zero_full"]:
        plan_phases += [("write", 95), ("finalize", 2)]
    else:
        plan_phases += [("partition", 2), ("format", 4 if j["quick_format"] else 20), ("bootrec", 1),
                        ("copy", 70 if j["boot_type"] == "image" else 3), ("patch", 8 if is_windows else 1), ("finalize", 3)]
    prog.plan(plan_phases)

    main_mount = None
    with mountctl.Inhibit(dev):
        try:
            mountctl.unmount_all(dev, log)
            fd = mountctl.open_exclusive(dev)
            os.close(fd)
            log(f"Current MBR: {bootrec.describe_mbr(dev)}")
            cur = layout.read_layout(dev)
            if cur:
                log(f"Current partition table: {cur.get('label', '?')} with {len(cur.get('partitions', []))} partition(s)")

            if j["bad_blocks"]:
                _bad_blocks(dev, j["bad_blocks"], prog, cancel, log)

            if dd_mode:
                prog.phase("write", "Writing image...")
                writer.write_image(j["image"], dev, emitter=prog, cancel=cancel, log=log, verify=j["verify"])
                prog.phase("finalize", "Finalizing...")
                sync_device(dev)
                return {"ok": True}
            if j["boot_type"] == "none" and j["zero_full"]:
                prog.phase("write", "Zeroing drive...")
                writer.zero_drive(dev, emitter=prog, cancel=cancel, log=log, full=True)
                return {"ok": True}

            # ---- partition
            prog.phase("partition", "Creating partition table...")
            layout.wipe_disk_signatures(dev, disk_size, sector, log)
            cluster = j["cluster_size"] or 0
            parts = layout.plan(disk_size, sector, j["scheme"], j["fs"], bootable, extras,
                                persistence_size=j["persistence_size"], old_bios_fixes=j["old_bios_fixes"],
                                cluster_size=cluster, uefi_ntfs_size=os.path.getsize(payload_path("uefi-ntfs.img")))
            for p in parts:
                log(f"● Creating {p.name} (offset: {p.offset}, size: {human_size(p.size)})")
            layout.clear_partition_starts(dev, parts, sector)
            layout.apply(dev, parts, j["scheme"], sector, mbr_uefi_marker=(j["scheme"] == "mbr" and j["target"] == "uefi"), log=log)
            by_role = {p.role: p for p in parts}
            main = by_role["main"]
            if cancel:
                cancel.check()

            if "uefi_ntfs" in by_role:
                p = by_role["uefi_ntfs"]
                log("Writing UEFI:NTFS data...")
                with open(payload_path("uefi-ntfs.img"), "rb") as f:
                    write_at(p.device, 0, f.read())
                # The image comes labelled RUFUS_BOOT; the silent-install answer
                # file refers to this partition by label, so keep both in step.
                fsmod.set_label(p.device, "fat32", "FUBUKI_BOOT")

            # ---- format
            prog.phase("format", "Formatting...")
            if "persistence" in by_role:
                p = by_role["persistence"]
                kind = "casper" if report and report["uses_casper"] else "live"
                log(f"Using {'Ubuntu' if kind == 'casper' else 'Debian'}-like method to enable persistence")
                fsmod.mkfs(p.device, "ext4", "casper-rw" if kind == "casper" else "persistence", quick=True, log=log, cancel=cancel)
                linux.setup_persistence(p.device, kind, mountctl.mount_dir_for(dev, "persist"), log)
            label = j["label"] or (report["label"] if report else "") or ""
            if j["fs"] not in ("ext2", "ext3", "ext4"):
                label = fsmod.valid_label(label, j["fs"], disk_size)
            fsmod.mkfs(main.device, j["fs"], label, cluster_size=cluster, quick=j["quick_format"], sector_size=sector,
                       size=main.size, emitter=prog, cancel=cancel, log=log)
            usb_label = fsmod.read_label(main.device) or label
            if cancel:
                cancel.check()

            # ---- boot records
            prog.phase("bootrec", "Writing boot records...")
            uses_syslinux = j["boot_type"] == "syslinux" or (
                j["boot_type"] == "image" and report["has_syslinux"] and not (is_windows and j["target"] == "dual"))
            uses_grub2 = not uses_syslinux and (j["boot_type"] == "grub2" or (j["boot_type"] == "image" and report["has_grub2"]))
            mbr_kind = _mbr_kind(j, report, bootable, is_windows, uses_syslinux, uses_grub2, needs_masquerading)
            if j["scheme"] == "mbr":
                bootrec.fix_mbr_entries(dev, parts.index(main), j["fs"],
                                        (0x81 if needs_masquerading else 0x80) if (bootable and j["target"] != "uefi") else None, log)
            if mbr_kind:
                bootrec.write_mbr_code(dev, mbr_kind, log)
            if mbr_kind == "msg":
                bootrec.write_sbr(dev, PROTECTIVE_MESSAGE.encode("cp437", "replace") + b"\x00", 17 * KB, parts[0].offset, log)
            pbr_variant = None
            if bootable and j["target"] != "uefi" and not uses_syslinux and not uses_grub2 and j["boot_type"] != "uefi_ntfs":
                if j["boot_type"] == "freedos":
                    pbr_variant = "fd"
                elif report and report["has_bootmgr"]:
                    pbr_variant = "pe"
                elif report and report["winpe"]:
                    pbr_variant = "nt"
                else:
                    pbr_variant = "std"
            if pbr_variant and j["fs"] in ("fat16", "fat32", "ntfs") and not j["wintogo"]:
                bootrec.write_pbr(main.device, j["fs"], pbr_variant, log)

            # ---- content
            prog.phase("copy", "Copying files..." if j["boot_type"] == "image" else "Preparing volume...")
            main_mount = mountctl.mount_dir_for(dev, "main")
            modified = []
            if j["boot_type"] == "image" and j["wintogo"]:
                _windows_to_go(j, report, main, main_mount, prog, cancel, log)
                if pbr_variant and j["fs"] == "ntfs" and j["target"] != "uefi":
                    mountctl.unmount(main_mount)
                    bootrec.write_pbr(main.device, "ntfs", "pe", log)
                    mountctl.mount(main.device, "ntfs", main_mount, log)
            else:
                mountctl.mount(main.device, j["fs"], main_mount, log)
                if j["boot_type"] == "image":
                    reader = open_image(j["image"])
                    with reader:
                        modified, split = extract.extract_iso(reader, report, main_mount, j["fs"], usb_label, emitter=prog, cancel=cancel,
                                                              log=log, persistence=bool(j["persistence_size"]), temp_dir=j["temp_dir"])
                        modified += split
                        if report["has_kolibrios"]:
                            e = reader.get("HD_Load/USB_Boot/MTLD_F32")
                            if e:
                                reader.extract(e, os.path.join(main_mount, "MTLD_F32"))
                elif j["boot_type"] == "freedos":
                    linux.install_freedos(main_mount, log)
                elif j["boot_type"] == "syslinux":
                    pass

            # ---- boot loaders
            if bootable and j["target"] != "uefi" and not j["wintogo"]:
                if uses_syslinux:
                    linux.install_syslinux(main.device, main_mount, report or {"syslinux_cfgs": []}, j["fs"], log,
                                           embedded=(j["boot_type"] == "syslinux"))
                elif uses_grub2:
                    linux.install_grub2(dev, main_mount, report, log)
            if j["boot_type"] == "image" and is_windows and not j["wintogo"]:
                if j["target"] in ("uefi", "dual") and report.get("has_win7_efi"):
                    inst = windows._find(main_mount, *report["wininst"][0]["path"].strip("/").split("/"))
                    if inst:
                        windows.setup_win7_efi(main_mount, inst, log, cancel)
                if j["target"] == "bios" and report["winpe"]:
                    log("WARNING: Windows XP / WinPE 2.x media is not supported; the drive may not boot")

            # ---- Windows customization
            prog.phase("patch", "Applying customization..." if is_windows else "Finalizing files...")
            if j["boot_type"] == "image" and is_windows and j["windows_options"]:
                modified += windows.apply_customization(main_mount, report, j["windows_options"], j["username"],
                                                        j["edition_index"], wintogo=j["wintogo"], log=log, emitter=prog,
                                                        cancel=cancel, temp_dir=j["temp_dir"])
            if j["boot_type"] == "image" and report["has_md5sum"] and modified and not j["wintogo"]:
                extract.update_md5sum(main_mount, report["has_md5sum"], modified, log)
            if j["extended_label"] and j["fs"] in ("fat16", "fat32", "exfat", "ntfs"):
                extract.write_autorun(main_mount, j["label"] or label, log)

            prog.phase("finalize", "Finalizing...")
            mountctl.unmount(main_mount, log)
            main_mount = None
            sync_device(dev)
            layout.reread(dev)
            log("Done.")
            return {"ok": True, "label": usb_label}
        finally:
            if main_mount:
                try:
                    mountctl.unmount(main_mount, log)
                except Exception:
                    pass
            sync_device(dev)


def _mbr_kind(j, report, bootable, is_windows, uses_syslinux, uses_grub2, needs_masquerading):
    if j["scheme"] == "gpt":
        return "msg" if bootable else "zero"
    if j["boot_type"] == "image" and is_windows and j["target"] == "dual":
        return "rufus" if (needs_masquerading or j["rufus_mbr"]) else "win7"
    if not bootable or j["target"] == "uefi":
        return "zero"
    if uses_syslinux:
        return "syslinux"
    if uses_grub2:
        return None   # grub-install writes boot.img + core.img itself
    if j["boot_type"] == "image" and report and report["has_kolibrios"] and j["fs"] in ("fat16", "fat32"):
        return "win7"
    if needs_masquerading or j["rufus_mbr"]:
        return "rufus"
    return "win7"


def _bad_blocks(dev, passes, prog, cancel, log):
    prog.phase("badblocks", "Checking for bad blocks...")
    patterns = ["0xaa", "0x55", "0xff", "0x00"][:max(1, min(4, passes))]
    cmd = ["badblocks", "-w", "-s", "-b", "4096"]
    for p in patterns:
        cmd += ["-t", p]
    cmd.append(dev)
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    if cancel:
        cancel.track(proc)
    bad = []
    try:
        buf = ""
        while True:
            ch = proc.stderr.read(1)
            if not ch:
                break
            buf += ch
            if ch in "\r\n\b":
                m = re.search(r"(\d+(?:\.\d+)?)% done", buf)
                if m:
                    prog.update(float(m.group(1)) / 100.0)
                buf = ""
        out, _ = proc.communicate()
        bad = [l for l in out.splitlines() if l.strip().isdigit()]
    finally:
        if cancel:
            cancel.untrack(proc)
    if cancel and cancel.cancelled:
        raise Cancelled()
    if bad:
        raise UsbError(f"bad blocks check found {len(bad)} bad block(s); this drive should not be trusted")
    log("Bad blocks: check completed, 0 bad blocks found")
    # The destructive test wrote patterns over the whole drive: clear again.
    layout.wipe_disk_signatures(dev, int(open(f"/sys/class/block/{os.path.basename(dev)}/size").read()) * 512, 512, log)


def _windows_to_go(j, report, main, main_mount, prog, cancel, log):
    """Extract install.wim to temp space, apply, install boot files."""
    inst = report["wininst"][0]
    tmpdir = tempfile.mkdtemp(prefix="fubuki-wtg-", dir=j["temp_dir"])
    try:
        free = shutil.disk_usage(tmpdir).free
        if free < inst["size"] + 64 * MB:
            raise UsbError(f"Windows To Go needs {human_size(inst['size'])} of temporary space in {tmpdir}; "
                           f"only {human_size(free)} is free (set TMPDIR to a larger location)")
        prog.status("Extracting install image...")
        with open_image(j["image"]) as reader:
            e = reader.get(inst["entry"])
            wim_tmp = os.path.join(tmpdir, e.name)
            done = [0]

            def cb(n):
                done[0] += n
                prog.update(min(0.3, 0.3 * done[0] / e.size), f"{human_size(done[0])} extracted")
            reader.extract(e, wim_tmp, progress=cb, cancel=cancel)
            bcd = {}
            for key, path in (("efi", "efi/microsoft/boot/bcd"), ("bios", "boot/bcd")):
                be = reader.get(path)
                if be:
                    bcd[key] = os.path.join(tmpdir, f"BCD_{key}")
                    reader.extract(be, bcd[key])
        if "efi" not in bcd or "bios" not in bcd:
            raise UsbError("image has no BCD template to build the boot store from")
        index = j["wintogo_index"] or 1
        names = {i["index"]: i["name"] for i in report["win_editions"]}
        log(f"Windows To Go: edition {index} ({names.get(index, '?')})")
        windows.setup_windows_to_go(None, report, wim_tmp, index, main.device, main_mount, j["target"], bcd,
                                    set(j["windows_options"]), log=log, emitter=prog, cancel=cancel, temp_dir=j["temp_dir"])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

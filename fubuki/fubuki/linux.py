"""Boot loaders for non-Windows media, plus persistence and FreeDOS.

Rufus carries its own copies of ldlinux.sys and GRUB's core.img and patches
them by hand because Windows has neither. Here the real `extlinux` and
`grub-install` do the work; what remains is choosing where and keeping the
Syslinux modules in step with the loader.
"""

import os
import shutil
import subprocess

from .util import run, UsbError, payload_path, require_tool, which

from .i18n import _

SYSLINUX_LIB = "/usr/lib/syslinux/bios"
# Modules a 6.x ldlinux.sys needs beside the ones the ISO already ships.
SYSLINUX_CORE_MODULES = ("ldlinux.c32", "libcom32.c32", "libutil.c32", "libmenu.c32", "libgpl.c32")


def syslinux_cfg_dir(report):
    """The directory holding the config Syslinux should load, without the
    leading slash. isolinux.cfg wins over syslinux.cfg over extlinux.conf."""
    cfgs = report.get("syslinux_cfgs") or []
    for want in ("isolinux.cfg", "syslinux.cfg", "extlinux.conf"):
        for c in cfgs:
            if os.path.basename(c).lower() == want:
                return os.path.dirname(c).strip("/")
    return os.path.dirname(cfgs[0]).strip("/") if cfgs else ""


def install_syslinux(part_dev, mount_dir, report, fs, log=None, embedded=False, reactos_path=None):
    """Install ldlinux.sys into the config directory and write Syslinux's
    volume boot record. Works on FAT, NTFS and ext through extlinux.

    reactos_path: a ReactOS image has no Syslinux of its own; FreeLoader is
    started as a multiboot kernel through mboot.c32 from a one-entry config."""
    require_tool("extlinux", "syslinux")
    cfg_dir = "" if (embedded or reactos_path) else syslinux_cfg_dir(report)
    target_dir = os.path.join(mount_dir, cfg_dir) if cfg_dir else mount_dir
    os.makedirs(target_dir, exist_ok=True)
    if reactos_path:
        for m in ("mboot.c32", "libcom32.c32"):
            shutil.copy2(os.path.join(SYSLINUX_LIB, m), os.path.join(mount_dir, m))
        with open(os.path.join(mount_dir, "syslinux.cfg"), "w") as f:
            f.write(f"DEFAULT ReactOS\nLABEL ReactOS\n  KERNEL mboot.c32\n  APPEND {reactos_path}\n")
        if log:
            log(f"Setting up ReactOS: mboot.c32 chain to {reactos_path}")

    # Syslinux only reads syslinux.cfg; an ISO that has isolinux.cfg alone
    # gets a one-line syslinux.cfg that chains to it.
    names = {n.lower() for n in os.listdir(target_dir)}
    if "syslinux.cfg" not in names:
        if "isolinux.cfg" in names:
            with open(os.path.join(target_dir, "syslinux.cfg"), "w") as f:
                f.write("DEFAULT loadconfig\n\nLABEL loadconfig\n  CONFIG isolinux.cfg\n"
                        f"  APPEND /{cfg_dir}/\n" if cfg_dir else
                        "DEFAULT loadconfig\n\nLABEL loadconfig\n  CONFIG isolinux.cfg\n")
            if log:
                log(f"Created {cfg_dir}/syslinux.cfg chaining to isolinux.cfg")
        elif "extlinux.conf" in names:
            shutil.copy2(os.path.join(target_dir, "extlinux.conf"), os.path.join(target_dir, "syslinux.cfg"))

    # The .c32 modules must match ldlinux.sys exactly. Ours is the host's,
    # so every module the image brought is replaced with the host's build.
    replaced = 0
    if os.path.isdir(SYSLINUX_LIB):
        wanted = set(SYSLINUX_CORE_MODULES)
        for scan_dir in {target_dir, mount_dir}:
            for n in os.listdir(scan_dir):
                if n.lower().endswith(".c32"):
                    wanted.add(n.lower())
        for n in sorted(wanted):
            src = os.path.join(SYSLINUX_LIB, n)
            if not os.path.isfile(src):
                continue
            for d in {target_dir, mount_dir} if n in SYSLINUX_CORE_MODULES else {target_dir}:
                existing = [x for x in os.listdir(d) if x.lower() == n]
                dst = os.path.join(d, existing[0] if existing else n)
                if existing or n in SYSLINUX_CORE_MODULES:
                    shutil.copy2(src, dst)
                    replaced += 1
    if log:
        log(f"Installed Syslinux {syslinux_version()} to /{cfg_dir} (updated {replaced} modules)")
    os.sync()
    r = run(["extlinux", "--install", target_dir], check=False, log=log)
    if r.returncode != 0:
        # extlinux needs FIBMAP; fall back to the syslinux installer on the
        # raw device, which does its own FAT walking.
        if fs in ("fat16", "fat32", "ntfs") and which("syslinux"):
            os.sync()
            subprocess.run(["umount", mount_dir], check=False)
            try:
                run(["syslinux", "--install", "--directory", "/" + cfg_dir if cfg_dir else "/", part_dev], log=log)
            finally:
                run(["mount", part_dev, mount_dir], log=log)
        else:
            raise UsbError(_("extlinux failed: %s") % (r.stderr or r.stdout).strip().splitlines()[-1:].__str__())
    os.sync()


def syslinux_version():
    try:
        r = subprocess.run(["syslinux", "--version"], capture_output=True, text=True)
        return (r.stdout or r.stderr).split()[1]
    except Exception:
        return "?"


def install_grub2(disk_dev, mount_dir, report, log=None):
    """BIOS GRUB into the MBR gap, modules under /boot/grub/i386-pc on the
    stick. Images that keep their config in /boot/grub2 get a stub."""
    require_tool("grub-install", "grub")
    boot_dir = os.path.join(mount_dir, "boot")
    os.makedirs(boot_dir, exist_ok=True)
    run(["grub-install", "--target=i386-pc", f"--boot-directory={boot_dir}", "--force",
         "--no-floppy", "--recheck", "--skip-fs-probe", disk_dev], log=log)
    grub_dir = os.path.join(boot_dir, "grub")
    cfg = os.path.join(grub_dir, "grub.cfg")
    alt = os.path.join(boot_dir, "grub2", "grub.cfg")
    if not os.path.exists(cfg) and os.path.exists(alt):
        with open(cfg, "w") as f:
            f.write("set prefix=($root)/boot/grub2\nconfigfile /boot/grub2/grub.cfg\n")
        if log:
            log("Created /boot/grub/grub.cfg stub for /boot/grub2")
    elif not os.path.exists(cfg):
        # Some images only carry an EFI grub.cfg; point BIOS GRUB at it.
        for cand in ("EFI/BOOT/grub.cfg", "efi/boot/grub.cfg", "EFI/boot/grub.cfg", "boot/grub/loopback.cfg"):
            p = os.path.join(mount_dir, cand)
            if os.path.exists(p):
                with open(cfg, "w") as f:
                    f.write(f"search --no-floppy --set=root --file /{cand}\nconfigfile /{cand}\n")
                if log:
                    log(f"Created /boot/grub/grub.cfg chaining to /{cand}")
                break
    if log:
        log(f"Installed GRUB {grub_version()} for BIOS boot")
    os.sync()


def grub_version():
    try:
        r = subprocess.run(["grub-install", "--version"], capture_output=True, text=True)
        return r.stdout.strip().split()[-1]
    except Exception:
        return "?"


def setup_persistence(part_dev, kind, mount_tmp, log=None):
    """Debian-style persistence needs a persistence.conf on the volume;
    casper just needs the label."""
    if kind != "live":
        return
    os.makedirs(mount_tmp, exist_ok=True)
    run(["mount", part_dev, mount_tmp], log=log)
    try:
        with open(os.path.join(mount_tmp, "persistence.conf"), "w") as f:
            f.write("/ union\n")
        if log:
            log("Created persistence.conf")
    finally:
        os.sync()
        run(["umount", mount_tmp], check=False)


FREEDOS_ROOT = ("KERNEL.SYS", "COMMAND.COM")


def install_freedos(mount_dir, log=None, keyboard=None, codepage=None):
    """Copy the FreeDOS kernel and shell, locale files into LOCALE\\, and
    an AUTOEXEC.BAT that sets the keyboard, like Rufus's SetDOSLocale."""
    src = payload_path("freedos")
    locale_dir = os.path.join(mount_dir, "LOCALE")
    os.makedirs(locale_dir, exist_ok=True)
    for name in sorted(os.listdir(src)):
        dst = os.path.join(mount_dir if name in FREEDOS_ROOT else locale_dir, name)
        shutil.copy2(os.path.join(src, name), dst)
    kb = keyboard or _dos_keyboard()
    cp = codepage or 437
    with open(os.path.join(mount_dir, "AUTOEXEC.BAT"), "w", newline="\r\n") as f:
        f.write("@echo off\nset PATH=.\\;\\;\\LOCALE\n")
        f.write("display con=(ega,,1)\n")
        f.write(f"mode con codepage prepare=(({cp}) \\LOCALE\\ega.cpx)\n")
        f.write(f"mode con codepage select={cp}\n")
        if kb and kb != "us":
            f.write(f"keyb {kb},,\\LOCALE\\keyboard.sys\n")
    with open(os.path.join(mount_dir, "CONFIG.SYS"), "w", newline="\r\n") as f:
        f.write("")
    if log:
        log("Installed FreeDOS 1.4 (KERNEL.SYS, COMMAND.COM, LOCALE\\)")


def _dos_keyboard():
    """xkb layout name -> FreeDOS keyb id, for the common layouts."""
    try:
        r = subprocess.run(["localectl", "status"], capture_output=True, text=True, timeout=5)
        import re
        m = re.search(r"X11 Layout:\s*(\S+)", r.stdout)
        layout = m.group(1).split(",")[0] if m else "us"
    except Exception:
        layout = "us"
    return {"us": "us", "gb": "uk", "de": "gr", "fr": "fr", "es": "sp", "it": "it", "pt": "po", "br": "br",
            "nl": "nl", "be": "be", "ch": "sg", "se": "sv", "no": "no", "dk": "dk", "fi": "su", "pl": "pl",
            "cz": "cz", "hu": "hu", "ru": "ru", "tr": "tr", "gr": "gk", "ca": "cf", "latam": "la"}.get(layout, "us")


def install_grub4dos(disk_dev, mount_dir, first_partition_offset, from_image, log=None):
    """Grub4DOS: its MBR code in sector 0 (written by the caller), the rest
    of grldr.mbr right after it, and grldr in the root unless the image
    brought its own. Rufus does the same from the same 0.4.6a release."""
    from .bootrec import write_sbr
    with open(payload_path("grub4dos", "grldr.mbr"), "rb") as f:
        mbr_rest = f.read()[512:]
    write_sbr(disk_dev, mbr_rest, 512, first_partition_offset, log)
    if not from_image:
        shutil.copy2(payload_path("grub4dos", "grldr"), os.path.join(mount_dir, "grldr"))
        if log:
            log("Installed grldr (Grub4DOS 0.4.6a); add a menu.lst to the root of the drive")

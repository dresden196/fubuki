"""Windows install media and Windows To Go: everything Rufus does after the
files are on the drive. Ported from wue.c, minus the parts that only exist
because Windows has bcdboot and an offline registry API.
"""

import os
import shutil
import struct
import tempfile

from . import unattend as ua
from . import wim as wimmod
from .util import UsbError, run, payload_path, human_size, MB

PE_AMD64, PE_ARM64 = 0x8664, 0xAA64
BCD_BOOTMGR = "{9dea862c-5cdd-4e70-acc1-f32b344d4795}"
BCD_TYPE_OSLOADER = 0x10200003
# BCD element ids
EL_DEVICE, EL_PATH, EL_DESCRIPTION, EL_INHERIT = "11000001", "12000002", "12000004", "14000006"
EL_OSDEVICE, EL_SYSTEMROOT, EL_WINPE, EL_DETECTHAL = "21000001", "22000002", "26000022", "26000010"
EL_DEFAULT, EL_DISPLAYORDER, EL_TIMEOUT, EL_RECOVERY = "23000003", "24000001", "25000004", "26000015"
EL_BOOTMENUPOLICY = "250000c2"
# An 88-byte "boot" device: the partition the boot manager itself came from.
BOOT_DEVICE_BLOB = bytes(16) + struct.pack("<III", 5, 0, 0x48) + bytes(88 - 28)


def pe_machine(path):
    with open(path, "rb") as f:
        head = f.read(0x40)
        if head[:2] != b"MZ":
            return 0
        off = struct.unpack_from("<I", head, 0x3C)[0]
        f.seek(off)
        sig = f.read(6)
        if sig[:4] != b"PE\0\0":
            return 0
        return struct.unpack_from("<H", sig, 4)[0]


def _find(mount_dir, *parts):
    """Case-insensitive path lookup on the (case-preserving) target."""
    cur = mount_dir
    for p in parts:
        if not os.path.isdir(cur):
            return None
        match = None
        for n in os.listdir(cur):
            if n.lower() == p.lower():
                match = n
                break
        if match is None:
            return None
        cur = os.path.join(cur, match)
    return cur


def _hive_set_labconfig(hive_path, log=None):
    import hivex
    h = hivex.Hivex(hive_path, write=True)
    setup = h.node_get_child(h.root(), "Setup")
    if setup is None:
        raise UsbError("SYSTEM hive has no Setup key")
    lab = h.node_get_child(setup, "LabConfig")
    if lab is None:
        lab = h.node_add_child(setup, "LabConfig")
    h.node_set_values(lab, [{"key": n, "t": 4, "value": struct.pack("<I", 1)} for n in ua.BYPASS_NAMES])
    h.commit(None)
    if log:
        for n in ua.BYPASS_NAMES:
            log(f"Created 'HKLM\\SYSTEM\\Setup\\LabConfig\\{n}' registry key")


def apply_customization(mount_dir, report, options, username="", edition_index=1, wintogo=False,
                        log=None, emitter=None, cancel=None, temp_dir=None):
    """Write the answer file and patch boot.wim. Returns list of modified
    paths (relative, leading slash) for md5sum maintenance."""
    options = set(options)
    if not options:
        return []
    modified = []
    arch = report.get("win_arch") or "x64"
    lines, removable = ua.build(arch, options, username, edition_index, report.get("win_language") or "en-US", log)
    if not lines:
        return []
    if emitter:
        emitter.status("Applying Windows customization...")
    if log:
        log("Applying Windows customization:")

    if wintogo:
        panther = os.path.join(_find(mount_dir, "Windows") or os.path.join(mount_dir, "Windows"), "Panther")
        os.makedirs(panther, exist_ok=True)
        with open(os.path.join(panther, "unattend.xml"), "w", encoding="utf-8") as f:
            f.write(ua.render(lines))
        if log:
            log("Added 'Windows\\Panther\\unattend.xml'")
        return modified

    winpe = bool(options & ua.WINPE_SETUP)
    build = (report.get("win_version") or {}).get("build", 0)
    if winpe:
        # In-place upgrades without TPM/SB: back up appraiserres.dll and leave
        # an empty one so setup.exe does not extract its own.
        appr = _find(mount_dir, "sources", "appraiserres.dll")
        if appr:
            os.replace(appr, appr[:-4] + ".bak")
            open(appr, "wb").close()
            modified.append("/sources/appraiserres.dll")
            if log:
                log("Renamed 'sources\\appraiserres.dll' -> 'appraiserres.bak' and created a placeholder")
        if build >= 26000:
            setup = _find(mount_dir, "setup.exe")
            if setup:
                machine = pe_machine(setup)
                wrapper = {PE_AMD64: "setup_x64.exe", PE_ARM64: "setup_arm64.exe"}.get(machine)
                if wrapper is None:
                    if log:
                        log(f"WARNING: unsupported setup.exe architecture 0x{machine:x}; no in-place upgrade wrapper added")
                else:
                    dll = os.path.join(os.path.dirname(setup), "setup.dll")
                    os.replace(setup, dll)
                    shutil.copyfile(payload_path("setup", wrapper), setup)
                    modified += ["/setup.exe", "/setup.dll"]
                    if log:
                        log("Renamed 'setup.exe' -> 'setup.dll' and added the Windows 11 24H2 bypass wrapper as 'setup.exe'")

    boot_wim = _find(mount_dir, "sources", "boot.wim")
    need_wim = (winpe or ua.USE_MS2023_BOOTLOADERS in options) and boot_wim is not None
    wim_index = 2
    commands = []
    tmp = tempfile.mkdtemp(prefix="fubuki-wue-", dir=temp_dir)
    try:
        if need_wim:
            count = wimmod.image_count(boot_wim)
            if count < 2:
                if log:
                    log("WARNING: this looks like an UNOFFICIAL Windows image (no Setup at boot.wim index 2)")
                wim_index = 1

        if ua.BYPASS_REQUIREMENTS in options and need_wim:
            try:
                wimmod.extract_paths(boot_wim, wim_index, ["/Windows/System32/config/SYSTEM"], tmp, log=None, cancel=cancel)
                hive = os.path.join(tmp, "SYSTEM")
                _hive_set_labconfig(hive, log)
                commands.append(f"add {hive} /Windows/System32/config/SYSTEM")
                only_bypass = (options & ua.WINPE_SETUP) == {ua.BYPASS_REQUIREMENTS}
                lines = ua.without_bypass_section(lines, removable, only_bypass)
                options.discard(ua.BYPASS_REQUIREMENTS)
                winpe = bool(options & ua.WINPE_SETUP)
            except Exception as ex:  # hivex or wimlib trouble: fall back to unattend
                if log:
                    log(f"Could not patch the registry directly ({ex}); using the unattend.xml fallback")

        xml_path = os.path.join(tmp, "unattend.xml")
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(ua.render(lines))

        if winpe:
            if not need_wim:
                raise UsbError("sources/boot.wim not found; cannot add the answer file")
            commands.append(f"add {xml_path} /Autounattend.xml")
            if log:
                log("Added 'Autounattend.xml' to 'sources\\boot.wim'")
        else:
            panther = os.path.join(mount_dir, "sources", "$OEM$", "$$", "Panther")
            os.makedirs(panther, exist_ok=True)
            shutil.copyfile(xml_path, os.path.join(panther, "unattend.xml"))
            if log:
                log("Created 'sources\\$OEM$\\$$\\Panther\\unattend.xml'")

        if ua.USE_MS2023_BOOTLOADERS in options and need_wim:
            _apply_2023_bootloaders(mount_dir, boot_wim, wim_index, tmp, arch, log, modified, cancel)

        if commands:
            if emitter:
                emitter.status("Updating boot.wim...")
            if log:
                log(f"Updating 'sources\\boot.wim[{wim_index}]'...")
            wimmod.update(boot_wim, wim_index, commands, log=None, cancel=cancel)
            modified.append("/sources/boot.wim")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return modified


def _apply_2023_bootloaders(mount_dir, boot_wim, wim_index, tmp, arch, log, modified, cancel):
    out = os.path.join(tmp, "ex")
    try:
        wimmod.extract_paths(boot_wim, wim_index, ["/Windows/Boot/EFI_EX", "/Windows/Boot/Fonts_EX"], out, cancel=cancel)
    except UsbError:
        if log:
            log("Could not extract 2023 signed UEFI bootloaders - ignoring option")
        return
    n = 0
    fonts = os.path.join(out, "Fonts_EX")
    if os.path.isdir(fonts):
        dst_dir = _find(mount_dir, "efi", "microsoft", "boot") or os.path.join(mount_dir, "efi", "microsoft", "boot")
        for f in os.listdir(fonts):
            shutil.copyfile(os.path.join(fonts, f), os.path.join(dst_dir, f.replace("_EX", "")))
            n += 1
    src = os.path.join(out, "EFI_EX", "bootmgfw_EX.efi")
    if os.path.isfile(src):
        for a in ("x64", "arm64", "ia32", "aa64"):
            dst = _find(mount_dir, "efi", "boot", f"boot{'x64' if a == 'x64' else 'aa64' if a == 'arm64' else a}.efi")
            if dst:
                shutil.copyfile(src, dst)
                modified.append("/efi/boot/" + os.path.basename(dst))
                n += 1
                break
    src = os.path.join(out, "EFI_EX", "bootmgr_EX.efi")
    dst = _find(mount_dir, "bootmgr.efi")
    if os.path.isfile(src) and dst:
        shutil.copyfile(src, dst)
        modified.append("/bootmgr.efi")
        n += 1
    if log and n:
        log(f"Replaced {n} EFI boot files with 'Windows UEFI CA 2023' signed versions")
        log("Note: the target machine must have the 'Windows UEFI CA 2023' certificate enrolled to boot this media")


def setup_win7_efi(mount_dir, wininst_path_on_target, log=None, cancel=None):
    """Windows 7 x64 ISOs have bootmgr.efi but no /efi/boot/bootx64.efi;
    pull bootmgfw.efi out of install.wim."""
    dst_dir = os.path.join(mount_dir, "efi", "boot")
    os.makedirs(dst_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="fubuki-w7-")
    try:
        wimmod.extract_paths(wininst_path_on_target, 1, ["/Windows/Boot/EFI/bootmgfw.efi"], tmp, cancel=cancel)
        shutil.copyfile(os.path.join(tmp, "bootmgfw.efi"), os.path.join(dst_dir, "bootx64.efi"))
        if log:
            log("Win7 EFI boot setup: created efi/boot/bootx64.efi")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ Windows To Go

def _utf16(s):
    return s.encode("utf-16-le") + b"\x00\x00"


def _patch_bcd(src, dst, loader_path, description="Windows To Go", log=None):
    """Turn the install media's BCD (which boots WinPE from a ramdisk) into a
    store that boots \\Windows from the partition the boot manager came from."""
    import hivex
    shutil.copyfile(src, dst)
    h = hivex.Hivex(dst, write=True)
    objs = h.node_get_child(h.root(), "Objects")
    loaders = []
    for o in h.node_children(objs):
        desc = h.node_get_child(o, "Description")
        t = None
        if desc is not None:
            for v in h.node_values(desc):
                if h.value_key(v) == "Type":
                    t = h.value_dword(v)
        if t == BCD_TYPE_OSLOADER:
            loaders.append(o)
    if not loaders:
        raise UsbError("no OS loader entry in the BCD template")
    for o in loaders:
        el = h.node_get_child(o, "Elements")
        wanted = {
            EL_DEVICE: (3, BOOT_DEVICE_BLOB),
            EL_OSDEVICE: (3, BOOT_DEVICE_BLOB),
            EL_PATH: (1, _utf16(loader_path)),
            EL_SYSTEMROOT: (1, _utf16("\\Windows")),
            EL_DESCRIPTION: (1, _utf16(description)),
            EL_DETECTHAL: (3, b"\x01"),
            EL_BOOTMENUPOLICY: (3, struct.pack("<Q", 1)),
            EL_RECOVERY: (3, b"\x00"),
        }
        for name, (t, val) in wanted.items():
            n = h.node_get_child(el, name)
            if n is None:
                n = h.node_add_child(el, name)
            h.node_set_value(n, {"key": "Element", "t": t, "value": val})
        for name in (EL_WINPE,):
            n = h.node_get_child(el, name)
            if n is not None:
                h.node_delete_child(n)
    # Boot manager: no timeout, first loader is the default.
    bm = h.node_get_child(objs, BCD_BOOTMGR)
    if bm is not None:
        el = h.node_get_child(bm, "Elements")
        guid = h.node_name(loaders[0])
        for name, (t, val) in {EL_DEFAULT: (1, _utf16(guid)), EL_DISPLAYORDER: (7, _utf16(guid) + b"\x00\x00"),
                               EL_TIMEOUT: (3, struct.pack("<Q", 0))}.items():
            n = h.node_get_child(el, name)
            if n is None:
                n = h.node_add_child(el, name)
            h.node_set_value(n, {"key": "Element", "t": t, "value": val})
    # A store sysprep will accept as *the* system store carries these
    # marks under Description; the DVD's store does not, and specialize
    # then fails with "File is not system store" (0xC0000098).
    desc = h.node_get_child(h.root(), "Description")
    if desc is None:
        desc = h.node_add_child(h.root(), "Description")
    vals = {h.value_key(v): dict(key=h.value_key(v), t=h.value_type(v)[0], value=h.value_value(v)[1]) for v in h.node_values(desc)}
    vals["KeyName"] = {"key": "KeyName", "t": 1, "value": _utf16("BCD00000001")}
    vals["System"] = {"key": "System", "t": 4, "value": struct.pack("<I", 1)}
    vals["TreatAsSystem"] = {"key": "TreatAsSystem", "t": 4, "value": struct.pack("<I", 1)}
    h.node_set_values(desc, list(vals.values()))
    h.commit(None)
    if log:
        log(f"Built {os.path.basename(dst)} for {loader_path}")


def _set_san_policy(system_hive, log=None):
    """SanPolicy=4: internal drives stay offline in Windows To Go, so a
    Win11 boot cannot 'upgrade' the host's volumes behind its back."""
    import hivex
    h = hivex.Hivex(system_hive, write=True)
    root = h.root()
    sel = h.node_get_child(root, "Select")
    current = 1
    if sel is not None:
        for v in h.node_values(sel):
            if h.value_key(v) == "Current":
                current = h.value_dword(v)
    cs = h.node_get_child(root, f"ControlSet{current:03d}")
    if cs is None:
        return
    node = cs
    for name in ("Services", "partmgr", "Parameters"):
        nxt = h.node_get_child(node, name)
        if nxt is None:
            nxt = h.node_add_child(node, name)
        node = nxt
    vals = [dict(key=h.value_key(v), t=h.value_type(v)[0], value=h.value_value(v)[1]) for v in h.node_values(node)
            if h.value_key(v) != "SanPolicy"]
    vals.append({"key": "SanPolicy", "t": 4, "value": struct.pack("<I", 4)})
    h.node_set_values(node, vals)
    h.commit(None)
    if log:
        log("Set internal drives offline (partmgr SanPolicy=4)")


def setup_windows_to_go(reader, report, wim_temp, index, main_dev, mount_dir, target, iso_bcd_paths,
                        options, log=None, emitter=None, cancel=None, temp_dir=None):
    """Apply install.wim[index] to the NTFS partition and make it boot.

    wim_temp: path of install.wim/.esd already extracted to temporary space.
    target: 'bios' | 'uefi' | 'dual'.
    Boot files live on the NTFS partition itself; on UEFI, UEFI:NTFS chains
    into them, which is Rufus's layout when no ESP is used. The BCD's device
    is then simply "boot", the partition the boot manager was read from.
    """
    if emitter:
        emitter.status("Applying Windows image (this takes a while)...")
    if log:
        log(f"Windows To Go: applying image index {index} to {main_dev}")
    wimmod.apply(wim_temp, index, main_dev, log=log, cancel=cancel, emitter=emitter, ntfs_device=True)

    _try_mount(main_dev, mount_dir, log)
    win = _find(mount_dir, "Windows")
    if not win:
        raise UsbError("applied image has no Windows directory")
    boot_src = _find(win, "Boot")
    tmp = tempfile.mkdtemp(prefix="fubuki-wtg-", dir=temp_dir)
    try:
        if target in ("uefi", "dual"):
            efi_dir = os.path.join(mount_dir, "EFI")
            ms_boot = os.path.join(efi_dir, "Microsoft", "Boot")
            os.makedirs(os.path.join(efi_dir, "Boot"), exist_ok=True)
            os.makedirs(ms_boot, exist_ok=True)
            src_efi = _find(boot_src, "EFI") if boot_src else None
            if not src_efi:
                raise UsbError("applied image has no Windows\\Boot\\EFI")
            for f in os.listdir(src_efi):
                if f.lower().endswith(".efi") or f.lower().endswith(".dll"):
                    shutil.copyfile(os.path.join(src_efi, f), os.path.join(ms_boot, f))
            bootmgfw = _find(src_efi, "bootmgfw.efi")
            arch_name = {"x64": "bootx64.efi", "arm64": "bootaa64.efi", "x86": "bootia32.efi"}.get(report.get("win_arch") or "x64", "bootx64.efi")
            shutil.copyfile(bootmgfw, os.path.join(efi_dir, "Boot", arch_name))
            fonts = _find(boot_src, "Fonts")
            if fonts:
                shutil.copytree(fonts, os.path.join(ms_boot, "Fonts"), dirs_exist_ok=True)
            res = _find(boot_src, "Resources")
            if res:
                shutil.copytree(res, os.path.join(ms_boot, "Resources"), dirs_exist_ok=True)
            _patch_bcd(iso_bcd_paths["efi"], os.path.join(ms_boot, "BCD"), "\\Windows\\system32\\winload.efi", log=log)
            if log:
                log("Installed EFI boot files on the Windows partition")
        if target in ("bios", "dual"):
            pcat = _find(boot_src, "PCAT") if boot_src else None
            if pcat and _find(pcat, "bootmgr"):
                shutil.copyfile(_find(pcat, "bootmgr"), os.path.join(mount_dir, "bootmgr"))
                bdir = os.path.join(mount_dir, "Boot")
                os.makedirs(bdir, exist_ok=True)
                fonts = _find(boot_src, "Fonts")
                if fonts:
                    shutil.copytree(fonts, os.path.join(bdir, "Fonts"), dirs_exist_ok=True)
                _patch_bcd(iso_bcd_paths["bios"], os.path.join(bdir, "BCD"), "\\Windows\\system32\\winload.exe", log=log)
                if log:
                    log("Installed BIOS boot files (bootmgr, Boot\\BCD)")
            elif log:
                log("WARNING: image has no PCAT boot files; BIOS boot not set up")
        if ua.OFFLINE_INTERNAL_DRIVES in options:
            hive = _find(win, "System32", "config", "SYSTEM")
            if hive:
                try:
                    _set_san_policy(hive, log)
                except Exception as ex:
                    if log:
                        log(f"WARNING: could not set SanPolicy: {ex}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _try_mount(dev, mount_dir, log=None):
    """Mount an NTFS volume: kernel ntfs3 first, ntfs-3g as fallback."""
    os.makedirs(mount_dir, exist_ok=True)
    for cmd in (["mount", "-t", "ntfs3", "-o", "windows_names", dev, mount_dir],
                ["ntfs-3g", "-o", "windows_names", dev, mount_dir],
                ["mount", "-t", "ntfs-3g", dev, mount_dir]):
        r = run(cmd, check=False, log=log)
        if r.returncode == 0:
            return
    raise UsbError(f"could not mount {dev} (NTFS): neither ntfs3 nor ntfs-3g worked")

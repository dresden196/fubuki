"""Windows ISO downloader: a port of Fido (Pete Batard, GPLv3), the script
Rufus runs behind its DOWNLOAD button.

Microsoft's consumer download pages hand out time-limited ISO links after
a small dance: a fresh session id must be announced to vlscppe, answered
through ov-df, and then the SKU list and the download links are fetched
with that session. The version, release and edition tables are Fido's
(the product edition ids are Microsoft's). UEFI Shell images come from
Pete Batard's GitHub releases.
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

from .util import UsbError, Cancelled, human_size, MB

from .i18n import _

FIDO_VERSION = "1.70"
ORG_ID = "y6jn8c31"
PROFILE_ID = "606624d44113"
INSTANCE_ID = "560dc9f3-1aa5-4a2f-b63c-9e18f8d0e175"
TIMEOUT = 30
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")

MS_CONNECTOR = "https://www.microsoft.com/software-download-connector/api/"
UEFI_SHELL_RELEASES = "https://github.com/pbatard/UEFI-Shell/releases/download/"

# (name, [releases]); a release is (name, [(edition name, ids)]). Editions
# with several ids are Microsoft's x86/x64 and ARM64 halves of one product.
VERSIONS = [
    ("Windows 11", "windows11", [
        ("25H2 v2 (Build 26200.8037 - 2026.03)", [
            ("Windows 11 Home/Pro/Edu", [3321, 3324]),
            ("Windows 11 Home China", [3322, 3325]),
            ("Windows 11 Pro China", [3323, 3326]),
        ]),
    ]),
    ("Windows 10", "Windows10ISO", [
        ("22H2 v1 (Build 19045.2965 - 2023.05)", [
            ("Windows 10 Home/Pro/Edu", [2618]),
            ("Windows 10 Home China", [2378]),
        ]),
    ]),
    ("UEFI Shell 2.2", "UEFI_SHELL 2.2", [
        (rel, [("Release", 0), ("Debug", 1)]) for rel in (
            "26H1 (edk2-stable202602)", "25H2 (edk2-stable202511)", "25H1 (edk2-stable202505)",
            "24H2 (edk2-stable202411)", "24H1 (edk2-stable202405)", "23H2 (edk2-stable202311)",
            "23H1 (edk2-stable202305)", "22H2 (edk2-stable202211)", "22H1 (edk2-stable202205)",
            "21H2 (edk2-stable202108)", "21H1 (edk2-stable202105)", "20H2 (edk2-stable202011)")
    ]),
    ("UEFI Shell 2.0", "UEFI_SHELL 2.0", [
        ("4.632 [20100426]", [("Release", 0)]),
    ]),
]

ARCH_NAMES = {0: "x86", 1: "x64", 2: "ARM64"}


def _is_shell(version_index):
    return VERSIONS[version_index][1].startswith("UEFI_SHELL")


def system_locale():
    """The locale to query Microsoft with, en-US style, from the environment."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var)
        if v and v not in ("C", "POSIX"):
            code = v.split(".")[0].split("@")[0].replace("_", "-")
            if "-" in code:
                lang, region = code.split("-", 1)
                return f"{lang.lower()}-{region.upper()}"
            return code.lower()
    return "en-US"


def host_arch():
    m = os.uname().machine
    return {"x86_64": "x64", "aarch64": "ARM64", "i686": "x86", "i386": "x86"}.get(m, "x64")


# ------------------------------------------------------------ HTTP

def _request(url, headers=None, referer=None, log=None):
    h = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if referer:
        h["Referer"] = referer
    if headers:
        h.update(headers)
    if log:
        log(f"Querying {url}")
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read(), r.headers
    except urllib.error.HTTPError as e:
        raise UsbError(_("Microsoft's server answered %s for %s") % (e.code, url.split("?")[0]))
    except urllib.error.URLError as e:
        raise UsbError(_("could not reach %s: %s") % (urllib.parse.urlsplit(url).netloc, e.reason))
    except TimeoutError:
        raise UsbError(_("timed out talking to %s") % urllib.parse.urlsplit(url).netloc)


def _json(url, referer=None, log=None):
    body, _h = _request(url, referer=referer, log=log)
    try:
        return json.loads(body.decode("utf-8", "replace") or "null")
    except ValueError:
        raise UsbError(_("unexpected answer from Microsoft's server"))


# ------------------------------------------------------------ tables

def versions():
    return [{"index": i, "name": v[0]} for i, v in enumerate(VERSIONS)]


def releases(version_index):
    return [{"index": i, "name": r[0]} for i, r in enumerate(VERSIONS[version_index][2])]


def editions(version_index, release_index, locale=None):
    """Editions of a release. The China editions are only offered to a
    Chinese locale, as Fido does."""
    locale = locale or system_locale()
    out = []
    for i, (name, ids) in enumerate(VERSIONS[version_index][2][release_index][1]):
        if "China" in name and not locale.lower().startswith("zh"):
            continue
        out.append({"index": i, "name": name, "ids": ids if isinstance(ids, list) else [ids]})
    return out


# ------------------------------------------------------------ Microsoft

def _new_session(locale, log=None):
    """Announce a fresh session id the way the download page's JavaScript
    would, and return it."""
    session = str(uuid.uuid4())
    _request(f"https://vlscppe.microsoft.com/tags?org_id={ORG_ID}&session_id={session}", log=log)
    body, _h = _request(f"https://ov-df.microsoft.com/mdt.js?instanceId={INSTANCE_ID}&PageId=si&session_id={session}", log=log)
    text = body.decode("utf-8", "replace")
    w = re.search(r"[?&]w=([A-F0-9]+)", text)
    rticks = re.search(r'rticks\=\"\+?(\d+)', text)
    if not w or not rticks:
        raise UsbError(_("could not complete Microsoft's download handshake"))
    _request(f"https://ov-df.microsoft.com/?session_id={session}&CustomerId={INSTANCE_ID}&PageId=si"
             f"&w={w.group(1)}&mdt={int(time.time() * 1000)}&rticks={rticks.group(1)}", log=log)
    return session


def languages(version_index, release_index, edition_index, locale=None, log=None, cancel=None):
    """The languages Microsoft offers for an edition. Each entry carries
    the (session, sku) pairs needed to ask for its download links."""
    locale = locale or system_locale()
    if _is_shell(version_index):
        return [{"name": "en-us", "display": "English (US)", "data": []}]
    eds = editions(version_index, release_index, locale)
    ed = next((e for e in eds if e["index"] == edition_index), None)
    if ed is None:
        raise UsbError(_("unknown edition"))
    langs = {}
    for edition_id in ed["ids"]:
        if cancel:
            cancel.check()
        session = _new_session(locale, log)
        url = (MS_CONNECTOR + "getskuinformationbyproductedition"
               f"?profile={PROFILE_ID}&productEditionId={edition_id}&SKU=undefined&friendlyFileName=undefined"
               f"&Locale={locale}&sessionID={session}")
        r = None
        for attempt in range(3):
            if attempt:
                time.sleep(2)
            r = _json(url, log=log)
            if r and not r.get("Errors") and r.get("Skus"):
                break
        if not r or r.get("Errors"):
            err = (r or {}).get("Errors") or [{}]
            raise UsbError(err[0].get("Value") or _("could not retrieve the language list from Microsoft"))
        for sku in r.get("Skus") or []:
            entry = langs.setdefault(sku["Language"], {"name": sku["Language"], "display": sku.get("LocalizedLanguage") or sku["Language"], "data": []})
            entry["data"].append({"session": session, "sku": sku["Id"]})
    if not langs:
        raise UsbError(_("could not retrieve the language list from Microsoft"))
    out = list(langs.values())
    pref = preferred_language(out, locale)
    for i, entry in enumerate(out):
        entry["preferred"] = (i == pref)
    return out


def preferred_language(langs, locale):
    """Index of the language matching the locale, Fido's table."""
    loc = locale
    lo = loc.lower()
    tests = [
        (lo.startswith("ar"), "Arabic"), (lo == "pt-br", "Brazil"), (lo.startswith("bg"), "Bulgar"),
        (lo == "zh-cn", ("Chinese", "simp")), (lo == "zh-tw", ("Chinese", "trad")), (lo.startswith("hr"), "Croat"),
        (lo.startswith("cs"), "Czech"), (lo.startswith("da"), "Danish"), (lo.startswith("nl"), "Dutch"),
        (lo == "en-us", "=English"), (lo.startswith("en"), ("English", "inter")), (lo.startswith("et"), "Eston"),
        (lo.startswith("fi"), "Finn"), (lo == "fr-ca", ("French", "Canad")), (lo.startswith("fr"), "=French"),
        (lo.startswith("de"), "German"), (lo.startswith("el"), "Greek"), (lo.startswith("he"), "Hebrew"),
        (lo.startswith("hu"), "Hungar"), (lo.startswith("id"), "Indones"), (lo.startswith("it"), "Italia"),
        (lo.startswith("ja"), "Japan"), (lo.startswith("ko"), "Korea"), (lo.startswith("lv"), "Latvia"),
        (lo.startswith("lt"), "Lithuania"), (lo.startswith("ms"), "Malay"), (lo.startswith("nb"), "Norw"),
        (lo.startswith("fa"), "Persia"), (lo.startswith("pl"), "Polish"), (lo == "pt-pt", "=Portuguese"),
        (lo.startswith("ro"), "Romania"), (lo.startswith("ru"), "Russia"), (lo.startswith("sr"), "Serbia"),
        (lo.startswith("sk"), "Slovak"), (lo.startswith("sl"), "Slovenia"), (lo == "es-es", "=Spanish"),
        (lo.startswith("es"), ("Spanish", "Mexic")), (lo.startswith("sv"), "Swedish"), (lo.startswith("th"), "Thai"),
        (lo.startswith("tr"), "Turk"), (lo.startswith("uk"), "Ukrain"), (lo.startswith("vi"), "Vietnam"),
    ]
    for i, entry in enumerate(langs):
        name = entry["display"]
        for cond, pat in tests:
            if not cond:
                continue
            if isinstance(pat, tuple):
                if all(p.lower() in name.lower() for p in pat):
                    return i
            elif pat.startswith("="):
                if name == pat[1:]:
                    return i
            elif pat.lower() in name.lower():
                return i
    for i, entry in enumerate(langs):
        if entry["name"].

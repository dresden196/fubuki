"""Small helpers shared by every module: running commands, sizes, events."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time


from .i18n import _

KB = 1024
MB = KB * KB
GB = MB * KB
TB = GB * KB


class UsbError(Exception):
    """A failure the user needs to hear about, with a message meant for them."""


class Cancelled(Exception):
    """The user pressed Cancel. Cleanup still runs; the drive is left unfinished."""


class CancelToken:
    def __init__(self):
        self._ev = threading.Event()
        self._procs = set()
        self._lock = threading.Lock()

    def cancel(self):
        self._ev.set()
        with self._lock:
            for p in list(self._procs):
                try:
                    p.kill()
                except Exception:
                    pass

    @property
    def cancelled(self):
        return self._ev.is_set()

    def check(self):
        if self._ev.is_set():
            raise Cancelled()

    def track(self, proc):
        with self._lock:
            self._procs.add(proc)

    def untrack(self, proc):
        with self._lock:
            self._procs.discard(proc)


class Emitter:
    """Where progress, log lines and status go.

    In JSON mode each event is one line on stdout, which is what the window
    reads. In text mode they are printed for a person at a terminal.
    """

    def __init__(self, json_mode=False, stream=None, verbose=False):
        self.json_mode = json_mode
        self.stream = stream or sys.stdout
        self.verbose = verbose
        self._lock = threading.Lock()
        self._last_progress = (None, -1.0, 0.0)

    def _write(self, obj):
        with self._lock:
            self.stream.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.stream.flush()

    def log(self, text):
        if self.json_mode:
            self._write({"event": "log", "text": text})
        else:
            with self._lock:
                print(text, file=sys.stderr)

    def status(self, text):
        """The one-line status the window shows under the progress bar."""
        if self.json_mode:
            self._write({"event": "status", "text": text})
        else:
            with self._lock:
                print("==> " + text, file=sys.stderr)

    def progress(self, phase, value, message=None):
        """value is 0..1 within the phase, or None for indeterminate."""
        now = time.monotonic()
        last_phase, last_val, last_t = self._last_progress
        # Rate-limit: a copy loop can produce thousands of updates a second.
        if phase == last_phase and value is not None and last_val is not None \
                and abs(value - last_val) < 0.002 and now - last_t < 0.2 and value < 1.0:
            return
        self._last_progress = (phase, value, now)
        if self.json_mode:
            ev = {"event": "progress", "phase": phase, "value": value}
            if message:
                ev["message"] = message
            self._write(ev)
        elif self.verbose and value is not None:
            with self._lock:
                print(f"    [{phase}] {value * 100:5.1f}%" + (f" {message}" if message else ""),
                      file=sys.stderr, end="\r")

    def event(self, **kw):
        if self.json_mode:
            self._write(kw)


def human_size(n, binary=True):
    """1536 -> '1.5 KB'. Rufus's SizeToHumanReadable, same rounding."""
    n = float(n)
    base = 1024.0 if binary else 1000.0
    units = ["bytes", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while n >= base and i < len(units) - 1:
        n /= base
        i += 1
    if i == 0:
        return f"{int(n)} {units[i]}"
    return f"{n:.1f} {units[i]}".replace(".0 ", " ")


def align_up(value, alignment):
    return ((value + alignment - 1) // alignment) * alignment


def align_down(value, alignment):
    return (value // alignment) * alignment


def which(name):
    return shutil.which(name)


def require_tool(name, package=None):
    if not shutil.which(name):
        hint = f" (install {package})" if package else ""
        raise UsbError(_("'%s' is not available on this system%s") % (name, hint))


def run(cmd, check=True, input=None, capture=True, env=None, cancel=None, cwd=None, log=None):
    """Run a command and return CompletedProcess. Raises UsbError on failure."""
    if log:
        log("$ " + " ".join(str(c) for c in cmd))
    full_env = dict(os.environ)
    full_env["LC_ALL"] = "C"
    if env:
        full_env.update(env)
    proc = subprocess.Popen(
        [str(c) for c in cmd],
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=full_env, cwd=cwd,
    )
    if cancel:
        cancel.track(proc)
    try:
        out, err = proc.communicate(input=input.encode() if isinstance(input, str) else input)
    finally:
        if cancel:
            cancel.untrack(proc)
    if cancel and cancel.cancelled:
        raise Cancelled()
    res = subprocess.CompletedProcess(cmd, proc.returncode,
                                      out.decode("utf-8", "replace") if out else "",
                                      err.decode("utf-8", "replace") if err else "")
    if check and res.returncode != 0:
        msg = (res.stderr or res.stdout or "").strip().splitlines()
        tail = msg[-1] if msg else f"exit code {res.returncode}"
        raise UsbError(_("%s failed: %s") % (os.path.basename(str(cmd[0])), tail))
    return res


def sync_device(dev):
    """Flush everything to the disk before we declare it done."""
    os.sync()
    try:
        fd = os.open(dev, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
    subprocess.run(["blockdev", "--flushbufs", dev], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_at(dev, offset, size):
    fd = os.open(dev, os.O_RDONLY)
    try:
        return os.pread(fd, size, offset)
    finally:
        os.close(fd)


def write_at(dev, offset, data):
    fd = os.open(dev, os.O_WRONLY)
    try:
        n = os.pwrite(fd, data, offset)
        if n != len(data):
            raise UsbError(_("short write to %s at %s") % (dev, offset))
        os.fsync(fd)
    finally:
        os.close(fd)


def payload_dir():
    from . import DEFAULT_PAYLOAD_DIR
    return os.environ.get("FUBUKI_PAYLOAD", DEFAULT_PAYLOAD_DIR)


def payload_path(*parts):
    p = os.path.join(payload_dir(), *parts)
    if not os.path.exists(p):
        raise UsbError(_("missing payload file: %s") % p)
    return p

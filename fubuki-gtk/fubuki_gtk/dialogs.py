"""The dialogs: the Windows options, the checksums, and the last warning
before a drive is erased."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from .engine import current_user, remember_windows_choices, saved_windows_choices  # noqa: E402
from .i18n import _, fmt  # noqa: E402

# Rufus's "Windows User Experience" dialog: what to patch into the installer
# before it runs. The option names are the engine's; only the labels live
# here.
WINDOWS_OPTIONS = [
    ("bypass_requirements", _("Remove requirement for 4GB+ RAM, Secure Boot and TPM 2.0")),
    ("no_online_account", _("Remove requirement for an online Microsoft account")),
    ("set_user", _("Create a local account with username:")),
    ("duplicate_locale", _("Set regional options to the same values as this user's")),
    ("no_data_collection", _("Disable data collection (Skip privacy questions)")),
    ("disable_bitlocker", _("Disable BitLocker automatic device encryption")),
    ("offline_internal_drives", _("Set internal drives offline")),
    ("qol_enhancements", _("Apply Windows quality-of-life defaults (no OneDrive/Outlook/Copilot/ads)")),
    ("silent_install", _("Silent unattended installation (wipes disk 0!)")),
    ("use_ms2023_bootloaders", _("Use 'Windows UEFI CA 2023' signed bootloaders")),
]

HASH_ALGORITHMS = [("md5", "MD5"), ("sha1", "SHA1"), ("sha256", "SHA256"), ("sha512", "SHA512")]


def confirm_write(parent, device_text, size_text, on_ok):
    """The last thing between the user and an erased drive."""
    body = (fmt(_("Device: %1\nSize: %2"), device_text, size_text) if size_text
            else fmt(_("Device: %1"), device_text))
    dialog = Adw.AlertDialog(
        heading=fmt(_("WARNING: ALL DATA ON DEVICE '%1' WILL BE DESTROYED."), device_text),
        body=body + "\n\n" + _("To continue with this operation, click OK. To quit click CANCEL."),
    )
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("ok", _("OK"))
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def on_response(_dialog, response):
        if response == "ok":
            on_ok()

    dialog.connect("response", on_response)
    dialog.present(parent)


def confirm_close(parent, on_yes):
    dialog = Adw.AlertDialog(
        heading=_("Write in progress"),
        body=_("A drive is still being written. Cancel it and close?"),
    )
    dialog.add_response("no", _("No"))
    dialog.add_response("yes", _("Yes"))
    dialog.set_response_appearance("yes", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("no")
    dialog.set_close_response("no")

    def on_response(_dialog, response):
        if response == "yes":
            on_yes()

    dialog.connect("response", on_response)
    dialog.present(parent)


class WindowsOptionsDialog(Adw.Dialog):
    """Asks how the Windows installer should be customised and hands the
    completed job to `on_done`."""

    def __init__(self, backend, job, editions, wintogo, on_done):
        super().__init__(title=_("Windows User Experience"), content_width=520)
        self._job = job
        self._editions = list(editions or [])
        self._on_done = on_done
        self._switches = {}

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_a: self.close())
        header.pack_start(cancel)
        ok = Gtk.Button(label=_("OK"))
        ok.add_css_class("suggested-action")
        ok.connect("clicked", self._accept)
        header.pack_end(ok)
        view.add_top_bar(header)
        self.set_default_widget(ok)

        group = Adw.PreferencesGroup(description=_("Customize the Windows installation:"))
        # Only what this engine says it understands, so an option it would
        # reject is never offered.
        known = backend.windows_options or [k for k, _l in WINDOWS_OPTIONS]
        saved = saved_windows_choices()
        on = list(saved["options"]) if saved["saved"] else list(backend.windows_defaults)
        # Taking internal drives offline is what keeps a Windows To Go stick
        # from mounting, or worse, using the host's own Windows.
        if wintogo and "offline_internal_drives" not in on:
            on.append("offline_internal_drives")

        self._username = None
        self._edition = None
        for key, label in WINDOWS_OPTIONS:
            if key not in known:
                continue
            if key == "set_user":
                row = Adw.ExpanderRow(title=label, show_enable_switch=True, enable_expansion=key in on)
                self._username = Adw.EntryRow(title=_("Username"), text=saved["username"] or current_user())
                row.add_row(self._username)
                self._switches[key] = row
            elif key == "silent_install" and self._editions:
                row = Adw.ExpanderRow(title=label, show_enable_switch=True, enable_expansion=key in on)
                self._edition = Adw.ComboRow(title=_("Windows edition"))
                self._edition.set_model(Gtk.StringList.new([str(e.get("name", "")) for e in self._editions]))
                row.add_row(self._edition)
                self._switches[key] = row
            else:
                row = Adw.SwitchRow(title=label, active=key in on)
                self._switches[key] = row
            group.add(row)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                      max_content_height=640)
        clamp = Adw.Clamp(margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        clamp.set_child(group)
        scroller.set_child(clamp)
        view.set_content(scroller)
        self.set_child(view)

    def _chosen(self):
        chosen = []
        for key, row in self._switches.items():
            active = row.get_enable_expansion() if isinstance(row, Adw.ExpanderRow) else row.get_active()
            if active:
                chosen.append(key)
        return chosen

    def _accept(self, *_args):
        chosen = self._chosen()
        username = self._username.get_text().strip() if self._username is not None else ""
        if not username:
            chosen = [k for k in chosen if k != "set_user"]
        remember_windows_choices(chosen, username)

        job = dict(self._job)
        job["windows_options"] = chosen
        job["username"] = username
        index = 1
        if self._edition is not None:
            selected = self._edition.get_selected()
            if 0 <= selected < len(self._editions):
                index = int(self._editions[selected].get("index", 1))
        job["edition_index"] = index
        self.close()
        self._on_done(job)


class ChecksumDialog(Adw.Dialog):
    """Rufus's "#" button: the image's checksums, computed by the engine while
    a progress bar shows the file being read."""

    def __init__(self, backend, path, name):
        super().__init__(title=_("Checksums"), content_width=560)
        self._backend = backend

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=18, margin_start=18, margin_end=18)
        title = Gtk.Label(label=name, ellipsize=Pango.EllipsizeMode.MIDDLE, xalign=0)
        title.add_css_class("heading")
        box.append(title)
        self._bar = Gtk.ProgressBar()
        box.append(self._bar)

        grid = Gtk.Grid(row_spacing=6, column_spacing=12)
        self._entries = {}
        for row, (key, label) in enumerate(HASH_ALGORITHMS):
            grid.attach(Gtk.Label(label=label, xalign=0, width_chars=7), 0, row, 1, 1)
            entry = Gtk.Entry(editable=False, hexpand=True, can_focus=True)
            entry.add_css_class("monospace")
            grid.attach(entry, 1, row, 1, 1)
            self._entries[key] = entry
        box.append(grid)
        view.set_content(box)
        self.set_child(view)

        self._handler = backend.connect("hash-changed", self._update)
        self.connect("closed", self._on_closed)
        backend.hash(path)
        self._update()

    def _update(self, *_args):
        backend = self._backend
        self._bar.set_visible(backend.hashing)
        self._bar.set_fraction(max(0.0, min(1.0, backend.hash_progress)))
        for key, entry in self._entries.items():
            entry.set_text(str(backend.hashes.get(key) or ""))
            entry.set_placeholder_text(_("Computing…") if backend.hashing else "")

    def _on_closed(self, *_args):
        self._backend.disconnect(self._handler)
        # Closing while the file is still being read stops the read; a
        # checksum nobody is waiting for is only disk traffic.
        if self._backend.hashing:
            self._backend.cancel()


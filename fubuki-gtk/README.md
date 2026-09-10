# fubuki-gtk

The GNOME window for Fubuki: GTK 4 and libadwaita, in Python. It is the
same tool as `fubuki-ui` (the KDE window) laid out the same way as Rufus,
and it drives the same engine over the same protocol (`fubuki/PROTOCOL.md`).
Everything that looks at a disk or an image and every decision about what
will boot is the engine's; this window draws what it reports and hands it
a job.

Two engine processes are used: one as the user, for listing drives and
probing images, and one under `pkexec` (polkit action
`io.github.dresden196.fubuki.write`), started the first time START is
pressed and kept for the next write so the password is asked once per
session.

## Running from the tree

    FUBUKI_ENGINE=$PWD/../fubuki/bin/fubuki \
    FUBUKI_LIB=$PWD/../fubuki \
    FUBUKI_PAYLOAD=$PWD/../fubuki/payload \
    FUBUKI_GTK_LIB=$PWD \
    ./bin/fubuki-gtk [image.iso]

`FUBUKI_ENGINE` points at the engine (default `/usr/bin/fubuki`);
`FUBUKI_LIB` and `FUBUKI_PAYLOAD` are passed through to it, including
across `pkexec`. `FUBUKI_GTK_DEBUG=1` echoes the log pane to the terminal.
When already root, the privileged engine is started directly.

## Packaging

`makepkg` here builds `fubuki-gtk` for Arch Linux; it depends on the
`fubuki` engine package, which also carries the shared icon. Translations
go in `po/<lang>/fubuki-gtk.po`, from the template `po/fubuki-gtk.pot`:

    xgettext --from-code=UTF-8 -k_ -kN_ -kngettext:1,2 -kpgettext:1c,2 \
        -o po/fubuki-gtk.pot fubuki_gtk/*.py

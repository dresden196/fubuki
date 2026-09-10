#!/usr/bin/env bash
# Build the Fubuki packages from this tree into dist/, for a distribution
# to import into its repository. The packages carry Fubuki's own version
# (the PKGBUILD pkgver, matching the git tag), not the consumer's.
#
#   ./release.sh            -> dist/fubuki-<ver>-any.pkg.tar.zst, dist/fubuki-ui-<ver>-x86_64.pkg.tar.zst,
#                              dist/fubuki-gtk-<ver>-any.pkg.tar.zst
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="$ROOT/dist"; mkdir -p "$DIST"
for p in fubuki fubuki-ui fubuki-gtk; do
    (cd "$ROOT/$p" && rm -rf src pkg && PKGDEST="$DIST" makepkg -f --noconfirm -s && rm -rf src pkg)
done
rm -f "$DIST"/*-debug-*.pkg.tar.zst
ls -1 "$DIST"

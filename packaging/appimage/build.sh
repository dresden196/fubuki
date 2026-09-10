#!/usr/bin/env bash
# Build the AppImage with appimage-builder (pip install appimage-builder)
# from Arch packages, into dist/. Run on Arch; pacman does the fetching.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
export FUBUKI_VERSION="${FUBUKI_VERSION:-$(sed -n 's/^pkgver=//p' "$ROOT/fubuki/PKGBUILD")}"
cd "$HERE"
appimage-builder --recipe AppImageBuilder.yml --skip-test
mkdir -p "$ROOT/dist"
mv -f Fubuki-*.AppImage "$ROOT/dist/"
rm -rf AppDir appimage-build
ls -la "$ROOT/dist"/Fubuki-*.AppImage

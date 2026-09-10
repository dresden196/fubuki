#!/usr/bin/env bash
# Build the AppImages with appimage-builder (pip install appimage-builder)
# from Arch packages, into dist/. Run on Arch; pacman does the fetching.
#   build.sh          both
#   build.sh qt|gtk   one of them
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
export FUBUKI_VERSION="${FUBUKI_VERSION:-$(sed -n 's/^pkgver=//p' "$ROOT/fubuki/PKGBUILD")}"
cd "$HERE"
for which in ${@:-gtk qt}; do
    rm -rf AppDir appimage-build
    appimage-builder --recipe "AppImageBuilder-$which.yml" --skip-test
    mkdir -p "$ROOT/dist"
    mv -f Fubuki-*.AppImage "$ROOT/dist/"
    rm -rf AppDir appimage-build
done
ls -la "$ROOT/dist"/Fubuki-*.AppImage

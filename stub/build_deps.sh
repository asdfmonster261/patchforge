#!/usr/bin/env bash
# build_deps.sh — Cross-compile zlib and bzip2 as static Win32/Win64 libraries
# for use by the PatchForge HDiffPatch stub.
#
# Output layout:
#   third_party/zlib/x64/{include/zlib.h, lib/libz.a}
#   third_party/zlib/x86/{include/zlib.h, lib/libz.a}
#   third_party/bzip2/x64/{include/bzlib.h, lib/libbz2.a}
#   third_party/bzip2/x86/{include/bzlib.h, lib/libbz2.a}
#
# Run once from the stub/ directory.  Safe to re-run; skips if already built.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TP="$SCRIPT_DIR/third_party"
BUILD_TMP="$TP/_build"

CC64=x86_64-w64-mingw32-gcc
CC32=i686-w64-mingw32-gcc
AR64=x86_64-w64-mingw32-ar
AR32=i686-w64-mingw32-ar
RANLIB64=x86_64-w64-mingw32-ranlib
RANLIB32=i686-w64-mingw32-ranlib

ZLIB_VER=1.3.2
ZLIB_SHA256=bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16

BZ2_VER=1.0.8
BZ2_SHA256=ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269

mkdir -p "$BUILD_TMP"

# ---------------------------------------------------------------------------
# Helper: download + verify tarball (optional fallback URL)
# ---------------------------------------------------------------------------
fetch() {
    local url="$1" dest="$2" sha="$3" fallback="${4:-}"
    if [[ ! -f "$dest" ]]; then
        echo "  Downloading $(basename "$dest")..."
        if ! curl -fsSL "$url" -o "$dest" 2>/dev/null && [[ -n "$fallback" ]]; then
            echo "  Primary URL failed, trying fallback..."
            curl -fsSL "$fallback" -o "$dest"
        fi
    fi
    echo "  Verifying $(basename "$dest")..."
    echo "$sha  $dest" | sha256sum -c --quiet
    echo "  OK"
}

# ---------------------------------------------------------------------------
# zlib
# ---------------------------------------------------------------------------
build_zlib() {
    local arch="$1"   # x64 | x86
    local cc ar ranlib
    if [[ "$arch" == "x64" ]]; then
        cc=$CC64; ar=$AR64; ranlib=$RANLIB64
        host=x86_64-w64-mingw32
    else
        cc=$CC32; ar=$AR32; ranlib=$RANLIB32
        host=i686-w64-mingw32
    fi

    local out="$TP/zlib/$arch"
    if [[ -f "$out/lib/libz.a" ]]; then
        echo "  zlib/$arch already built — skipping"
        return
    fi

    echo "  Building zlib $ZLIB_VER [$arch]..."
    local src="$BUILD_TMP/zlib-$ZLIB_VER"
    [[ -d "$src" ]] || tar -xzf "$BUILD_TMP/zlib-${ZLIB_VER}.tar.gz" -C "$BUILD_TMP"

    local work="$BUILD_TMP/zlib-build-$arch"
    rm -rf "$work"
    cp -a "$src" "$work"
    pushd "$work" > /dev/null

    # zlib's ./configure doesn't properly support --host; use the makefile directly
    mkdir -p "$out/include" "$out/lib" "$out/bin"
    make -f win32/Makefile.gcc \
        SHARED_MODE=0 \
        PREFIX="${host}-" \
        CC="$cc" AR="$ar" RANLIB="$ranlib" \
        INCLUDE_PATH="$out/include" \
        LIBRARY_PATH="$out/lib" \
        BINARY_PATH="$out/bin" \
        install 2>&1

    popd > /dev/null
    echo "  zlib/$arch -> $out"
}

# ---------------------------------------------------------------------------
# bzip2
# ---------------------------------------------------------------------------
build_bzip2() {
    local arch="$1"
    local cc ar ranlib
    if [[ "$arch" == "x64" ]]; then
        cc=$CC64; ar=$AR64; ranlib=$RANLIB64
    else
        cc=$CC32; ar=$AR32; ranlib=$RANLIB32
    fi

    local out="$TP/bzip2/$arch"
    if [[ -f "$out/lib/libbz2.a" ]]; then
        echo "  bzip2/$arch already built — skipping"
        return
    fi

    echo "  Building bzip2 $BZ2_VER [$arch]..."
    local src="$BUILD_TMP/bzip2-$BZ2_VER"
    [[ -d "$src" ]] || tar -xzf "$BUILD_TMP/bzip2-${BZ2_VER}.tar.gz" -C "$BUILD_TMP"

    local work="$BUILD_TMP/bzip2-build-$arch"
    rm -rf "$work"
    cp -a "$src" "$work"
    pushd "$work" > /dev/null

    # bzip2 Makefile honours CC/AR/RANLIB directly; build libbz2.a only
    make libbz2.a \
        CC="$cc" AR="$ar" RANLIB="$ranlib" \
        CFLAGS="-O2 -D_FILE_OFFSET_BITS=64 -fno-asynchronous-unwind-tables" \
        2>&1

    mkdir -p "$out/include" "$out/lib"
    cp bzlib.h   "$out/include/"
    cp libbz2.a  "$out/lib/"

    popd > /dev/null
    echo "  bzip2/$arch -> $out"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
echo "=== PatchForge stub deps build ==="
echo "Fetching sources..."

fetch \
    "https://zlib.net/zlib-${ZLIB_VER}.tar.gz" \
    "$BUILD_TMP/zlib-${ZLIB_VER}.tar.gz" \
    "$ZLIB_SHA256" \
    "https://github.com/madler/zlib/releases/download/v${ZLIB_VER}/zlib-${ZLIB_VER}.tar.gz"

fetch \
    "https://sourceware.org/pub/bzip2/bzip2-${BZ2_VER}.tar.gz" \
    "$BUILD_TMP/bzip2-${BZ2_VER}.tar.gz" \
    "$BZ2_SHA256"

echo ""
echo "Building zlib..."
build_zlib x64
build_zlib x86

echo ""
echo "Building bzip2..."
build_bzip2 x64
build_bzip2 x86

echo ""
echo "=== Done ==="
echo "  third_party/zlib/x64/lib/libz.a   — $(du -sh "$TP/zlib/x64/lib/libz.a"   | cut -f1)"
echo "  third_party/zlib/x86/lib/libz.a   — $(du -sh "$TP/zlib/x86/lib/libz.a"   | cut -f1)"
echo "  third_party/bzip2/x64/lib/libbz2.a — $(du -sh "$TP/bzip2/x64/lib/libbz2.a" | cut -f1)"
echo "  third_party/bzip2/x86/lib/libbz2.a — $(du -sh "$TP/bzip2/x86/lib/libbz2.a" | cut -f1)"

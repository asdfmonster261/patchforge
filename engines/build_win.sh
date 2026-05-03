#!/usr/bin/env bash
# build_win.sh — Cross-compile the patcher engines for Windows x64
# using MinGW-w64. Outputs to engines/win-x64/.
#
# Engines:
#   HDiffPatch  -> hdiffz.exe, hpatchz.exe
#   JojoDiff    -> jdiff.exe, jptch.exe
#
# Run from anywhere; the script discovers paths from its own location.
# Safe to re-run; clean intermediates with `rm -rf _build/` inside this dir.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="$(cd "$REPO_ROOT/.." && pwd)"
SRC="$WORKSPACE/source_code"
OUT="$SCRIPT_DIR/win-x64"
BUILD_TMP="$SCRIPT_DIR/_build"

CC=x86_64-w64-mingw32-gcc
CXX=x86_64-w64-mingw32-g++
AR=x86_64-w64-mingw32-ar
STRIP=x86_64-w64-mingw32-strip

mkdir -p "$OUT" "$BUILD_TMP"

# Sanity-check toolchain
for tool in "$CC" "$CXX" "$AR" "$STRIP" git make; do
    command -v "$tool" >/dev/null || { echo "Missing tool: $tool"; exit 1; }
done

# ---------------------------------------------------------------------------
# HDiffPatch — needs sisong forks of seven sibling deps cloned alongside
# ---------------------------------------------------------------------------
build_hdiffpatch() {
    echo "==> Building HDiffPatch (hdiffz.exe, hpatchz.exe)"
    local work="$BUILD_TMP/hdiffpatch-tree"
    rm -rf "$work"
    mkdir -p "$work"

    # Layout: $work/hdiffpatch + $work/{bzip2,zlib,libdeflate,libmd5,xxHash,zstd,lzma}
    cp -a "$SRC/hdiffpatch" "$work/hdiffpatch"
    find "$work/hdiffpatch" -name '*.o' -delete

    for repo in bzip2 zlib libdeflate libmd5 xxHash zstd lzma; do
        if [[ ! -d "$work/$repo" ]]; then
            git clone --depth 1 -q "https://github.com/sisong/$repo.git" "$work/$repo"
        fi
    done

    pushd "$work/hdiffpatch" > /dev/null
    make -j"$(nproc)" \
        CC="$CC" CXX="$CXX" AR="$AR" STRIP="$STRIP" \
        OS=Windows_NT 'RM=rm -f' \
        'PATCH_LINK=-municode -lpthread -static' \
        'DIFF_LINK=-municode -lpthread -static -static-libstdc++' \
        hpatchz hdiffz
    "$STRIP" hdiffz.exe hpatchz.exe
    cp hdiffz.exe hpatchz.exe "$OUT/"
    popd > /dev/null
}

# ---------------------------------------------------------------------------
# JojoDiff — patched main.cpp; link winpthreads statically
# ---------------------------------------------------------------------------
build_jojodiff() {
    echo "==> Building JojoDiff (jdiff.exe, jptch.exe)"
    local work="$BUILD_TMP/jojodiff"
    rm -rf "$work"
    cp -a "$SRC/jojodiff" "$work"
    # Source tree may carry stale Linux .o files from prior native builds.
    rm -rf "$work/bin"
    mkdir -p "$work/bin"
    pushd "$work" > /dev/null
    make CPP="$CXX" DIFF_EXE=jdiff.exe PTCH_EXE=jptch.exe \
        LDFLAGS="-pthread -static -static-libgcc -static-libstdc++"
    "$STRIP" jdiff.exe jptch.exe
    cp jdiff.exe jptch.exe "$OUT/"
    popd > /dev/null
}

build_hdiffpatch
build_jojodiff

echo
echo "Done. Engines staged in: $OUT"
ls -la "$OUT"/*.exe

# PatchForge stub Makefile — cross-compile Windows patchers with MinGW-w64
#
# Targets:
#   make all         — build all three patcher stubs (x64)
#   make win32       — build all three patcher stubs (x86)
#   make installer   — build installer stubs (x64 + x86)
#   make clean

CC64  = x86_64-w64-mingw32-gcc
CC32  = i686-w64-mingw32-gcc
STRIP64 = x86_64-w64-mingw32-strip
STRIP32 = i686-w64-mingw32-strip

SRC_ROOT = ../..
HDIFF    = $(SRC_ROOT)/source_code/hdiffpatch
JOJO     = $(SRC_ROOT)/source_code/jojodiff
XD3      = $(SRC_ROOT)/source_code/xdelta3
LZMA     = $(SRC_ROOT)/source_code/lzma/C
OUT      = prebuilt

# Third-party static libs (built by build_deps.sh)
TP       = third_party
ZLIB64   = $(TP)/zlib/x64
ZLIB32   = $(TP)/zlib/x86
BZ2_64   = $(TP)/bzip2/x64
BZ2_32   = $(TP)/bzip2/x86
XZ64     = $(TP)/liblzma/x64
XZ32     = $(TP)/liblzma/x86

DIRPATCH = $(HDIFF)/dirDiffPatch/dir_patch

CFLAGS_COMMON = -O2 -Wall -mwindows \
    -I$(HDIFF) -I$(HDIFF)/libHDiffPatch/HPatch \
    -I$(HDIFF)/dirDiffPatch \
    -I$(DIRPATCH) \
    -I$(XD3) \
    -I$(JOJO)/headers \
    -I$(LZMA) \
    -DNDEBUG -D_LARGEFILE_SOURCE -D_FILE_OFFSET_BITS=64 \
    -DZ7_ST -D_IS_USED_MULTITHREAD=0

CFLAGS_HDIFF_BASE = $(CFLAGS_COMMON) \
    -D_IS_NEED_ALL_CompressPlugin=0 \
    -D_IS_NEED_DEFAULT_CompressPlugin=0 \
    -D_IS_NEED_ALL_ChecksumPlugin=0 \
    -D_IS_NEED_DEFAULT_ChecksumPlugin=0 \
    -D_CompressPlugin_lzma \
    -D_CompressPlugin_lzma2 \
    -D_IS_NEED_DIR_DIFF_PATCH=1 \
    -D_IS_NEED_BSDIFF=0 \
    -D_IS_NEED_VCDIFF=0 \
    -D_IS_NEED_SINGLE_STREAM_DIFF=0 \
    -D_IS_NEED_tempDirPatchListener=1

# lzma only (no zlib/bz2 deps) — default if build_deps.sh not yet run
CFLAGS_HDIFF = $(CFLAGS_HDIFF_BASE)

# Full: lzma + zlib + bzip2 (requires build_deps.sh)
CFLAGS_HDIFF_FULL64 = $(CFLAGS_HDIFF_BASE) \
    -D_CompressPlugin_zlib \
    -D_CompressPlugin_bz2 \
    -I$(ZLIB64)/include \
    -I$(BZ2_64)/include

CFLAGS_HDIFF_FULL32 = $(CFLAGS_HDIFF_BASE) \
    -D_CompressPlugin_zlib \
    -D_CompressPlugin_bz2 \
    -I$(ZLIB32)/include \
    -I$(BZ2_32)/include

CFLAGS_XD3_BASE = $(CFLAGS_COMMON) \
    -DXD3_MAIN=0 -DXD3_WIN32=1 -DXD3_DEBUG=0 \
    -DXD3_USE_LARGEFILE64=1 \
    -DSECONDARY_DJW=1 -DSECONDARY_FGK=1 -DSECONDARY_LZMA=1 \
    -DHAVE_LZMA_H=1 \
    -DEXTERNAL_COMPRESSION=0

CFLAGS_XD3 = $(CFLAGS_XD3_BASE) \
    -I$(XZ64)/include
CFLAGS_XD3_32 = $(CFLAGS_XD3_BASE) \
    -I$(XZ32)/include

CFLAGS_JOJO = $(CFLAGS_COMMON)

# Installer stub flags: only needs liblzma
CFLAGS_INST64 = $(CFLAGS_COMMON) -I$(XZ64)/include
CFLAGS_INST32 = $(CFLAGS_COMMON) -I$(XZ32)/include

LIBS     = -lcomdlg32 -lshell32 -ldwmapi -ladvapi32 -lole32 -luuid -lwindowscodecs
LIBS_FULL64 = -L$(ZLIB64)/lib -L$(BZ2_64)/lib -lz -lbz2 $(LIBS)
LIBS_FULL32 = -L$(ZLIB32)/lib -L$(BZ2_32)/lib -lz -lbz2 $(LIBS)

HDIFF_SRCS = \
    $(HDIFF)/libHDiffPatch/HPatch/patch.c \
    $(HDIFF)/file_for_patch.c \
    $(DIRPATCH)/dir_patch.c \
    $(DIRPATCH)/dir_patch_tools.c \
    $(DIRPATCH)/new_dir_output.c \
    $(DIRPATCH)/new_stream.c \
    $(DIRPATCH)/ref_stream.c \
    $(DIRPATCH)/res_handle_limit.c \
    $(LZMA)/LzmaDec.c \
    $(LZMA)/Lzma2Dec.c \
    $(LZMA)/7zStream.c \
    $(LZMA)/CpuArch.c \
    $(LZMA)/Alloc.c

.PHONY: all win32 full full32 installer uninstaller clean deps

all: $(OUT)/hdiffpatch_x64.exe $(OUT)/xdelta3_x64.exe $(OUT)/jojodiff_x64.exe
	@echo "=== All x64 stubs built ==="

win32: $(OUT)/hdiffpatch_x86.exe $(OUT)/xdelta3_x86.exe $(OUT)/jojodiff_x86.exe
	@echo "=== All x86 stubs built ==="

installer: $(OUT)/installer_x64.exe $(OUT)/installer_x86.exe
	@echo "=== Installer stubs built ==="

uninstaller: $(OUT)/uninstaller_x64.exe $(OUT)/uninstaller_x86.exe
	@echo "=== Uninstaller stubs built ==="

# Full stubs with zlib+bzip2 support (requires build_deps.sh first)
full: $(OUT)/hdiffpatch_full_x64.exe $(OUT)/xdelta3_x64.exe $(OUT)/jojodiff_x64.exe
	@echo "=== All x64 full stubs built ==="

full32: $(OUT)/hdiffpatch_full_x86.exe $(OUT)/xdelta3_x86.exe $(OUT)/jojodiff_x86.exe
	@echo "=== All x86 full stubs built ==="

deps:
	bash build_deps.sh

$(OUT):
	mkdir -p $(OUT)

# HDiffPatch stub x64
$(OUT)/hdiffpatch_x64.exe: hdiffpatch_stub.c stub_common.h $(HDIFF_SRCS) | $(OUT)
	$(CC64) $(CFLAGS_HDIFF) \
	    hdiffpatch_stub.c $(HDIFF_SRCS) \
	    $(LIBS) \
	    -o $@
	$(STRIP64) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

# HDiffPatch stub x86
$(OUT)/hdiffpatch_x86.exe: hdiffpatch_stub.c stub_common.h $(HDIFF_SRCS) | $(OUT)
	$(CC32) $(CFLAGS_HDIFF) \
	    hdiffpatch_stub.c $(HDIFF_SRCS) \
	    $(LIBS) \
	    -o $@
	$(STRIP32) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

# xdelta3 stub x64
$(OUT)/xdelta3_x64.exe: xdelta3_stub.c stub_common.h | $(OUT)
	$(CC64) $(CFLAGS_XD3) \
	    xdelta3_stub.c \
	    -L$(XZ64)/lib -llzma \
	    $(LIBS) \
	    -o $@
	$(STRIP64) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

# xdelta3 stub x86
$(OUT)/xdelta3_x86.exe: xdelta3_stub.c stub_common.h | $(OUT)
	$(CC32) $(CFLAGS_XD3_32) \
	    xdelta3_stub.c \
	    -L$(XZ32)/lib -llzma \
	    $(LIBS) \
	    -o $@
	$(STRIP32) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

# JojoDiff stub x64
$(OUT)/jojodiff_x64.exe: jojodiff_stub.c stub_common.h | $(OUT)
	$(CC64) $(CFLAGS_JOJO) \
	    jojodiff_stub.c \
	    $(LIBS) \
	    -o $@
	$(STRIP64) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

# JojoDiff stub x86
$(OUT)/jojodiff_x86.exe: jojodiff_stub.c stub_common.h | $(OUT)
	$(CC32) $(CFLAGS_JOJO) \
	    jojodiff_stub.c \
	    $(LIBS) \
	    -o $@
	$(STRIP32) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

# HDiffPatch full stub x64 (lzma + zlib + bzip2)
$(OUT)/hdiffpatch_full_x64.exe: hdiffpatch_stub.c stub_common.h $(HDIFF_SRCS) | $(OUT)
	$(CC64) $(CFLAGS_HDIFF_FULL64) \
	    hdiffpatch_stub.c $(HDIFF_SRCS) \
	    $(LIBS_FULL64) \
	    -o $@
	$(STRIP64) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

# HDiffPatch full stub x86 (lzma + zlib + bzip2)
$(OUT)/hdiffpatch_full_x86.exe: hdiffpatch_stub.c stub_common.h $(HDIFF_SRCS) | $(OUT)
	$(CC32) $(CFLAGS_HDIFF_FULL32) \
	    hdiffpatch_stub.c $(HDIFF_SRCS) \
	    $(LIBS_FULL32) \
	    -o $@
	$(STRIP32) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

# Installer stub x64
$(OUT)/installer_x64.exe: installer_stub.c | $(OUT)
	$(CC64) $(CFLAGS_INST64) \
	    installer_stub.c \
	    -L$(XZ64)/lib -llzma \
	    $(LIBS) \
	    -o $@
	$(STRIP64) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

# Installer stub x86
$(OUT)/installer_x86.exe: installer_stub.c | $(OUT)
	$(CC32) $(CFLAGS_INST32) \
	    installer_stub.c \
	    -L$(XZ32)/lib -llzma \
	    $(LIBS) \
	    -o $@
	$(STRIP32) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

# Uninstaller flags: no liblzma needed
CFLAGS_UNINST64 = -O2 -Wall -mwindows -DNDEBUG
CFLAGS_UNINST32 = -O2 -Wall -mwindows -DNDEBUG
LIBS_UNINST = -ldwmapi -ladvapi32 -lshell32

# Uninstaller stub x64
$(OUT)/uninstaller_x64.exe: uninstaller_stub.c | $(OUT)
	$(CC64) $(CFLAGS_UNINST64) \
	    uninstaller_stub.c \
	    $(LIBS_UNINST) \
	    -o $@
	$(STRIP64) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

# Uninstaller stub x86
$(OUT)/uninstaller_x86.exe: uninstaller_stub.c | $(OUT)
	$(CC32) $(CFLAGS_UNINST32) \
	    uninstaller_stub.c \
	    $(LIBS_UNINST) \
	    -o $@
	$(STRIP32) $@
	@echo "Built: $@  ($$(du -sh $@ | cut -f1))"

clean:
	rm -f $(OUT)/*.exe

"""Compression level metadata."""

# Levels that require the full HDiffPatch stub (zlib + bzip2 deps) on the
# Windows side. Anything else can use the standard LZMA-only stub.
STUB_FULL_REQUIRED = {"zip/1", "zip/9", "bzip/5", "bzip/9"}

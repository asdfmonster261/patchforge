# vendored from steamarchiver/crack/unstub/ — DO NOT MODIFY.
# This implements SteamStub DRM unpacking via low-level PE/x86/RC4 surgery.
# Re-vendor the file from upstream when fixing variant bugs.

"""
SteamStub Variant 3.1 Unpacker (x86 + x64).

Supports sub-variants v3.1.0, v3.1.1 and v3.1.2.
"""

import logging
import struct

from ..base_unpacker import BaseUnpacker
from ..pe import PeFile
from ..utils import find_pattern
from . import register

log = logging.getLogger("unstub")


# ============================================================================
# DRM header
# ============================================================================

def _u32(d, off):
    return struct.unpack_from("<I", d, off)[0]

def _u64(d, off):
    return struct.unpack_from("<Q", d, off)[0]


class SteamStub31Header:
    """
    0xF0-byte DRM header for Variant 3.1 (both x86 and x64).

    The layout is identical for both architectures — pointer-sized fields
    are always stored as 8 bytes; only the lower 32 bits matter on x86.
    """

    SIZE = 0xF0
    SIGNATURE_VALUE = 0xC0DEC0DF

    def __init__(self, data: bytes, is_64bit: bool):
        off = 0
        self.xor_key               = _u32(data, off);  off += 4
        self.signature              = _u32(data, off);  off += 4
        self.image_base             = _u64(data, off);  off += 8
        self.drm_entry_point        = _u64(data, off);  off += 8
        self.bind_section_offset    = _u32(data, off);  off += 4
        self.unknown0000            = _u32(data, off);  off += 4
        self.original_entry_point   = _u64(data, off);  off += 8
        self.unknown0001            = _u32(data, off);  off += 4
        self.payload_size           = _u32(data, off);  off += 4
        self.drmp_dll_offset        = _u32(data, off);  off += 4
        self.drmp_dll_size          = _u32(data, off);  off += 4
        self.steam_app_id           = _u32(data, off);  off += 4
        self.flags                  = _u32(data, off);  off += 4
        self.bind_section_vsize     = _u32(data, off);  off += 4
        self.unknown0002            = _u32(data, off);  off += 4
        self.code_section_va        = _u64(data, off);  off += 8
        self.code_section_raw_sz    = _u64(data, off);  off += 8
        self.aes_key                = data[off:off+0x20]; off += 0x20
        self.aes_iv                 = data[off:off+0x10]; off += 0x10
        self.code_section_stolen    = data[off:off+0x10]; off += 0x10
        self.encryption_keys        = list(struct.unpack_from("<4I", data, off)); off += 0x10
        self.unknown0003            = data[off:off+0x20]; off += 0x20
        self.get_module_handle_a_rva = _u64(data, off); off += 8
        self.get_module_handle_w_rva = _u64(data, off); off += 8
        self.load_library_a_rva      = _u64(data, off); off += 8
        self.load_library_w_rva      = _u64(data, off); off += 8
        self.get_proc_address_rva    = _u64(data, off); off += 8


# ============================================================================
# Detection patterns
# ============================================================================

# Signature found in .bind for all v3.x stubs
_V3X_PATTERN_X86 = (
    "E8 00 00 00 00 50 53 51 52 56 57 55 "
    "8B 44 24 1C 2D 05 00 00 00 8B CC 83 E4 F0 51 51 51 50"
)
_V3X_PATTERN_X64 = "E8 00 00 00 00 50 53 51 52 56 57 55 41 50"

# Sub-variant patterns (x86) — used to read header_size from the stub code.
_SUBVAR_X86 = [
    # (pattern, offset_to_header_size)
    ("55 8B EC 81 EC ?? ?? ?? ?? 53 ?? ?? ?? ?? ?? 68",                 0x10),  # v3.1.0
    ("55 8B EC 81 EC ?? ?? ?? ?? 53 ?? ?? ?? ?? ?? 8D 83",              0x16),  # v3.1.1
    ("55 8B EC 81 EC ?? ?? ?? ?? 56 ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? 8D",  0x10),  # v3.1.2
]

# Sub-variant patterns (x64) — presence alone confirms v3.1.
_SUBVAR_X64 = [
    "48 89 6C 24 ?? 48 89 74 24 ?? 57 48 81 EC ?? ?? ?? ?? 48 8D",  # v3.1.0
    "48 C7 84 24 ?? ?? ?? ?? ?? ?? ?? ?? 48",                        # v3.1.2
]


# ============================================================================
# Unpacker class
# ============================================================================

@register
class Variant31Unpacker(BaseUnpacker):
    """SteamStub Variant 3.1 — handles both x86 and x64."""

    @property
    def name(self) -> str:
        arch = "x64" if self.pe and self.pe.is_64bit else "x86"
        return f"SteamStub Variant 3.1 ({arch})"

    @property
    def header_size(self) -> int:
        return SteamStub31Header.SIZE

    def parse_header(self, data: bytes) -> SteamStub31Header:
        is64 = self.pe.is_64bit if self.pe else False
        return SteamStub31Header(data, is64)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def can_process(self) -> bool:
        try:
            pe = PeFile(self.filepath)
            if not pe.parse():
                return False
            if not pe.has_section(".bind"):
                return False

            bind = pe.get_section_data(".bind")
            if bind is None:
                return False

            # Check for the v3.x main signature
            main_pat = _V3X_PATTERN_X64 if pe.is_64bit else _V3X_PATTERN_X86
            if find_pattern(bind, main_pat) == -1:
                return False

            if pe.is_64bit:
                return any(find_pattern(bind, p) != -1 for p in _SUBVAR_X64)
            else:
                for pat, off in _SUBVAR_X86:
                    pos = find_pattern(bind, pat)
                    if pos != -1:
                        hsz = struct.unpack_from("<I", bind, pos + off)[0]
                        if hsz == SteamStub31Header.SIZE:
                            return True
                return False
        except Exception:
            return False

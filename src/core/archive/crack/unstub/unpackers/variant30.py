# vendored from steamarchiver/crack/unstub/ — DO NOT MODIFY.
# This implements SteamStub DRM unpacking via low-level PE/x86/RC4 surgery.
# Re-vendor the file from upstream when fixing variant bugs.

"""
SteamStub Variant 3.0 Unpacker (x86 + x64).

Key differences from Variant 3.1:
- Signature is 0xC0DEC0DE (not 0xC0DEC0DF).
- Header sizes are 0xB0 or 0xD0 (not 0xF0).
- All pointer fields are uint32 (except ImageBase which is uint64).
- Has ``HasTlsCallback`` field with TLS OEP rebuild logic.
- Step 5 uses ``SizeOfRawData`` from the PE section, not from the DRM header.
"""

import logging
import struct

from ..base_unpacker import BaseUnpacker, SteamStubDrmFlags
from ..crypto import steam_xor, aes_decrypt_cbc, aes_rebuild_iv
from ..pe import PeFile
from ..utils import align, find_pattern
from . import register

log = logging.getLogger("unstub")


# ============================================================================
# DRM header
# ============================================================================

def _u32(d, off):
    return struct.unpack_from("<I", d, off)[0]

def _u64(d, off):
    return struct.unpack_from("<Q", d, off)[0]


class SteamStub30Header:
    """
    DRM header for Variant 3.0.

    Supports both 0xB0 and 0xD0 sizes.  All pointer fields are uint32
    except ``image_base`` which is uint64.
    """

    SIGNATURE_VALUE = 0xC0DEC0DE

    def __init__(self, data: bytes, header_size: int):
        self.header_size = header_size
        off = 0
        self.xor_key                = _u32(data, off); off += 4
        self.signature              = _u32(data, off); off += 4
        self.image_base             = _u64(data, off); off += 8
        self.drm_entry_point        = _u32(data, off); off += 4
        self.bind_section_offset    = _u32(data, off); off += 4
        self.unknown0000            = _u32(data, off); off += 4
        self.original_entry_point   = _u32(data, off); off += 4
        self.unknown0001            = _u32(data, off); off += 4
        self.payload_size           = _u32(data, off); off += 4
        self.drmp_dll_offset        = _u32(data, off); off += 4
        self.drmp_dll_size          = _u32(data, off); off += 4
        self.steam_app_id           = _u32(data, off); off += 4
        self.flags                  = _u32(data, off); off += 4
        self.bind_section_vsize     = _u32(data, off); off += 4
        self.unknown0002            = _u32(data, off); off += 4
        self.code_section_va        = _u32(data, off); off += 4
        self.code_section_raw_sz    = _u32(data, off); off += 4
        self.aes_key                = data[off:off+0x20]; off += 0x20
        self.aes_iv                 = data[off:off+0x10]; off += 0x10
        self.code_section_stolen    = data[off:off+0x10]; off += 0x10
        self.encryption_keys        = list(struct.unpack_from("<4I", data, off)); off += 0x10
        # Fields after the arrays (present in 0xD0 headers)
        if header_size >= 0xD0:
            self.has_tls_callback   = _u32(data, off); off += 4
            self.unknown0004        = _u32(data, off); off += 4
            self.unknown0005        = _u32(data, off); off += 4
            self.unknown0006        = _u32(data, off); off += 4
            self.unknown0007        = _u32(data, off); off += 4
            self.unknown0008        = _u32(data, off); off += 4
            self.get_module_handle_a_rva = _u32(data, off); off += 4
            self.get_module_handle_w_rva = _u32(data, off); off += 4
            self.load_library_a_rva      = _u32(data, off); off += 4
            self.load_library_w_rva      = _u32(data, off); off += 4
            self.get_proc_address_rva    = _u32(data, off); off += 4
            self.unknown0009        = _u32(data, off); off += 4
            self.unknown0010        = _u32(data, off); off += 4
            self.unknown0011        = _u32(data, off); off += 4
        else:
            self.has_tls_callback = 0


# ============================================================================
# Detection
# ============================================================================

_V3X_PATTERN_X86 = (
    "E8 00 00 00 00 50 53 51 52 56 57 55 "
    "8B 44 24 1C 2D 05 00 00 00 8B CC 83 E4 F0 51 51 51 50"
)
_V3X_PATTERN_X64 = "E8 00 00 00 00 50 53 51 52 56 57 55 41 50"

_HEADER_SIZE_PATTERN_30 = "48 8D 91 ?? ?? ?? ?? 48"
_HEADER_SIZE_PATTERN_31 = "48 8D 91 ?? ?? ?? ?? 41"

_SUBVAR_X86_30 = [
    ("55 8B EC 81 EC ?? ?? ?? ?? 53 ?? ?? ?? ?? ?? 68", 0x10),
]

_VALID_HEADER_SIZES = (0xB0, 0xD0)


def _get_header_size_x64(bind: bytes) -> int:
    """Extract header size from .bind for x64 v3.0."""
    for pat in [_HEADER_SIZE_PATTERN_30, _HEADER_SIZE_PATTERN_31]:
        pos = find_pattern(bind, pat)
        if pos != -1:
            val = abs(struct.unpack_from("<i", bind, pos + 3)[0])
            if val in _VALID_HEADER_SIZES:
                return val
    return 0


# ============================================================================
# Unpacker
# ============================================================================

@register
class Variant30Unpacker(BaseUnpacker):
    """SteamStub Variant 3.0 — handles both x86 and x64."""

    HEADER_SIGNATURE = 0xC0DEC0DE

    def __init__(self, filepath, options):
        super().__init__(filepath, options)
        self._header_size: int = 0
        self.tls_oep_override: int = 0

    @property
    def name(self) -> str:
        arch = "x64" if self.pe and self.pe.is_64bit else "x86"
        return f"SteamStub Variant 3.0 ({arch})"

    @property
    def header_size(self) -> int:
        return self._header_size

    def parse_header(self, data: bytes) -> SteamStub30Header:
        return SteamStub30Header(data, self._header_size)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def can_process(self) -> bool:
        try:
            pe = PeFile(self.filepath)
            if not pe.parse() or not pe.has_section(".bind"):
                return False

            bind = pe.get_section_data(".bind")
            if bind is None:
                return False

            if pe.is_64bit:
                if find_pattern(bind, _V3X_PATTERN_X64) == -1:
                    return False
                hsz = _get_header_size_x64(bind)
                if hsz in _VALID_HEADER_SIZES:
                    self._header_size = hsz
                    return True
            else:
                if find_pattern(bind, _V3X_PATTERN_X86) == -1:
                    return False
                for pat, off in _SUBVAR_X86_30:
                    pos = find_pattern(bind, pat)
                    if pos != -1:
                        hsz = struct.unpack_from("<I", bind, pos + off)[0]
                        if hsz in _VALID_HEADER_SIZES:
                            self._header_size = hsz
                            return True
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Step 1 — v3.0 signature + TLS callback rebuild
    # ------------------------------------------------------------------

    def step1(self) -> bool:
        pe = self.pe
        hsz = self._header_size

        # Re-derive header size if needed
        if hsz == 0:
            bind = pe.get_section_data(".bind")
            if bind and pe.is_64bit:
                hsz = _get_header_size_x64(bind)
            if hsz == 0:
                log.error("Could not determine header size.")
                return False
            self._header_size = hsz

        # Try EP first
        file_offset = pe.get_file_offset_from_rva(pe.opt_address_of_entry_point)
        if file_offset >= hsz:
            header_data = bytearray(pe.file_data[file_offset - hsz:file_offset])
            self.xor_key = steam_xor(header_data, hsz)
            self.header = self.parse_header(bytes(header_data))
            if self.header.signature == self.HEADER_SIGNATURE:
                return True

        # Try TLS callback
        if not pe.tls_callbacks:
            log.error("DRM header signature mismatch and no TLS callbacks.")
            return False

        tls_rva = pe.get_rva_from_va(pe.tls_callbacks[0])
        file_offset = pe.get_file_offset_from_rva(tls_rva)
        if file_offset < hsz:
            log.error("Could not resolve TLS callback file offset.")
            return False

        header_data = bytearray(pe.file_data[file_offset - hsz:file_offset])
        self.xor_key = steam_xor(header_data, hsz)
        self.header = self.parse_header(bytes(header_data))

        if self.header.signature != self.HEADER_SIGNATURE:
            log.error("DRM header signature mismatch (TLS attempt also failed).")
            return False

        self.tls_as_oep = True
        self.tls_oep_rva = tls_rva

        if self.header.has_tls_callback != 1 or pe.tls_callbacks[0] == 0:
            return True

        return self._rebuild_tls_callback_info()

    def _rebuild_tls_callback_info(self) -> bool:
        """Rebuild TLS callback and compute the real OEP."""
        pe = self.pe
        h = self.header

        # Verify TLS callback points into .bind
        tls_cb_rva = pe.get_rva_from_va(pe.tls_callbacks[0])
        bind_sec = pe.get_section(".bind")
        owner = pe.get_owner_section(tls_cb_rva)
        if owner is None or bind_sec is None:
            return False
        if owner.VirtualAddress != bind_sec.VirtualAddress:
            return False

        # Find section containing TLS callback table and patch it
        tls_dir_rva = pe.opt_data_directories[9][0] if len(pe.opt_data_directories) > 9 else 0
        if tls_dir_rva == 0:
            return False

        tls_dir_off = pe.get_file_offset_from_rva(tls_dir_rva)
        if pe.is_64bit:
            cb_table_va = struct.unpack_from("<Q", pe.file_data, tls_dir_off + 24)[0]
        else:
            cb_table_va = struct.unpack_from("<I", pe.file_data, tls_dir_off + 12)[0]

        cb_table_rva = cb_table_va - pe.opt_image_base
        cb_table_file_off = pe.get_file_offset_from_rva(cb_table_rva)
        cb_section = pe.get_owner_section(cb_table_rva)
        if cb_section is None:
            return False

        cb_sec_idx = pe.get_section_index(cb_section)
        offset_in_section = cb_table_file_off - cb_section.PointerToRawData

        # Write restored callback: ImageBase + OriginalEntryPoint
        restored_addr = pe.opt_image_base + h.original_entry_point
        if pe.is_64bit:
            struct.pack_into("<Q", pe.section_data[cb_sec_idx], offset_in_section, restored_addr)
        else:
            struct.pack_into("<I", pe.section_data[cb_sec_idx], offset_in_section, restored_addr)
        log.debug(" --> Restored TLS callback address.")

        # Find XOR key in EP code to compute real OEP
        entry_off = pe.get_file_offset_from_rva(pe.opt_address_of_entry_point)
        ep_code = pe.file_data[entry_off:entry_off + 0x100]
        res = find_pattern(ep_code, "48 81 EA ?? ?? ?? ?? 8B 12 81 F2")
        if res == -1:
            log.error("Could not find TLS XOR pattern in EP code.")
            return False

        # key = (ulong)((long)XorKey ^ (int)code_xor_val)
        code_xor_val = struct.unpack_from("<i", ep_code, res + 0x0B)[0]
        key = (h.xor_key ^ code_xor_val) & 0xFFFFFFFFFFFFFFFF
        off = (pe.opt_image_base + pe.opt_address_of_entry_point + key) & 0xFFFFFFFFFFFFFFFF
        self.tls_oep_override = (off - pe.opt_image_base) & 0xFFFFFFFF

        log.debug(f" --> TLS OEP override: 0x{self.tls_oep_override:X}")
        return True

    # ------------------------------------------------------------------
    # Step 5 — v3.0 uses SizeOfRawData, full buffer output
    # ------------------------------------------------------------------

    def step5(self) -> bool:
        h = self.header
        if h.flags & SteamStubDrmFlags.NoEncryption:
            log.debug(" --> Code section is not encrypted.")
            return True

        try:
            sec = self.pe.sections[self.code_section_index]
            log.debug(f" --> {sec.section_name} linked as main code section.")
            log.debug(f" --> {sec.section_name} section is encrypted.")

            if sec.SizeOfRawData == 0:
                self.code_section_data = bytearray()
                return True

            code_off = self.pe.get_file_offset_from_rva(sec.VirtualAddress)
            encrypted = self.pe.file_data[code_off:code_off + sec.SizeOfRawData]
            combined = bytearray(h.code_section_stolen) + bytearray(encrypted)

            rebuilt_iv = aes_rebuild_iv(h.aes_key, h.aes_iv)
            decrypted = aes_decrypt_cbc(bytes(combined), h.aes_key, rebuilt_iv)

            self.code_section_data = bytearray(decrypted)
            return True
        except Exception as e:
            log.error(f" --> Error decrypting code section: {e}")
            return False

    # ------------------------------------------------------------------
    # Step 6 — v3.0 has TLS OEP override
    # ------------------------------------------------------------------

    def step6(self) -> bool:
        pe = self.pe
        h = self.header

        try:
            if self.options.get("zerodostub", False) and pe.dos_stub_size > 0:
                pe.dos_stub_data = b"\x00" * pe.dos_stub_size

            do_realign = self.options.get("realign", False)
            pe.rebuild_sections(do_realign)

            if pe.sections:
                sa = pe.opt_section_alignment or 0x1000
                last = max(pe.sections, key=lambda s: s.VirtualAddress)
                pe.set_size_of_image(last.VirtualAddress + align(last.VirtualSize, sa))

            # EP: TLS override or original
            if h.has_tls_callback != 1:
                pe.set_entry_point(h.original_entry_point & 0xFFFFFFFF)
            else:
                pe.set_entry_point(self.tls_oep_override & 0xFFFFFFFF)

            pe.set_checksum(0)
            unpacked_path = self.filepath + ".unpacked.exe"

            with open(unpacked_path, "wb") as f:
                f.write(pe.dos_header_bytes)
                if pe.dos_stub_size > 0:
                    f.write(pe.dos_stub_data)
                f.write(struct.pack("<I", pe.nt_signature))
                f.write(pe.pack_file_header())
                f.write(bytes(pe.optional_header_bytes))

                for i in range(len(pe.sections)):
                    section = pe.sections[i]
                    f.write(section.pack())
                    header_resume_pos = f.tell()
                    f.seek(section.PointerToRawData)
                    if i == self.code_section_index and self.code_section_data is not None:
                        f.write(bytes(self.code_section_data))
                    else:
                        f.write(bytes(pe.section_data[i]))
                    f.seek(header_resume_pos)

                f.seek(0, 2)
                if pe.overlay_data:
                    f.write(pe.overlay_data)

            log.info(f" --> Unpacked file saved to disk!")
            log.info(f" --> File Saved As: {unpacked_path}")
            return True
        except Exception as e:
            log.error(f" --> Error saving unpacked file: {e}")
            return False

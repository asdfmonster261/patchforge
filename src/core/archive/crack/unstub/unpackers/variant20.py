# vendored from steamarchiver/crack/unstub/ — DO NOT MODIFY.
# This implements SteamStub DRM unpacking via low-level PE/x86/RC4 surgery.
# Re-vendor the file from upstream when fixing variant bugs.

"""
SteamStub Variant 2.0 Unpacker (x86 only).

Much simpler than v2.1 — no payload, no SteamDRMP.dll indirection:
- DRM header contains OEP, code section VA, XOR key directly.
- Code section uses simple rolling XOR (not AES).
- Three header size variants: 856, 884, 952.
"""

import logging
import struct
from typing import Optional

from ..crypto import steam_xor
from ..pe import PeFile, FILE_HEADER_SIZE
from ..utils import align, find_pattern, pe_checksum
from ..x86 import disassemble_ep_v20
from . import register

log = logging.getLogger("unstub")


# ============================================================================
# DRM flags (v2.0 uses different flag semantics than v2.1+)
# ============================================================================

class DrmFlags20:
    UseValidation = 0x01
    UseWinVerifyTrustValidation = 0x02
    UseEncodedCodeSection = 0x04
    UseThreadCheckValidation = 0x08
    UseMemoryMappedValidation = 0x10


# ============================================================================
# DRM header
# ============================================================================

def _u32(d, off):
    return struct.unpack_from("<I", d, off)[0]


class SteamStub20Header:
    """
    Unified DRM header for Variant 2.0.

    Handles all three size variants (856, 884, 952) by parsing common
    fields and adjusting offsets based on which extra fields are present.
    """

    VALID_SIZES = (856, 884, 952)

    def __init__(self, data: bytes, total_size: int):
        self.total_size = total_size
        off = 0

        self.xor_key1                   = _u32(data, off); off += 4
        self.xor_key2                   = _u32(data, off); off += 4
        self.get_module_handle_a_idata  = _u32(data, off); off += 4
        self.get_proc_address_idata     = _u32(data, off); off += 4

        if total_size == 856:
            # 856: GetModuleHandleW, no LoadLibraryA, no custom GetProcAddress
            self.get_module_handle_w_idata = _u32(data, off); off += 4
        elif total_size == 884:
            # 884: LoadLibraryA + custom GetProcAddress, no GetModuleHandleW
            self.load_library_a_idata      = _u32(data, off); off += 4
            self.get_proc_address_custom   = _u32(data, off); off += 4
        elif total_size == 952:
            # 952: GetModuleHandleW + custom GetProcAddress_bind
            self.get_module_handle_w_idata = _u32(data, off); off += 4
            self.get_proc_address_bind     = _u32(data, off); off += 4

        self.flags                      = _u32(data, off); off += 4
        self.unknown0000                = _u32(data, off); off += 4
        self.bind_section_va            = _u32(data, off); off += 4
        self.bind_section_code_size     = _u32(data, off); off += 4
        self.bind_section_hash          = _u32(data, off); off += 4
        self.oep                        = _u32(data, off); off += 4
        self.code_section_va            = _u32(data, off); off += 4
        self.code_section_size          = _u32(data, off); off += 4
        self.code_section_xor_key       = _u32(data, off); off += 4
        self.steam_app_id               = _u32(data, off); off += 4

        # SteamAppID string size varies by variant
        if total_size == 952:
            self.steam_app_id_string    = data[off:off+0x0C]; off += 0x0C
        else:
            self.steam_app_id_string    = data[off:off+0x08]; off += 0x08

        self.stub_data = data[off:]


# ============================================================================
# Detection
# ============================================================================

_V20_BIND_PATTERN = "53 51 52 56 57 55 8B EC 81 EC 00 10 00 00 BE"


# ============================================================================
# Unpacker
# ============================================================================

@register
class Variant20Unpacker:
    """
    SteamStub Variant 2.0 Unpacker (x86 only).

    Simpler than v2.1: no payload/DRMP chain, parameters directly in header,
    code section uses rolling XOR (not AES).
    """

    def __init__(self, filepath: str, options: dict):
        self.filepath = filepath
        self.options = options
        self.pe: Optional[PeFile] = None
        self.header: Optional[SteamStub20Header] = None
        self.code_section_index: int = -1
        self.code_section_data: Optional[bytearray] = None

    @property
    def name(self) -> str:
        return "SteamStub Variant 2.0 (x86)"

    def can_process(self) -> bool:
        try:
            pe = PeFile(self.filepath)
            if not pe.parse() or pe.is_64bit or not pe.has_section(".bind"):
                return False
            bind = pe.get_section_data(".bind")
            return bind is not None and find_pattern(bind, _V20_BIND_PATTERN) != -1
        except Exception:
            return False

    def process(self) -> bool:
        self.code_section_index = -1
        self.code_section_data = None

        self.pe = PeFile(self.filepath)
        if not self.pe.parse():
            log.error("Failed to parse PE file.")
            return False

        log.info(f"File is packed with {self.name}!")

        steps = [
            ("Step 1 - Read, disassemble and decode the SteamStub DRM header.", self.step1),
            ("Step 2 - Read, decrypt and process the main code section.", self.step2),
            ("Step 3 - Prepare the file sections.", self.step3),
            ("Step 4 - Rebuild and save the unpacked file.", self.step4),
        ]
        if self.options.get("recalcchecksum", False):
            steps.append(("Step 5 - Rebuild unpacked file checksum.", self.step5))

        for desc, fn in steps:
            log.info(desc)
            if not fn():
                return False
        return True

    # -- Step 1: Disassemble EP, decode DRM header --

    def step1(self) -> bool:
        pe = self.pe
        ep_file_off = pe.get_file_offset_from_rva(pe.opt_address_of_entry_point)
        if ep_file_off < 4:
            return False

        sig = struct.unpack_from("<I", pe.file_data, ep_file_off - 4)[0]
        if sig != 0xC0DEC0DE:
            log.error(f"DRM signature mismatch: 0x{sig:08X}")
            return False

        ep_code = pe.file_data[ep_file_off:ep_file_off + 4096]
        result = disassemble_ep_v20(ep_code)
        if result is None:
            log.error("Failed to extract DRM parameters from EP code.")
            return False

        struct_va, struct_size = result
        struct_rva = struct_va - pe.opt_image_base
        log.debug(f" --> Header VA=0x{struct_va:08X} Size={struct_size}")

        if struct_size not in SteamStub20Header.VALID_SIZES:
            log.error(f"Invalid/unknown variant header size: {struct_size}")
            return False

        file_off = pe.get_file_offset_from_rva(struct_rva)
        if file_off < 0:
            return False

        header_data = bytearray(pe.file_data[file_off:file_off + struct_size])
        # v2.0 always uses key=0 (self-keyed from first 4 bytes)
        steam_xor(header_data, len(header_data), 0)

        self.header = SteamStub20Header(bytes(header_data), struct_size)
        return True

    # -- Step 2: Decrypt code section (simple XOR) --

    def step2(self) -> bool:
        pe = self.pe
        h = self.header

        # Code section is found via BaseOfCode from the optional header
        code_section_rva = struct.unpack_from(
            "<I", pe.optional_header_bytes, 20  # BaseOfCode offset in PE32 opt header
        )[0]

        code_section = pe.get_owner_section(code_section_rva)
        if code_section is None or code_section.PointerToRawData == 0 or code_section.SizeOfRawData == 0:
            log.error("Could not find valid code section.")
            return False

        self.code_section_index = pe.get_section_index(code_section)
        log.debug(f" --> {code_section.section_name} linked as main code section.")

        # Read the code section data
        code_off = pe.get_file_offset_from_rva(code_section.VirtualAddress)
        code_data = bytearray(pe.file_data[code_off:code_off + code_section.SizeOfRawData])

        # Skip encoding if the flag is not set
        if not (h.flags & DrmFlags20.UseEncodedCodeSection):
            log.debug(f" --> {code_section.section_name} section is not encoded.")
            return True

        log.debug(f" --> {code_section.section_name} section is encoded.")

        # Simple rolling XOR decode
        key = h.code_section_xor_key
        num_dwords = h.code_section_size >> 2
        offset = 0
        for _ in range(num_dwords):
            val = struct.unpack_from("<I", code_data, offset)[0]
            struct.pack_into("<I", code_data, offset, (val ^ key) & 0xFFFFFFFF)
            key = val
            offset += 4

        self.code_section_data = code_data
        return True

    # -- Step 3: Remove .bind section --

    def step3(self) -> bool:
        pe = self.pe

        if not self.options.get("keepbind", False):
            bind = pe.get_section(".bind")
            if bind is None:
                log.error("Could not find .bind section.")
                return False
            pe.remove_section(bind)
            log.debug(" --> .bind section was removed from the file.")
        else:
            log.debug(" --> .bind section was kept in the file.")

        do_realign = self.options.get("realign", False)
        pe.rebuild_sections(do_realign)

        return True

    # -- Step 4: Rebuild and save --

    def step4(self) -> bool:
        pe = self.pe
        h = self.header

        try:
            if self.options.get("zerodostub", False) and pe.dos_stub_size > 0:
                pe.dos_stub_data = b"\x00" * pe.dos_stub_size

            # OEP is a VA in the header
            oep_rva = pe.get_rva_from_va(h.oep)
            pe.set_entry_point(oep_rva & 0xFFFFFFFF)
            pe.set_checksum(0)

            if pe.sections:
                sa = pe.opt_section_alignment or 0x1000
                last = max(pe.sections, key=lambda s: s.VirtualAddress)
                pe.set_size_of_image(last.VirtualAddress + align(last.VirtualSize, sa))

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

    # -- Step 5: Checksum --

    def step5(self) -> bool:
        try:
            path = self.filepath + ".unpacked.exe"
            cs = pe_checksum(path)
            with open(path, "r+b") as f:
                data = f.read()
                e_lfanew = struct.unpack_from("<I", data, 60)[0]
                f.seek(e_lfanew + 4 + FILE_HEADER_SIZE + 64)
                f.write(struct.pack("<I", cs))
            log.info(" --> Unpacked file updated with new checksum!")
            return True
        except Exception as e:
            log.error(f" --> Error recalculating checksum: {e}")
            return False

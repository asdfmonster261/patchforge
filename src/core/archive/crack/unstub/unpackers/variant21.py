# vendored from steamarchiver/crack/unstub/ — DO NOT MODIFY.
# This implements SteamStub DRM unpacking via low-level PE/x86/RC4 surgery.
# Re-vendor the file from upstream when fixing variant bugs.

"""
SteamStub Variant 2.1 Unpacker (x86 only).

This variant is architecturally different from v3.x:
- The DRM header location/size/XOR key are extracted by disassembling the EP code.
- The AES key, IV, stolen bytes, and OEP are NOT in the header — they are extracted
  from the decrypted SteamDRMP.dll file via pattern scanning + offset tables.
- The pipeline is: EP disasm -> header -> payload -> DRMP decrypt -> DRMP scan -> code decrypt.
"""

import logging
import os
import struct
from typing import List, Optional

from ..base_unpacker import SteamStubDrmFlags
from ..crypto import steam_xor, steam_drmp_decrypt, aes_decrypt_cbc, aes_rebuild_iv
from ..pe import PeFile, FILE_HEADER_SIZE
from ..utils import align, find_pattern, pe_checksum
from ..x86 import disassemble_ep_v21, get_drmp_offsets_dynamic
from . import register

log = logging.getLogger("unstub")


# ============================================================================
# DRM header
# ============================================================================

def _u32(d, off):
    return struct.unpack_from("<I", d, off)[0]


class SteamStub21Header:
    """DRM header for Variant 2.1. Two layout variants based on size."""

    def __init__(self, data: bytes, total_size: int, is_d0_variant: bool):
        self.total_size = total_size
        self.is_d0_variant = is_d0_variant
        off = 0

        self.xor_key                    = _u32(data, off); off += 4
        self.get_module_handle_a        = _u32(data, off); off += 4
        self.get_module_handle_w        = _u32(data, off); off += 4
        self.get_proc_address           = _u32(data, off); off += 4
        self.load_library_a             = _u32(data, off); off += 4

        if not is_d0_variant:
            self.load_library_w         = _u32(data, off); off += 4
        else:
            self.load_library_w         = 0

        self.bind_section_va            = _u32(data, off); off += 4
        self.bind_start_function_size   = _u32(data, off); off += 4
        self.payload_key_match          = _u32(data, off); off += 4
        self.payload_data_va            = _u32(data, off); off += 4
        self.payload_data_size          = _u32(data, off); off += 4
        self.steam_app_id               = _u32(data, off); off += 4
        self.unknown0001                = _u32(data, off); off += 4
        self.steam_app_id_string        = data[off:off+8];  off += 8
        self.drmp_dll_va_offset         = _u32(data, off); off += 4
        self.drmp_dll_size_offset       = _u32(data, off); off += 4
        self.xtea_keys_offset           = _u32(data, off); off += 4

        self.stub_data = data[off:]


# ============================================================================
# DRMP offset extraction (static)
# ============================================================================

def _get_drmp_offsets_static(data: bytes, use_fallback: bool) -> List[int]:
    """
    Extract 8 parameter offsets from a SteamDRMP.dll code block using
    hardcoded byte positions.

    Returns list of 8 offsets:
      0=Flags, 1=AppId, 2=OEP, 3=CodeVA, 4=CodeSize, 5=AESKey, 6=AESIV, 7=StolenBytes
    """
    if use_fallback:
        off0, off1, off2, off3, off4, off5, off6 = 2, 14, 25, 36, 47, 61, 72
    else:
        off0, off1, off2, off3, off4, off5, off6 = 2, 14, 26, 38, 50, 62, 67

    offsets = [
        struct.unpack_from("<i", data, off0)[0],
        struct.unpack_from("<i", data, off1)[0],
        struct.unpack_from("<i", data, off2)[0],
        struct.unpack_from("<i", data, off3)[0],
        struct.unpack_from("<i", data, off4)[0],
        struct.unpack_from("<i", data, off5)[0],
    ]

    aes_iv_offset = struct.unpack_from("<i", data, off6)[0]
    offsets.append(aes_iv_offset)
    offsets.append(aes_iv_offset + 16)

    return offsets


# ============================================================================
# Detection & DRMP patterns
# ============================================================================

_V21_BIND_PATTERN = "53 51 52 56 57 55 8B EC 81 EC 00 10 00 00 C7"

_DRMP_PATTERNS = [
    ("8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? "
     "8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? "
     "8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8D ?? ?? ?? ?? ?? 05", False),
    ("8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? "
     "8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B", False),
    ("8B ?? ?? ?? ?? ?? 89 ?? ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? A3 ?? ?? ?? ?? "
     "8B ?? ?? ?? ?? ?? A3 ?? ?? ?? ?? 8B ?? ?? ?? ?? ?? A3 ?? ?? ?? ?? 8B", True),
]


# ============================================================================
# Unpacker
# ============================================================================

@register
class Variant21Unpacker:
    """
    SteamStub Variant 2.1 Unpacker (x86 only).

    Does NOT inherit BaseUnpacker — the pipeline is fundamentally different.
    """

    def __init__(self, filepath: str, options: dict):
        self.filepath = filepath
        self.options = options
        self.pe: Optional[PeFile] = None
        self.header: Optional[SteamStub21Header] = None
        self.xor_key: int = 0
        self.payload_data: Optional[bytearray] = None
        self.drmp_data: Optional[bytearray] = None
        self.drmp_offsets: List[int] = []
        self.use_fallback_offsets: bool = False
        self.code_section_index: int = -1
        self.code_section_data: Optional[bytearray] = None

    @property
    def name(self) -> str:
        return "SteamStub Variant 2.1 (x86)"

    def can_process(self) -> bool:
        try:
            pe = PeFile(self.filepath)
            if not pe.parse() or pe.is_64bit or not pe.has_section(".bind"):
                return False
            bind = pe.get_section_data(".bind")
            return bind is not None and find_pattern(bind, _V21_BIND_PATTERN) != -1
        except Exception:
            return False

    def process(self) -> bool:
        self.xor_key = 0
        self.payload_data = None
        self.drmp_data = None
        self.drmp_offsets = []
        self.use_fallback_offsets = False
        self.code_section_index = -1
        self.code_section_data = None

        self.pe = PeFile(self.filepath)
        if not self.pe.parse():
            log.error("Failed to parse PE file.")
            return False

        log.info(f"File is packed with {self.name}!")

        steps = [
            ("Step 1 - Read, disassemble and decode the SteamStub DRM header.", self.step1),
            ("Step 2 - Read, decode and process the payload data.", self.step2),
            ("Step 3 - Read, decode and dump the SteamDRMP.dll file.", self.step3),
            ("Step 4 - Scan, dump and pull needed offsets from within the SteamDRMP.dll file.", self.step4),
            ("Step 5 - Read, decrypt and process the main code section.", self.step5),
            ("Step 6 - Rebuild and save the unpacked file.", self.step6),
        ]
        if self.options.get("recalcchecksum", False):
            steps.append(("Step 7 - Rebuild unpacked file checksum.", self.step7))

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
        result = disassemble_ep_v21(ep_code)
        if result is None:
            log.error("Failed to extract DRM parameters from EP code.")
            return False

        struct_va, struct_size, struct_xor_key = result
        struct_rva = struct_va - pe.opt_image_base
        log.debug(f" --> Header VA=0x{struct_va:08X} Size=0x{struct_size:X} XorKey=0x{struct_xor_key:08X}")

        file_off = pe.get_file_offset_from_rva(struct_rva)
        if file_off < 0:
            return False

        header_data = bytearray(pe.file_data[file_off:file_off + struct_size])
        self.xor_key = steam_xor(header_data, struct_size, struct_xor_key)

        is_d0 = (struct_size // 4) == 0xD0
        self.header = SteamStub21Header(bytes(header_data), struct_size, is_d0)
        return True

    # -- Step 2: Decode payload --

    def step2(self) -> bool:
        pe = self.pe
        h = self.header
        payload_rva = pe.get_rva_from_va(h.payload_data_va)
        payload_off = pe.get_file_offset_from_rva(payload_rva)
        payload = bytearray(pe.file_data[payload_off:payload_off + h.payload_data_size])
        self.xor_key = steam_xor(payload, h.payload_data_size, self.xor_key)
        self.payload_data = payload

        if self.options.get("dumppayload", False):
            try:
                with open(self.filepath + ".payload", "wb") as f:
                    f.write(payload)
                log.debug(" --> Saved payload to disk!")
            except Exception:
                pass
        return True

    # -- Step 3: Extract and decrypt SteamDRMP.dll --

    def step3(self) -> bool:
        h = self.header
        pe = self.pe
        payload = self.payload_data
        log.debug(" --> File has SteamDRMP.dll file!")

        try:
            drmp_va = struct.unpack_from("<I", payload, h.drmp_dll_va_offset)[0]
            drmp_size = struct.unpack_from("<I", payload, h.drmp_dll_size_offset)[0]
            drmp_rva = pe.get_rva_from_va(drmp_va)
            drmp_file_off = pe.get_file_offset_from_rva(drmp_rva)
            drmp_data = bytearray(pe.file_data[drmp_file_off:drmp_file_off + drmp_size])

            # XTEA keys from payload
            xtea_off = h.xtea_keys_offset
            num_keys = (len(payload) - xtea_off) // 4
            xtea_keys = [struct.unpack_from("<I", payload, xtea_off + i * 4)[0] for i in range(num_keys)]

            steam_drmp_decrypt(drmp_data, drmp_size, xtea_keys)
            self.drmp_data = drmp_data

            if self.options.get("dumpdrmp", False):
                try:
                    base_dir = os.path.dirname(self.filepath) or "."
                    with open(os.path.join(base_dir, "SteamDRMP.dll"), "wb") as f:
                        f.write(drmp_data)
                    log.debug(" --> Saved SteamDRMP.dll to disk!")
                except Exception:
                    pass
            return True
        except Exception as e:
            log.error(f" --> Error trying to decrypt the files SteamDRMP.dll data! {e}")
            return False

    # -- Step 4: Scan DRMP for parameter offsets --

    def step4(self) -> bool:
        drmp = self.drmp_data
        self.use_fallback_offsets = False

        drmp_offset = -1
        for pattern, is_fallback in _DRMP_PATTERNS:
            drmp_offset = find_pattern(drmp, pattern)
            if drmp_offset != -1:
                self.use_fallback_offsets = is_fallback
                break

        if drmp_offset == -1:
            log.error("Could not find offset block pattern in SteamDRMP.dll.")
            return False

        block_size = min(1024, len(drmp) - drmp_offset)
        drmp_block = bytes(drmp[drmp_offset:drmp_offset + block_size])

        if self.options.get("exp", False):
            offsets = get_drmp_offsets_dynamic(drmp_block)
        else:
            offsets = _get_drmp_offsets_static(drmp_block, self.use_fallback_offsets)

        if len(offsets) != 8:
            log.error(f"Expected 8 offsets from SteamDRMP.dll, got {len(offsets)}.")
            return False

        self.drmp_offsets = offsets
        log.debug(f" --> Extracted {len(offsets)} offsets from SteamDRMP.dll.")
        return True

    # -- Step 5: Decrypt code section --

    def step5(self) -> bool:
        pe = self.pe
        payload = self.payload_data
        offsets = self.drmp_offsets

        if not self.options.get("keepbind", False):
            bind = pe.get_section(".bind")
            if bind is None:
                log.error("Could not find .bind section.")
                return False
            pe.remove_section(bind)
            log.debug(" --> .bind section was removed from the file.")
        else:
            log.debug(" --> .bind section was kept in the file.")

        # Find the main code section from the DRMP offset
        code_va = struct.unpack_from("<I", payload, offsets[3])[0]
        code_rva = pe.get_rva_from_va(code_va)
        main_section = pe.get_owner_section(code_rva)

        if offsets[3] != 0 and main_section is not None:
            if main_section.PointerToRawData == 0 or main_section.SizeOfRawData == 0:
                log.error("Could not find valid code section.")
                return False

        # When the code section VA offset is 0 or doesn't resolve, the code
        # is not encrypted.  Fall back to the first section (.text).
        if main_section is None:
            main_section = pe.sections[0] if pe.sections else None
            if main_section is None:
                log.error("No sections in PE file.")
                return False

        log.debug(f" --> {main_section.section_name} linked as main code section.")
        self.code_section_index = pe.get_section_index(main_section)

        encrypted_size = 0
        flags = struct.unpack_from("<I", payload, offsets[0])[0]

        if flags & SteamStubDrmFlags.NoEncryption:
            log.debug(f" --> {main_section.section_name} section is not encrypted.")
            code_section_data = bytearray(pe.section_data[self.code_section_index])
        else:
            log.debug(f" --> {main_section.section_name} section is encrypted.")
            try:
                aes_key = bytes(payload[offsets[5]:offsets[5] + 32])
                aes_iv = bytes(payload[offsets[6]:offsets[6] + 16])
                code_stolen = bytes(payload[offsets[7]:offsets[7] + 16])
                encrypted_size = struct.unpack_from("<I", payload, offsets[4])[0]

                code_off = pe.get_file_offset_from_rva(main_section.VirtualAddress)
                encrypted = pe.file_data[code_off:code_off + encrypted_size]
                combined = bytearray(code_stolen) + bytearray(encrypted)

                rebuilt_iv = aes_rebuild_iv(aes_key, aes_iv)
                code_section_data = bytearray(aes_decrypt_cbc(bytes(combined), aes_key, rebuilt_iv))
            except Exception as e:
                log.error(f" --> Error trying to decrypt the files code section data! {e}")
                return False

        sdata = pe.section_data[self.code_section_index]
        copy_len = min(encrypted_size if encrypted_size > 0 else len(code_section_data), len(sdata))
        sdata[:copy_len] = code_section_data[:copy_len]
        self.code_section_data = sdata
        return True

    # -- Step 6: Rebuild and save --

    def step6(self) -> bool:
        pe = self.pe
        payload = self.payload_data
        offsets = self.drmp_offsets

        try:
            if self.options.get("zerodostub", False) and pe.dos_stub_size > 0:
                pe.dos_stub_data = b"\x00" * pe.dos_stub_size

            do_realign = self.options.get("realign", False)
            pe.rebuild_sections(do_realign)

            original_entry_va = struct.unpack_from("<I", payload, offsets[2])[0]
            original_entry_rva = pe.get_rva_from_va(original_entry_va)
            pe.set_entry_point(original_entry_rva & 0xFFFFFFFF)
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

    # -- Step 7: Checksum --

    def step7(self) -> bool:
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

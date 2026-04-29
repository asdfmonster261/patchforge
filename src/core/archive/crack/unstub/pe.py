# vendored from steamarchiver/crack/unstub/ — DO NOT MODIFY.
# This implements SteamStub DRM unpacking via low-level PE/x86/RC4 surgery.
# Re-vendor the file from upstream when fixing variant bugs.

"""
Minimal PE32 / PE64 parser.

Implements: DOS header, NT headers, sections, TLS callbacks, overlay detection.
"""

import logging
import struct
from typing import List, Optional

from .utils import align

log = logging.getLogger("unstub")

# ============================================================================
# Constants
# ============================================================================

IMAGE_DOS_SIGNATURE = 0x5A4D
IMAGE_NT_SIGNATURE = 0x00004550
IMAGE_FILE_MACHINE_I386 = 0x014C
IMAGE_FILE_MACHINE_AMD64 = 0x8664
IMAGE_NUMBEROF_DIRECTORY_ENTRIES = 16
IMAGE_DIRECTORY_ENTRY_TLS = 9

DOS_HEADER_SIZE = 64
FILE_HEADER_FMT = "<2H3I2H"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FMT)


# ============================================================================
# Section header
# ============================================================================

class SectionHeader:
    """IMAGE_SECTION_HEADER."""

    FMT = "<8sIIIIIIHHI"
    SIZE = struct.calcsize(FMT)

    def __init__(self, data: bytes, offset: int = 0):
        fields = struct.unpack_from(self.FMT, data, offset)
        self.Name: bytes = fields[0]
        self.VirtualSize: int = fields[1]
        self.VirtualAddress: int = fields[2]
        self.SizeOfRawData: int = fields[3]
        self.PointerToRawData: int = fields[4]
        self.PointerToRelocations: int = fields[5]
        self.PointerToLinenumbers: int = fields[6]
        self.NumberOfRelocations: int = fields[7]
        self.NumberOfLinenumbers: int = fields[8]
        self.Characteristics: int = fields[9]

    @property
    def section_name(self) -> str:
        return self.Name.rstrip(b"\x00").decode("ascii", errors="replace")

    def pack(self) -> bytes:
        return struct.pack(
            self.FMT,
            self.Name, self.VirtualSize, self.VirtualAddress,
            self.SizeOfRawData, self.PointerToRawData,
            self.PointerToRelocations, self.PointerToLinenumbers,
            self.NumberOfRelocations, self.NumberOfLinenumbers,
            self.Characteristics,
        )


# ============================================================================
# PE file
# ============================================================================

class PeFile:
    """
    Lightweight PE32/PE64 parser for reading, modifying, and rebuilding PE executables.
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.file_data: bytes = b""

        # DOS
        self.dos_header_bytes: bytes = b""
        self.dos_stub_data: bytes = b""
        self.dos_stub_size: int = 0

        # NT / File header
        self.nt_signature: int = 0
        self.file_header_machine: int = 0
        self.file_header_num_sections: int = 0
        self.file_header_time_date_stamp: int = 0
        self.file_header_pointer_to_symbol_table: int = 0
        self.file_header_number_of_symbols: int = 0
        self.file_header_size_of_optional_header: int = 0
        self.file_header_characteristics: int = 0
        self.is_64bit: bool = False

        # Optional header (kept as raw bytes for easy rewriting)
        self.optional_header_bytes: bytearray = bytearray()
        self.opt_address_of_entry_point: int = 0
        self.opt_image_base: int = 0
        self.opt_section_alignment: int = 0
        self.opt_file_alignment: int = 0
        self.opt_size_of_image: int = 0
        self.opt_size_of_headers: int = 0
        self.opt_checksum: int = 0
        self.opt_number_of_rva_and_sizes: int = 0
        self.opt_data_directories: list = []

        # Sections
        self.sections: List[SectionHeader] = []
        self.section_data: List[bytearray] = []

        # Overlay
        self.overlay_data: Optional[bytes] = None

        # TLS
        self.tls_callbacks: List[int] = []

        # Internal offsets
        self._nt_header_offset: int = 0
        self._optional_header_offset: int = 0
        self._sections_offset: int = 0

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse(self) -> bool:
        """Parse the PE file from disk.  Returns True on success."""
        try:
            with open(self.filepath, "rb") as f:
                self.file_data = f.read()
            return self._parse_internal()
        except Exception as e:
            log.error(f"Failed to parse PE: {e}")
            return False

    def _parse_internal(self) -> bool:
        data = self.file_data

        # --- DOS header ---
        if len(data) < DOS_HEADER_SIZE:
            return False
        if struct.unpack_from("<H", data, 0)[0] != IMAGE_DOS_SIGNATURE:
            return False
        self.dos_header_bytes = data[:DOS_HEADER_SIZE]
        e_lfanew = struct.unpack_from("<I", data, 60)[0]
        self._nt_header_offset = e_lfanew

        self.dos_stub_data = data[DOS_HEADER_SIZE:e_lfanew]
        self.dos_stub_size = len(self.dos_stub_data)

        # --- NT signature ---
        if len(data) < e_lfanew + 4:
            return False
        self.nt_signature = struct.unpack_from("<I", data, e_lfanew)[0]
        if self.nt_signature != IMAGE_NT_SIGNATURE:
            return False

        # --- File header ---
        fh_off = e_lfanew + 4
        fh = struct.unpack_from(FILE_HEADER_FMT, data, fh_off)
        (self.file_header_machine,
         self.file_header_num_sections,
         self.file_header_time_date_stamp,
         self.file_header_pointer_to_symbol_table,
         self.file_header_number_of_symbols,
         self.file_header_size_of_optional_header,
         self.file_header_characteristics) = fh
        self.is_64bit = self.file_header_machine == IMAGE_FILE_MACHINE_AMD64

        # --- Optional header ---
        self._optional_header_offset = fh_off + FILE_HEADER_SIZE
        opt_off = self._optional_header_offset
        opt_size = self.file_header_size_of_optional_header
        self.optional_header_bytes = bytearray(data[opt_off:opt_off + opt_size])

        if self.is_64bit:
            self.opt_address_of_entry_point = struct.unpack_from("<I", data, opt_off + 16)[0]
            self.opt_image_base = struct.unpack_from("<Q", data, opt_off + 24)[0]
            self.opt_section_alignment = struct.unpack_from("<I", data, opt_off + 32)[0]
            self.opt_file_alignment = struct.unpack_from("<I", data, opt_off + 36)[0]
            self.opt_size_of_image = struct.unpack_from("<I", data, opt_off + 56)[0]
            self.opt_size_of_headers = struct.unpack_from("<I", data, opt_off + 60)[0]
            self.opt_checksum = struct.unpack_from("<I", data, opt_off + 64)[0]
            self.opt_number_of_rva_and_sizes = struct.unpack_from("<I", data, opt_off + 108)[0]
            dd_off = opt_off + 112
        else:
            self.opt_address_of_entry_point = struct.unpack_from("<I", data, opt_off + 16)[0]
            self.opt_image_base = struct.unpack_from("<I", data, opt_off + 28)[0]
            self.opt_section_alignment = struct.unpack_from("<I", data, opt_off + 32)[0]
            self.opt_file_alignment = struct.unpack_from("<I", data, opt_off + 36)[0]
            self.opt_size_of_image = struct.unpack_from("<I", data, opt_off + 56)[0]
            self.opt_size_of_headers = struct.unpack_from("<I", data, opt_off + 60)[0]
            self.opt_checksum = struct.unpack_from("<I", data, opt_off + 64)[0]
            self.opt_number_of_rva_and_sizes = struct.unpack_from("<I", data, opt_off + 92)[0]
            dd_off = opt_off + 96

        self.opt_data_directories = []
        for i in range(min(self.opt_number_of_rva_and_sizes, IMAGE_NUMBEROF_DIRECTORY_ENTRIES)):
            rva, size = struct.unpack_from("<II", data, dd_off + i * 8)
            self.opt_data_directories.append((rva, size))

        # --- Sections ---
        self._sections_offset = opt_off + opt_size
        sec_off = self._sections_offset
        self.sections = []
        self.section_data = []

        # First pass: parse all section headers
        for i in range(self.file_header_num_sections):
            sh = SectionHeader(data, sec_off + i * SectionHeader.SIZE)
            self.sections.append(sh)

        # Build a sorted list of raw-data boundaries so we can read each
        # section's data up to the start of the next one.  
        raw_starts = sorted(
            [(s.PointerToRawData, idx) for idx, s in enumerate(self.sections)
             if s.PointerToRawData > 0 and s.SizeOfRawData > 0]
        )

        # Determine per-section read ranges
        section_read_end = {}
        for order, (rp, idx) in enumerate(raw_starts):
            if order + 1 < len(raw_starts):
                # Read up to the next section's start (preserves padding)
                section_read_end[idx] = raw_starts[order + 1][0]
            else:
                # Last section: read exactly SizeOfRawData (overlay follows)
                s = self.sections[idx]
                section_read_end[idx] = rp + s.SizeOfRawData

        # Second pass: read section data including inter-section padding
        for i, sh in enumerate(self.sections):
            if sh.SizeOfRawData > 0 and sh.PointerToRawData > 0:
                end = section_read_end.get(i, sh.PointerToRawData + sh.SizeOfRawData)
                sd = bytearray(data[sh.PointerToRawData:end])
            else:
                sd = bytearray()
            self.section_data.append(sd)

        # --- Overlay ---
        # Overlay starts after the last section's data (including any
        # alignment padding up to the next boundary).
        if self.sections and raw_starts:
            last_rp, last_idx = raw_starts[-1]
            last_sec = self.sections[last_idx]
            # The on-disk extent of the last section, including padding
            overlay_start = section_read_end[last_idx]
            # But clamp to actual file size and ensure we don't include
            # data that belongs to the last section itself
            if overlay_start < len(data):
                self.overlay_data = data[overlay_start:]
            else:
                self.overlay_data = None
        else:
            self.overlay_data = None

        # --- TLS ---
        self._parse_tls()

        return True

    def _parse_tls(self):
        self.tls_callbacks = []
        if IMAGE_DIRECTORY_ENTRY_TLS >= len(self.opt_data_directories):
            return
        tls_rva, tls_size = self.opt_data_directories[IMAGE_DIRECTORY_ENTRY_TLS]
        if tls_rva == 0 or tls_size == 0:
            return

        tls_offset = self.get_file_offset_from_rva(tls_rva)
        if tls_offset < 0:
            return

        try:
            if self.is_64bit:
                cb_va = struct.unpack_from("<Q", self.file_data, tls_offset + 24)[0]
            else:
                cb_va = struct.unpack_from("<I", self.file_data, tls_offset + 12)[0]
            if cb_va == 0:
                return

            cb_rva = cb_va - self.opt_image_base
            cb_offset = self.get_file_offset_from_rva(cb_rva)
            if cb_offset < 0:
                return

            ptr_size = 8 if self.is_64bit else 4
            ptr_fmt = "<Q" if self.is_64bit else "<I"
            pos = cb_offset
            while pos + ptr_size <= len(self.file_data):
                val = struct.unpack_from(ptr_fmt, self.file_data, pos)[0]
                if val == 0:
                    break
                self.tls_callbacks.append(val)
                pos += ptr_size
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Section helpers
    # ------------------------------------------------------------------

    def has_section(self, name: str) -> bool:
        return any(s.section_name == name for s in self.sections)

    def get_section(self, name: str) -> Optional[SectionHeader]:
        for s in self.sections:
            if s.section_name == name:
                return s
        return None

    def get_section_data(self, name: str) -> Optional[bytes]:
        for i, s in enumerate(self.sections):
            if s.section_name == name:
                return bytes(self.section_data[i])
        return None

    def get_section_index(self, section: SectionHeader) -> int:
        for i, s in enumerate(self.sections):
            if s is section:
                return i
        return -1

    def remove_section(self, section: SectionHeader) -> None:
        idx = self.get_section_index(section)
        if idx >= 0:
            self.sections.pop(idx)
            self.section_data.pop(idx)

    # ------------------------------------------------------------------
    # RVA / VA helpers
    # ------------------------------------------------------------------

    def get_file_offset_from_rva(self, rva: int) -> int:
        for s in self.sections:
            limit = s.VirtualAddress + max(s.VirtualSize, s.SizeOfRawData)
            if s.VirtualAddress <= rva < limit:
                return s.PointerToRawData + (rva - s.VirtualAddress)
        return -1

    def get_rva_from_va(self, va: int) -> int:
        return va - self.opt_image_base

    def get_owner_section(self, rva: int) -> Optional[SectionHeader]:
        for s in self.sections:
            limit = s.VirtualAddress + max(s.VirtualSize, s.SizeOfRawData)
            if s.VirtualAddress <= rva < limit:
                return s
        return None

    # ------------------------------------------------------------------
    # Optional-header patching (for output)
    # ------------------------------------------------------------------

    def set_entry_point(self, ep: int) -> None:
        self.opt_address_of_entry_point = ep
        struct.pack_into("<I", self.optional_header_bytes, 16, ep)

    def set_checksum(self, cs: int) -> None:
        self.opt_checksum = cs
        struct.pack_into("<I", self.optional_header_bytes, 64, cs)

    def set_size_of_image(self, size: int) -> None:
        self.opt_size_of_image = size
        struct.pack_into("<I", self.optional_header_bytes, 56, size)

    # ------------------------------------------------------------------
    # Section rebuilding
    # ------------------------------------------------------------------

    def rebuild_sections(self, realign: bool = True) -> None:
        """Recalculate section raw-data pointers after modifications."""
        if not realign:
            return

        fa = self.opt_file_alignment or 0x200
        headers_end = self._sections_offset + len(self.sections) * SectionHeader.SIZE
        current_offset = align(headers_end, fa)

        for i, s in enumerate(self.sections):
            s.PointerToRawData = current_offset
            if s.SizeOfRawData > 0:
                s.SizeOfRawData = align(len(self.section_data[i]), fa)
                pad = s.SizeOfRawData - len(self.section_data[i])
                if pad > 0:
                    self.section_data[i].extend(b"\x00" * pad)
            current_offset += s.SizeOfRawData

    # ------------------------------------------------------------------
    # File writing
    # ------------------------------------------------------------------

    def pack_file_header(self) -> bytes:
        """Serialize the COFF file header with the current section count."""
        return struct.pack(
            FILE_HEADER_FMT,
            self.file_header_machine,
            len(self.sections),
            self.file_header_time_date_stamp,
            self.file_header_pointer_to_symbol_table,
            self.file_header_number_of_symbols,
            self.file_header_size_of_optional_header,
            self.file_header_characteristics,
        )

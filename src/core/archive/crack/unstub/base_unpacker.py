# vendored from steamarchiver/crack/unstub/ — DO NOT MODIFY.
# This implements SteamStub DRM unpacking via low-level PE/x86/RC4 surgery.
# Re-vendor the file from upstream when fixing variant bugs.

"""
Abstract base class for all SteamStub variant unpackers.

Subclasses must implement:
    - ``name``              – human-readable name shown in logs.
    - ``can_process()``     – return True if *filepath* matches this variant.
    - ``header_size``       – size of the DRM header in bytes.
    - ``parse_header()``    – turn raw bytes into a header object.

The base class supplies the shared 7-step unpacking pipeline used by
Variant 3.0 and 3.1 (and likely future v3.x variants).  Steps that differ
between variants can be overridden individually.
"""

import logging
import os
import struct
from abc import ABC, abstractmethod
from enum import IntFlag
from typing import Optional

from .pe import PeFile, FILE_HEADER_SIZE
from .crypto import steam_xor, steam_drmp_decrypt, aes_decrypt_cbc, aes_rebuild_iv
from .utils import align, pe_checksum

log = logging.getLogger("unstub")


# ============================================================================
# Shared flags
# ============================================================================

class SteamStubDrmFlags(IntFlag):
    NoModuleVerification = 0x02
    NoEncryption = 0x04
    NoOwnershipCheck = 0x10
    NoDebuggerCheck = 0x20
    NoErrorDialog = 0x40


# ============================================================================
# Base unpacker
# ============================================================================

class BaseUnpacker(ABC):
    """
    Shared unpacking pipeline for SteamStub v3.x variants.

    Lifecycle::

        unpacker = SomeVariant(filepath, options)
        if unpacker.can_process():
            unpacker.process()
    """

    def __init__(self, filepath: str, options: dict):
        self.filepath = filepath
        self.options = options

        # Populated during process()
        self.pe: Optional[PeFile] = None
        self.header = None                  # Variant-specific header object
        self.xor_key: int = 0
        self.tls_as_oep: bool = False
        self.tls_oep_rva: int = 0
        self.code_section_index: int = -1
        self.code_section_data: Optional[bytearray] = None

    # ------------------------------------------------------------------
    # Abstract interface – subclasses MUST implement
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable unpacker name for log messages."""
        ...

    @abstractmethod
    def can_process(self) -> bool:
        """Return True if the file matches this variant."""
        ...

    @property
    @abstractmethod
    def header_size(self) -> int:
        """Size of the DRM header in bytes (e.g. 0xF0)."""
        ...

    @abstractmethod
    def parse_header(self, data: bytes) -> object:
        """
        Parse raw (already XOR-decoded) header bytes into a header object.

        The returned object must expose at least the following attributes::

            signature           : int
            bind_section_offset : int
            original_entry_point: int
            payload_size        : int
            drmp_dll_offset     : int
            drmp_dll_size       : int
            flags               : int
            code_section_va     : int
            code_section_raw_sz : int
            aes_key             : bytes
            aes_iv              : bytes
            code_section_stolen : bytes
            encryption_keys     : list[int]
        """
        ...

    # ------------------------------------------------------------------
    # Overridable constants
    # ------------------------------------------------------------------

    HEADER_SIGNATURE = 0xC0DEC0DF

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process(self) -> bool:
        """Run the full unpacking pipeline.  Returns True on success."""
        self.tls_as_oep = False
        self.tls_oep_rva = 0
        self.code_section_data = None
        self.code_section_index = -1
        self.xor_key = 0

        self.pe = PeFile(self.filepath)
        if not self.pe.parse():
            log.error("Failed to parse PE file.")
            return False

        log.info(f"File is packed with {self.name}!")

        steps = [
            ("Step 1 - Read, decode and validate the SteamStub DRM header.", self.step1),
            ("Step 2 - Read, decode and process the payload data.",          self.step2),
            ("Step 3 - Read, decode and dump the SteamDRMP.dll file.",       self.step3),
            ("Step 4 - Handle .bind section. Find code section.",            self.step4),
            ("Step 5 - Read, decrypt and process code section.",             self.step5),
            ("Step 6 - Rebuild and save the unpacked file.",                 self.step6),
        ]
        if self.options.get("recalcchecksum", False):
            steps.append(("Step 7 - Rebuild unpacked file checksum.", self.step7))

        for desc, fn in steps:
            log.info(desc)
            if not fn():
                return False

        return True

    # ------------------------------------------------------------------
    # Step implementations (override any that differ per variant)
    # ------------------------------------------------------------------

    def step1(self) -> bool:
        """Read, XOR-decode and validate the DRM header."""
        pe = self.pe
        hsz = self.header_size

        file_offset = pe.get_file_offset_from_rva(pe.opt_address_of_entry_point)
        if file_offset < 0 or file_offset < hsz:
            log.error("Could not resolve entry point file offset.")
            return False

        header_data = bytearray(pe.file_data[file_offset - hsz:file_offset])
        if len(header_data) < hsz:
            log.error("Not enough data for DRM header.")
            return False

        self.xor_key = steam_xor(header_data, hsz)
        self.header = self.parse_header(bytes(header_data))

        if self.header.signature == self.HEADER_SIGNATURE:
            return True

        # Retry with the first TLS callback as OEP
        if not pe.tls_callbacks:
            log.error("DRM header signature mismatch and no TLS callbacks.")
            return False

        tls_rva = pe.get_rva_from_va(pe.tls_callbacks[0])
        file_offset = pe.get_file_offset_from_rva(tls_rva)
        if file_offset < 0 or file_offset < hsz:
            log.error("Could not resolve TLS callback file offset.")
            return False

        header_data = bytearray(pe.file_data[file_offset - hsz:file_offset])
        if len(header_data) < hsz:
            log.error("Not enough data for DRM header at TLS callback.")
            return False

        self.xor_key = steam_xor(header_data, hsz)
        self.header = self.parse_header(bytes(header_data))

        if self.header.signature != self.HEADER_SIGNATURE:
            log.error("DRM header signature mismatch (TLS attempt also failed).")
            return False

        self.tls_as_oep = True
        self.tls_oep_rva = tls_rva
        return True

    def _bind_base_rva(self) -> int:
        """RVA of the .bind section start (accounts for TLS-as-OEP)."""
        if self.tls_as_oep:
            return self.tls_oep_rva - self.header.bind_section_offset
        return self.pe.opt_address_of_entry_point - self.header.bind_section_offset

    def step2(self) -> bool:
        """Decode the payload data."""
        h = self.header
        payload_size = (h.payload_size + 0x0F) & 0xFFFFFFF0
        if payload_size == 0:
            return True

        log.debug(" --> File has payload data!")
        payload_addr = self.pe.get_file_offset_from_rva(self._bind_base_rva())
        payload = bytearray(self.pe.file_data[payload_addr:payload_addr + payload_size])
        self.xor_key = steam_xor(payload, payload_size, self.xor_key)

        if self.options.get("dumppayload", False):
            try:
                out = self.filepath + ".payload"
                with open(out, "wb") as f:
                    f.write(payload)
                log.debug(f" --> Saved payload to: {out}")
            except Exception:
                pass
        return True

    def step3(self) -> bool:
        """Decrypt and optionally dump SteamDRMP.dll."""
        h = self.header
        if h.drmp_dll_size == 0:
            log.debug(" --> File does not contain a SteamDRMP.dll file.")
            return True

        log.debug(" --> File has SteamDRMP.dll file!")
        try:
            drmp_rva = self._bind_base_rva() + h.drmp_dll_offset
            drmp_addr = self.pe.get_file_offset_from_rva(drmp_rva)
            drmp_data = bytearray(self.pe.file_data[drmp_addr:drmp_addr + h.drmp_dll_size])
            steam_drmp_decrypt(drmp_data, h.drmp_dll_size, h.encryption_keys)

            if self.options.get("dumpdrmp", False):
                base_dir = os.path.dirname(self.filepath) or "."
                out = os.path.join(base_dir, "SteamDRMP.dll")
                with open(out, "wb") as f:
                    f.write(drmp_data)
                log.debug(f" --> Saved SteamDRMP.dll to: {out}")
            return True
        except Exception as e:
            log.error(f" --> Error decrypting SteamDRMP.dll: {e}")
            return False

    def step4(self) -> bool:
        """Remove .bind section (unless --keepbind) and locate the code section."""
        pe = self.pe
        h = self.header

        if not self.options.get("keepbind", False):
            bind = pe.get_section(".bind")
            if bind is None:
                log.error("Could not find .bind section.")
                return False
            pe.remove_section(bind)
            log.debug(" --> .bind section was removed from the file.")
        else:
            log.debug(" --> .bind section was kept in the file.")

        if h.flags & SteamStubDrmFlags.NoEncryption:
            return True

        code_sec = pe.get_owner_section(h.code_section_va)
        if code_sec is None or code_sec.PointerToRawData == 0 or code_sec.SizeOfRawData == 0:
            log.error("Could not find a valid code section.")
            return False

        self.code_section_index = pe.get_section_index(code_sec)
        return True

    def step5(self) -> bool:
        """AES-CBC decrypt the code section."""
        h = self.header
        if h.flags & SteamStubDrmFlags.NoEncryption:
            log.debug(" --> Code section is not encrypted.")
            return True

        try:
            sec = self.pe.sections[self.code_section_index]
            log.debug(f" --> {sec.section_name} linked as main code section.")
            log.debug(f" --> {sec.section_name} section is encrypted.")

            if sec.SizeOfRawData == 0:
                log.debug(f" --> {sec.section_name} section is empty; skipping decryption.")
                self.code_section_data = bytearray()
                return True

            # Build the combined buffer: stolen(16) + encrypted(CodeSectionRawSize)
            # CodeSectionRawSize from the DRM header — only these bytes were encrypted.
            raw_size = int(h.code_section_raw_sz)
            code_off = self.pe.get_file_offset_from_rva(sec.VirtualAddress)
            encrypted = self.pe.file_data[code_off:code_off + raw_size]

            combined = bytearray(h.code_section_stolen) + bytearray(encrypted)

            # Rebuild the IV (AES-ECB decrypt) before CBC decryption
            rebuilt_iv = aes_rebuild_iv(h.aes_key, h.aes_iv)

            # AES-CBC decrypt
            decrypted = aes_decrypt_cbc(bytes(combined), h.aes_key, rebuilt_iv)

            # Merge into the section data — only overwrite CodeSectionRawSize bytes.
            # Any trailing bytes (SizeOfRawData - CodeSectionRawSize) stay as-is.
            sdata = self.pe.section_data[self.code_section_index]
            copy_len = min(raw_size, len(sdata), len(decrypted))
            sdata[:copy_len] = decrypted[:copy_len]
            self.code_section_data = sdata
            return True
        except Exception as e:
            log.error(f" --> Error decrypting code section: {e}")
            return False

    def step6(self) -> bool:
        """Rebuild and write the unpacked PE.

        - DontRealignSections defaults to True (sections keep original offsets).
        - SizeOfImage is recalculated after .bind removal.
        - Section headers and data are written using seek-based interleaving.
        """
        pe = self.pe
        h = self.header

        try:
            # Zero the DOS stub if desired..
            if self.options.get("zerodostub", False) and pe.dos_stub_size > 0:
                pe.dos_stub_data = b"\x00" * pe.dos_stub_size

            # Rebuild sections only if explicitly requested (default: don't realign)
            do_realign = self.options.get("realign", False)
            pe.rebuild_sections(do_realign)

            # Recalculate SizeOfImage based on remaining sections
            if pe.sections:
                sa = pe.opt_section_alignment or 0x1000
                last = max(pe.sections, key=lambda s: s.VirtualAddress)
                new_size_of_image = last.VirtualAddress + align(last.VirtualSize, sa)
                pe.set_size_of_image(new_size_of_image)

            # Update the optional header fields
            pe.set_entry_point(int(h.original_entry_point) & 0xFFFFFFFF)
            pe.set_checksum(0)

            unpacked_path = self.filepath + ".unpacked.exe"

            with open(unpacked_path, "wb") as f:
                # Write the DOS header..
                f.write(pe.dos_header_bytes)

                # Write the DOS stub..
                if pe.dos_stub_size > 0:
                    f.write(pe.dos_stub_data)

                # Write the NT headers (signature + file header + optional header)..
                f.write(struct.pack("<I", pe.nt_signature))
                f.write(pe.pack_file_header())
                f.write(bytes(pe.optional_header_bytes))

                # For each section: write header sequentially, then seek to
                # PointerToRawData to write data, then seek back.
                for i in range(len(pe.sections)):
                    section = pe.sections[i]

                    # Write section header at current position..
                    f.write(section.pack())

                    # Save position after the header..
                    header_resume_pos = f.tell()

                    # Seek to section's raw data offset and write data there..
                    f.seek(section.PointerToRawData)
                    if i == self.code_section_index and self.code_section_data is not None:
                        f.write(bytes(self.code_section_data))
                    else:
                        f.write(bytes(pe.section_data[i]))

                    # Seek back to continue writing headers..
                    f.seek(header_resume_pos)

                # Seek to end of file for overlay..
                f.seek(0, 2)

                # Write overlay data if present..
                if pe.overlay_data:
                    f.write(pe.overlay_data)

            log.info(f" --> Unpacked file saved to disk!")
            log.info(f" --> File Saved As: {unpacked_path}")
            return True
        except Exception as e:
            log.error(f" --> Error saving unpacked file: {e}")
            return False

    def step7(self) -> bool:
        """Recalculate the PE checksum."""
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

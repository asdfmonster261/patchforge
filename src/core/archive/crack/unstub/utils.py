# vendored from steamarchiver/crack/unstub/ — DO NOT MODIFY.
# This implements SteamStub DRM unpacking via low-level PE/x86/RC4 surgery.
# Re-vendor the file from upstream when fixing variant bugs.

"""
Utility helpers: pattern matching, alignment, PE checksum.
"""

import struct

FILE_HEADER_SIZE = struct.calcsize("<2H3I2H")


def find_pattern(data: bytes, pattern_str: str) -> int:
    """
    Find a byte pattern with ?? wildcards in data.

    Args:
        data: The byte buffer to search.
        pattern_str: Space-separated hex bytes, e.g. "E8 00 ?? ?? 00".

    Returns:
        Offset of the first match, or -1 if not found.
    """
    parts = pattern_str.strip().split()
    pattern_bytes = []
    mask = []
    for p in parts:
        if p == "??":
            pattern_bytes.append(0)
            mask.append(False)
        else:
            pattern_bytes.append(int(p, 16))
            mask.append(True)

    plen = len(pattern_bytes)
    for i in range(len(data) - plen + 1):
        match = True
        for j in range(plen):
            if mask[j] and data[i + j] != pattern_bytes[j]:
                match = False
                break
        if match:
            return i
    return -1


def align(value: int, alignment: int) -> int:
    """Round *value* up to the nearest multiple of *alignment*."""
    return (value + alignment - 1) & ~(alignment - 1)


def pe_checksum(filepath: str) -> int:
    """
    Calculate a PE file checksum (equivalent to MapFileAndCheckSum).
    """
    with open(filepath, "rb") as f:
        data = bytearray(f.read())

    e_lfanew = struct.unpack_from("<I", data, 60)[0]
    cs_off = e_lfanew + 4 + FILE_HEADER_SIZE + 64
    struct.pack_into("<I", data, cs_off, 0)

    checksum = 0
    top = 0x1_0000_0000
    size = len(data)
    remainder = size % 4

    for i in range(0, size - remainder, 4):
        val = struct.unpack_from("<I", data, i)[0]
        checksum = (checksum + val) % top
        checksum = (checksum >> 16) + (checksum & 0xFFFF)

    if remainder:
        val = int.from_bytes(data[size - remainder:], "little")
        checksum += val
        checksum = (checksum >> 16) + (checksum & 0xFFFF)

    checksum = (checksum >> 16) + (checksum & 0xFFFF)
    checksum += size
    return checksum & 0xFFFFFFFF

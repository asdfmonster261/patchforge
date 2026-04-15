"""File verification — CRC32C, MD5, and filesize."""

import hashlib
import struct
from pathlib import Path

METHODS = ["crc32c", "md5", "filesize"]


def _crc32c(path: Path) -> str:
    """CRC32C via hardware intrinsic if available, else pure-Python fallback."""
    try:
        import crcmod
        crc_fn = crcmod.predefined.mkCrcFun("crc-32c")
        buf = bytearray(1 << 20)
        crc = 0
        with open(path, "rb") as f:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                crc = crc_fn(buf[:n], crc)
        return format(crc & 0xFFFFFFFF, "08x")
    except ImportError:
        pass

    # Pure-Python CRC32C using the Castagnoli polynomial
    poly = 0x82F63B78
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ (poly if crc & 1 else 0)
        table.append(crc)

    crc = 0xFFFFFFFF
    buf = bytearray(1 << 20)
    with open(path, "rb") as f:
        while True:
            n = f.readinto(buf)
            if not n:
                break
            for b in buf[:n]:
                crc = (crc >> 8) ^ table[(crc ^ b) & 0xFF]
    return format((crc ^ 0xFFFFFFFF) & 0xFFFFFFFF, "08x")


def compute(path: Path, method: str) -> str:
    """Return checksum string for path using the given method."""
    if method == "crc32c":
        return _crc32c(path)
    elif method == "md5":
        h = hashlib.md5()
        buf = bytearray(1 << 20)
        with open(path, "rb") as f:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                h.update(buf[:n])
        return h.hexdigest()
    elif method == "filesize":
        return str(path.stat().st_size)
    else:
        raise ValueError(f"Unknown verification method: {method!r}")


def verify(path: Path, method: str, expected: str) -> bool:
    """Return True if file matches expected checksum."""
    return compute(path, method) == expected

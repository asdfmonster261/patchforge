"""File verification — CRC32C, MD5, and filesize."""

import hashlib
from pathlib import Path

import crcmod

_crc32c_fn = crcmod.predefined.mkCrcFun("crc-32c")


def _crc32c(path: Path) -> str:
    """Streaming CRC32C using crcmod (C-accelerated)."""
    buf = bytearray(1 << 20)
    crc = 0
    with open(path, "rb") as f:
        while True:
            n = f.readinto(buf)
            if not n:
                break
            crc = _crc32c_fn(buf[:n], crc)
    return format(crc & 0xFFFFFFFF, "08x")


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

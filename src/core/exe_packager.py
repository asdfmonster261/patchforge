"""
exe_packager.py — Append patch data + JSON metadata to a prebuilt Win32 stub.

Output format (appended to stub exe):
  [patch data bytes         ]
  [JSON metadata, UTF-8     ]
  [metadata length, 4 bytes LE]
  ["XPATCH01", 8 bytes      ]
"""

import json
import struct
from pathlib import Path

MAGIC = b"XPATCH01"
STUB_DIR = Path(__file__).parent.parent.parent / "stub" / "prebuilt"


def _stub_path(engine: str, arch: str, compression: str) -> Path:
    """Return the correct prebuilt stub for the given engine/arch/compression."""
    needs_full = False
    if engine == "hdiffpatch":
        needs_full = compression in {"zip/1", "zip/9", "bzip/5", "bzip/9"}
        variant = "full_" if needs_full else ""
        name = f"hdiffpatch_{variant}{arch}.exe"
    else:
        name = f"{engine}_{arch}.exe"

    p = STUB_DIR / name
    if not p.exists():
        make_target = "full" if needs_full else "all"
        raise FileNotFoundError(
            f"Prebuilt stub not found: {p}\n"
            f"Run 'make {make_target}' in stub/ to build it."
        )
    return p


def package(
    stub_engine: str,
    arch: str,
    compression: str,
    patch_data: bytes,
    metadata: dict,
    output_path: Path,
    icon_path: Path | None = None,
) -> Path:
    """
    Build the output .exe by appending patch_data and metadata to the stub.

    metadata must include all fields expected by stub_common.h (app_name,
    version, engine, compression, verify_method, orig_checksum, new_checksum,
    orig_size, new_size, find_method, registry_key, registry_value,
    ini_path, ini_section, ini_key).

    patch_data_offset and patch_data_size are computed here and injected.
    """
    stub_path = _stub_path(stub_engine, arch, compression)
    stub_bytes = stub_path.read_bytes()

    if icon_path is not None:
        from . import pe_icon
        stub_bytes = pe_icon.inject(stub_bytes, Path(icon_path))

    patch_data_offset = len(stub_bytes)
    patch_data_size = len(patch_data)

    meta = dict(metadata)
    meta["patch_data_offset"] = patch_data_offset
    meta["patch_data_size"] = patch_data_size

    meta_json = json.dumps(meta, separators=(",", ":")).encode("utf-8")
    meta_len = struct.pack("<I", len(meta_json))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as f:
        f.write(stub_bytes)
        f.write(patch_data)
        f.write(meta_json)
        f.write(meta_len)
        f.write(MAGIC)

    return output_path

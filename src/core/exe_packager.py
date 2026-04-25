"""
exe_packager.py — Append patch data + JSON metadata to a prebuilt Win32 stub.

Output format (appended to stub exe):
  [patch data bytes                  ]
  [extra_file_0 bytes                ] \
  [extra_file_1 bytes                ]  > zero or more extra files
  ...                                  /
  [backdrop image bytes              ]  (zero bytes if no backdrop)
  [JSON metadata, UTF-8              ]
  [metadata length, 4 bytes LE       ]
  ["XPATCH01", 8 bytes               ]

The JSON metadata contains:
  patch_data_offset  — byte offset of patch data in this exe
  patch_data_size    — byte length of patch data
  backdrop_offset    — byte offset of backdrop blob (0 if none)
  backdrop_size      — byte length of backdrop blob (0 if none)
  extra_files        — [{dest, offset, size}, ...] (absent if empty)
  ...plus all the patching-behaviour fields
"""

import json
import os
import re
import struct
import zlib
from pathlib import Path

MAGIC        = b"XPATCH01"
REPACK_MAGIC = b"XPACK01\x00"
STUB_DIR = Path(__file__).parent.parent.parent / "stub" / "prebuilt"


def _copy_with_crc(src_path: Path, dst_f, chunk_size: int = 1024 * 1024) -> int:
    """Stream src_path into dst_f (open file), returning its CRC32. Used
    instead of shutil.copyfileobj so the CRC comes for free during the copy."""
    crc = 0
    with open(src_path, "rb") as src:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            dst_f.write(chunk)
    return crc & 0xFFFFFFFF


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
    extra_files: list | None = None,   # [{dest: str, data: bytes}, ...]
    backdrop_data: bytes | None = None,
) -> Path:
    """
    Build the output .exe by appending patch_data, optional extra files,
    optional backdrop image, and metadata JSON to the stub.
    """
    stub_path = _stub_path(stub_engine, arch, compression)
    stub_bytes = stub_path.read_bytes()

    # Minimal PE sanity check — catches a corrupted or wrong-arch stub early
    if len(stub_bytes) < 0x40:
        raise ValueError(f"Stub file is too small to be a valid PE: {stub_path}")
    e_lfanew = struct.unpack_from("<I", stub_bytes, 0x3C)[0]
    if e_lfanew + 4 > len(stub_bytes) or stub_bytes[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise ValueError(f"Stub file does not have a valid PE signature: {stub_path}")

    if icon_path is not None:
        from . import pe_icon
        stub_bytes = pe_icon.inject(stub_bytes, Path(icon_path))

    patch_data_offset = len(stub_bytes)
    patch_data_size   = len(patch_data)

    # Lay out extra files immediately after patch data
    ef_base = patch_data_offset + patch_data_size
    ef_meta = []
    ef_cursor = ef_base
    if extra_files:
        for ef in extra_files:
            ef_meta.append({
                "dest":   ef["dest"],
                "offset": ef_cursor,
                "size":   len(ef["data"]),
            })
            ef_cursor += len(ef["data"])

    # Backdrop follows extra files
    bd_offset = ef_cursor
    bd_size   = len(backdrop_data) if backdrop_data else 0

    # Build metadata dict
    meta = dict(metadata)
    meta["patch_data_offset"] = patch_data_offset
    meta["patch_data_size"]   = patch_data_size
    meta["backdrop_offset"]   = bd_offset
    meta["backdrop_size"]     = bd_size
    if ef_meta:
        meta["extra_files"] = ef_meta

    meta_json = json.dumps(meta, separators=(",", ":")).encode("utf-8")
    meta_len  = struct.pack("<I", len(meta_json))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as f:
        f.write(stub_bytes)
        f.write(patch_data)
        if extra_files:
            for ef in extra_files:
                f.write(ef["data"])
        if backdrop_data:
            f.write(backdrop_data)
        f.write(meta_json)
        f.write(meta_len)
        f.write(MAGIC)

    return output_path


# ---------------------------------------------------------------------------
# Repack packaging
# ---------------------------------------------------------------------------

def _installer_stub_path(arch: str) -> Path:
    name = f"installer_{arch}.exe"
    p = STUB_DIR / name
    if not p.exists():
        raise FileNotFoundError(
            f"Prebuilt installer stub not found: {p}\n"
            f"Run 'make installer' in stub/ to build it."
        )
    return p


def _uninstaller_stub_path(arch: str) -> Path:
    name = f"uninstaller_{arch}.exe"
    p = STUB_DIR / name
    if not p.exists():
        raise FileNotFoundError(
            f"Prebuilt uninstaller stub not found: {p}\n"
            f"Run 'make uninstaller' in stub/ to build it."
        )
    return p


def _make_arp_subkey(app_name: str) -> str:
    """Sanitize app_name for use as a Windows registry subkey name."""
    key = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', app_name).strip()
    return key or "Game"


def _build_uninstaller_blob(
    arch: str,
    arp_subkey: str,
    metadata: dict,
    file_list: list[dict],
    icon_path: Path | None = None,
) -> bytes:
    """
    Append data JSON + trailer to the prebuilt uninstaller stub.

    Layout:
      [uninstaller_stub.exe]
      [data JSON, UTF-8    ]
      [4B LE: data_len     ]
      [8B magic: UNINST01  ]
    """
    stub = _uninstaller_stub_path(arch).read_bytes()

    if icon_path is not None:
        from . import pe_icon
        stub = pe_icon.inject(stub, Path(icon_path))

    data = {
        "app_name":             metadata.get("app_name", ""),
        "version":              metadata.get("version", ""),
        "company_info":         metadata.get("company_info", ""),
        "arp_subkey":           arp_subkey,
        "install_registry_key": metadata.get("install_registry_key", ""),
        "shortcut_name":             metadata.get("shortcut_name", "") or metadata.get("app_name", ""),
        "shortcut_create_desktop":   metadata.get("shortcut_create_desktop", False),
        "shortcut_create_startmenu": metadata.get("shortcut_create_startmenu", False),
        "files": [
            {"path": f["path"], "component": f["component"]}
            for f in file_list
        ],
    }
    data_json = json.dumps(data, separators=(",", ":")).encode("utf-8")
    data_len  = struct.pack("<I", len(data_json))
    return stub + data_json + data_len + b"UNINST01"


_BIN_FILENAME = "base_game.bin"


def package_repack(
    arch: str,
    pack_blob_path: Path,
    metadata: dict,
    output_path: Path,
    file_list: list[dict] | None = None,
    icon_path: Path | None = None,
    backdrop_data: bytes | None = None,
    include_uninstaller: bool = True,
    split_bin: bool = False,
) -> tuple[Path, Path | None]:
    """
    Build a self-extracting installer .exe.

    pack_blob_path is a Path to the XPACK01 temp file produced by
    xpack_archive.build(); it is stream-copied so the compressed game data
    never needs to live in process memory.

    Single-file layout (split_bin=False):
      [installer_stub.exe  ]
      [XPACK01 blob        ]
      [uninstaller blob    ]
      [backdrop bytes      ]
      [JSON metadata       ]
      [4B LE: meta_len     ]
      [8B magic XPACK01\\x00]

    Split layout (split_bin=True):
      exe: [installer_stub.exe][uninstaller blob][backdrop bytes][metadata][magic]
      base_game.bin: [XPACK01 blob]

    Returns (exe_path, bin_path) where bin_path is None in single-file mode.
    """
    stub_path = _installer_stub_path(arch)
    stub_bytes = stub_path.read_bytes()

    if len(stub_bytes) < 0x40:
        raise ValueError(f"Stub file is too small to be a valid PE: {stub_path}")
    e_lfanew = struct.unpack_from("<I", stub_bytes, 0x3C)[0]
    if e_lfanew + 4 > len(stub_bytes) or stub_bytes[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise ValueError(f"Stub does not have a valid PE signature: {stub_path}")

    if icon_path is not None:
        from . import pe_icon
        stub_bytes = pe_icon.inject(stub_bytes, Path(icon_path))

    pack_data_size = Path(pack_blob_path).stat().st_size

    arp_subkey = _make_arp_subkey(metadata.get("app_name", ""))
    uninst_blob = b""
    if include_uninstaller and file_list is not None:
        uninst_blob = _build_uninstaller_blob(arch, arp_subkey, metadata, file_list, icon_path)
    uninst_size = len(uninst_blob)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if split_bin:
        # Pack data goes to a separate file; exe contains only stub + uninst + backdrop.
        bin_path = output_path.parent / _BIN_FILENAME
        with open(bin_path, "wb") as bf:
            blob_crc = _copy_with_crc(Path(pack_blob_path), bf)

        pack_data_offset = 0           # offset within the bin file
        uninst_offset    = len(stub_bytes)
        bd_offset        = uninst_offset + uninst_size
        bd_size          = len(backdrop_data) if backdrop_data else 0

        meta = dict(metadata)
        meta["bin_file"]            = _BIN_FILENAME
        meta["pack_data_offset"]    = pack_data_offset
        meta["pack_data_size"]      = pack_data_size
        meta["uninstaller_offset"]  = uninst_offset
        meta["uninstaller_size"]    = uninst_size
        meta["arp_subkey"]          = arp_subkey
        meta["backdrop_offset"]     = bd_offset
        meta["backdrop_size"]       = bd_size
        # Default bin_part_crcs covers the full blob. For multi-part builds
        # the repack_builder overwrites this via patch_repack_metadata() with
        # one CRC per split part.
        meta["bin_part_crcs"]       = [blob_crc]

        meta_json = json.dumps(meta, separators=(",", ":")).encode("utf-8")
        meta_len  = struct.pack("<I", len(meta_json))

        with open(output_path, "wb") as f:
            f.write(stub_bytes)
            f.write(uninst_blob)
            if backdrop_data:
                f.write(backdrop_data)
            f.write(meta_json)
            f.write(meta_len)
            f.write(REPACK_MAGIC)

        return output_path, bin_path

    # Single-file: pack data embedded immediately after stub.
    pack_data_offset = len(stub_bytes)
    uninst_offset    = pack_data_offset + pack_data_size
    bd_offset        = uninst_offset + uninst_size
    bd_size          = len(backdrop_data) if backdrop_data else 0

    # Write the exe; the blob CRC is computed during the embed copy.
    # We can't know the CRC until after the blob's been written, so
    # writing happens in two passes: body first (to collect CRC), then
    # re-open and seek to write final metadata.
    with open(output_path, "wb") as f:
        f.write(stub_bytes)
        blob_crc = _copy_with_crc(Path(pack_blob_path), f)
        f.write(uninst_blob)
        if backdrop_data:
            f.write(backdrop_data)

        meta = dict(metadata)
        meta["pack_data_offset"]    = pack_data_offset
        meta["pack_data_size"]      = pack_data_size
        meta["uninstaller_offset"]  = uninst_offset
        meta["uninstaller_size"]    = uninst_size
        meta["arp_subkey"]          = arp_subkey
        meta["backdrop_offset"]     = bd_offset
        meta["backdrop_size"]       = bd_size
        meta["bin_part_crcs"]       = [blob_crc]

        meta_json = json.dumps(meta, separators=(",", ":")).encode("utf-8")
        meta_len  = struct.pack("<I", len(meta_json))
        f.write(meta_json)
        f.write(meta_len)
        f.write(REPACK_MAGIC)

    return output_path, None


def patch_repack_metadata(exe_path: Path, extra_fields: dict) -> None:
    """
    Merge `extra_fields` into the JSON metadata embedded at the end of a
    repack exe (XPACK01 format). Streams the body to a temp file and uses
    os.replace() for an atomic swap, so a crash mid-rewrite leaves either
    the old exe or the new one intact — never a truncated hybrid.

    Used by the repack builder to inject bin_part_crcs after the split pass
    (which is where they're computed) without needing an extra read of the
    full blob before packaging. Memory usage is bounded by the streaming
    chunk size — safe for arbitrarily large exes.
    """
    exe_path = Path(exe_path)
    tmp_path = exe_path.with_suffix(exe_path.suffix + ".tmp")
    CHUNK = 1024 * 1024

    with open(exe_path, "rb") as src:
        src.seek(0, 2)
        total_size = src.tell()
        if total_size < 12:
            raise ValueError(f"Exe too short to be an XPACK01 file: {exe_path}")
        src.seek(total_size - 12)
        old_meta_len = struct.unpack("<I", src.read(4))[0]
        magic = src.read(8)
        if magic != REPACK_MAGIC:
            raise ValueError(f"Not an XPACK01 exe: {exe_path}")
        old_meta_start = total_size - 12 - old_meta_len
        if old_meta_start < 0:
            raise ValueError(f"Malformed metadata length in {exe_path}")
        src.seek(old_meta_start)
        old_meta = json.loads(src.read(old_meta_len))
        old_meta.update(extra_fields)
        new_meta = json.dumps(old_meta, separators=(",", ":")).encode("utf-8")

        # Stream the body into the temp file, then append the new tail.
        src.seek(0)
        try:
            with open(tmp_path, "wb") as dst:
                remaining = old_meta_start
                while remaining > 0:
                    n = min(CHUNK, remaining)
                    buf = src.read(n)
                    if not buf:
                        raise IOError(
                            f"Unexpected EOF rewriting {exe_path}: "
                            f"{remaining} bytes still expected"
                        )
                    dst.write(buf)
                    remaining -= len(buf)
                dst.write(new_meta)
                dst.write(struct.pack("<I", len(new_meta)))
                dst.write(REPACK_MAGIC)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    os.replace(tmp_path, exe_path)

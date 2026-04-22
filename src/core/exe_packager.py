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
import re
import shutil
import struct
from pathlib import Path

MAGIC        = b"XPATCH01"
REPACK_MAGIC = b"XPACK01\x00"
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


def package_repack(
    arch: str,
    pack_blob_path: Path,
    metadata: dict,
    output_path: Path,
    file_list: list[dict] | None = None,
    icon_path: Path | None = None,
    backdrop_data: bytes | None = None,
    include_uninstaller: bool = True,
) -> Path:
    """
    Build a self-extracting installer .exe.

    pack_blob_path is a Path to the XPACK01 temp file produced by
    xpack_archive.build(); it is stream-copied into the output exe so the
    compressed game data never needs to live in process memory.

    Output layout:
      [installer_stub.exe  ]
      [XPACK01 blob        ]
      [uninstaller blob    ]  (zero bytes if include_uninstaller=False)
      [backdrop bytes      ]  (zero bytes if no backdrop)
      [JSON metadata       ]
      [4B LE: meta_len     ]
      [8B magic XPACK01\\x00]
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

    pack_data_offset = len(stub_bytes)
    pack_data_size   = Path(pack_blob_path).stat().st_size

    # Build uninstaller blob (if requested)
    arp_subkey = _make_arp_subkey(metadata.get("app_name", ""))
    uninst_blob = b""
    if include_uninstaller and file_list is not None:
        uninst_blob = _build_uninstaller_blob(arch, arp_subkey, metadata, file_list, icon_path)

    uninst_offset = pack_data_offset + pack_data_size
    uninst_size   = len(uninst_blob)

    bd_offset = uninst_offset + uninst_size
    bd_size   = len(backdrop_data) if backdrop_data else 0

    meta = dict(metadata)
    meta["pack_data_offset"]    = pack_data_offset
    meta["pack_data_size"]      = pack_data_size
    meta["uninstaller_offset"]  = uninst_offset
    meta["uninstaller_size"]    = uninst_size
    meta["arp_subkey"]          = arp_subkey
    meta["backdrop_offset"]     = bd_offset
    meta["backdrop_size"]       = bd_size

    meta_json = json.dumps(meta, separators=(",", ":")).encode("utf-8")
    meta_len  = struct.pack("<I", len(meta_json))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as f:
        f.write(stub_bytes)
        with open(pack_blob_path, "rb") as blob_f:
            shutil.copyfileobj(blob_f, f)
        f.write(uninst_blob)
        if backdrop_data:
            f.write(backdrop_data)
        f.write(meta_json)
        f.write(meta_len)
        f.write(REPACK_MAGIC)

    return output_path

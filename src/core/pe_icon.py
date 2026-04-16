"""
pe_icon.py — Inject a .ico file as the application icon into a prebuilt Win32 stub.

The stubs ship without a .rsrc section.  This module:
  1. Parses the .ico file (supports multi-image icons).
  2. Builds a minimal .rsrc section (RT_ICON entries + RT_GROUP_ICON).
  3. Appends the new section to the stub and patches the PE headers.

Returns modified stub bytes ready for exe_packager to append patch data.
"""

import struct
from pathlib import Path

# Windows resource type IDs
_RT_ICON       = 3
_RT_GROUP_ICON = 14
_LANG_NEUTRAL  = 0x0000

# .rsrc section characteristics: initialized data, readable
_RSRC_CHARS = 0x40000040  # IMAGE_SCN_CNT_INITIALIZED_DATA | IMAGE_SCN_MEM_READ


def _align(n: int, align: int) -> int:
    return (n + align - 1) & ~(align - 1)


def _fix_and_mask_alpha(img: dict) -> dict:
    """
    Some icon tools produce 32bpp BMP icons where transparency is stored in
    the AND mask but alpha bytes are left at zero (or near-zero).  Windows
    Vista+ detects any non-zero alpha and uses the alpha channel instead of
    the AND mask, making most of the icon transparent and showing whatever
    background is behind it (typically white).

    Fix: if a 32bpp BMP image has near-zero average alpha, read the AND mask
    and use it to write proper alpha=0 (transparent) / alpha=255 (opaque)
    values.  The biHeight and data layout are left intact so Windows will
    detect the now-correct non-zero alpha and render via alpha channel.
    """
    data = img["data"]
    if len(data) < 40:
        return img

    # Skip PNG images — they carry their own alpha channel
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return img

    biSize, biWidth, biHeight, _planes, biBitCount, biCompression = \
        struct.unpack_from("<IiiHHI", data, 0)

    # Only process: 32bpp BI_RGB with positive height (has AND mask appended)
    if biBitCount != 32 or biCompression != 0 or biHeight <= 0:
        return img

    h = biHeight // 2   # actual pixel rows; biHeight includes AND mask rows
    w = biWidth
    if h <= 0 or w <= 0:
        return img

    pixel_start = biSize
    pixel_size  = h * w * 4
    if len(data) < pixel_start + pixel_size:
        return img

    pixels = bytearray(data[pixel_start:pixel_start + pixel_size])

    # If average alpha is already meaningful (>8 per pixel) leave it alone
    if sum(pixels[3::4]) > h * w * 8:
        return img

    # Locate AND mask (1bpp, DWORD-aligned rows, same bottom-up order)
    and_row_bytes = ((w + 31) // 32) * 4
    and_start     = pixel_start + pixel_size
    and_size      = h * and_row_bytes
    if len(data) < and_start + and_size:
        return img

    and_mask = data[and_start:and_start + and_size]

    # Rewrite alpha: AND bit 1 = transparent (0), AND bit 0 = opaque (255)
    for row in range(h):
        row_base = row * and_row_bytes
        for byte_idx in range((w + 7) // 8):
            m = and_mask[row_base + byte_idx]
            for bit in range(8):
                col = byte_idx * 8 + bit
                if col >= w:
                    break
                pixels[(row * w + col) * 4 + 3] = 0 if (m >> (7 - bit)) & 1 else 255

    new_data = data[:pixel_start] + bytes(pixels) + data[pixel_start + pixel_size:]
    return {**img, "data": new_data, "planes": 1, "bit_count": 32}


def _parse_ico(data: bytes) -> list[dict]:
    """Parse an .ico file and return list of image dicts."""
    if len(data) < 6:
        raise ValueError("Not a valid .ico file (too short)")
    reserved, img_type, count = struct.unpack_from("<HHH", data, 0)
    if img_type != 1:
        raise ValueError(f"Not an icon file (type={img_type})")
    images = []
    for i in range(count):
        off = 6 + i * 16
        w, h, color_count, res, planes, bit_count, byte_count, img_off = \
            struct.unpack_from("<BBBBHHiI", data, off)
        # width/height of 0 means 256
        img = {
            "width":       w,
            "height":      h,
            "color_count": color_count,
            "reserved":    res,
            "planes":      planes,
            "bit_count":   bit_count,
            "data":        data[img_off:img_off + byte_count],
        }
        images.append(_fix_and_mask_alpha(img))
    if not images:
        raise ValueError("Icon file contains no images")
    return images


def _build_rsrc(images: list[dict], section_rva: int) -> bytes:
    """
    Build the raw bytes of a .rsrc section containing RT_ICON + RT_GROUP_ICON.

    section_rva: the virtual address the section will occupy in memory
                 (needed so DATA_ENTRY RVAs are correct).
    """
    N = len(images)

    # ------------------------------------------------------------------ #
    # Layout (all offsets from section start):                            #
    #                                                                     #
    #  0x00  Root dir  (16) + 2 entries (16)              = 0x20         #
    #  0x20  RT_ICON dir (16) + N entries (N*8)           = 0x30+N*8    #
    #  0x30+N*8   N × (lang dir 16 + 1 entry 8)  = N*24                 #
    #  0x30+N*32  RT_GROUP_ICON dir (16) + 1 entry (8)   = 0x48+N*32   #
    #  0x48+N*32  group lang dir (16) + 1 entry (8)      = 0x60+N*32   #
    #  0x60+N*32  (N+1) × IMAGE_RESOURCE_DATA_ENTRY (16) = 0x70+N*48   #
    #  0x70+N*48  image[0] data (padded to 4)                           #
    #  ...        image[N-1] data                                       #
    #             GRPICONDIR (6 + N*14 bytes)                           #
    # ------------------------------------------------------------------ #

    ROOT_DIR       = 0x00
    ICON_DIR       = 0x20
    ICON_ENTRIES   = 0x30             # N × 8 bytes
    LANG_DIRS      = 0x30 + N * 8    # N × 24 bytes
    GROUP_DIR      = LANG_DIRS + N * 24
    GROUP_LANG_DIR = GROUP_DIR + 24
    DATA_ENTRIES   = GROUP_LANG_DIR + 24    # (N+1) × 16 bytes
    DATA_START     = DATA_ENTRIES + (N + 1) * 16

    # Image data offsets (4-byte aligned)
    img_offsets = []
    pos = DATA_START
    for img in images:
        img_offsets.append(pos)
        pos += _align(len(img["data"]), 4)
    grp_offset = pos
    grp_size   = 6 + N * 14
    total      = _align(pos + grp_size, 4)

    buf = bytearray(total)

    def w_dir(off, named=0, id_count=0):
        """Write IMAGE_RESOURCE_DIRECTORY (16 bytes)."""
        # Characteristics(4) + TimeDateStamp(4) + MajorVersion(2) + MinorVersion(2)
        # + NumberOfNamedEntries(2) + NumberOfIdEntries(2)
        struct.pack_into("<IIHHHH", buf, off, 0, 0, 0, 0, named, id_count)

    def w_entry(off, id_val, target_off, is_dir=False):
        """Write IMAGE_RESOURCE_DIRECTORY_ENTRY (8 bytes)."""
        flags = 0x80000000 if is_dir else 0
        struct.pack_into("<II", buf, off, id_val, target_off | flags)

    def w_data(off, rva, size):
        """Write IMAGE_RESOURCE_DATA_ENTRY (16 bytes)."""
        struct.pack_into("<IIII", buf, off, rva, size, 0, 0)

    # Root directory: 2 ID entries
    w_dir(ROOT_DIR, id_count=2)
    w_entry(0x10, _RT_ICON,       ICON_DIR,       is_dir=True)
    w_entry(0x18, _RT_GROUP_ICON, GROUP_DIR,       is_dir=True)

    # RT_ICON directory: N ID entries (IDs 1..N)
    w_dir(ICON_DIR, id_count=N)
    for i in range(N):
        lang_off = LANG_DIRS + i * 24
        w_entry(ICON_ENTRIES + i * 8, i + 1, lang_off, is_dir=True)

    # Language directories for each icon image
    for i in range(N):
        lang_off = LANG_DIRS + i * 24
        w_dir(lang_off, id_count=1)
        w_entry(lang_off + 16, _LANG_NEUTRAL, DATA_ENTRIES + i * 16, is_dir=False)

    # RT_GROUP_ICON directory: 1 entry (ID=1)
    w_dir(GROUP_DIR, id_count=1)
    w_entry(GROUP_DIR + 16, 1, GROUP_LANG_DIR, is_dir=True)

    # Group language directory
    w_dir(GROUP_LANG_DIR, id_count=1)
    w_entry(GROUP_LANG_DIR + 16, _LANG_NEUTRAL, DATA_ENTRIES + N * 16, is_dir=False)

    # DATA_ENTRY for each icon image
    for i, img in enumerate(images):
        w_data(DATA_ENTRIES + i * 16, section_rva + img_offsets[i], len(img["data"]))

    # DATA_ENTRY for GRPICONDIR
    w_data(DATA_ENTRIES + N * 16, section_rva + grp_offset, grp_size)

    # Raw image bytes
    for i, img in enumerate(images):
        buf[img_offsets[i]:img_offsets[i] + len(img["data"])] = img["data"]

    # GRPICONDIR  (6 bytes header + N × 14-byte entries)
    struct.pack_into("<HHH", buf, grp_offset, 0, 1, N)
    for i, img in enumerate(images):
        e = grp_offset + 6 + i * 14
        struct.pack_into("<BBBBHHIH", buf, e,
                         img["width"], img["height"], img["color_count"], img["reserved"],
                         img["planes"], img["bit_count"],
                         len(img["data"]),
                         i + 1)   # RT_ICON ID

    return bytes(buf)


def inject(pe_bytes: bytes, ico_path: Path) -> bytes:
    """
    Return pe_bytes with a .rsrc section added containing the icon at ico_path.

    Raises ValueError if the icon is invalid or the PE already has a .rsrc
    section (unsupported — stubs are built without one by design).
    """
    images = _parse_ico(Path(ico_path).read_bytes())

    # ------------------------------------------------------------------ #
    # Parse PE headers manually — avoids pefile dependency at runtime    #
    # ------------------------------------------------------------------ #
    buf = bytearray(pe_bytes)

    # DOS header → e_lfanew at offset 0x3C
    e_lfanew = struct.unpack_from("<I", buf, 0x3C)[0]
    pe_sig = buf[e_lfanew:e_lfanew + 4]
    if pe_sig != b"PE\x00\x00":
        raise ValueError("Not a valid PE file")

    coff_off  = e_lfanew + 4
    num_sections = struct.unpack_from("<H", buf, coff_off + 2)[0]
    opt_hdr_size = struct.unpack_from("<H", buf, coff_off + 16)[0]
    opt_off  = coff_off + 20
    magic    = struct.unpack_from("<H", buf, opt_off)[0]
    is_64    = (magic == 0x20B)

    file_align = struct.unpack_from("<I", buf, opt_off + 36)[0]
    sect_align = struct.unpack_from("<I", buf, opt_off + 32)[0]

    # SizeOfImage is at opt_off+56 for both PE32 and PE32+
    size_of_image_off = opt_off + 56

    # Data directory base: PE32=opt_off+96, PE32+=opt_off+112
    dd_base = opt_off + (112 if is_64 else 96)
    # Resource directory = entry 2
    rsrc_dd_off = dd_base + 2 * 8

    # Check no .rsrc exists
    sect_tbl_off = opt_off + opt_hdr_size
    for i in range(num_sections):
        sh = sect_tbl_off + i * 40
        name = buf[sh:sh + 8].rstrip(b"\x00")
        if name == b".rsrc":
            raise ValueError("PE already contains a .rsrc section")

    # Determine new section placement
    # Virtual: right after the last section (aligned to SectionAlignment)
    max_rva = 0
    for i in range(num_sections):
        sh = sect_tbl_off + i * 40
        virt_addr = struct.unpack_from("<I", buf, sh + 12)[0]
        virt_size = struct.unpack_from("<I", buf, sh + 8)[0]
        max_rva = max(max_rva, virt_addr + virt_size)
    new_rva = _align(max_rva, sect_align)

    # File: right after existing raw data (aligned to FileAlignment)
    new_raw_off = _align(len(pe_bytes), file_align)

    # Build .rsrc section
    rsrc_raw   = _build_rsrc(images, new_rva)
    rsrc_file  = _align(len(rsrc_raw), file_align)
    rsrc_vsize = len(rsrc_raw)

    # Verify there's room for a new section header
    new_hdr_off = sect_tbl_off + num_sections * 40
    # First section's raw data starts here (header area ends)
    min_raw = min(
        struct.unpack_from("<I", buf, sect_tbl_off + i * 40 + 20)[0]
        for i in range(num_sections)
        if struct.unpack_from("<I", buf, sect_tbl_off + i * 40 + 20)[0] > 0
    )
    if new_hdr_off + 40 > min_raw:
        raise ValueError("No room in PE header for an additional section entry")

    # Patch PE headers
    # 1. NumberOfSections
    struct.pack_into("<H", buf, coff_off + 2, num_sections + 1)

    # 2. SizeOfImage
    new_size_image = _align(new_rva + rsrc_vsize, sect_align)
    struct.pack_into("<I", buf, size_of_image_off, new_size_image)

    # 3. Resource data directory entry (RVA + Size)
    struct.pack_into("<II", buf, rsrc_dd_off, new_rva, rsrc_vsize)

    # 4. Write new section header
    sect_hdr = struct.pack("<8sIIIIIIHHI",
        b".rsrc\x00\x00\x00",  # Name
        rsrc_vsize,            # VirtualSize
        new_rva,               # VirtualAddress
        rsrc_file,             # SizeOfRawData
        new_raw_off,           # PointerToRawData
        0, 0, 0, 0,            # Relocs/LineNums
        _RSRC_CHARS,           # Characteristics
    )
    buf[new_hdr_off:new_hdr_off + 40] = sect_hdr

    # 5. Extend file to new_raw_off (zero-pad any gap)
    if len(buf) < new_raw_off:
        buf.extend(b"\x00" * (new_raw_off - len(buf)))

    # 6. Append .rsrc data (file-aligned, zero-padded)
    buf.extend(rsrc_raw)
    buf.extend(b"\x00" * (rsrc_file - len(rsrc_raw)))

    return bytes(buf)

# vendored from steamarchiver/crack/unstub/ — DO NOT MODIFY.
# This implements SteamStub DRM unpacking via low-level PE/x86/RC4 surgery.
# Re-vendor the file from upstream when fixing variant bugs.

"""
Minimal x86-32 instruction decoder.

Only implements enough to support SteamStub v2.x unpacking:
- Instruction length decoding (to advance the PC correctly)
- Classification of mov/lea/add instructions
- Extraction of immediate and displacement operands

This is NOT a general-purpose disassembler.
"""

import struct
from typing import NamedTuple, Optional


class X86Inst(NamedTuple):
    """Decoded x86 instruction."""
    length: int         # Total instruction length in bytes
    mnemonic: str       # 'mov', 'lea', 'add', 'push', 'sub', 'other', etc.
    op0_type: str       # 'reg', 'mem', 'imm', 'none'
    op1_type: str       # 'reg', 'mem', 'imm', 'none'
    imm_value: int      # Immediate operand value (or 0)
    disp_value: int     # Memory displacement value (or 0)


# ============================================================================
# ModR/M + SIB helpers
# ============================================================================

def _modrm_extra_len(data: bytes, offset: int, has_imm: int = 0) -> tuple:
    """
    Parse ModR/M (and optional SIB) to determine extra bytes after the opcode.

    Returns (extra_bytes, mod, reg, rm, displacement).
    """
    if offset >= len(data):
        return (0, 0, 0, 0, 0)

    modrm = data[offset]
    mod = (modrm >> 6) & 3
    reg = (modrm >> 3) & 7
    rm = modrm & 7

    extra = 1  # the ModR/M byte itself
    disp = 0

    if mod == 3:
        # Register-register, no extra bytes
        pass
    elif mod == 0:
        if rm == 4:
            extra += 1  # SIB byte
            if offset + 1 < len(data):
                sib = data[offset + 1]
                base = sib & 7
                if base == 5:
                    extra += 4  # disp32
                    if offset + 2 + 4 <= len(data):
                        disp = struct.unpack_from("<i", data, offset + 2)[0]
        elif rm == 5:
            extra += 4  # disp32
            if offset + 1 + 4 <= len(data):
                disp = struct.unpack_from("<i", data, offset + 1)[0]
    elif mod == 1:
        if rm == 4:
            extra += 1  # SIB byte
        extra += 1  # disp8
        disp_off = offset + extra - 1
        if disp_off < len(data):
            disp = struct.unpack_from("<b", data, disp_off)[0]
    elif mod == 2:
        if rm == 4:
            extra += 1  # SIB byte
        extra += 4  # disp32
        disp_off = offset + extra - 4
        if disp_off + 4 <= len(data):
            disp = struct.unpack_from("<i", data, disp_off)[0]

    return (extra, mod, reg, rm, disp)


# ============================================================================
# Main decoder
# ============================================================================

def decode_instruction(data: bytes, offset: int) -> Optional[X86Inst]:
    """
    Decode a single x86-32 instruction at *offset* in *data*.

    Returns an X86Inst or None if we can't decode.
    """
    if offset >= len(data):
        return None

    pos = offset
    start = offset

    # --- Handle common prefixes ---
    prefixes = {0x26, 0x2E, 0x36, 0x3E, 0x64, 0x65, 0x66, 0x67, 0xF0, 0xF2, 0xF3}
    operand_size_prefix = False
    while pos < len(data) and data[pos] in prefixes:
        if data[pos] == 0x66:
            operand_size_prefix = True
        pos += 1

    if pos >= len(data):
        return None

    opcode = data[pos]
    pos += 1

    mnemonic = "other"
    op0_type = "none"
    op1_type = "none"
    imm_value = 0
    disp_value = 0

    # --- Single-byte opcodes ---

    # push reg (50-57)
    if 0x50 <= opcode <= 0x57:
        mnemonic = "push"
        return X86Inst(pos - start, mnemonic, "reg", "none", 0, 0)

    # pop reg (58-5F)
    if 0x58 <= opcode <= 0x5F:
        mnemonic = "pop"
        return X86Inst(pos - start, mnemonic, "reg", "none", 0, 0)

    # mov reg, imm32 (B8-BF)
    if 0xB8 <= opcode <= 0xBF:
        mnemonic = "mov"
        imm_sz = 2 if operand_size_prefix else 4
        if pos + imm_sz <= len(data):
            imm_value = struct.unpack_from("<I" if imm_sz == 4 else "<H", data, pos)[0]
        pos += imm_sz
        return X86Inst(pos - start, mnemonic, "reg", "imm", imm_value, 0)

    # mov r8, imm8 (B0-B7)
    if 0xB0 <= opcode <= 0xB7:
        pos += 1
        return X86Inst(pos - start, "mov", "reg", "imm", 0, 0)

    # Short jumps and loops (70-7F, EB, E0-E3)
    if (0x70 <= opcode <= 0x7F) or opcode == 0xEB or (0xE0 <= opcode <= 0xE3):
        pos += 1  # rel8
        return X86Inst(pos - start, "jmp", "imm", "none", 0, 0)

    # CALL rel32 (E8), JMP rel32 (E9)
    if opcode in (0xE8, 0xE9):
        pos += 4  # rel32
        return X86Inst(pos - start, "call" if opcode == 0xE8 else "jmp", "imm", "none", 0, 0)

    # RET (C3, CB)
    if opcode in (0xC3, 0xCB):
        return X86Inst(pos - start, "ret", "none", "none", 0, 0)

    # RET imm16 (C2, CA)
    if opcode in (0xC2, 0xCA):
        pos += 2
        return X86Inst(pos - start, "ret", "imm", "none", 0, 0)

    # NOP (90)
    if opcode == 0x90:
        return X86Inst(pos - start, "nop", "none", "none", 0, 0)

    # INT3 (CC), INT imm8 (CD)
    if opcode == 0xCC:
        return X86Inst(pos - start, "int3", "none", "none", 0, 0)
    if opcode == 0xCD:
        pos += 1
        return X86Inst(pos - start, "int", "imm", "none", 0, 0)

    # PUSHFD/POPFD (9C/9D), PUSHAD/POPAD (60/61)
    if opcode in (0x9C, 0x9D, 0x60, 0x61):
        return X86Inst(pos - start, "other", "none", "none", 0, 0)

    # CDQ (99), CLD (FC), STD (FD), CLC (F8), STC (F9), CMC (F5)
    if opcode in (0x99, 0xFC, 0xFD, 0xF8, 0xF9, 0xF5, 0x98, 0x9E, 0x9F):
        return X86Inst(pos - start, "other", "none", "none", 0, 0)

    # LEAVE (C9)
    if opcode == 0xC9:
        return X86Inst(pos - start, "leave", "none", "none", 0, 0)

    # PUSH imm32 (68)
    if opcode == 0x68:
        imm_sz = 2 if operand_size_prefix else 4
        pos += imm_sz
        return X86Inst(pos - start, "push", "imm", "none", 0, 0)

    # PUSH imm8 (6A)
    if opcode == 0x6A:
        pos += 1
        return X86Inst(pos - start, "push", "imm", "none", 0, 0)

    # MOV EAX, moffs32 (A1) / MOV moffs32, EAX (A3)
    if opcode in (0xA1, 0xA3):
        pos += 4
        return X86Inst(pos - start, "mov", "reg" if opcode == 0xA1 else "mem", "mem" if opcode == 0xA1 else "reg", 0, 0)

    # ADD/OR/ADC/SBB/AND/SUB/XOR/CMP AL, imm8 (04,0C,14,1C,24,2C,34,3C)
    if opcode in (0x04, 0x0C, 0x14, 0x1C, 0x24, 0x2C, 0x34, 0x3C):
        pos += 1
        return X86Inst(pos - start, "other", "reg", "imm", 0, 0)

    # ADD/OR/ADC/SBB/AND/SUB/XOR/CMP EAX, imm32 (05,0D,15,1D,25,2D,35,3D)
    if opcode in (0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x35, 0x3D):
        imm_sz = 2 if operand_size_prefix else 4
        if pos + imm_sz <= len(data):
            imm_value = struct.unpack_from("<I" if imm_sz == 4 else "<H", data, pos)[0]
        pos += imm_sz
        mnemonics = {0x05: "add", 0x0D: "or", 0x15: "adc", 0x1D: "sbb",
                     0x25: "and", 0x2D: "sub", 0x35: "xor", 0x3D: "cmp"}
        return X86Inst(pos - start, mnemonics.get(opcode, "other"), "reg", "imm", imm_value, 0)

    # XCHG EAX, reg (91-97)
    if 0x91 <= opcode <= 0x97:
        return X86Inst(pos - start, "xchg", "reg", "reg", 0, 0)

    # INC/DEC reg (40-4F)
    if 0x40 <= opcode <= 0x4F:
        return X86Inst(pos - start, "inc" if opcode < 0x48 else "dec", "reg", "none", 0, 0)

    # REP/REPE/REPNE prefixed string ops (F3 A4/A5/A6/A7/AA/AB/AE/AF)
    # Already consumed as prefix; opcode here is the string op
    if opcode in (0xA4, 0xA5, 0xA6, 0xA7, 0xAA, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF):
        return X86Inst(pos - start, "string", "none", "none", 0, 0)

    # TEST AL/EAX, imm (A8/A9)
    if opcode == 0xA8:
        pos += 1
        return X86Inst(pos - start, "test", "reg", "imm", 0, 0)
    if opcode == 0xA9:
        pos += (2 if operand_size_prefix else 4)
        return X86Inst(pos - start, "test", "reg", "imm", 0, 0)

    # --- Opcodes with ModR/M ---

    # Determine if this opcode uses ModR/M
    has_modrm = False
    imm_size = 0  # extra immediate bytes after ModR/M+disp

    # Group 1: 80-83 (ALU r/m, imm)
    if opcode in (0x80, 0x82):
        has_modrm = True
        imm_size = 1
    elif opcode == 0x81:
        has_modrm = True
        imm_size = 2 if operand_size_prefix else 4
    elif opcode == 0x83:
        has_modrm = True
        imm_size = 1

    # ALU r/m, r and r, r/m (00-03, 08-0B, 10-13, 18-1B, 20-23, 28-2B, 30-33, 38-3B)
    elif (opcode & 0xC4) == 0x00 and (opcode & 0x03) <= 3 and opcode not in (0x04,0x05,0x0C,0x0D,0x14,0x15,0x1C,0x1D,0x24,0x25,0x2C,0x2D,0x34,0x35,0x3C,0x3D,0x06,0x07,0x0E,0x16,0x17,0x1E,0x1F,0x26,0x27,0x2E,0x2F,0x36,0x37,0x3E,0x3F):
        has_modrm = True

    # MOV r/m, r (88, 89) and MOV r, r/m (8A, 8B)
    elif opcode in (0x88, 0x89, 0x8A, 0x8B):
        has_modrm = True
        mnemonic = "mov"

    # LEA r, m (8D)
    elif opcode == 0x8D:
        has_modrm = True
        mnemonic = "lea"

    # MOV r/m, imm (C6=8bit, C7=32bit)
    elif opcode == 0xC6:
        has_modrm = True
        imm_size = 1
        mnemonic = "mov"
    elif opcode == 0xC7:
        has_modrm = True
        imm_size = 2 if operand_size_prefix else 4
        mnemonic = "mov"

    # TEST r/m, r (84, 85)
    elif opcode in (0x84, 0x85):
        has_modrm = True
        mnemonic = "test"

    # TEST r/m, imm (F6 /0, F7 /0)
    elif opcode == 0xF6:
        has_modrm = True
        # imm_size depends on reg field - but /0 has imm8, others have 0
        # We'll peek at the reg field
        if pos < len(data):
            reg_field = (data[pos] >> 3) & 7
            if reg_field in (0, 1):
                imm_size = 1
    elif opcode == 0xF7:
        has_modrm = True
        if pos < len(data):
            reg_field = (data[pos] >> 3) & 7
            if reg_field in (0, 1):
                imm_size = 2 if operand_size_prefix else 4

    # MOVZX/MOVSX (0F B6, 0F B7, 0F BE, 0F BF)
    elif opcode == 0x0F:
        if pos < len(data):
            opcode2 = data[pos]
            pos += 1

            # Two-byte opcodes with ModR/M
            if opcode2 in (0xB6, 0xB7, 0xBE, 0xBF, 0xAF, 0xAB, 0xA3, 0xA5, 0xAD,
                           0xB0, 0xB1, 0xB3, 0xBA, 0xBC, 0xBD,
                           0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47,
                           0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F):
                has_modrm = True
                if opcode2 == 0xBA:
                    imm_size = 1

            # Jcc rel32 (0F 80-8F)
            elif 0x80 <= opcode2 <= 0x8F:
                pos += 4
                return X86Inst(pos - start, "jcc", "imm", "none", 0, 0)

            # SETcc (0F 90-9F)
            elif 0x90 <= opcode2 <= 0x9F:
                has_modrm = True

            # IMUL r, r/m, imm (0F AF handled above)
            # SHLD/SHRD (0F A4/AC imm8, 0F A5/AD cl)
            elif opcode2 in (0xA4, 0xAC):
                has_modrm = True
                imm_size = 1

            # Other 0F xx without modrm
            elif opcode2 in (0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x31, 0xA2):
                return X86Inst(pos - start, "other", "none", "none", 0, 0)

            else:
                # Unknown 0F opcode - try with ModR/M as fallback
                has_modrm = True

    # IMUL r, r/m, imm32 (69) or imm8 (6B)
    elif opcode == 0x69:
        has_modrm = True
        imm_size = 2 if operand_size_prefix else 4
    elif opcode == 0x6B:
        has_modrm = True
        imm_size = 1

    # Shift/rotate group (D0-D3, C0-C1)
    elif opcode in (0xD0, 0xD1, 0xD2, 0xD3):
        has_modrm = True
    elif opcode == 0xC0:
        has_modrm = True
        imm_size = 1
    elif opcode == 0xC1:
        has_modrm = True
        imm_size = 1

    # Group 3 (F6, F7) - already handled above
    # Group 4/5 (FE, FF)
    elif opcode in (0xFE, 0xFF):
        has_modrm = True

    # MOV segment register (8C, 8E)
    elif opcode in (0x8C, 0x8E):
        has_modrm = True

    # Other ModR/M opcodes (catch-all for common ones)
    elif opcode in (0x62, 0x63, 0x86, 0x87, 0xD8, 0xD9, 0xDA, 0xDB,
                    0xDC, 0xDD, 0xDE, 0xDF, 0x8F):
        has_modrm = True

    # ENTER (C8)
    elif opcode == 0xC8:
        pos += 3  # imm16 + imm8
        return X86Inst(pos - start, "enter", "imm", "none", 0, 0)

    if has_modrm and pos < len(data):
        extra, mod, reg, rm, disp = _modrm_extra_len(data, pos)
        pos += extra

        # Read immediate if present
        if imm_size > 0 and pos + imm_size <= len(data):
            if imm_size == 4:
                imm_value = struct.unpack_from("<I", data, pos)[0]
            elif imm_size == 2:
                imm_value = struct.unpack_from("<H", data, pos)[0]
            elif imm_size == 1:
                imm_value = struct.unpack_from("<B", data, pos)[0]
            pos += imm_size

        disp_value = disp

        # Classify operand types
        if mod == 3:
            op0_type = "reg"
            op1_type = "reg" if imm_size == 0 else "imm"
        else:
            # Determine direction from opcode
            if opcode in (0xC7, 0xC6):
                # mov r/m, imm
                op0_type = "mem"
                op1_type = "imm"
            elif opcode in (0x89, 0x88):
                # mov r/m, r
                op0_type = "mem"
                op1_type = "reg"
            elif opcode in (0x8B, 0x8A, 0x8D):
                # mov/lea r, r/m
                op0_type = "reg"
                op1_type = "mem"
            elif opcode in (0x80, 0x81, 0x82, 0x83):
                op0_type = "mem" if mod != 3 else "reg"
                op1_type = "imm"
                alu_mnemonics = ["add", "or", "adc", "sbb", "and", "sub", "xor", "cmp"]
                mnemonic = alu_mnemonics[reg] if reg < 8 else "other"
            else:
                if opcode & 1:  # odd = word/dword
                    if opcode & 2:  # bit 1 = direction
                        op0_type = "reg"
                        op1_type = "mem"
                    else:
                        op0_type = "mem"
                        op1_type = "reg"
                else:
                    op0_type = "mem"
                    op1_type = "reg"

        return X86Inst(pos - start, mnemonic, op0_type, op1_type, imm_value, disp_value)
    elif has_modrm:
        # Ran out of data
        return None

    # If we get here with no match, return a 1-byte "other"
    return X86Inst(pos - start, "other", "none", "none", 0, 0)


# ============================================================================
# Higher-level helpers for SteamStub
# ============================================================================

def disassemble_ep_v21(data: bytes, max_bytes: int = 4096):
    """
    Disassemble the v2.1 entry point stub code and extract three values:

    1. structOffset — RVA of the DRM header (from 1st ``mov [mem], imm32``)
    2. structSize   — header size in bytes (from 1st ``mov reg, imm32`` × 4)
    3. structXorKey  — XOR key (from 2nd ``mov [mem], imm32``)

    Returns (offset, size, xor_key) or None on failure.
    """
    struct_offset = 0
    struct_size = 0
    struct_xor_key = 0
    pos = 0

    while pos < min(len(data), max_bytes):
        inst = decode_instruction(data, pos)
        if inst is None:
            break

        if inst.length == 0:
            break

        # Check if all values are found
        if struct_offset > 0 and struct_size > 0 and struct_xor_key > 0:
            return (struct_offset, struct_size, struct_xor_key)

        # mov [mem], imm32
        if inst.mnemonic == "mov" and inst.op0_type == "mem" and inst.op1_type == "imm":
            if struct_offset == 0:
                struct_offset = inst.imm_value  # This is a VA, caller subtracts ImageBase
            else:
                struct_xor_key = inst.imm_value

        # mov reg, imm32
        if inst.mnemonic == "mov" and inst.op0_type == "reg" and inst.op1_type == "imm":
            if struct_size == 0:
                struct_size = inst.imm_value * 4

        pos += inst.length

    # Final check
    if struct_offset > 0 and struct_size > 0 and struct_xor_key > 0:
        return (struct_offset, struct_size, struct_xor_key)

    return None


def get_drmp_offsets_dynamic(data: bytes):
    """
    Disassemble a block of SteamDRMP.dll code to extract 8 parameter offsets.

    Scans for the following instruction pattern (in order):
    - 5× ``mov reg, [reg+disp32]`` → offsets 0-4 (flags, appid, OEP, code VA, code size)
    - 1× ``lea reg, [reg+disp32]`` → offset 5 (AES key), offset 6 (AES IV = disp+16)
    - 1× ``add reg, imm32`` → offset 7 (stolen bytes)

    After the ``lea``, one ``mov`` is skipped (GetModuleHandleA load).

    Returns a list of 8 int offsets, or an empty list on failure.
    """
    offsets = []
    count = 0
    pos = 0
    skip_mov = False

    while pos < len(data) and count < 8:
        inst = decode_instruction(data, pos)
        if inst is None or inst.length == 0:
            break

        # mov reg, [reg+disp] — memory to register
        if not skip_mov and inst.mnemonic == "mov" and inst.op0_type == "reg" and inst.op1_type == "mem":
            offsets.append(inst.disp_value)
            count += 1

        # lea reg, [reg+disp]
        if inst.mnemonic == "lea" and inst.op0_type == "reg" and inst.op1_type == "mem":
            offsets.append(inst.disp_value)       # AES key offset
            offsets.append(inst.disp_value + 16)   # AES IV offset
            count += 2
            skip_mov = True  # Skip next mov (GetModuleHandleA)

        # add reg, imm32
        if inst.mnemonic == "add" and inst.op0_type == "reg" and inst.op1_type == "imm":
            offsets.append(inst.imm_value)
            count += 1

        pos += inst.length

    return offsets if len(offsets) == 8 else []


def disassemble_ep_v20(data: bytes, max_bytes: int = 4096):
    """
    Disassemble the v2.0 entry point stub code and extract two values:

    1. structOffset — RVA of the DRM header (from 1st ``mov reg, imm32``)
    2. structSize   — header size in bytes (from 2nd ``mov reg, imm32`` × 4)

    v2.0 uses key=0 for XOR (self-keyed from first 4 bytes of header).

    Returns (offset_va, size) or None on failure.
    """
    struct_offset = 0
    struct_size = 0
    pos = 0

    while pos < min(len(data), max_bytes):
        inst = decode_instruction(data, pos)
        if inst is None or inst.length == 0:
            break

        # Both values found
        if struct_offset > 0 and struct_size > 0:
            return (struct_offset, struct_size)

        # mov reg, imm32
        if inst.mnemonic == "mov" and inst.op0_type == "reg" and inst.op1_type == "imm":
            if struct_offset == 0:
                struct_offset = inst.imm_value  # VA, caller subtracts ImageBase
            else:
                struct_size = inst.imm_value * 4

        pos += inst.length

    if struct_offset > 0 and struct_size > 0:
        return (struct_offset, struct_size)

    return None

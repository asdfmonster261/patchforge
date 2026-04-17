/*
 * jojodiff_stub.c — PatchForge Windows patcher stub (JojoDiff engine, directory mode)
 *
 * Compiled with MinGW-w64.  Patch data is a PFMD container (dir_patch_format.h)
 * where each modified file has its own jdiff patch, new files are raw content,
 * and deleted files are flagged for removal.
 *
 * The jdiff binary patch format (reference: jojodiff src/JOutBin.cpp):
 *   Bytes 0x00–0xF4  — equal byte: advance source cursor, output this byte
 *   0xF5 (JMOD)      — XOR-modify: output old_byte ^ next_patch_byte
 *   0xF6 (JINS)      — insert: output next_patch_byte (source unchanged)
 *   0xF7 (JDEL)      — delete: skip varint-encoded N source bytes
 *   0xF8 (JBKT)      — backtrack: rewind source by varint-encoded N bytes
 *   0xF9 (JEQL)      — equal run: copy varint-encoded N source bytes to output
 *   0xFA (JLITESC)   — literal 0xF5 byte in output
 */

#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0600
#include <windows.h>
#include <dwmapi.h>
#include <commdlg.h>
#include <shlobj.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <limits.h>

/* ---- Globals ---- */
HWND g_hwnd           = NULL;
HWND g_hwnd_status    = NULL;
HWND g_hwnd_progress  = NULL;
HWND g_hwnd_filepath  = NULL;
HWND g_hwnd_log       = NULL;
HWND g_hwnd_btn_patch = NULL;
static int g_close_countdown = 0;
#define TIMER_CLOSE 1
HWND g_hwnd_chk_backup  = NULL;
HWND g_hwnd_chk_verify  = NULL;
HBRUSH g_brush_bg    = NULL;
HBRUSH g_brush_light = NULL;
HBRUSH g_brush_log   = NULL;
HFONT g_font_normal  = NULL;
HFONT g_font_title   = NULL;
char g_exe_path[MAX_PATH] = {0};

#include "stub_common.h"
#include "dir_patch_format.h"

/* WM_PATCH_DONE/PROG/LOG_MSG and IDC_CHK_* are defined in stub_common.h */

/* jdiff binary opcodes (from JDefs.h in jojodiff source) */
#define JJ_ESC  0xA7
#define JJ_MOD  0xA6   /* modify: output new bytes (source cursor advances via lzMod) */
#define JJ_INS  0xA5   /* insert: output new bytes (source cursor unchanged)          */
#define JJ_DEL  0xA4   /* delete: skip N source bytes                                 */
#define JJ_EQL  0xA3   /* equal: copy N source bytes to output                        */
#define JJ_BKT  0xA2   /* backtrack: rewind source by N bytes                         */

PatchMeta g_meta;
static char  *g_patch_data = NULL;
static size_t g_patch_size = 0;
static int    g_patch_result = 0;

/*
 * Read a JojoDiff length value from the patch stream (ufGetInt from jpatch.cpp).
 * Encoding:
 *   0..251       → value + 1          (1 byte,  range 1..252)
 *   252  + B1    → 253 + B1           (2 bytes, range 253..508)
 *   253  + B1 B2 → (B1<<8)|B2        (3 bytes, 16-bit BE)
 *   254  + 4B    → 32-bit BE          (5 bytes)
 *   255  + 8B    → 64-bit BE          (9 bytes)
 * Returns -1 on read error.
 */
static long jdiff_read_len(FILE *f)
{
    int b0 = fgetc(f);
    if (b0 == EOF) return -1;
    if (b0 <= 251) return (long)(b0 + 1);
    if (b0 == 252) {
        int b1 = fgetc(f);
        if (b1 == EOF) return -1;
        return (long)(253 + b1);
    }
    if (b0 == 253) {
        int hi = fgetc(f), lo = fgetc(f);
        if (hi == EOF || lo == EOF) return -1;
        return (long)((hi << 8) | lo);
    }
    if (b0 == 254) {
        int b[4];
        for (int i = 0; i < 4; i++) { b[i] = fgetc(f); if (b[i] == EOF) return -1; }
        return (long)(((unsigned long)b[0] << 24) | ((unsigned long)b[1] << 16) |
                      ((unsigned long)b[2] <<  8) |  (unsigned long)b[3]);
    }
    /* 255: 64-bit value — read 8 bytes and return low 32 bits (game files won't exceed 4 GB) */
    {
        long val = 0;
        for (int i = 0; i < 8; i++) {
            int b = fgetc(f);
            if (b == EOF) return -1;
            val = (val << 8) | b;
        }
        return val;
    }
}

/*
 * Apply a jdiff patch (in-memory) to old_path, writing result to new_path.
 *
 * JojoDiff binary format (from JDefs.h + JOutBin.cpp + jpatch.cpp):
 *   All operations begin with ESC(0xA7) followed by an opcode byte.
 *   MOD(0xA6): data bytes follow; each byte goes to output; source cursor is
 *              advanced implicitly via lzMod counter (not by fread).
 *   INS(0xA5): data bytes follow; inserted into output; source unchanged.
 *   DEL(0xA4): length follows; skip (len + lzMod) source bytes; lzMod = 0.
 *   EQL(0xA3): length follows; flush lzMod, then copy len source bytes to output.
 *   BKT(0xA2): length follows; seek source by (lzMod - len); lzMod = 0.
 *   ESC ESC:   literal ESC(0xA7) byte in the data stream.
 *   Data bytes equal to ESC are escaped: ESC <next-byte>; the decoder outputs
 *   ESC then next-byte (via the lbEsc flag for unknown-opcode escapes).
 */
static int apply_jojodiff(const char *old_path, const unsigned char *patch_data,
                            uint32_t patch_len, const char *new_path)
{
    /* Write patch to temp file so we can use FILE* streaming */
    char tmp_dir[MAX_PATH], tmp_patch[MAX_PATH];
    GetTempPathA(MAX_PATH, tmp_dir);
    GetTempFileNameA(tmp_dir, "pfgj", 0, tmp_patch);

    FILE *fptmp = fopen(tmp_patch, "wb");
    if (!fptmp) return 0;
    if (fwrite(patch_data, 1, patch_len, fptmp) != patch_len) {
        fclose(fptmp); DeleteFileA(tmp_patch); return 0;
    }
    fclose(fptmp);

    FILE *fold   = fopen(old_path,  "rb");
    FILE *fpatch = fopen(tmp_patch, "rb");
    FILE *fnew   = fopen(new_path,  "wb");
    if (!fold || !fpatch || !fnew) {
        if (fold)   fclose(fold);
        if (fpatch) fclose(fpatch);
        if (fnew)   fclose(fnew);
        DeleteFileA(tmp_patch);
        return 0;
    }

    int  liOpr = JJ_ESC;  /* current operation */
    long lzMod = 0;       /* source bytes consumed implicitly by MOD */
    int  ok    = 1;
    int  liInp;

    while ((liInp = fgetc(fpatch)) != EOF) {
        int lbChg = 0;  /* set when opcode consumed the byte — skip data handler */
        int lbEsc = 0;  /* set for unknown ESC sequences — prefix output with ESC */

        if (liInp == JJ_ESC) {
            liInp = fgetc(fpatch);
            if (liInp == EOF) { ok = 0; goto done; }
            switch (liInp) {
            case JJ_MOD:
                liOpr = JJ_MOD;
                lbChg = 1;
                break;
            case JJ_INS:
                liOpr = JJ_INS;
                lbChg = 1;
                break;
            case JJ_DEL: {
                long lzOff = jdiff_read_len(fpatch);
                if (lzOff < 0 || lzMod > LONG_MAX - lzOff ||
                    fseek(fold, lzOff + lzMod, SEEK_CUR) != 0)
                    { ok = 0; goto done; }
                lzMod = 0;
                lbChg = 1;
                break;
            }
            case JJ_EQL: {
                long lzOff = jdiff_read_len(fpatch);
                if (lzOff < 0) { ok = 0; goto done; }
                if (lzMod > 0) {
                    if (fseek(fold, lzMod, SEEK_CUR) != 0) { ok = 0; goto done; }
                    lzMod = 0;
                }
                unsigned char eql_buf[4096];
                long rem = lzOff;
                while (rem > 0) {
                    long chunk = rem < 4096 ? rem : 4096;
                    if ((long)fread(eql_buf, 1, (size_t)chunk, fold) != chunk)
                        { ok = 0; goto done; }
                    if ((long)fwrite(eql_buf, 1, (size_t)chunk, fnew) != chunk)
                        { ok = 0; goto done; }
                    rem -= chunk;
                }
                lbChg = 1;
                break;
            }
            case JJ_BKT: {
                long lzOff = jdiff_read_len(fpatch);
                if (lzOff < 0 || fseek(fold, lzMod - lzOff, SEEK_CUR) != 0)
                    { ok = 0; goto done; }
                lzMod = 0;
                lbChg = 1;
                break;
            }
            case JJ_ESC:
                /* ESC ESC = literal ESC byte; lbEsc stays 0, liInp = JJ_ESC */
                break;
            default:
                /* Unknown escape — treat preceding ESC as literal data */
                lbEsc = 1;
                break;
            }
        }

        if (!lbChg) {
            switch (liOpr) {
            case JJ_MOD:
                if (lbEsc) { fputc(JJ_ESC, fnew); lzMod++; }
                fputc(liInp, fnew);
                lzMod++;
                break;
            case JJ_INS:
                if (lbEsc) fputc(JJ_ESC, fnew);
                fputc(liInp, fnew);
                break;
            default:
                break;
            }
        }
    }
done:
    fclose(fold);
    fclose(fpatch);
    fclose(fnew);
    DeleteFileA(tmp_patch);
    return ok;
}

/* ---- PFMD entry callback ---- */
struct DirPatchCtx {
    const char *game_dir;
    char err_msg[MAX_PATH + 128];
    int  had_error;
};

static int jojo_apply_entry(int op, const char *rel_path,
                              const unsigned char *data, uint32_t data_len,
                              void *userdata)
{
    struct DirPatchCtx *ctx = (struct DirPatchCtx *)userdata;
    char full_path[MAX_PATH];
    snprintf(full_path, MAX_PATH, "%s\\%s", ctx->game_dir, rel_path);

    if (op == PFMD_OP_DELETE) {
        if (g_meta.delete_extra_files)
            DeleteFileA(full_path);
        return 1;
    }

    if (op == PFMD_OP_NEW) {
        pfmd_ensure_parent_dirs(full_path);
        FILE *f = fopen(full_path, "wb");
        if (!f) {
            snprintf(ctx->err_msg, sizeof(ctx->err_msg),
                "ERROR: cannot write new file: %s", rel_path);
            ctx->had_error = 1;
            return 0;
        }
        if (fwrite(data, 1, data_len, f) != data_len) {
            fclose(f);
            snprintf(ctx->err_msg, sizeof(ctx->err_msg),
                "ERROR: write failed for new file: %s", rel_path);
            ctx->had_error = 1;
            return 0;
        }
        fclose(f);
        return 1;
    }

    if (op == PFMD_OP_PATCH) {
        /* Write output to a temp file in the same directory as the target
         * to ensure same-drive rename (avoids cross-device MoveFileEx failure). */
        char parent[MAX_PATH], tmp_out[MAX_PATH];
        pfmd_parent_dir(full_path, parent);
        pfmd_ensure_parent_dirs(full_path);
        if (!GetTempFileNameA(parent, "pfgj", 0, tmp_out)) {
            snprintf(ctx->err_msg, sizeof(ctx->err_msg),
                "ERROR: cannot create temp file for: %s", rel_path);
            ctx->had_error = 1;
            return 0;
        }
        DeleteFileA(tmp_out);  /* GetTempFileName creates a placeholder; remove it */

        int ok = apply_jojodiff(full_path, data, data_len, tmp_out);
        if (ok) {
            ok = MoveFileExA(tmp_out, full_path, MOVEFILE_REPLACE_EXISTING);
        }
        if (!ok) {
            DeleteFileA(tmp_out);
            snprintf(ctx->err_msg, sizeof(ctx->err_msg),
                "ERROR: patch failed for: %s", rel_path);
            ctx->had_error = 1;
            return 0;
        }
        return 1;
    }

    return 1; /* unknown op — skip */
}

/* ---- Apply directory patch ---- */
static int apply_dir_jojodiff(const char *game_dir,
                                const char *patch_data, size_t patch_size)
{
    PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Applying patch..."));

    if (patch_size < 9 || memcmp(patch_data, "PFMD", 4) != 0) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("ERROR: not a valid directory patch (missing PFMD header)"));
        return 0;
    }

    struct DirPatchCtx ctx;
    ctx.game_dir   = game_dir;
    ctx.had_error  = 0;
    ctx.err_msg[0] = '\0';

    int ok = pfmd_iterate((const unsigned char *)patch_data, patch_size,
                           jojo_apply_entry, &ctx);

    if (!ok || ctx.had_error) {
        const char *msg = ctx.err_msg[0] ? ctx.err_msg : "ERROR: directory patch failed";
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup(msg));
        return 0;
    }
    return 1;
}

/* ---- Patch thread ---- */
struct PatchArgs {
    char game_dir[MAX_PATH];
    int  do_backup;
    int  do_verify;
};

static DWORD WINAPI patch_thread(LPVOID arg)
{
    struct PatchArgs *a = (struct PatchArgs *)arg;

    PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Checking game version..."));
    PostMessageA(g_hwnd, WM_PATCH_PROG, 3, 0);
    {
        char err[512] = {0};
        if (!verify_source_files(a->game_dir, &g_meta, err, sizeof(err))) {
            PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup(err));
            MessageBoxA(g_hwnd, err, "Wrong Version", MB_OK | MB_ICONERROR);
            g_patch_result = 0;
            PostMessageA(g_hwnd, WM_PATCH_PROG, 100, 0);
            PostMessageA(g_hwnd, WM_PATCH_DONE, 0, 0);
            free(a);
            return 0;
        }
    }

    PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Reading patch data..."));
    PostMessageA(g_hwnd, WM_PATCH_PROG, 5, 0);

    if (g_meta.run_before[0]) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Running pre-patch command..."));
        pfg_run_and_wait(g_meta.run_before);
    }

    if (a->do_backup) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Creating backup..."));
        pfg_do_backup(a->game_dir, &g_meta);
    }

    PostMessageA(g_hwnd, WM_PATCH_PROG, 15, 0);
    PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Applying patch (in-place)..."));

    FileStamp *ts_snap = NULL; int ts_count = 0;
    if (g_meta.preserve_timestamps)
        ts_snap = pfg_snapshot_timestamps(a->game_dir, &ts_count);

    int ok = apply_dir_jojodiff(a->game_dir, g_patch_data, g_patch_size);

    if (ok && a->do_verify) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Verifying..."));
        if (!verify_all_checksums(a->game_dir, &g_meta)) {
            ok = 0;
            PostMessageA(g_hwnd, WM_LOG_MSG, 0,
                (LPARAM)_strdup("WARNING: One or more files failed verification."));
        }
    }

    if (ok && g_meta.num_extra_files > 0) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Installing extra files..."));
        pfg_write_extra_files(a->game_dir, &g_meta);
    }

    if (ts_snap) {
        if (ok) pfg_restore_timestamps(ts_snap, ts_count);
        free(ts_snap);
    }

    if (ok && g_meta.run_after[0]) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Running post-patch command..."));
        pfg_run_and_wait(g_meta.run_after);
    }

    g_patch_result = ok;
    PostMessageA(g_hwnd, WM_PATCH_PROG, 100, 0);
    PostMessageA(g_hwnd, WM_PATCH_DONE, ok, 0);
    free(a);
    return 0;
}

/* ---- Browse for folder ---- */
static int browse_for_folder(HWND owner, char *out, int out_len)
{
    BROWSEINFOA bi = {0};
    bi.hwndOwner       = owner;
    bi.pszDisplayName  = out;
    bi.lpszTitle       = "Select game folder to patch:";
    bi.ulFlags         = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    LPITEMIDLIST pidl  = SHBrowseForFolderA(&bi);
    if (!pidl) return 0;
    SHGetPathFromIDListA(pidl, out);
    CoTaskMemFree(pidl);
    return 1;
}

/* ---- Window procedure ---- */
static int g_progress_pct = 0;

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_CREATE: {
        g_hwnd = hwnd;
        enable_dark_titlebar(hwnd);

        /* Title */
        HWND lbl = CreateWindowExA(0, "STATIC",
            g_meta.app_name[0] ? g_meta.app_name : "PatchForge Patcher",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 16, 680, 30, hwnd, NULL, NULL, NULL);
        SendMessageA(lbl, WM_SETFONT, (WPARAM)g_font_title, TRUE);

        /* Change summary */
        {
            int m = g_meta.files_modified, a = g_meta.files_added, r = g_meta.files_removed;
            if (m + a + r > 0) {
                char cbuf[128] = {0};
                int pos = 0;
                if (m) pos += snprintf(cbuf + pos, sizeof(cbuf) - pos, "%d modified", m);
                if (a) pos += snprintf(cbuf + pos, sizeof(cbuf) - pos, "%s%d added",
                                       pos ? "  \xB7  " : "", a);
                if (r) pos += snprintf(cbuf + pos, sizeof(cbuf) - pos, "%s%d removed",
                                       pos ? "  \xB7  " : "", r);
                HWND clbl = CreateWindowExA(0, "STATIC", cbuf,
                    WS_CHILD | WS_VISIBLE | SS_LEFT,
                    20, 50, 680, 16, hwnd, NULL, NULL, NULL);
                SendMessageA(clbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            }
        }

        /* Description */
        if (g_meta.description[0]) {
            HWND desc = CreateWindowExA(0, "STATIC", g_meta.description,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                20, 68, 680, 16, hwnd, NULL, NULL, NULL);
            SendMessageA(desc, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        }

        /* Game folder row */
        HWND flbl = CreateWindowExA(0, "STATIC", "Game folder:",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 102, 90, 18, hwnd, NULL, NULL, NULL);
        SendMessageA(flbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        g_hwnd_filepath = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
            115, 100, 499, 22, hwnd, (HMENU)IDC_FILEPATH, NULL, NULL);
        SendMessageA(g_hwnd_filepath, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        CreateWindowExA(0, "BUTTON", "Browse...",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            622, 100, 78, 22, hwnd, (HMENU)IDC_BTN_BROWSE, NULL, NULL);

        /* Backup checkbox */
        g_hwnd_chk_backup = CreateWindowExA(0, "BUTTON",
            "Create backup before patching",
            WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
            20, 134, 260, 20, hwnd, (HMENU)IDC_CHK_BACKUP, NULL, NULL);
        SendMessageA(g_hwnd_chk_backup, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        SendMessageA(g_hwnd_chk_backup, BM_SETCHECK, BST_CHECKED, 0);

        /* Verify checkbox */
        g_hwnd_chk_verify = CreateWindowExA(0, "BUTTON",
            "Verify after patching",
            WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
            20, 158, 260, 20, hwnd, (HMENU)IDC_CHK_VERIFY, NULL, NULL);
        SendMessageA(g_hwnd_chk_verify, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        SendMessageA(g_hwnd_chk_verify, BM_SETCHECK, BST_CHECKED, 0);

        /* Log area */
        g_hwnd_log = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL |
            ES_READONLY | WS_VSCROLL,
            20, 192, 680, 110, hwnd, (HMENU)IDC_LOG, NULL, NULL);
        SendMessageA(g_hwnd_log, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Progress bar */
        g_hwnd_progress = CreateWindowExA(0, "STATIC", "",
            WS_CHILD | WS_VISIBLE | SS_OWNERDRAW,
            20, 310, 680, 8, hwnd, (HMENU)IDC_PROGRESS, NULL, NULL);

        /* Status */
        g_hwnd_status = CreateWindowExA(0, "STATIC",
            "Select the game folder and click Patch.",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 326, 510, 16, hwnd, (HMENU)IDC_STATUS, NULL, NULL);
        SendMessageA(g_hwnd_status, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Patch / Cancel buttons */
        g_hwnd_btn_patch = CreateWindowExA(0, "BUTTON", "Patch",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            530, 354, 80, 28, hwnd, (HMENU)IDC_BTN_PATCH, NULL, NULL);
        CreateWindowExA(0, "BUTTON", "Cancel",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            620, 354, 72, 28, hwnd, (HMENU)IDC_BTN_CANCEL, NULL, NULL);

        /* Bottom-left info: company · copyright · contact, then version on the line below */
        {
            char info[512] = {0};
            const char * const info_parts[3] = {
                g_meta.company_info, g_meta.copyright, g_meta.contact
            };
            for (int i = 0; i < 3; i++) {
                if (info_parts[i][0]) {
                    if (info[0]) {
                        size_t l = strlen(info);
                        snprintf(info + l, sizeof(info) - l, "  \xB7  %s", info_parts[i]);
                    } else {
                        snprintf(info, sizeof(info), "%s", info_parts[i]);
                    }
                }
            }
            if (info[0]) {
                HWND infolbl = CreateWindowExA(0, "STATIC", info,
                    WS_CHILD | WS_VISIBLE | SS_LEFT | SS_ENDELLIPSIS,
                    20, 360, 500, 16, hwnd, NULL, NULL, NULL);
                SendMessageA(infolbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            }
            if (g_meta.version[0]) {
                char verbuf[80] = {0};
                snprintf(verbuf, sizeof(verbuf), "Version: %s", g_meta.version);
                HWND verlbl = CreateWindowExA(0, "STATIC", verbuf,
                    WS_CHILD | WS_VISIBLE | SS_LEFT | SS_ENDELLIPSIS,
                    20, 384, 500, 16, hwnd, NULL, NULL, NULL);
                SendMessageA(verlbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            }
        }

        /* Auto-detect game folder; preset path (from UAC relaunch) takes priority */
        char auto_path[MAX_PATH] = {0};
        if (strcmp(g_meta.find_method, "registry") == 0)
            find_via_registry(&g_meta, auto_path, MAX_PATH);
        else if (strcmp(g_meta.find_method, "ini") == 0)
            find_via_ini(&g_meta, auto_path, MAX_PATH);
        {
            const char *init = g_preset_path[0] ? g_preset_path : auto_path;
            if (init[0]) SetWindowTextA(g_hwnd_filepath, init);
        }

        break;
    }

    case WM_CTLCOLORSTATIC: {
        HDC  dc  = (HDC)wp;
        HWND ctl = (HWND)lp;
        SetTextColor(dc, COL_TEXT);
        if (ctl == g_hwnd_log) {
            SetBkColor(dc, COL_LOG_BG);
            return (LRESULT)g_brush_log;
        }
        if (g_backdrop_bmp) {
            SetBkMode(dc, TRANSPARENT);
            return (LRESULT)GetStockObject(NULL_BRUSH);
        }
        SetBkColor(dc, COL_BG);
        return (LRESULT)g_brush_bg;
    }
    case WM_CTLCOLOREDIT: {
        HDC  dc  = (HDC)wp;
        SetTextColor(dc, COL_TEXT);
        SetBkColor(dc, COL_BG_LIGHT);
        return (LRESULT)g_brush_light;
    }

    case WM_DRAWITEM: {
        DRAWITEMSTRUCT *dis = (DRAWITEMSTRUCT *)lp;
        if (dis->CtlID == IDC_PROGRESS) {
            pfg_draw_progress(dis->hDC, dis->rcItem, g_progress_pct);
            return TRUE;
        }
        COLORREF bg = (dis->CtlID == IDC_BTN_PATCH) ? COL_ACCENT : COL_BG_LIGHT;
        paint_button(dis, bg, COL_TEXT);
        return TRUE;
    }

    case WM_COMMAND: {
        int id = LOWORD(wp);
        if (id == IDC_BTN_BROWSE) {
            char path[MAX_PATH] = {0};
            GetWindowTextA(g_hwnd_filepath, path, MAX_PATH);
            if (browse_for_folder(hwnd, path, MAX_PATH))
                SetWindowTextA(g_hwnd_filepath, path);
        } else if (id == IDC_BTN_PATCH) {
            char path[MAX_PATH] = {0};
            GetWindowTextA(g_hwnd_filepath, path, MAX_PATH);
            if (!path[0]) {
                set_status("Please select the game folder first.", COL_ERROR);
                return 0;
            }
            DWORD attr = GetFileAttributesA(path);
            if (attr == INVALID_FILE_ATTRIBUTES || !(attr & FILE_ATTRIBUTE_DIRECTORY)) {
                set_status("Folder not found. Please select a valid directory.", COL_ERROR);
                return 0;
            }
            if (!pfg_check_free_space(hwnd, path, g_meta.required_free_space_gb)) return 0;
            if (!pfg_check_elevate(path)) return 0;
            EnableWindow(g_hwnd_btn_patch, FALSE);
            set_status("Patching...", COL_TEXT);
            struct PatchArgs *args = (struct PatchArgs *)malloc(sizeof(*args));
            strncpy(args->game_dir, path, MAX_PATH - 1);
            args->game_dir[MAX_PATH - 1] = '\0';
            args->do_backup = (SendMessageA(g_hwnd_chk_backup,
                                            BM_GETCHECK, 0, 0) == BST_CHECKED);
            args->do_verify = (SendMessageA(g_hwnd_chk_verify,
                                            BM_GETCHECK, 0, 0) == BST_CHECKED);
            CloseHandle(CreateThread(NULL, 0, patch_thread, args, 0, NULL));
        } else if (id == IDC_BTN_CANCEL) {
            DestroyWindow(hwnd);
        }
        break;
    }

    case WM_PATCH_DONE:
        if (wp) {
            log_append("Done — game updated successfully.");
            MessageBoxA(hwnd, "Patch applied successfully!\nYour game has been updated.",
                        g_meta.app_name[0] ? g_meta.app_name : "PatchForge",
                        MB_OK | MB_ICONINFORMATION);
            if (g_meta.close_delay > 0) {
                g_close_countdown = g_meta.close_delay;
                char buf[64];
                snprintf(buf, sizeof(buf), "Done! Closing in %d seconds…", g_close_countdown);
                set_status(buf, COL_SUCCESS);
                SetTimer(hwnd, TIMER_CLOSE, 1000, NULL);
            } else {
                set_status("Patch applied successfully!", COL_SUCCESS);
            }
        } else {
            set_status("Patching failed. See log for details.", COL_ERROR);
            log_append("ERROR: Patch failed. Your game folder has not been modified.");
            MessageBoxA(hwnd,
                "Patching failed.\n\nYour game folder has not been modified.",
                "Error", MB_OK | MB_ICONERROR);
        }
        EnableWindow(g_hwnd_btn_patch, TRUE);
        break;

    case WM_TIMER:
        if (wp == TIMER_CLOSE) {
            g_close_countdown--;
            if (g_close_countdown <= 0) {
                KillTimer(hwnd, TIMER_CLOSE);
                DestroyWindow(hwnd);
            } else {
                char buf[64];
                snprintf(buf, sizeof(buf), "Done! Closing in %d seconds…", g_close_countdown);
                set_status(buf, COL_SUCCESS);
            }
        }
        break;

    case WM_PATCH_PROG:
        g_progress_pct = (int)wp;
        set_progress((int)wp);
        break;

    case WM_LOG_MSG: {
        char *s = (char *)lp;
        log_append(s);
        free(s);
        break;
    }

    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(hwnd, &ps);
        pfg_paint_background(hwnd, dc);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_ERASEBKGND: return 1;
    case WM_DESTROY: PostQuitMessage(0); break;
    }
    return DefWindowProcA(hwnd, msg, wp, lp);
}

/* ---- Common helper implementations ---- */
void log_message(const char *fmt, ...)
{
    char buf[512];
    va_list v; va_start(v, fmt);
    vsnprintf(buf, sizeof(buf), fmt, v);
    va_end(v);
    log_append(buf);
}
void set_status(const char *msg, COLORREF col)
{
    (void)col;
    if (g_hwnd_status) {
        SetWindowTextA(g_hwnd_status, msg);
        InvalidateRect(g_hwnd_status, NULL, TRUE);
    }
}
void set_progress(int pct)
{
    if (!g_hwnd_progress) return;
    InvalidateRect(g_hwnd_progress, NULL, TRUE);
    UpdateWindow(g_hwnd_progress);
}
int browse_for_file(HWND owner, char *out, int out_len, const char *filter)
{
    (void)filter;
    return browse_for_folder(owner, out, out_len);
}
int find_target_file(char *out_path, int out_len) { (void)out_path; (void)out_len; return 0; }
int do_patch(const char *t, const char *d, size_t s) { (void)t;(void)d;(void)s; return 0; }

/* ---- WinMain ---- */
int WINAPI WinMain(HINSTANCE hi, HINSTANCE hp, LPSTR cmd, int show)
{
    (void)hp;

    if (cmd && cmd[0]) {
        char *src = cmd, *dst = g_preset_path, *end = g_preset_path + MAX_PATH - 1;
        if (*src == '"') src++;
        while (*src && *src != '"' && dst < end) *dst++ = *src++;
        *dst = '\0';
    }

    CoInitialize(NULL);

    if (!read_patch_meta_impl(&g_meta, NULL, &g_patch_data, &g_patch_size)) {
        MessageBoxA(NULL,
            "This patcher is not a valid PatchForge patch.\n"
            "The patch data may be missing or corrupted.",
            "PatchForge", MB_OK | MB_ICONERROR);
        return 1;
    }

    pfg_load_backdrop();

    g_brush_bg    = CreateSolidBrush(COL_BG);
    g_brush_light = CreateSolidBrush(COL_BG_LIGHT);
    g_brush_log   = CreateSolidBrush(COL_LOG_BG);
    g_font_normal = CreateFontA(14, 0, 0, 0, FW_NORMAL, 0, 0, 0,
                                DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY,
                                DEFAULT_PITCH, "Segoe UI");
    g_font_title  = CreateFontA(22, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0,
                                DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY,
                                DEFAULT_PITCH, "Segoe UI");

    WNDCLASSEXA wc = {0};
    wc.cbSize        = sizeof(wc);
    wc.style         = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc   = WndProc;
    wc.hInstance     = hi;
    wc.hCursor       = LoadCursor(NULL, IDC_ARROW);
    wc.hbrBackground = g_brush_bg;
    wc.lpszClassName = "PatchForgeStub";
    wc.hIcon         = LoadIcon(GetModuleHandle(NULL), MAKEINTRESOURCE(1));
    if (!wc.hIcon) wc.hIcon = LoadIcon(NULL, IDI_APPLICATION);
    RegisterClassExA(&wc);

    const char *title = g_meta.window_title[0] ? g_meta.window_title :
                        (g_meta.app_name[0] ? g_meta.app_name : "PatchForge Patcher");
    DWORD wstyle = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    RECT wr = {0, 0, 720, 412};
    AdjustWindowRect(&wr, wstyle, FALSE);
    HWND hwnd = CreateWindowExA(
        0, "PatchForgeStub", title, wstyle,
        CW_USEDEFAULT, CW_USEDEFAULT,
        wr.right - wr.left, wr.bottom - wr.top,
        NULL, NULL, hi, NULL);

    ShowWindow(hwnd, show);
    UpdateWindow(hwnd);

    MSG msg;
    while (GetMessageA(&msg, NULL, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }

    free(g_meta.checksums);
    free(g_meta.source_checksums);
    free(g_patch_data);
    if (g_backdrop_bmp) DeleteObject(g_backdrop_bmp);
    DeleteObject(g_brush_bg);
    DeleteObject(g_brush_light);
    DeleteObject(g_brush_log);
    DeleteObject(g_font_normal);
    DeleteObject(g_font_title);
    CoUninitialize();
    return (int)msg.wParam;
}

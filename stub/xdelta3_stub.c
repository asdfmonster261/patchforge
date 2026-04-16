/*
 * xdelta3_stub.c — PatchForge Windows patcher stub (xdelta3 engine, directory mode)
 *
 * Compiled with MinGW-w64.  Patch data is a PFMD container (dir_patch_format.h)
 * where each modified file has its own xdelta3 patch, new files are raw content,
 * and deleted files are flagged for removal.
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

/* ---- xdelta3 in library mode ---- */
#define XD3_MAIN  0
#define XD3_WIN32 1
#define XD3_DEBUG 0
#define XD3_USE_LARGEFILE64 1
#define SECONDARY_DJW 1
#define SECONDARY_FGK 1
#define EXTERNAL_COMPRESSION 0
#include "../../source_code/xdelta3/xdelta3.h"
#include "../../source_code/xdelta3/xdelta3.c"

/* ---- Globals ---- */
HWND g_hwnd           = NULL;
HWND g_hwnd_status    = NULL;
HWND g_hwnd_progress  = NULL;
HWND g_hwnd_filepath  = NULL;
HWND g_hwnd_log       = NULL;
HWND g_hwnd_btn_patch = NULL;
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

PatchMeta g_meta;
static char  *g_patch_data = NULL;
static size_t g_patch_size = 0;
static int    g_patch_result = 0;

/* ---- xdelta3 streaming decode: old_path + in-memory patch -> new_path ---- */
static int xd3_decode_file(const char *old_path, const unsigned char *patch_data,
                            size_t patch_size, const char *new_path)
{
    FILE *fold = fopen(old_path, "rb");
    FILE *fnew = fopen(new_path, "wb");
    if (!fold || !fnew) {
        if (fold) fclose(fold);
        if (fnew) fclose(fnew);
        return 0;
    }

    xd3_stream stream;
    xd3_config config;
    xd3_source source;
    memset(&stream, 0, sizeof(stream));
    memset(&source, 0, sizeof(source));
    xd3_init_config(&config, XD3_ADLER32);
    config.winsize = 1024 * 1024;
    xd3_config_stream(&stream, &config);

    /* Load source (old) file into memory */
    fseek(fold, 0, SEEK_END);
    long src_size = ftell(fold);
    if (src_size < 0) { fclose(fold); fclose(fnew); return 0; }
    fseek(fold, 0, SEEK_SET);
    uint8_t *src_buf = NULL;
    if (src_size > 0) {
        src_buf = (uint8_t *)malloc(src_size);
        if (!src_buf) { fclose(fold); fclose(fnew); return 0; }
        if ((long)fread(src_buf, 1, src_size, fold) != src_size) {
            free(src_buf); fclose(fold); fclose(fnew); return 0;
        }
    }
    fclose(fold);

    source.blksize   = (usize_t)(src_size > 0 ? src_size : 1);
    source.curblkno  = 0;
    source.curblk    = src_buf ? src_buf : (const uint8_t *)"";
    source.onblk     = (usize_t)src_size;
    source.eof_known = 1;
    source.max_blkno = 0;
    source.onlastblk = (usize_t)src_size;
    xd3_set_source(&stream, &source);

    size_t inp_pos = 0;
    int ret = 0, done = 0;

    while (!done) {
        size_t avail = patch_size - inp_pos;
        size_t chunk = avail < (size_t)config.winsize ? avail : config.winsize;
        xd3_avail_input(&stream, patch_data + inp_pos, chunk);
        inp_pos += chunk;

    process:
        ret = xd3_decode_input(&stream);
        switch (ret) {
        case XD3_INPUT:
            if (inp_pos >= patch_size) {
                /* All input consumed — signal EOF to xdelta3 via flush. */
                if (!(stream.flags & XD3_FLUSH)) {
                    xd3_set_flags(&stream, XD3_FLUSH | stream.flags);
                    xd3_avail_input(&stream, patch_data + patch_size, 0);
                    goto process;
                }
                /* Already flushing and still asked for input: clean EOF. */
                ret = 0;
                done = 1;
            }
            break;
        case XD3_OUTPUT:
            if (fwrite(stream.next_out, 1, stream.avail_out, fnew) != stream.avail_out)
                { ret = -1; done = 1; break; }
            xd3_consume_output(&stream);
            goto process;
        case XD3_GETSRCBLK:
            stream.src->curblk   = src_buf ? src_buf : (const uint8_t *)"";
            stream.src->onblk    = (usize_t)src_size;
            stream.src->curblkno = stream.src->getblkno;
            goto process;
        case XD3_GOTHEADER:
        case XD3_WINSTART:
        case XD3_WINFINISH:
            goto process;
        default:
            done = 1;
            break;
        }
    }

    fclose(fnew);
    free(src_buf);
    int ok = (ret == 0 || ret == XD3_WINFINISH);
    xd3_close_stream(&stream);
    xd3_free_stream(&stream);
    return ok;
}

/* ---- PFMD entry callback ---- */
struct DirPatchCtx {
    const char *game_dir;
    char err_msg[MAX_PATH + 128];
    int  had_error;
};

static int xd3_apply_entry(int op, const char *rel_path,
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
        /* Write output to a temp file in the same directory as the target so
         * MoveFileEx is always a same-drive rename (avoids cross-device failure). */
        char parent[MAX_PATH], tmp_out[MAX_PATH];
        pfmd_parent_dir(full_path, parent);
        pfmd_ensure_parent_dirs(full_path);
        if (!GetTempFileNameA(parent, "pfgx", 0, tmp_out)) {
            snprintf(ctx->err_msg, sizeof(ctx->err_msg),
                "ERROR: cannot create temp file for: %s", rel_path);
            ctx->had_error = 1;
            return 0;
        }
        DeleteFileA(tmp_out);  /* GetTempFileName creates a placeholder; remove it */

        int ok = xd3_decode_file(full_path, data, data_len, tmp_out);
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
static int apply_dir_xdelta3(const char *game_dir,
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
                           xd3_apply_entry, &ctx);

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

    int ok = apply_dir_xdelta3(a->game_dir, g_patch_data, g_patch_size);

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
            20, 18, 560, 32, hwnd, NULL, NULL, NULL);
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
                    20, 56, 560, 16, hwnd, NULL, NULL, NULL);
                SendMessageA(clbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            }
        }

        /* Description */
        if (g_meta.description[0]) {
            HWND desc = CreateWindowExA(0, "STATIC", g_meta.description,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                20, 74, 560, 16, hwnd, NULL, NULL, NULL);
            SendMessageA(desc, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        }

        /* Game folder row */
        HWND flbl = CreateWindowExA(0, "STATIC", "Game folder:",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 118, 90, 18, hwnd, NULL, NULL, NULL);
        SendMessageA(flbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        g_hwnd_filepath = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
            115, 116, 370, 22, hwnd, (HMENU)IDC_FILEPATH, NULL, NULL);
        SendMessageA(g_hwnd_filepath, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        CreateWindowExA(0, "BUTTON", "Browse...",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            492, 116, 80, 22, hwnd, (HMENU)IDC_BTN_BROWSE, NULL, NULL);

        /* Backup checkbox */
        g_hwnd_chk_backup = CreateWindowExA(0, "BUTTON",
            "Create backup before patching",
            WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
            20, 148, 260, 20, hwnd, (HMENU)IDC_CHK_BACKUP, NULL, NULL);
        SendMessageA(g_hwnd_chk_backup, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        SendMessageA(g_hwnd_chk_backup, BM_SETCHECK, BST_CHECKED, 0);

        /* Verify checkbox */
        g_hwnd_chk_verify = CreateWindowExA(0, "BUTTON",
            "Verify after patching",
            WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
            20, 170, 260, 20, hwnd, (HMENU)IDC_CHK_VERIFY, NULL, NULL);
        SendMessageA(g_hwnd_chk_verify, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        SendMessageA(g_hwnd_chk_verify, BM_SETCHECK, BST_CHECKED, 0);

        /* Log area */
        g_hwnd_log = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL |
            ES_READONLY | WS_VSCROLL,
            20, 202, 552, 110, hwnd, (HMENU)IDC_LOG, NULL, NULL);
        SendMessageA(g_hwnd_log, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Progress bar */
        g_hwnd_progress = CreateWindowExA(0, "STATIC", "",
            WS_CHILD | WS_VISIBLE | SS_OWNERDRAW,
            20, 324, 552, 12, hwnd, (HMENU)IDC_PROGRESS, NULL, NULL);

        /* Status */
        g_hwnd_status = CreateWindowExA(0, "STATIC",
            "Select the game folder and click Patch.",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 342, 440, 18, hwnd, (HMENU)IDC_STATUS, NULL, NULL);
        SendMessageA(g_hwnd_status, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Patch / Cancel buttons */
        g_hwnd_btn_patch = CreateWindowExA(0, "BUTTON", "Patch",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            412, 370, 80, 28, hwnd, (HMENU)IDC_BTN_PATCH, NULL, NULL);
        CreateWindowExA(0, "BUTTON", "Cancel",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            500, 370, 72, 28, hwnd, (HMENU)IDC_BTN_CANCEL, NULL, NULL);

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
                    20, 374, 385, 20, hwnd, NULL, NULL, NULL);
                SendMessageA(infolbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            }
            if (g_meta.version[0]) {
                char verbuf[80] = {0};
                snprintf(verbuf, sizeof(verbuf), "Version: %s", g_meta.version);
                HWND verlbl = CreateWindowExA(0, "STATIC", verbuf,
                    WS_CHILD | WS_VISIBLE | SS_LEFT | SS_ENDELLIPSIS,
                    20, 394, 385, 20, hwnd, NULL, NULL, NULL);
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
            set_status("Patch applied successfully!", COL_SUCCESS);
            log_append("Done — game updated successfully.");
            MessageBoxA(hwnd, "Patch applied successfully!\nYour game has been updated.",
                        g_meta.app_name[0] ? g_meta.app_name : "PatchForge",
                        MB_OK | MB_ICONINFORMATION);
        } else {
            set_status("Patching failed. See log for details.", COL_ERROR);
            log_append("ERROR: Patch failed. Your game folder has not been modified.");
            MessageBoxA(hwnd,
                "Patching failed.\n\nYour game folder has not been modified.",
                "Error", MB_OK | MB_ICONERROR);
        }
        EnableWindow(g_hwnd_btn_patch, TRUE);
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
    RECT wr = {0, 0, 600, 428};
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

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

/* ---- Globals ---- */
HWND g_hwnd           = NULL;
HWND g_hwnd_status    = NULL;
HWND g_hwnd_progress  = NULL;
HWND g_hwnd_filepath  = NULL;
HWND g_hwnd_log       = NULL;
HWND g_hwnd_btn_patch = NULL;
HWND g_hwnd_chk_backup = NULL;
HBRUSH g_brush_bg    = NULL;
HBRUSH g_brush_light = NULL;
HFONT g_font_normal  = NULL;
HFONT g_font_title   = NULL;
char g_exe_path[MAX_PATH] = {0};

#include "stub_common.h"
#include "dir_patch_format.h"

#define WM_PATCH_DONE  (WM_USER + 1)
#define WM_PATCH_PROG  (WM_USER + 2)
#define WM_LOG_MSG     (WM_USER + 3)
#define IDC_CHK_BACKUP 1008

/* jdiff binary opcodes */
#define JESC     0xF5
#define JMOD     0xF5   /* modify: out = old ^ patch_byte  */
#define JINS     0xF6   /* insert: out = patch_byte        */
#define JDEL     0xF7   /* delete N source bytes           */
#define JBKT     0xF8   /* backtrack N source bytes        */
#define JEQL     0xF9   /* copy N source bytes to output   */
#define JLITESC  0xFA   /* literal 0xF5 output byte        */

PatchMeta g_meta;
static char  *g_patch_data = NULL;
static size_t g_patch_size = 0;
static int    g_patch_result = 0;

/* ---- Read a variable-length unsigned int from a FILE stream ---- */
static long jdiff_read_varint(FILE *f)
{
    unsigned char b;
    long acc = 0;
    int  shift = 0;
    while (fread(&b, 1, 1, f) == 1) {
        acc |= (long)(b & 0x7F) << shift;
        shift += 7;
        if (!(b & 0x80)) break;
    }
    return acc;
}

/*
 * Apply a jdiff patch (in-memory) to old_path, writing result to new_path.
 * The patch bytes are written to a temp file so the existing FILE-based loop
 * can operate on them without change.
 */
static int apply_jojodiff(const char *old_path, const unsigned char *patch_data,
                            uint32_t patch_len, const char *new_path)
{
    /* Write patch to temp file */
    char tmp_dir[MAX_PATH], tmp_patch[MAX_PATH];
    GetTempPathA(MAX_PATH, tmp_dir);
    GetTempFileNameA(tmp_dir, "pfgj", 0, tmp_patch);

    FILE *fptmp = fopen(tmp_patch, "wb");
    if (!fptmp) return 0;
    fwrite(patch_data, 1, patch_len, fptmp);
    fclose(fptmp);

    FILE *fold   = fopen(old_path,   "rb");
    FILE *fpatch = fopen(tmp_patch,  "rb");
    FILE *fnew   = fopen(new_path,   "wb");
    if (!fold || !fpatch || !fnew) {
        if (fold)   fclose(fold);
        if (fpatch) fclose(fpatch);
        if (fnew)   fclose(fnew);
        DeleteFileA(tmp_patch);
        return 0;
    }

    unsigned char op;
    int ok = 1;

    while (fread(&op, 1, 1, fpatch) == 1) {
        if (op < JESC) {
            /* Equal byte: advance source cursor; op IS the output byte. */
            unsigned char dummy;
            if (fread(&dummy, 1, 1, fold) != 1) { ok = 0; break; }
            fwrite(&op, 1, 1, fnew);
        } else {
            switch (op) {
            case JMOD: {
                unsigned char mod_byte, old_byte;
                if (fread(&mod_byte, 1, 1, fpatch) != 1) { ok = 0; goto done; }
                if (fread(&old_byte, 1, 1, fold)   != 1) { ok = 0; goto done; }
                unsigned char out_byte = old_byte ^ mod_byte;
                fwrite(&out_byte, 1, 1, fnew);
                break;
            }
            case JINS: {
                unsigned char ins_byte;
                if (fread(&ins_byte, 1, 1, fpatch) != 1) { ok = 0; goto done; }
                fwrite(&ins_byte, 1, 1, fnew);
                break;
            }
            case JDEL: {
                long len = jdiff_read_varint(fpatch);
                fseek(fold, len, SEEK_CUR);
                break;
            }
            case JBKT: {
                long len = jdiff_read_varint(fpatch);
                fseek(fold, -len, SEEK_CUR);
                break;
            }
            case JEQL: {
                long len = jdiff_read_varint(fpatch);
                unsigned char *buf = (unsigned char *)malloc(len);
                if (!buf) { ok = 0; goto done; }
                if ((long)fread(buf, 1, len, fold) != len) { free(buf); ok = 0; goto done; }
                fwrite(buf, 1, len, fnew);
                free(buf);
                break;
            }
            case JLITESC: {
                unsigned char esc = JESC;
                fwrite(&esc, 1, 1, fnew);
                break;
            }
            default:
                /* Unknown opcode — skip silently */
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
        fwrite(data, 1, data_len, f);
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
};

static DWORD WINAPI patch_thread(LPVOID arg)
{
    struct PatchArgs *a = (struct PatchArgs *)arg;

    PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Reading patch data..."));
    PostMessageA(g_hwnd, WM_PATCH_PROG, 5, 0);

    if (a->do_backup) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Creating backup..."));
        char backup[MAX_PATH];
        snprintf(backup, MAX_PATH, "%s_pfg_backup", a->game_dir);
        if (!pfmd_copy_dir(a->game_dir, backup)) {
            PostMessageA(g_hwnd, WM_LOG_MSG, 0,
                (LPARAM)_strdup("WARNING: backup incomplete, continuing anyway."));
        } else {
            char msg[MAX_PATH + 32];
            snprintf(msg, sizeof(msg), "Backup saved: %s", backup);
            PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup(msg));
        }
    }

    PostMessageA(g_hwnd, WM_PATCH_PROG, 15, 0);
    PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Applying patch (in-place)..."));

    int ok = apply_dir_jojodiff(a->game_dir, g_patch_data, g_patch_size);

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
            20, 18, 560, 28, hwnd, NULL, NULL, NULL);
        SendMessageA(lbl, WM_SETFONT, (WPARAM)g_font_title, TRUE);

        /* Version */
        if (g_meta.version[0]) {
            char vbuf[128];
            snprintf(vbuf, sizeof(vbuf), "Version %s", g_meta.version);
            HWND vlbl = CreateWindowExA(0, "STATIC", vbuf,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                20, 50, 300, 16, hwnd, NULL, NULL, NULL);
            SendMessageA(vlbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        }

        /* Description */
        if (g_meta.description[0]) {
            HWND desc = CreateWindowExA(0, "STATIC", g_meta.description,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                20, 70, 560, 16, hwnd, NULL, NULL, NULL);
            SendMessageA(desc, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        }

        /* Game folder row */
        HWND flbl = CreateWindowExA(0, "STATIC", "Game folder:",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 100, 90, 18, hwnd, NULL, NULL, NULL);
        SendMessageA(flbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        g_hwnd_filepath = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
            115, 98, 370, 22, hwnd, (HMENU)IDC_FILEPATH, NULL, NULL);
        SendMessageA(g_hwnd_filepath, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        CreateWindowExA(0, "BUTTON", "Browse...",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            492, 98, 80, 22, hwnd, (HMENU)IDC_BTN_BROWSE, NULL, NULL);

        /* Backup checkbox */
        g_hwnd_chk_backup = CreateWindowExA(0, "BUTTON",
            "Create backup before patching",
            WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
            20, 130, 260, 20, hwnd, (HMENU)IDC_CHK_BACKUP, NULL, NULL);
        SendMessageA(g_hwnd_chk_backup, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        SendMessageA(g_hwnd_chk_backup, BM_SETCHECK, BST_CHECKED, 0);

        /* Log area */
        g_hwnd_log = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL |
            ES_READONLY | WS_VSCROLL,
            20, 162, 552, 110, hwnd, (HMENU)IDC_LOG, NULL, NULL);
        SendMessageA(g_hwnd_log, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Progress bar */
        g_hwnd_progress = CreateWindowExA(0, "STATIC", "",
            WS_CHILD | WS_VISIBLE | SS_OWNERDRAW,
            20, 284, 552, 12, hwnd, (HMENU)IDC_PROGRESS, NULL, NULL);

        /* Status */
        g_hwnd_status = CreateWindowExA(0, "STATIC",
            "Select the game folder and click Patch.",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 302, 440, 18, hwnd, (HMENU)IDC_STATUS, NULL, NULL);
        SendMessageA(g_hwnd_status, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Patch / Cancel buttons */
        g_hwnd_btn_patch = CreateWindowExA(0, "BUTTON", "Patch",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            412, 330, 80, 28, hwnd, (HMENU)IDC_BTN_PATCH, NULL, NULL);
        CreateWindowExA(0, "BUTTON", "Cancel",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            500, 330, 72, 28, hwnd, (HMENU)IDC_BTN_CANCEL, NULL, NULL);

        /* Auto-detect game folder */
        char auto_path[MAX_PATH] = {0};
        if (strcmp(g_meta.find_method, "registry") == 0)
            find_via_registry(&g_meta, auto_path, MAX_PATH);
        else if (strcmp(g_meta.find_method, "ini") == 0)
            find_via_ini(&g_meta, auto_path, MAX_PATH);
        if (auto_path[0])
            SetWindowTextA(g_hwnd_filepath, auto_path);

        log_append("Engine: JojoDiff (directory patch)");
        if (g_meta.version[0]) {
            char b[128]; snprintf(b, sizeof(b), "Version: %s", g_meta.version);
            log_append(b);
        }
        break;
    }

    case WM_CTLCOLORSTATIC:
    case WM_CTLCOLOREDIT: {
        HDC dc = (HDC)wp;
        SetTextColor(dc, COL_TEXT);
        SetBkColor(dc, COL_BG_LIGHT);
        return (LRESULT)g_brush_light;
    }

    case WM_DRAWITEM: {
        DRAWITEMSTRUCT *dis = (DRAWITEMSTRUCT *)lp;
        if (dis->CtlID == IDC_PROGRESS) {
            RECT r = dis->rcItem;
            HBRUSH bg = CreateSolidBrush(COL_PROGRESS_BG);
            FillRect(dis->hDC, &r, bg);
            DeleteObject(bg);
            if (g_progress_pct > 0) {
                RECT f = r;
                f.right = r.left + (int)((r.right - r.left) * g_progress_pct / 100);
                HBRUSH ac = CreateSolidBrush(COL_ACCENT);
                FillRect(dis->hDC, &f, ac);
                DeleteObject(ac);
            }
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
            EnableWindow(g_hwnd_btn_patch, FALSE);
            set_status("Patching...", COL_TEXT);
            struct PatchArgs *args = (struct PatchArgs *)malloc(sizeof(*args));
            strncpy(args->game_dir, path, MAX_PATH - 1);
            args->game_dir[MAX_PATH - 1] = '\0';
            args->do_backup = (SendMessageA(g_hwnd_chk_backup,
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
        RECT r;
        GetClientRect(hwnd, &r);
        FillRect(dc, &r, g_brush_bg);
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
    (void)hp; (void)cmd;

    if (!read_patch_meta_impl(&g_meta, NULL, &g_patch_data, &g_patch_size)) {
        MessageBoxA(NULL,
            "This patcher is not a valid PatchForge patch.\n"
            "The patch data may be missing or corrupted.",
            "PatchForge", MB_OK | MB_ICONERROR);
        return 1;
    }

    g_brush_bg    = CreateSolidBrush(COL_BG);
    g_brush_light = CreateSolidBrush(COL_BG_LIGHT);
    g_font_normal = CreateFontA(14, 0, 0, 0, FW_NORMAL, 0, 0, 0,
                                DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY,
                                DEFAULT_PITCH, "Segoe UI");
    g_font_title  = CreateFontA(18, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0,
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
    wc.hIcon         = LoadIcon(NULL, IDI_APPLICATION);
    RegisterClassExA(&wc);

    const char *title = g_meta.app_name[0] ? g_meta.app_name : "PatchForge Patcher";
    HWND hwnd = CreateWindowExA(
        0, "PatchForgeStub", title,
        WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
        CW_USEDEFAULT, CW_USEDEFAULT, 600, 380,
        NULL, NULL, hi, NULL);

    ShowWindow(hwnd, show);
    UpdateWindow(hwnd);

    MSG msg;
    while (GetMessageA(&msg, NULL, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }

    free(g_patch_data);
    DeleteObject(g_brush_bg);
    DeleteObject(g_brush_light);
    DeleteObject(g_font_normal);
    DeleteObject(g_font_title);
    return (int)msg.wParam;
}

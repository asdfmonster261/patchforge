/*
 * hdiffpatch_stub.c — PatchForge Windows patcher stub (HDiffPatch engine)
 *
 * Compiled with MinGW-w64 for Win32/Win64.
 * HDiffPatch apply logic (patch.c) is compiled in directly — no temp files.
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

/* ---- Pull in HDiffPatch apply core ---- */
/* Compression plugins (_CompressPlugin_lzma etc.) set via -D flags in Makefile.
   _CompressPlugin_zlib and _CompressPlugin_bz2 added by full/full32 targets. */
#define _IS_NEED_DEFAULT_ChecksumPlugin 0
#define _IS_NEED_ALL_ChecksumPlugin     0
#define _IS_NEED_DEFAULT_CompressPlugin 0
#define _IS_NEED_ALL_CompressPlugin     0
#define _IS_NEED_DIR_DIFF_PATCH         0
#define _IS_NEED_BSDIFF                 0
#define _IS_NEED_VCDIFF                 0
#define _IS_USED_MULTITHREAD            0

#include "../../source_code/hdiffpatch/libHDiffPatch/HPatch/patch.h"
#include "../../source_code/hdiffpatch/file_for_patch.h"
#include "../../source_code/hdiffpatch/decompress_plugin_demo.h"

/* ---- Globals ---- */
HWND g_hwnd         = NULL;
HWND g_hwnd_status  = NULL;
HWND g_hwnd_progress= NULL;
HWND g_hwnd_filepath= NULL;
HWND g_hwnd_log     = NULL;
HWND g_hwnd_btn_patch = NULL;
HBRUSH g_brush_bg    = NULL;
HBRUSH g_brush_light = NULL;
HFONT g_font_normal  = NULL;
HFONT g_font_title   = NULL;
char g_exe_path[MAX_PATH] = {0};

#include "stub_common.h"

/* ---- Control IDs ---- */
#define WM_PATCH_DONE  (WM_USER + 1)
#define WM_PATCH_PROG  (WM_USER + 2)
#define WM_LOG_MSG     (WM_USER + 3)

/* ---- Patch metadata ---- */
PatchMeta g_meta;
static char  *g_patch_data = NULL;
static size_t g_patch_size = 0;
static int    g_patch_result = 0;

/* ---- Apply patch using libHDiffPatch file stream API ---- */
static int apply_hdiff(const char *old_path, const char *new_path,
                       const char *patch_data, size_t patch_size)
{
    /* Write patch data to temp file */
    char tmp_dir[MAX_PATH], tmp_patch[MAX_PATH];
    GetTempPathA(MAX_PATH, tmp_dir);
    GetTempFileNameA(tmp_dir, "pfgp", 0, tmp_patch);
    FILE *fp = fopen(tmp_patch, "wb");
    if (!fp) return 0;
    fwrite(patch_data, 1, patch_size, fp);
    fclose(fp);

    /* Open file streams */
    hpatch_TFileStreamInput  old_stream, diff_stream;
    hpatch_TFileStreamOutput new_stream;
    hpatch_TFileStreamInput_init(&old_stream);
    hpatch_TFileStreamInput_init(&diff_stream);
    hpatch_TFileStreamOutput_init(&new_stream);

    int ok = 0;
    if (!hpatch_TFileStreamInput_open(&old_stream,  old_path))   goto cleanup;
    if (!hpatch_TFileStreamInput_open(&diff_stream, tmp_patch))  goto cleanup;

    /* Determine new file size from diff header */
    hpatch_compressedDiffInfo info;
    if (!getCompressedDiffInfo(&info, &diff_stream.base))        goto cleanup;
    hpatch_TFileStreamInput_setOffset(&diff_stream, 0);

    if (!hpatch_TFileStreamOutput_open(&new_stream, new_path, info.newDataSize)) goto cleanup;

    /* Select decompressor by compressType string from diff header */
    hpatch_TDecompress *dec = NULL;
    {
        /* Build table of all compiled-in plugins */
        hpatch_TDecompress *plugins[8];
        int nplugins = 0;
#ifdef _CompressPlugin_lzma
        static hpatch_TDecompress lzma_dec  = {0};
        static hpatch_TDecompress lzma2_dec = {0};
        lzma_dec  = lzmaDecompressPlugin;
        lzma2_dec = lzma2DecompressPlugin;
        plugins[nplugins++] = &lzma_dec;
        plugins[nplugins++] = &lzma2_dec;
#endif
#ifdef _CompressPlugin_zlib
        static hpatch_TDecompress zlib_dec      = {0};
        static hpatch_TDecompress zlib_dec_df   = {0};
        zlib_dec    = zlibDecompressPlugin;
        zlib_dec_df = zlibDecompressPlugin_deflate;
        plugins[nplugins++] = &zlib_dec;
        plugins[nplugins++] = &zlib_dec_df;
#endif
#ifdef _CompressPlugin_bz2
        static hpatch_TDecompress bz2_dec = {0};
        bz2_dec = bz2DecompressPlugin;
        plugins[nplugins++] = &bz2_dec;
#endif
        /* Find the one that claims it can open this compressType */
        for (int i = 0; i < nplugins; i++) {
            if (plugins[i]->is_can_open((const char*)info.compressType)) {
                dec = plugins[i];
                break;
            }
        }
        /* dec == NULL means no compression or unknown — patch_decompress_with_cache
           handles NULL as "no decompression needed" */
    }

    /* 4 MB patch cache */
    #define PATCH_CACHE_SIZE (4 * 1024 * 1024)
    hpatch_byte *cache = (hpatch_byte *)malloc(PATCH_CACHE_SIZE);
    if (!cache) goto cleanup;

    hpatch_BOOL result = patch_decompress_with_cache(
        &new_stream.base, &old_stream.base, &diff_stream.base,
        dec, cache, cache + PATCH_CACHE_SIZE);

    free(cache);
    ok = (result == hpatch_TRUE);

cleanup:
    hpatch_TFileStreamOutput_close(&new_stream);
    hpatch_TFileStreamInput_close(&diff_stream);
    hpatch_TFileStreamInput_close(&old_stream);
    DeleteFileA(tmp_patch);
    return ok;
}

/* ---- Patch worker thread ---- */
struct PatchArgs { char target[MAX_PATH]; };

static DWORD WINAPI patch_thread(LPVOID arg)
{
    struct PatchArgs *a = (struct PatchArgs *)arg;
    char tmp_out[MAX_PATH];
    char tmp_dir[MAX_PATH];

    PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Reading patch data..."));
    PostMessageA(g_hwnd, WM_PATCH_PROG, 10, 0);

    /* Write output to temp file first, then rename on success */
    GetTempPathA(MAX_PATH, tmp_dir);
    GetTempFileNameA(tmp_dir, "pfgo", 0, tmp_out);

    PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Applying patch..."));
    PostMessageA(g_hwnd, WM_PATCH_PROG, 20, 0);

    int ok = apply_hdiff(a->target, tmp_out, g_patch_data, g_patch_size);

    if (ok) {
        PostMessageA(g_hwnd, WM_PATCH_PROG, 90, 0);
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Replacing original file..."));

        /* Back up original, replace with patched */
        char backup[MAX_PATH];
        snprintf(backup, MAX_PATH, "%s.pfg_backup", a->target);
        MoveFileExA(a->target, backup, MOVEFILE_REPLACE_EXISTING);
        if (!MoveFileExA(tmp_out, a->target, MOVEFILE_REPLACE_EXISTING)) {
            /* Restore backup on failure */
            MoveFileExA(backup, a->target, MOVEFILE_REPLACE_EXISTING);
            ok = 0;
        } else {
            DeleteFileA(backup);
        }
    } else {
        DeleteFileA(tmp_out);
    }

    g_patch_result = ok;
    PostMessageA(g_hwnd, WM_PATCH_PROG, 100, 0);
    PostMessageA(g_hwnd, WM_PATCH_DONE, ok, 0);
    free(a);
    return 0;
}

/* ---- Window procedure ---- */
LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_CREATE: {
        g_hwnd = hwnd;
        enable_dark_titlebar(hwnd);

        /* Title label */
        HWND lbl = CreateWindowExA(0, "STATIC", g_meta.app_name[0] ? g_meta.app_name : "PatchForge Patcher",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 18, 560, 28, hwnd, NULL, NULL, NULL);
        SendMessageA(lbl, WM_SETFONT, (WPARAM)g_font_title, TRUE);

        /* Description */
        if (g_meta.description[0]) {
            HWND desc = CreateWindowExA(0, "STATIC", g_meta.description,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                20, 52, 560, 18, hwnd, NULL, NULL, NULL);
            SendMessageA(desc, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        }

        /* File path label */
        HWND flbl = CreateWindowExA(0, "STATIC", "Target file:",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 84, 120, 18, hwnd, NULL, NULL, NULL);
        SendMessageA(flbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* File path edit */
        g_hwnd_filepath = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
            20, 104, 440, 24, hwnd, (HMENU)IDC_FILEPATH, NULL, NULL);
        SendMessageA(g_hwnd_filepath, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Browse button */
        CreateWindowExA(0, "BUTTON", "Browse...",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            468, 104, 80, 24, hwnd, (HMENU)IDC_BTN_BROWSE, NULL, NULL);

        /* Log area */
        g_hwnd_log = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL |
            ES_READONLY | WS_VSCROLL,
            20, 144, 560, 120, hwnd, (HMENU)IDC_LOG, NULL, NULL);
        SendMessageA(g_hwnd_log, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Progress bar (custom drawn via WM_PAINT on a static) */
        g_hwnd_progress = CreateWindowExA(0, "STATIC", "",
            WS_CHILD | WS_VISIBLE | SS_OWNERDRAW,
            20, 280, 560, 14, hwnd, (HMENU)IDC_PROGRESS, NULL, NULL);

        /* Status label */
        g_hwnd_status = CreateWindowExA(0, "STATIC", "Select target file and click Patch.",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 300, 460, 18, hwnd, (HMENU)IDC_STATUS, NULL, NULL);
        SendMessageA(g_hwnd_status, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Patch / Cancel buttons */
        g_hwnd_btn_patch = CreateWindowExA(0, "BUTTON", "Patch",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            420, 330, 80, 28, hwnd, (HMENU)IDC_BTN_PATCH, NULL, NULL);
        CreateWindowExA(0, "BUTTON", "Cancel",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            508, 330, 72, 28, hwnd, (HMENU)IDC_BTN_CANCEL, NULL, NULL);

        /* Auto-detect target path */
        char auto_path[MAX_PATH] = {0};
        if (strcmp(g_meta.find_method, "registry") == 0)
            find_via_registry(&g_meta, auto_path, MAX_PATH);
        else if (strcmp(g_meta.find_method, "ini") == 0)
            find_via_ini(&g_meta, auto_path, MAX_PATH);

        if (auto_path[0])
            SetWindowTextA(g_hwnd_filepath, auto_path);

        log_append("Engine: HDiffPatch");
        if (g_meta.version[0])  { char b[128]; snprintf(b,sizeof(b),"Version: %s", g_meta.version); log_append(b); }
        if (g_meta.compression[0]) { char b[128]; snprintf(b,sizeof(b),"Compression: %s", g_meta.compression); log_append(b); }
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
        int id = dis->CtlID;
        if (id == IDC_PROGRESS) {
            /* Custom progress bar */
            RECT r = dis->rcItem;
            HBRUSH bg = CreateSolidBrush(COL_PROGRESS_BG);
            FillRect(dis->hDC, &r, bg);
            DeleteObject(bg);
            /* filled portion drawn externally via InvalidateRect */
            return TRUE;
        }
        COLORREF bg = (id == IDC_BTN_PATCH) ? COL_ACCENT : COL_BG_LIGHT;
        paint_button(dis, bg, COL_TEXT);
        return TRUE;
    }

    case WM_COMMAND: {
        int id = LOWORD(wp);
        if (id == IDC_BTN_BROWSE) {
            char path[MAX_PATH] = {0};
            if (browse_for_file(hwnd, path, MAX_PATH, "All Files\0*.*\0\0"))
                SetWindowTextA(g_hwnd_filepath, path);
        } else if (id == IDC_BTN_PATCH) {
            char path[MAX_PATH] = {0};
            GetWindowTextA(g_hwnd_filepath, path, MAX_PATH);
            if (!path[0]) {
                set_status("Please select the target file first.", COL_ERROR);
                return 0;
            }
            if (GetFileAttributesA(path) == INVALID_FILE_ATTRIBUTES) {
                set_status("File not found.", COL_ERROR);
                return 0;
            }
            EnableWindow(g_hwnd_btn_patch, FALSE);
            set_status("Patching...", COL_TEXT);

            struct PatchArgs *args = (struct PatchArgs *)malloc(sizeof(struct PatchArgs));
            strncpy(args->target, path, MAX_PATH - 1);
            CloseHandle(CreateThread(NULL, 0, patch_thread, args, 0, NULL));
        } else if (id == IDC_BTN_CANCEL) {
            DestroyWindow(hwnd);
        }
        break;
    }

    case WM_PATCH_DONE:
        if (wp) {
            set_status("Patch applied successfully!", COL_SUCCESS);
            log_append("Done — patch applied successfully.");
            MessageBoxA(hwnd, "Patch applied successfully!", g_meta.app_name, MB_OK | MB_ICONINFORMATION);
        } else {
            set_status("Patching failed. See log for details.", COL_ERROR);
            log_append("ERROR: Patch failed.");
            MessageBoxA(hwnd, "Patching failed. The original file has not been modified.", "Error", MB_OK | MB_ICONERROR);
        }
        EnableWindow(g_hwnd_btn_patch, TRUE);
        break;

    case WM_PATCH_PROG:
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

    case WM_ERASEBKGND:
        return 1;

    case WM_DESTROY:
        PostQuitMessage(0);
        break;
    }
    return DefWindowProcA(hwnd, msg, wp, lp);
}

/* ---- Implementations of common helpers ---- */
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
    if (g_hwnd_status) {
        SetWindowTextA(g_hwnd_status, msg);
        InvalidateRect(g_hwnd_status, NULL, TRUE);
    }
}

static int g_progress_pct = 0;
void set_progress(int pct)
{
    g_progress_pct = pct;
    if (!g_hwnd_progress) return;
    RECT r;
    GetClientRect(g_hwnd_progress, &r);
    HDC dc = GetDC(g_hwnd_progress);
    HBRUSH bg = CreateSolidBrush(COL_PROGRESS_BG);
    FillRect(dc, &r, bg);
    DeleteObject(bg);
    if (pct > 0) {
        RECT filled = r;
        filled.right = r.left + (int)((r.right - r.left) * pct / 100);
        HBRUSH ac = CreateSolidBrush(COL_ACCENT);
        FillRect(dc, &filled, ac);
        DeleteObject(ac);
    }
    ReleaseDC(g_hwnd_progress, dc);
}

int browse_for_file(HWND owner, char *out, int out_len, const char *filter)
{
    OPENFILENAMEA ofn = {0};
    ofn.lStructSize = sizeof(ofn);
    ofn.hwndOwner   = owner;
    ofn.lpstrFilter = filter;
    ofn.lpstrFile   = out;
    ofn.nMaxFile    = out_len;
    ofn.Flags       = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST;
    return GetOpenFileNameA(&ofn);
}

/* ---- WinMain ---- */
int WINAPI WinMain(HINSTANCE hi, HINSTANCE hp, LPSTR cmd, int show)
{
    (void)hp; (void)cmd;

    /* Read patch metadata from appended data */
    if (!read_patch_meta_impl(&g_meta, NULL, &g_patch_data, &g_patch_size)) {
        MessageBoxA(NULL,
            "This patcher is not a valid PatchForge patch.\n"
            "The patch data may be missing or corrupted.",
            "PatchForge", MB_OK | MB_ICONERROR);
        return 1;
    }

    /* Create brushes and fonts */
    g_brush_bg    = CreateSolidBrush(COL_BG);
    g_brush_light = CreateSolidBrush(COL_BG_LIGHT);
    g_font_normal = CreateFontA(14, 0, 0, 0, FW_NORMAL, 0, 0, 0,
                                DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY,
                                DEFAULT_PITCH, "Segoe UI");
    g_font_title  = CreateFontA(18, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0,
                                DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY,
                                DEFAULT_PITCH, "Segoe UI");

    /* Register window class */
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
        CW_USEDEFAULT, CW_USEDEFAULT, 620, 400,
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

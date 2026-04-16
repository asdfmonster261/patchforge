/*
 * hdiffpatch_stub.c — PatchForge Windows patcher stub (HDiffPatch engine, directory mode)
 *
 * Compiled with MinGW-w64 for Win32/Win64.
 * Uses HDiffPatch TDirPatcher API for in-place directory patching.
 * Patch data is appended to this exe (see stub_common.h for layout).
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

/* ---- HDiffPatch dir-patch includes ----
   Compression plugins and dir patch mode set via -D flags in Makefile. */
#define _IS_NEED_DEFAULT_ChecksumPlugin 0
#define _IS_NEED_ALL_ChecksumPlugin     0
#define _IS_NEED_DEFAULT_CompressPlugin 0
#define _IS_NEED_ALL_CompressPlugin     0
#define _IS_NEED_BSDIFF                 0
#define _IS_NEED_VCDIFF                 0
#define _IS_USED_MULTITHREAD            0

#include "../../source_code/hdiffpatch/libHDiffPatch/HPatch/patch.h"
#include "../../source_code/hdiffpatch/file_for_patch.h"
#include "../../source_code/hdiffpatch/decompress_plugin_demo.h"
#include "../../source_code/hdiffpatch/hpatch_dir_listener.h"

/* ---- Globals ---- */
HWND g_hwnd          = NULL;
HWND g_hwnd_status   = NULL;
HWND g_hwnd_progress = NULL;
HWND g_hwnd_filepath = NULL;
HWND g_hwnd_log      = NULL;
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

/* ---- Extra control IDs ---- */
/* WM_PATCH_DONE/PROG/LOG_MSG and IDC_CHK_* are defined in stub_common.h */

/* ---- Patch metadata ---- */
PatchMeta g_meta;
static char  *g_patch_data = NULL;
static size_t g_patch_size = 0;
static int    g_patch_result = 0;

/* ---- Build decompressor table ---- */
static hpatch_TDecompress *_pick_decompressor(const char *compressType)
{
    hpatch_TDecompress *plugins[8];
    int n = 0;
#ifdef _CompressPlugin_lzma
    static hpatch_TDecompress lzma_dec  = {0};
    static hpatch_TDecompress lzma2_dec = {0};
    lzma_dec  = lzmaDecompressPlugin;
    lzma2_dec = lzma2DecompressPlugin;
    plugins[n++] = &lzma_dec;
    plugins[n++] = &lzma2_dec;
#endif
#ifdef _CompressPlugin_zlib
    static hpatch_TDecompress zlib_dec    = {0};
    static hpatch_TDecompress zlib_dec_df = {0};
    zlib_dec    = zlibDecompressPlugin;
    zlib_dec_df = zlibDecompressPlugin_deflate;
    plugins[n++] = &zlib_dec;
    plugins[n++] = &zlib_dec_df;
#endif
#ifdef _CompressPlugin_bz2
    static hpatch_TDecompress bz2_dec = {0};
    bz2_dec = bz2DecompressPlugin;
    plugins[n++] = &bz2_dec;
#endif
    for (int i = 0; i < n; i++)
        if (plugins[i]->is_can_open(compressType))
            return plugins[i];
    return NULL; /* no compression */
}

/* _copy_dir_recursive is now pfg_copy_dir in stub_common.h */

/* ---- Apply directory patch using TDirPatcher ---- */
static int apply_dir_hdiff(const char *game_dir,
                            const char *patch_data, size_t patch_size)
{
    char msg[MAX_PATH + 128];

    /* Write patch data to a temp file (system temp is fine — no rename needed) */
    char tmp_dir[MAX_PATH], tmp_patch[MAX_PATH], tmp_new[MAX_PATH];
    GetTempPathA(MAX_PATH, tmp_dir);

    GetTempFileNameA(tmp_dir, "pfgp", 0, tmp_patch);
    DeleteFileA(tmp_patch);

    {
        FILE *fp = fopen(tmp_patch, "wb");
        if (!fp) {
            PostMessageA(g_hwnd, WM_LOG_MSG, 0,
                (LPARAM)_strdup("ERROR: failed to create temp patch file"));
            return 0;
        }
        fwrite(patch_data, 1, patch_size, fp);
        fclose(fp);
    }

    /*
     * tmp_new MUST be on the same drive/filesystem as game_dir.
     * tempDirPatchListener uses rename() to move files back in-place;
     * rename() fails across drive letters (e.g. C:\Temp vs Z:\game on Wine).
     * Place tmp_new as a sibling of game_dir so they share the same device.
     */
    {
        char parent[MAX_PATH];
        strncpy(parent, game_dir, MAX_PATH - 1);
        parent[MAX_PATH - 1] = '\0';
        size_t plen = strlen(parent);
        if (plen > 1 && (parent[plen-1] == '\\' || parent[plen-1] == '/'))
            parent[--plen] = '\0';
        char *sep = strrchr(parent, '\\');
        if (!sep) sep = strrchr(parent, '/');
        if (sep) {
            *sep = '\0';
        } else {
            strncpy(parent, tmp_dir, MAX_PATH - 1);
        }
        GetTempFileNameA(parent, "pfgn", 0, tmp_new);
        DeleteFileA(tmp_new);
    }

    if (!CreateDirectoryA(tmp_new, NULL)) {
        snprintf(msg, sizeof(msg),
            "ERROR: failed to create temp new dir (err %lu)", GetLastError());
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup(msg));
        DeleteFileA(tmp_patch);
        return 0;
    }

    /* Open diff as stream */
    hpatch_TFileStreamInput diff_stream;
    hpatch_TFileStreamInput_init(&diff_stream);
    int ok = 0;

    if (!hpatch_TFileStreamInput_open(&diff_stream, tmp_patch)) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("ERROR: failed to open patch file stream"));
        goto cleanup_files;
    }

    /* Select decompressor */
    TDirDiffInfo ddi;
    memset(&ddi, 0, sizeof(ddi));
    if (!getDirDiffInfo(&ddi, &diff_stream.base)) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("ERROR: patch data corrupt or not a dir diff"));
        goto cleanup_stream;
    }
    hpatch_TFileStreamInput_setOffset(&diff_stream, 0);

    hpatch_TDecompress *dec = _pick_decompressor(
        (const char*)ddi.hdiffInfo.compressType);

    /* Initialise patcher */
    TDirPatcher patcher;
    TDirPatcher_init(&patcher);

    const TDirDiffInfo *pddi = NULL;
    if (!TDirPatcher_open(&patcher, &diff_stream.base, &pddi)) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("ERROR: TDirPatcher_open failed"));
        goto cleanup_patcher;
    }

    if (!TDirPatcher_loadDirData(&patcher, dec, game_dir, tmp_new)) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("ERROR: TDirPatcher_loadDirData failed"));
        goto cleanup_patcher;
    }

    /* Open streams */
    const hpatch_TStreamInput  *old_ref = NULL;
    const hpatch_TStreamOutput *new_dir_stream = NULL;

    if (!TDirPatcher_openOldRefAsStream(&patcher, 32, &old_ref)) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("ERROR: TDirPatcher_openOldRefAsStream failed"));
        goto cleanup_patcher;
    }

    /* tempDirPatchListener: patches to tmp_new, then patchFinish moves
     * everything back in-place. patchBegin/patchFinish must be called
     * manually — the library does not call them automatically. */
    IHPatchDirListener listener = tempDirPatchListener;
    listener.base.listenerImport = &listener;

    if (!listener.patchBegin(&listener, &patcher)) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("ERROR: patchBegin failed"));
        goto cleanup_refs;
    }

    if (!TDirPatcher_openNewDirAsStream(&patcher, &listener.base, &new_dir_stream)) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("ERROR: TDirPatcher_openNewDirAsStream failed"));
        listener.patchFinish(&listener, hpatch_FALSE);
        goto cleanup_refs;
    }

    PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Applying patch..."));

    #define DIR_CACHE_SIZE (4 * 1024 * 1024)
    hpatch_byte *cache = (hpatch_byte *)malloc(DIR_CACHE_SIZE);
    if (!cache) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("ERROR: out of memory for patch cache"));
        listener.patchFinish(&listener, hpatch_FALSE);
        goto cleanup_streams;
    }

    hpatch_BOOL patch_ok = TDirPatcher_patch(
        &patcher, new_dir_stream, old_ref,
        cache, cache + DIR_CACHE_SIZE, 1);

    free(cache);

    /* patchFinish moves files from tmp_new into game_dir in-place */
    hpatch_BOOL finish_ok = listener.patchFinish(&listener, patch_ok);
    ok = (patch_ok == hpatch_TRUE) && (finish_ok == hpatch_TRUE);

    if (!ok) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup(patch_ok ? "ERROR: file move-back failed" : "ERROR: patch failed"));
    }

cleanup_streams:
    TDirPatcher_closeNewDirStream(&patcher);
cleanup_refs:
    TDirPatcher_closeOldRefStream(&patcher);
cleanup_patcher:
    TDirPatcher_close(&patcher);
cleanup_stream:
    hpatch_TFileStreamInput_close(&diff_stream);
cleanup_files:
    DeleteFileA(tmp_patch);
    if (!ok) {
        /* Best-effort recursive delete of temp dir */
        char search[MAX_PATH];
        snprintf(search, MAX_PATH, "%s\\*", tmp_new);
        WIN32_FIND_DATAA fd;
        HANDLE h = FindFirstFileA(search, &fd);
        if (h != INVALID_HANDLE_VALUE) {
            do {
                if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..")) continue;
                char p[MAX_PATH];
                snprintf(p, MAX_PATH, "%s\\%s", tmp_new, fd.cFileName);
                DeleteFileA(p);
            } while (FindNextFileA(h, &fd));
            FindClose(h);
        }
        RemoveDirectoryA(tmp_new);
    }
    return ok;
}

/* ---- Patch worker thread ---- */
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

    /* Run before */
    if (g_meta.run_before[0]) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Running pre-patch command..."));
        pfg_run_and_wait(g_meta.run_before);
    }

    /* Backup */
    if (a->do_backup) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Creating backup..."));
        pfg_do_backup(a->game_dir, &g_meta);
    }

    PostMessageA(g_hwnd, WM_PATCH_PROG, 15, 0);
    PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Applying patch (in-place)..."));

    int ok = apply_dir_hdiff(a->game_dir, g_patch_data, g_patch_size);

    if (ok && a->do_verify) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Verifying..."));
        if (!verify_all_checksums(a->game_dir, &g_meta)) {
            ok = 0;
            PostMessageA(g_hwnd, WM_LOG_MSG, 0,
                (LPARAM)_strdup("WARNING: One or more files failed verification."));
        }
    }

    /* Extra files */
    if (ok && g_meta.num_extra_files > 0) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Installing extra files..."));
        pfg_write_extra_files(a->game_dir, &g_meta);
    }

    /* Run after */
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
    bi.hwndOwner = owner;
    bi.pszDisplayName = out;
    bi.lpszTitle = "Select game folder to patch:";
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    LPITEMIDLIST pidl = SHBrowseForFolderA(&bi);
    if (!pidl) return 0;
    SHGetPathFromIDListA(pidl, out);
    CoTaskMemFree(pidl);
    return 1;
}

/* ---- Window procedure ---- */
LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    static int g_progress_pct = 0;

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
                    20, 70, 560, 16, hwnd, NULL, NULL, NULL);
                SendMessageA(clbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            }
        }

        /* Description */
        if (g_meta.description[0]) {
            HWND desc = CreateWindowExA(0, "STATIC", g_meta.description,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                20, 88, 560, 16, hwnd, NULL, NULL, NULL);
            SendMessageA(desc, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        }

        /* Game folder row — shifted +18px to make room for change summary */
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
        g_hwnd_chk_backup = CreateWindowExA(0, "BUTTON", "Create backup before patching",
            WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
            20, 148, 260, 20, hwnd, (HMENU)IDC_CHK_BACKUP, NULL, NULL);
        SendMessageA(g_hwnd_chk_backup, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        SendMessageA(g_hwnd_chk_backup, BM_SETCHECK, BST_CHECKED, 0);

        /* Verify checkbox */
        g_hwnd_chk_verify = CreateWindowExA(0, "BUTTON", "Verify after patching",
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

        log_append("Engine: HDiffPatch (directory patch)");
        if (g_meta.version[0]) {
            char b[128]; snprintf(b, sizeof(b), "Version: %s", g_meta.version);
            log_append(b);
        }
        if (g_meta.compression[0]) {
            char b[128]; snprintf(b, sizeof(b), "Compression: %s", g_meta.compression);
            log_append(b);
        }
        if (g_meta.company_info[0]) {
            char b[256]; snprintf(b, sizeof(b), "Publisher: %s", g_meta.company_info);
            log_append(b);
        }
        if (g_meta.copyright[0]) {
            char b[256]; snprintf(b, sizeof(b), "%s", g_meta.copyright);
            log_append(b);
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
            /* Transparent text over backdrop — no opaque rectangle behind labels */
            SetBkMode(dc, TRANSPARENT);
            return (LRESULT)GetStockObject(NULL_BRUSH);
        }
        /* No backdrop: solid bg so no lighter rectangle behind labels/checkboxes */
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
            if (attr == INVALID_FILE_ATTRIBUTES ||
                !(attr & FILE_ATTRIBUTE_DIRECTORY)) {
                set_status("Folder not found. Please select a valid directory.", COL_ERROR);
                return 0;
            }
            /* Smart UAC: test write access; relaunch elevated if needed */
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

    case WM_ERASEBKGND:
        return 1;

    case WM_DESTROY:
        PostQuitMessage(0);
        break;
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
    /* Not used in dir mode — kept for stub_common.h compat */
    return browse_for_folder(owner, out, out_len);
}

int find_target_file(char *out_path, int out_len) { (void)out_path; (void)out_len; return 0; }
int do_patch(const char *t, const char *d, size_t s) { (void)t;(void)d;(void)s; return 0; }

/* ---- WinMain ---- */
int WINAPI WinMain(HINSTANCE hi, HINSTANCE hp, LPSTR cmd, int show)
{
    (void)hp;

    /* Parse first argument as pre-selected game folder (from UAC relaunch) */
    if (cmd && cmd[0]) {
        char *src = cmd;
        char *dst = g_preset_path;
        char *end = g_preset_path + MAX_PATH - 1;
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
    wc.hIcon         = LoadIcon(GetModuleHandle(NULL), MAKEINTRESOURCE(1));
    if (!wc.hIcon) wc.hIcon = LoadIcon(NULL, IDI_APPLICATION);
    RegisterClassExA(&wc);

    const char *title = g_meta.window_title[0] ? g_meta.window_title :
                        (g_meta.app_name[0] ? g_meta.app_name : "PatchForge Patcher");
    /* Compute outer window size from desired client area so the non-client
       frame (title bar + borders) never clips controls at the bottom. */
    DWORD wstyle = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    RECT wr = {0, 0, 600, 418};
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

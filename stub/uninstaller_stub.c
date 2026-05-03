/*
 * uninstaller_stub.c — PatchForge self-contained game uninstaller (Win32)
 *
 * At build time, the packager appends a data blob to this exe:
 *   [data JSON, UTF-8    ]
 *   [4B LE: data_len     ]
 *   [8B magic: "UNINST01"]
 *
 * Data JSON fields:
 *   app_name             — display name
 *   version              — version string
 *   company_info         — publisher name
 *   arp_subkey           — registry subkey under Uninstall\
 *   install_registry_key — game's own HKCU registry key (to delete)
 *   files                — [{path, component}, ...]
 *
 * At uninstall time:
 *   - Reads InstallLocation + InstalledComponents from A/RP registry
 *   - Deletes installed files, prunes empty dirs
 *   - Deletes game registry key + A/RP entry
 *   - Self-deletes via temp batch script
 */

#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0600
#include <windows.h>
#include <shellapi.h>
#include <dwmapi.h>
#include <shlobj.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ---- Colours (same dark palette as installer) ---- */
#define COL_BG          RGB(0x12, 0x12, 0x18)
#define COL_BG_LIGHT    RGB(0x20, 0x20, 0x2c)
#define COL_LOG_BG      RGB(0x18, 0x18, 0x20)
#define COL_HOVER       RGB(0x2c, 0x2c, 0x3c)
#define COL_PRESSED     RGB(0x3a, 0x3a, 0x55)
#define COL_ACCENT      RGB(0x42, 0x87, 0xf5)
#define COL_ACCENT_HOV  RGB(0x58, 0x97, 0xff)
#define COL_TEXT        RGB(0xd7, 0xd7, 0xe1)
#define COL_TEXT_DIM    RGB(0xa0, 0xa0, 0xb9)
#define COL_SUCCESS     RGB(0x3c, 0xb9, 0x69)
#define COL_ERROR       RGB(0xe6, 0x46, 0x46)
#define COL_BORDER      RGB(0x2a, 0x2a, 0x3a)
#define COL_PROGRESS_BG RGB(0x1a, 0x1a, 0x24)

/* ---- Control IDs ---- */
#define IDC_STATUS     1001
#define IDC_PROGRESS   1002
#define IDC_BTN_UNINST 1003
#define IDC_BTN_CANCEL 1004
#define IDC_LOG        1005

/* ---- Thread messages ---- */
#define WM_UNINST_DONE (WM_USER + 1)
#define WM_UNINST_PROG (WM_USER + 2)
#define WM_LOG_MSG     (WM_USER + 3)

/* ---- Limits ---- */
#define MAX_ERRORS          200
#define MAX_INSTALLED_COMPS  64   /* matches installer MAX_COMPONENTS with headroom */

typedef struct {
    char path[512];
    int  component;
} UninstFile;

/* ---- Global state ---- */
static char       g_own_path[MAX_PATH]           = {0};
static char       g_install_dir[MAX_PATH]        = {0};
static char       g_app_name[256]                = {0};
static char       g_version[64]                  = {0};
static char       g_company_info[256]            = {0};
static char       g_arp_subkey[256]              = {0};
static char       g_install_registry_key[512]    = {0};
static char       g_shortcut_name[256]           = {0};
static int        g_shortcut_create_desktop      = 0;
static int        g_shortcut_create_startmenu    = 0;
static char       g_user_desktop[MAX_PATH]       = {0};
static char       g_user_programs[MAX_PATH]      = {0};
static UninstFile *g_files                       = NULL;
static int         g_num_files                   = 0;
static int         g_installed_comps[MAX_INSTALLED_COMPS] = {0};
static int         g_num_installed_comps         = 0;
static HKEY        g_arp_hive                    = NULL;
static char        g_arp_key_path[512]           = {0};
static int         g_uninst_done                 = 0;
static int         g_relocated                   = 0;

static HWND   g_hwnd              = NULL;
static HWND   g_hwnd_progress     = NULL;
static HWND   g_hwnd_log          = NULL;
static HWND   g_hwnd_status       = NULL;
static HWND   g_hwnd_btn_uninst   = NULL;
static HWND   g_hwnd_btn_cancel   = NULL;
static HBRUSH g_brush_bg          = NULL;
static HBRUSH g_brush_light       = NULL;
static HBRUSH g_brush_log         = NULL;
static HFONT  g_font_normal       = NULL;
static HFONT  g_font_title        = NULL;
static int    g_btn_hover_uninst  = 0;
static int    g_btn_hover_cancel  = 0;

LRESULT CALLBACK WndProc(HWND, UINT, WPARAM, LPARAM);

/* ==================================================================== */
/* JSON helpers                                                          */
/* ==================================================================== */

static const char *json_get_str(const char *json, const char *key,
                                char *out, int out_len)
{
    char search[128];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return NULL;
    p += strlen(search);
    while (*p == ' ' || *p == ':') p++;
    if (*p != '"') return NULL;
    p++;
    int i = 0;
    while (*p && *p != '"' && i < out_len - 1) {
        if (*p == '\\' && *(p + 1)) p++;
        out[i++] = *p++;
    }
    out[i] = '\0';
    return out;
}

static int64_t json_get_int(const char *json, const char *key)
{
    char search[128];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return 0;
    p += strlen(search);
    while (*p == ' ' || *p == ':') p++;
    return (int64_t)_atoi64(p);
}

static int json_get_bool(const char *json, const char *key, int def)
{
    char search[128];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return def;
    p += strlen(search);
    while (*p == ' ' || *p == ':') p++;
    if (strncmp(p, "true",  4) == 0) return 1;
    if (strncmp(p, "false", 5) == 0) return 0;
    return def;
}

#include "path_safe.h"   /* pfg_path_is_safe() */

/* Parse "files":[{path,component},...] into g_files/g_num_files. */
static void json_parse_files(const char *json)
{
    const char *p = strstr(json, "\"files\"");
    if (!p) return;
    p = strchr(p, '[');
    if (!p) return;
    p++;

    int capacity = 4096;
    g_files = (UninstFile *)malloc(capacity * sizeof(UninstFile));
    if (!g_files) return;
    g_num_files = 0;

    while (1) {
        while (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t' || *p == ',') p++;
        if (*p == ']' || !*p) break;
        if (*p != '{') break;

        /* Locate the matching closing brace */
        const char *obj_start = p;
        const char *q = p + 1;
        int depth = 1;
        while (*q && depth > 0) {
            if      (*q == '{') depth++;
            else if (*q == '}') depth--;
            q++;
        }

        int obj_len = (int)(q - obj_start);
        char *tmp = (char *)malloc(obj_len + 1);
        if (!tmp) break;
        memcpy(tmp, obj_start, obj_len);
        tmp[obj_len] = '\0';

        if (g_num_files >= capacity) {
            capacity *= 2;
            UninstFile *nf = (UninstFile *)realloc(g_files, capacity * sizeof(UninstFile));
            if (!nf) { free(tmp); break; }
            g_files = nf;
        }

        UninstFile *uf = &g_files[g_num_files];
        memset(uf, 0, sizeof(*uf));
        json_get_str(tmp, "path", uf->path, sizeof(uf->path));
        uf->component = (int)json_get_int(tmp, "component");
        free(tmp);

        /* Refuse any entry whose path would escape the install dir. */
        if (uf->path[0] && pfg_path_is_safe(uf->path)) g_num_files++;
        p = q;
    }
}

/* ==================================================================== */
/* Read own UNINST01 data blob                                           */
/* ==================================================================== */

static int read_uninst_data(void)
{
    FILE *f = fopen(g_own_path, "rb");
    if (!f) return 0;

    /* Last 12 bytes: [4B data_len][8B "UNINST01"] */
    _fseeki64(f, -12, SEEK_END);
    uint32_t data_len = 0;
    char magic[9] = {0};
    fread(&data_len, 4, 1, f);
    fread(magic, 8, 1, f);

    if (memcmp(magic, "UNINST01", 8) != 0) { fclose(f); return 0; }

    _fseeki64(f, -(int64_t)(12 + data_len), SEEK_END);
    char *buf = (char *)malloc(data_len + 1);
    if (!buf) { fclose(f); return 0; }
    fread(buf, 1, data_len, f);
    buf[data_len] = '\0';
    fclose(f);

    json_get_str(buf, "app_name",             g_app_name,             sizeof(g_app_name));
    json_get_str(buf, "version",              g_version,              sizeof(g_version));
    json_get_str(buf, "company_info",         g_company_info,         sizeof(g_company_info));
    json_get_str(buf, "arp_subkey",           g_arp_subkey,           sizeof(g_arp_subkey));
    json_get_str(buf, "install_registry_key", g_install_registry_key, sizeof(g_install_registry_key));
    json_get_str(buf, "shortcut_name",        g_shortcut_name,        sizeof(g_shortcut_name));
    g_shortcut_create_desktop   = json_get_bool(buf, "shortcut_create_desktop",   0);
    g_shortcut_create_startmenu = json_get_bool(buf, "shortcut_create_startmenu", 0);
    json_parse_files(buf);

    free(buf);
    return 1;
}

/* ==================================================================== */
/* Read install location + installed components from A/RP registry       */
/* ==================================================================== */

static int read_arp_registry(void)
{
    if (!g_arp_subkey[0]) return 0;

    int klen = snprintf(g_arp_key_path, sizeof(g_arp_key_path),
        "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\%s",
        g_arp_subkey);
    if (klen < 0 || klen >= (int)sizeof(g_arp_key_path)) return 0;

    HKEY hives[2] = {HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER};
    for (int i = 0; i < 2; i++) {
        HKEY hkey = NULL;
        if (RegOpenKeyExA(hives[i], g_arp_key_path, 0, KEY_READ, &hkey) != ERROR_SUCCESS)
            continue;

        DWORD sz = MAX_PATH;
        RegQueryValueExA(hkey, "InstallLocation", NULL, NULL,
                         (BYTE *)g_install_dir, &sz);

        char comp_str[256] = {0};
        sz = sizeof(comp_str);
        RegQueryValueExA(hkey, "InstalledComponents", NULL, NULL,
                         (BYTE *)comp_str, &sz);
        RegCloseKey(hkey);

        /* Parse "0,1,2" → int array */
        g_num_installed_comps = 0;
        char *ctx = NULL;
        char *tok = strtok_s(comp_str, ",", &ctx);
        while (tok && g_num_installed_comps < MAX_INSTALLED_COMPS) {
            /* skip empty tokens and non-numeric garbage */
            char *end = tok;
            while (*end == ' ') end++;
            if (*end >= '0' && *end <= '9')
                g_installed_comps[g_num_installed_comps++] = atoi(end);
            tok = strtok_s(NULL, ",", &ctx);
        }

        g_arp_hive = hives[i];
        return 1;
    }
    return 0;
}

/* ==================================================================== */
/* UI helpers                                                            */
/* ==================================================================== */

static void set_status(const char *msg)
{
    SetWindowTextA(g_hwnd_status, msg);
    InvalidateRect(g_hwnd_status, NULL, TRUE);
}

static void set_progress(int pct)
{
    if (pct < 0)   pct = 0;
    if (pct > 100) pct = 100;
    SetWindowLongA(g_hwnd_progress, GWLP_USERDATA, pct);
    InvalidateRect(g_hwnd_progress, NULL, FALSE);
}

static void log_append(const char *msg)
{
    if (!g_hwnd_log) return;
    int len = GetWindowTextLengthA(g_hwnd_log);
    SendMessageA(g_hwnd_log, EM_SETSEL, len, len);
    SendMessageA(g_hwnd_log, EM_REPLACESEL, FALSE, (LPARAM)msg);
    SendMessageA(g_hwnd_log, EM_REPLACESEL, FALSE, (LPARAM)"\r\n");
    SendMessageA(g_hwnd_log, EM_SCROLLCARET, 0, 0);
}

static void paint_button(DRAWITEMSTRUCT *dis, COLORREF bg, COLORREF fg)
{
    HDC dc = dis->hDC;
    RECT r = dis->rcItem;
    int is_uninst = (dis->CtlID == IDC_BTN_UNINST);
    HBRUSH br = CreateSolidBrush(
        (dis->itemState & ODS_SELECTED) ? COL_PRESSED :
        ((is_uninst ? g_btn_hover_uninst : g_btn_hover_cancel) ? COL_HOVER : bg));
    FillRect(dc, &r, br);
    DeleteObject(br);
    SetTextColor(dc, fg);
    SetBkMode(dc, TRANSPARENT);
    SelectObject(dc, g_font_normal);
    char txt[128] = {0};
    GetWindowTextA(dis->hwndItem, txt, sizeof(txt));
    DrawTextA(dc, txt, -1, &r, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
}

static void enable_dark_titlebar(HWND hwnd)
{
    BOOL dark = TRUE;
    DwmSetWindowAttribute(hwnd, 20, &dark, sizeof(dark));
}

/* ==================================================================== */
/* Directory pruning (bottom-up, skips non-empty dirs)                  */
/* ==================================================================== */

static void prune_empty_dirs(const char *dir)
{
    char pattern[MAX_PATH];
    snprintf(pattern, MAX_PATH, "%s\\*", dir);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (strcmp(fd.cFileName, ".") == 0 || strcmp(fd.cFileName, "..") == 0)
            continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            char sub[MAX_PATH];
            snprintf(sub, MAX_PATH, "%s\\%s", dir, fd.cFileName);
            prune_empty_dirs(sub);
            RemoveDirectoryA(sub);
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
}

/* ==================================================================== */
/* Self-delete via temp batch script                                     */
/* ==================================================================== */


/* ==================================================================== */
/* Self-relocation                                                       */
/* ==================================================================== */

/* Copy own exe to %TEMP% and relaunch with --relocated.
   If successful, never returns (calls ExitProcess).
   If copy or launch fails, returns so we run in place as a fallback. */
static void relocate_and_relaunch(void)
{
    char temp_dir[MAX_PATH];
    GetTempPathA(MAX_PATH, temp_dir);
    int tlen = (int)strlen(temp_dir);
    if (tlen > 0 && temp_dir[tlen - 1] == '\\') temp_dir[--tlen] = '\0';

    char temp_path[MAX_PATH];
    snprintf(temp_path, sizeof(temp_path),
             "%s\\pf_uninst_%08lx.exe", temp_dir, (unsigned long)GetTickCount());

    if (!CopyFileA(g_own_path, temp_path, FALSE)) return;

    char cmd_line[MAX_PATH + 32];
    snprintf(cmd_line, sizeof(cmd_line), "\"%s\" --relocated", temp_path);

    STARTUPINFOA si = {0}; si.cb = sizeof(si);
    PROCESS_INFORMATION pi = {0};
    if (CreateProcessA(NULL, cmd_line, NULL, NULL, FALSE, 0,
                       NULL, temp_dir, &si, &pi)) {
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        ExitProcess(0);
    }
    /* Launch failed — delete temp copy and fall through to run in place. */
    DeleteFileA(temp_path);
}

/* Delete the temp copy of ourselves after we exit. */
static void cleanup_temp_self(void)
{
    char temp_dir[MAX_PATH];
    GetTempPathA(MAX_PATH, temp_dir);
    int tlen = (int)strlen(temp_dir);
    if (tlen > 0 && temp_dir[tlen - 1] == '\\') temp_dir[--tlen] = '\0';

    char bat[MAX_PATH];
    snprintf(bat, sizeof(bat), "%s\\~pf_uninst_cleanup.bat", temp_dir);

    FILE *f = fopen(bat, "w");
    if (!f) return;
    fprintf(f,
        "@echo off\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        "del /f /q \"%s\"\r\n"
        "del /f /q \"%%~f0\"\r\n",
        g_own_path);
    fclose(f);

    char cmd_line[MAX_PATH + 64];
    snprintf(cmd_line, sizeof(cmd_line), "cmd.exe /c \"\"%s\"\"", bat);

    STARTUPINFOA si = {0}; si.cb = sizeof(si);
    PROCESS_INFORMATION pi = {0};
    CreateProcessA(NULL, cmd_line, NULL, NULL, FALSE,
                   CREATE_NO_WINDOW, NULL, NULL, &si, &pi);
    if (pi.hProcess) CloseHandle(pi.hProcess);
    if (pi.hThread)  CloseHandle(pi.hThread);
}

/* ==================================================================== */
/* Uninstall result (declared here so delete_shortcuts can reference it) */
/* ==================================================================== */

struct UninstResult {
    int  ok;
    char errors[MAX_ERRORS][MAX_PATH + 32];
    int  num_errors;
};

/* ==================================================================== */
/* Shortcut deletion (COM / IShellLink)                                  */
/* ==================================================================== */

static void delete_shortcuts(struct UninstResult *res)
{
    if (!g_shortcut_create_desktop && !g_shortcut_create_startmenu) return;

    const char *sname = g_shortcut_name[0] ? g_shortcut_name : g_app_name;
    if (!sname || !sname[0]) return;

    if (g_shortcut_create_startmenu && g_user_programs[0]) {
        const char *folder = g_app_name[0] ? g_app_name : sname;
        char subdir[MAX_PATH];
        snprintf(subdir, MAX_PATH, "%s\\%s", g_user_programs, folder);
        char lnk[MAX_PATH];
        snprintf(lnk, MAX_PATH, "%s\\%s.lnk", subdir, sname);
        if (!DeleteFileA(lnk) && GetLastError() != ERROR_FILE_NOT_FOUND
                && res->num_errors < MAX_ERRORS)
            snprintf(res->errors[res->num_errors++], MAX_PATH + 32, "%s", lnk);
        RemoveDirectoryA(subdir);  /* best-effort: may fail if non-empty */
    }

    if (g_shortcut_create_desktop && g_user_desktop[0]) {
        char lnk[MAX_PATH];
        snprintf(lnk, MAX_PATH, "%s\\%s.lnk", g_user_desktop, sname);
        if (!DeleteFileA(lnk) && GetLastError() != ERROR_FILE_NOT_FOUND
                && res->num_errors < MAX_ERRORS)
            snprintf(res->errors[res->num_errors++], MAX_PATH + 32, "%s", lnk);
    }
}

/* ==================================================================== */
/* Uninstall worker thread                                               */
/* ==================================================================== */

static DWORD WINAPI uninstall_thread(LPVOID param)
{
    struct UninstResult *res = (struct UninstResult *)param;
    res->num_errors = 0;

    /* Count files to process */
    int total = 0;
    for (int i = 0; i < g_num_files; i++) {
        for (int ci = 0; ci < g_num_installed_comps; ci++) {
            if (g_files[i].component == g_installed_comps[ci]) { total++; break; }
        }
    }
    if (total == 0) total = 1;
    int done = 0;

    for (int i = 0; i < g_num_files; i++) {
        /* Check component was installed */
        int installed = 0;
        for (int ci = 0; ci < g_num_installed_comps; ci++) {
            if (g_files[i].component == g_installed_comps[ci]) { installed = 1; break; }
        }
        if (!installed) continue;

        char full[MAX_PATH];
        snprintf(full, MAX_PATH, "%s\\%s", g_install_dir, g_files[i].path);
        for (char *fp = full; *fp; fp++) if (*fp == '/') *fp = '\\';

        BOOL ok = DeleteFileA(full);
        done++;

        if (!ok && GetLastError() != ERROR_FILE_NOT_FOUND
                && res->num_errors < MAX_ERRORS) {
            snprintf(res->errors[res->num_errors++], MAX_PATH + 32,
                     "%s", g_files[i].path);
        }

        PostMessageA(g_hwnd, WM_UNINST_PROG, (WPARAM)(done * 100 / total), 0);

        char *log_msg = (char *)malloc(MAX_PATH);
        if (log_msg) {
            snprintf(log_msg, MAX_PATH, "%s %s",
                     ok ? "Removed:" : "Error  :", g_files[i].path);
            PostMessageA(g_hwnd, WM_LOG_MSG, (WPARAM)log_msg, 0);
        }
    }

    /* Prune empty subdirectories */
    prune_empty_dirs(g_install_dir);

    /* Delete game registry key */
    if (g_install_registry_key[0])
        RegDeleteKeyA(HKEY_CURRENT_USER, g_install_registry_key);

    /* Delete A/RP entry */
    if (g_arp_hive && g_arp_key_path[0])
        RegDeleteKeyA(g_arp_hive, g_arp_key_path);

    /* Delete shortcuts */
    delete_shortcuts(res);

    /* Running from %TEMP% — delete original uninstall.exe then the game dir.
       Both should succeed: we're not in the game dir, nothing else is running. */
    if (g_relocated) {
        char uninst_path[MAX_PATH];
        snprintf(uninst_path, MAX_PATH, "%s\\uninstall.exe", g_install_dir);
        DeleteFileA(uninst_path);
        RemoveDirectoryA(g_install_dir);
    }

    res->ok = (res->num_errors == 0);
    PostMessageA(g_hwnd, WM_UNINST_DONE, (WPARAM)res->ok, (LPARAM)res);
    return 0;
}

/* ==================================================================== */
/* Window procedure                                                      */
/* ==================================================================== */

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {

    case WM_CREATE: {
        g_hwnd = hwnd;
        enable_dark_titlebar(hwnd);

        /* Title */
        char title_txt[320];
        snprintf(title_txt, sizeof(title_txt), "Uninstall %s", g_app_name);
        HWND lbl = CreateWindowExA(0, "STATIC", title_txt,
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 16, 520, 28, hwnd, NULL, NULL, NULL);
        SendMessageA(lbl, WM_SETFONT, (WPARAM)g_font_title, TRUE);

        /* Install location */
        char loc_txt[MAX_PATH + 32];
        snprintf(loc_txt, sizeof(loc_txt), "Location:  %s",
                 g_install_dir[0] ? g_install_dir : "(unknown)");
        HWND lloc = CreateWindowExA(0, "STATIC", loc_txt,
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 50, 520, 16, hwnd, NULL, NULL, NULL);
        SendMessageA(lloc, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Log */
        g_hwnd_log = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL |
            ES_READONLY | WS_VSCROLL,
            20, 78, 520, 170, hwnd, (HMENU)IDC_LOG, NULL, NULL);
        SendMessageA(g_hwnd_log, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Progress bar */
        g_hwnd_progress = CreateWindowExA(0, "STATIC", "",
            WS_CHILD | WS_VISIBLE | SS_OWNERDRAW,
            20, 256, 520, 8, hwnd, (HMENU)IDC_PROGRESS, NULL, NULL);
        SetWindowLongA(g_hwnd_progress, GWLP_USERDATA, 0);

        /* Status */
        g_hwnd_status = CreateWindowExA(0, "STATIC",
            "Click Uninstall to remove this game.",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 272, 360, 16, hwnd, (HMENU)IDC_STATUS, NULL, NULL);
        SendMessageA(g_hwnd_status, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Buttons */
        g_hwnd_btn_uninst = CreateWindowExA(0, "BUTTON", "Uninstall",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            398, 296, 68, 26, hwnd, (HMENU)IDC_BTN_UNINST, NULL, NULL);
        g_hwnd_btn_cancel = CreateWindowExA(0, "BUTTON", "Cancel",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            472, 296, 68, 26, hwnd, (HMENU)IDC_BTN_CANCEL, NULL, NULL);

        break;
    }

    case WM_COMMAND:
        if (LOWORD(wp) == IDC_BTN_UNINST) {
            EnableWindow(g_hwnd_btn_uninst, FALSE);
            EnableWindow(g_hwnd_btn_cancel, FALSE);
            set_status("Uninstalling...");
            set_progress(0);

            struct UninstResult *res = (struct UninstResult *)
                calloc(1, sizeof(struct UninstResult));
            if (res) {
                HANDLE t = CreateThread(NULL, 0, uninstall_thread, res, 0, NULL);
                if (t) CloseHandle(t);
                else { free(res); set_status("Error: could not start thread."); }
            }
        } else if (LOWORD(wp) == IDC_BTN_CANCEL) {
            DestroyWindow(hwnd);
        }
        break;

    case WM_UNINST_PROG:
        set_progress((int)wp);
        break;

    case WM_LOG_MSG: {
        char *s = (char *)wp;
        if (s) { log_append(s); free(s); }
        break;
    }

    case WM_UNINST_DONE: {
        struct UninstResult *res = (struct UninstResult *)lp;
        g_uninst_done = 1;
        set_progress(100);
        EnableWindow(g_hwnd_btn_cancel, TRUE);
        SetWindowTextA(g_hwnd_btn_cancel, "Close");

        if (res->ok) {
            set_status("Uninstall complete.");
        } else {
            /* Build error message */
            char msg[4096];
            int mlen = snprintf(msg, sizeof(msg),
                "The following files could not be deleted:\n\n");
            int show = res->num_errors < 15 ? res->num_errors : 15;
            for (int i = 0; i < show; i++) {
                int rem = (int)sizeof(msg) - mlen - 4;
                if (rem > 0)
                    mlen += snprintf(msg + mlen, rem, "  %s\n", res->errors[i]);
            }
            if (res->num_errors > 15) {
                int rem = (int)sizeof(msg) - mlen - 4;
                if (rem > 0)
                    snprintf(msg + mlen, rem, "\n  ...and %d more.",
                             res->num_errors - 15);
            }
            MessageBoxA(hwnd, msg, "Uninstall Errors", MB_OK | MB_ICONWARNING);
            set_status("Uninstall completed with errors.");
        }
        free(res);
        break;
    }

    case WM_DESTROY:
        if (g_uninst_done && g_relocated)
            cleanup_temp_self();
        PostQuitMessage(0);
        break;

    case WM_ERASEBKGND: {
        HDC dc = (HDC)wp;
        RECT r;
        GetClientRect(hwnd, &r);
        FillRect(dc, &r, g_brush_bg);

        /* Accent stripe at top */
        RECT stripe = {0, 0, r.right, 3};
        HBRUSH acc = CreateSolidBrush(COL_ACCENT);
        FillRect(dc, &stripe, acc);
        DeleteObject(acc);
        return 1;
    }

    case WM_CTLCOLORSTATIC: {
        HDC dc = (HDC)wp;
        HWND ctrl = (HWND)lp;
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, COL_TEXT);
        if (ctrl == g_hwnd_log)    return (LRESULT)g_brush_log;
        if (ctrl == g_hwnd_status) { SetTextColor(dc, COL_TEXT_DIM); return (LRESULT)g_brush_bg; }
        return (LRESULT)g_brush_bg;
    }

    case WM_CTLCOLOREDIT: {
        HDC dc = (HDC)wp;
        SetBkColor(dc, COL_LOG_BG);
        SetTextColor(dc, COL_TEXT);
        return (LRESULT)g_brush_log;
    }

    case WM_DRAWITEM: {
        DRAWITEMSTRUCT *dis = (DRAWITEMSTRUCT *)lp;
        if (dis->CtlID == IDC_BTN_UNINST)
            paint_button(dis, COL_ACCENT, COL_BG);
        else
            paint_button(dis, COL_BG_LIGHT, COL_TEXT);
        return TRUE;
    }

    case WM_MOUSEMOVE: {
        POINT pt = {LOWORD(lp), HIWORD(lp)};
        RECT ru, rc;
        if (g_hwnd_btn_uninst) GetWindowRect(g_hwnd_btn_uninst, &ru);
        if (g_hwnd_btn_cancel) GetWindowRect(g_hwnd_btn_cancel, &rc);
        POINT spt = pt;
        ClientToScreen(hwnd, &spt);
        int hu = PtInRect(&ru, spt);
        int hc = PtInRect(&rc, spt);
        if (hu != g_btn_hover_uninst || hc != g_btn_hover_cancel) {
            g_btn_hover_uninst = hu;
            g_btn_hover_cancel = hc;
            if (g_hwnd_btn_uninst) InvalidateRect(g_hwnd_btn_uninst, NULL, FALSE);
            if (g_hwnd_btn_cancel) InvalidateRect(g_hwnd_btn_cancel, NULL, FALSE);
        }
        break;
    }

    case WM_PAINT: {
        PAINTSTRUCT ps;
        BeginPaint(hwnd, &ps);
        EndPaint(hwnd, &ps);
        break;
    }

    /* Custom progress bar drawing */
    case WM_NOTIFY:
        break;

    default:
        break;
    }

    /* Progress bar self-draws */
    if (msg == WM_DRAWITEM) {
        DRAWITEMSTRUCT *dis = (DRAWITEMSTRUCT *)lp;
        if (dis->CtlID == IDC_PROGRESS) {
            int pct = (int)GetWindowLongA(dis->hwndItem, GWLP_USERDATA);
            RECT r = dis->rcItem;
            HBRUSH bg_br = CreateSolidBrush(COL_PROGRESS_BG);
            FillRect(dis->hDC, &r, bg_br);
            DeleteObject(bg_br);
            if (pct > 0) {
                RECT fill = r;
                fill.right = r.left + (r.right - r.left) * pct / 100;
                HBRUSH fg_br = CreateSolidBrush(COL_ACCENT);
                FillRect(dis->hDC, &fill, fg_br);
                DeleteObject(fg_br);
            }
            return TRUE;
        }
    }

    return DefWindowProcA(hwnd, msg, wp, lp);
}

/* ==================================================================== */
/* WinMain                                                               */
/* ==================================================================== */

int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrev,
                   LPSTR lpCmdLine, int nCmdShow)
{
    (void)hPrev;

    GetModuleFileNameA(NULL, g_own_path, MAX_PATH);
    g_relocated = (strstr(lpCmdLine, "--relocated") != NULL);

    SHGetFolderPathA(NULL, CSIDL_DESKTOPDIRECTORY, NULL, 0, g_user_desktop);
    SHGetFolderPathA(NULL, CSIDL_PROGRAMS,         NULL, 0, g_user_programs);

    if (!read_uninst_data()) {
        MessageBoxA(NULL,
            "This uninstaller is missing its embedded data.\n\n"
            "Please use the original uninstall.exe from the game's install folder.",
            "Uninstaller Error", MB_OK | MB_ICONERROR);
        return 1;
    }

    /* If running from the game directory, copy to %TEMP% and relaunch so we
       can freely delete the game folder at the end of uninstallation. */
    if (!g_relocated)
        relocate_and_relaunch();  /* exits on success; falls through on failure */

    if (!read_arp_registry() || !g_install_dir[0]) {
        MessageBoxA(NULL,
            "Could not find installation information in the registry.\n\n"
            "The game may have already been uninstalled.",
            g_app_name[0] ? g_app_name : "Uninstaller",
            MB_OK | MB_ICONWARNING);
        return 1;
    }

    /* Confirmation */
    char confirm[512];
    snprintf(confirm, sizeof(confirm),
        "Are you sure you want to uninstall %s?\n\n"
        "Install location: %s",
        g_app_name[0] ? g_app_name : "this game",
        g_install_dir);
    if (MessageBoxA(NULL, confirm,
                    g_app_name[0] ? g_app_name : "Uninstall",
                    MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2) != IDYES)
        return 0;

    /* Fonts and brushes */
    g_font_normal = CreateFontA(-13, 0, 0, 0, FW_NORMAL, 0, 0, 0,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, "Segoe UI");
    g_font_title = CreateFontA(-18, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, "Segoe UI");
    g_brush_bg    = CreateSolidBrush(COL_BG);
    g_brush_light = CreateSolidBrush(COL_BG_LIGHT);
    g_brush_log   = CreateSolidBrush(COL_LOG_BG);

    /* Register window class */
    WNDCLASSEXA wc = {0};
    wc.cbSize        = sizeof(wc);
    wc.lpfnWndProc   = WndProc;
    wc.hInstance     = hInstance;
    wc.hCursor       = LoadCursorA(NULL, IDC_ARROW);
    wc.hbrBackground = g_brush_bg;
    wc.lpszClassName = "PFUninstaller";
    wc.hIcon         = LoadIconA(hInstance, MAKEINTRESOURCEA(1));
    wc.hIconSm       = LoadIconA(hInstance, MAKEINTRESOURCEA(1));
    RegisterClassExA(&wc);

    /* Window title */
    char wnd_title[320];
    snprintf(wnd_title, sizeof(wnd_title), "Uninstall %s",
             g_app_name[0] ? g_app_name : "Game");

    /* Create and size the window */
    HWND hwnd = CreateWindowExA(0, "PFUninstaller", wnd_title,
        WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
        CW_USEDEFAULT, CW_USEDEFAULT, 560, 340, NULL, NULL, hInstance, NULL);

    RECT wr, cr;
    GetWindowRect(hwnd, &wr);
    GetClientRect(hwnd, &cr);
    int bx = (wr.right - wr.left) - (cr.right - cr.left);
    int by = (wr.bottom - wr.top) - (cr.bottom - cr.top);
    SetWindowPos(hwnd, NULL, 0, 0, 560 + bx, 336 + by,
                 SWP_NOMOVE | SWP_NOZORDER);

    ShowWindow(hwnd, nCmdShow);
    UpdateWindow(hwnd);

    MSG m;
    while (GetMessageA(&m, NULL, 0, 0)) {
        TranslateMessage(&m);
        DispatchMessageA(&m);
    }

    DeleteObject(g_font_normal);
    DeleteObject(g_font_title);
    DeleteObject(g_brush_bg);
    DeleteObject(g_brush_light);
    DeleteObject(g_brush_log);
    if (g_files) free(g_files);

    return (int)m.wParam;
}

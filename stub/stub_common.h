/*
 * stub_common.h — shared Win32 UI and patch-data reading for PatchForge stubs
 *
 * Patch file layout (read from end of exe):
 *   [patch data bytes .............]
 *   [JSON metadata null-terminated ]
 *   [metadata length  — 4 bytes LE ]
 *   [magic "XPATCH01" — 8 bytes    ]
 */

#ifndef STUB_COMMON_H
#define STUB_COMMON_H

#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0600  /* Vista+ for DWM */
#include <windows.h>
#include <dwmapi.h>
#include <commdlg.h>
#include <shlobj.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ---- Colours (dark theme) ---- */
#define COL_BG          RGB(0x1e, 0x1e, 0x1e)
#define COL_BG_LIGHT    RGB(0x2d, 0x2d, 0x2d)
#define COL_ACCENT      RGB(0x00, 0x7a, 0xcc)
#define COL_ACCENT_HOV  RGB(0x1a, 0x8a, 0xdc)
#define COL_TEXT        RGB(0xd4, 0xd4, 0xd4)
#define COL_TEXT_DIM    RGB(0x88, 0x88, 0x88)
#define COL_SUCCESS     RGB(0x4e, 0xc9, 0xb0)
#define COL_ERROR       RGB(0xf4, 0x47, 0x47)
#define COL_BORDER      RGB(0x3c, 0x3c, 0x3c)
#define COL_PROGRESS_BG RGB(0x3c, 0x3c, 0x3c)

/* ---- Patch magic ---- */
#define PATCH_MAGIC     "XPATCH01"
#define PATCH_MAGIC_LEN 8

/* ---- Controls ---- */
#define IDC_STATUS      1001
#define IDC_PROGRESS    1002
#define IDC_BTN_BROWSE  1003
#define IDC_BTN_PATCH   1004
#define IDC_BTN_CANCEL  1005
#define IDC_FILEPATH    1006
#define IDC_LOG         1007

/* ---- Patch metadata (populated from JSON at startup) ---- */
typedef struct {
    char app_name[256];
    char version[64];
    char description[512];
    char engine[32];        /* "xdelta3", "jojodiff", "hdiffpatch" */
    char compression[32];   /* e.g. "lzma/ultra" */
    char verify_method[32]; /* "crc32c", "md5", "filesize" */
    char orig_checksum[64];
    char new_checksum[64];
    int64_t orig_size;
    int64_t new_size;
    char find_method[32];   /* "manual", "registry", "ini" */
    char registry_key[512];
    char registry_value[256];
    char ini_path[512];
    char ini_section[128];
    char ini_key[128];
    int64_t patch_data_offset; /* byte offset in this exe where patch data starts */
    int64_t patch_data_size;
} PatchMeta;

/* ---- Global state ---- */
extern HWND g_hwnd;
extern HWND g_hwnd_status;
extern HWND g_hwnd_progress;
extern HWND g_hwnd_filepath;
extern HWND g_hwnd_log;
extern HWND g_hwnd_btn_patch;
extern HBRUSH g_brush_bg;
extern HBRUSH g_brush_light;
extern HFONT g_font_normal;
extern HFONT g_font_title;
extern PatchMeta g_meta;
extern char g_exe_path[MAX_PATH];

/* ---- Forward declarations ---- */
LRESULT CALLBACK WndProc(HWND, UINT, WPARAM, LPARAM);
void log_message(const char *fmt, ...);
void set_status(const char *msg, COLORREF col);
void set_progress(int pct);
int read_patch_meta(PatchMeta *meta, char **patch_data_out, size_t *patch_size_out);
int browse_for_file(HWND owner, char *out_path, int out_len, const char *filter);
int find_target_file(char *out_path, int out_len);
int do_patch(const char *target_path, const char *patch_data, size_t patch_size);

/* ---- Simple JSON key extraction (no external deps) ---- */
static const char *json_get_str(const char *json, const char *key,
                                char *out, int out_len)
{
    char search[128];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return NULL;
    p += strlen(search);
    while (*p == ' ' || *p == ':' || *p == ' ') p++;
    if (*p != '"') return NULL;
    p++;
    int i = 0;
    while (*p && *p != '"' && i < out_len - 1) {
        if (*p == '\\' && *(p+1)) { p++; }
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
    while (*p == ' ' || *p == ':' || *p == ' ') p++;
    return (int64_t)_atoi64(p);
}

/* ---- Read patch metadata and data from end of this exe ---- */
static int read_patch_meta_impl(PatchMeta *meta, char **json_out,
                                char **data_out, size_t *data_size_out)
{
    GetModuleFileNameA(NULL, g_exe_path, MAX_PATH);
    HANDLE f = CreateFileA(g_exe_path, GENERIC_READ, FILE_SHARE_READ,
                           NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return 0;

    LARGE_INTEGER fsz;
    GetFileSizeEx(f, &fsz);
    int64_t file_size = fsz.QuadPart;

    if (file_size < PATCH_MAGIC_LEN + 4) { CloseHandle(f); return 0; }

    /* Read magic at end */
    char magic[PATCH_MAGIC_LEN];
    LARGE_INTEGER pos;
    pos.QuadPart = file_size - PATCH_MAGIC_LEN;
    SetFilePointerEx(f, pos, NULL, FILE_BEGIN);
    DWORD rd;
    ReadFile(f, magic, PATCH_MAGIC_LEN, &rd, NULL);
    if (memcmp(magic, PATCH_MAGIC, PATCH_MAGIC_LEN) != 0) {
        CloseHandle(f); return 0;
    }

    /* Read metadata length (4 bytes LE, before magic) */
    uint32_t meta_len;
    pos.QuadPart = file_size - PATCH_MAGIC_LEN - 4;
    SetFilePointerEx(f, pos, NULL, FILE_BEGIN);
    ReadFile(f, &meta_len, 4, &rd, NULL);
    if (meta_len == 0 || meta_len > 65536) { CloseHandle(f); return 0; }

    /* Read JSON metadata */
    char *json = (char *)malloc(meta_len + 1);
    pos.QuadPart = file_size - PATCH_MAGIC_LEN - 4 - meta_len;
    SetFilePointerEx(f, pos, NULL, FILE_BEGIN);
    ReadFile(f, json, meta_len, &rd, NULL);
    json[meta_len] = '\0';

    /* Read patch data */
    int64_t data_end = file_size - PATCH_MAGIC_LEN - 4 - meta_len;
    int64_t data_start = json_get_int(json, "patch_data_offset");
    int64_t data_size  = data_end - data_start;
    if (data_size <= 0 || data_start < 0) { free(json); CloseHandle(f); return 0; }

    char *data = (char *)malloc((size_t)data_size);
    pos.QuadPart = data_start;
    SetFilePointerEx(f, pos, NULL, FILE_BEGIN);

    size_t remaining = (size_t)data_size;
    char *dst = data;
    while (remaining > 0) {
        DWORD chunk = (DWORD)(remaining > 65536 ? 65536 : remaining);
        ReadFile(f, dst, chunk, &rd, NULL);
        dst += rd; remaining -= rd;
    }
    CloseHandle(f);

    /* Parse JSON into meta struct */
    memset(meta, 0, sizeof(*meta));
    json_get_str(json, "app_name",       meta->app_name,      sizeof(meta->app_name));
    json_get_str(json, "version",        meta->version,       sizeof(meta->version));
    json_get_str(json, "description",    meta->description,   sizeof(meta->description));
    json_get_str(json, "engine",         meta->engine,        sizeof(meta->engine));
    json_get_str(json, "compression",    meta->compression,   sizeof(meta->compression));
    json_get_str(json, "verify_method",  meta->verify_method, sizeof(meta->verify_method));
    json_get_str(json, "orig_checksum",  meta->orig_checksum, sizeof(meta->orig_checksum));
    json_get_str(json, "new_checksum",   meta->new_checksum,  sizeof(meta->new_checksum));
    json_get_str(json, "find_method",    meta->find_method,   sizeof(meta->find_method));
    json_get_str(json, "registry_key",   meta->registry_key,  sizeof(meta->registry_key));
    json_get_str(json, "registry_value", meta->registry_value,sizeof(meta->registry_value));
    json_get_str(json, "ini_path",       meta->ini_path,      sizeof(meta->ini_path));
    json_get_str(json, "ini_section",    meta->ini_section,   sizeof(meta->ini_section));
    json_get_str(json, "ini_key",        meta->ini_key,       sizeof(meta->ini_key));
    meta->orig_size        = json_get_int(json, "orig_size");
    meta->new_size         = json_get_int(json, "new_size");
    meta->patch_data_offset= data_start;
    meta->patch_data_size  = data_size;

    if (json_out) *json_out = json; else free(json);
    *data_out      = data;
    *data_size_out = (size_t)data_size;
    return 1;
}

/* ---- Registry path lookup ---- */
static int find_via_registry(const PatchMeta *meta, char *out, int out_len)
{
    if (!meta->registry_key[0]) return 0;
    HKEY root = HKEY_LOCAL_MACHINE;
    /* Try HKLM first, then HKCU */
    for (int i = 0; i < 2; i++) {
        root = (i == 0) ? HKEY_LOCAL_MACHINE : HKEY_CURRENT_USER;
        HKEY hk;
        if (RegOpenKeyExA(root, meta->registry_key, 0, KEY_READ, &hk) == ERROR_SUCCESS) {
            DWORD type, sz = out_len;
            const char *val = meta->registry_value[0] ? meta->registry_value : "InstallPath";
            if (RegQueryValueExA(hk, val, NULL, &type, (BYTE*)out, &sz) == ERROR_SUCCESS) {
                RegCloseKey(hk);
                return 1;
            }
            RegCloseKey(hk);
        }
    }
    return 0;
}

/* ---- INI path lookup ---- */
static int find_via_ini(const PatchMeta *meta, char *out, int out_len)
{
    if (!meta->ini_path[0]) return 0;
    GetPrivateProfileStringA(meta->ini_section[0] ? meta->ini_section : "Settings",
                             meta->ini_key[0]     ? meta->ini_key     : "InstallPath",
                             "", out, out_len, meta->ini_path);
    return out[0] != '\0';
}

/* ---- Dark-theme Win32 helpers ---- */
static void enable_dark_titlebar(HWND hwnd)
{
    /* Windows 10 1809+ dark titlebar */
    BOOL dark = TRUE;
    /* DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (older) or 19 */
    DwmSetWindowAttribute(hwnd, 20, &dark, sizeof(dark));
    DwmSetWindowAttribute(hwnd, 19, &dark, sizeof(dark));
}

/* ---- Owner-draw button paint ---- */
static void paint_button(DRAWITEMSTRUCT *dis, COLORREF bg, COLORREF text_col)
{
    HDC dc = dis->hDC;
    RECT r = dis->rcItem;
    BOOL hover   = (dis->itemState & ODS_HOTLIGHT) != 0;
    BOOL pressed = (dis->itemState & ODS_SELECTED) != 0;

    COLORREF c = pressed ? COL_ACCENT :
                 hover   ? COL_ACCENT_HOV : bg;
    HBRUSH br = CreateSolidBrush(c);
    FillRect(dc, &r, br);
    DeleteObject(br);

    /* Border */
    HPEN pen = CreatePen(PS_SOLID, 1, COL_BORDER);
    HPEN old = (HPEN)SelectObject(dc, pen);
    HBRUSH nb = (HBRUSH)GetStockObject(NULL_BRUSH);
    HBRUSH ob = (HBRUSH)SelectObject(dc, nb);
    Rectangle(dc, r.left, r.top, r.right, r.bottom);
    SelectObject(dc, old); SelectObject(dc, ob);
    DeleteObject(pen);

    /* Text */
    char buf[128] = {0};
    GetWindowTextA(dis->hwndItem, buf, sizeof(buf));
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, text_col);
    SelectObject(dc, g_font_normal);
    DrawTextA(dc, buf, -1, &r, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
}

/* ---- Log and status helpers ---- */
static void log_append(const char *msg)
{
    if (!g_hwnd_log) return;
    int len = GetWindowTextLengthA(g_hwnd_log);
    SendMessageA(g_hwnd_log, EM_SETSEL, len, len);
    SendMessageA(g_hwnd_log, EM_REPLACESEL, FALSE, (LPARAM)msg);
    SendMessageA(g_hwnd_log, EM_REPLACESEL, FALSE, (LPARAM)"\r\n");
    SendMessageA(g_hwnd_log, WM_VSCROLL, SB_BOTTOM, 0);
}

#endif /* STUB_COMMON_H */

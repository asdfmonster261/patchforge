/*
 * stub_common.h — shared Win32 UI and patch-data reading for PatchForge stubs
 *
 * Patch file layout (read from end of exe):
 *   [patch data bytes            ]
 *   [extra_file_0 bytes          ]  \
 *   [extra_file_1 bytes          ]   > zero or more extra files
 *   ...                              /
 *   [backdrop image bytes        ]   (zero bytes if no backdrop)
 *   [JSON metadata UTF-8         ]
 *   [metadata length  — 4 bytes LE]
 *   [magic "XPATCH01" — 8 bytes  ]
 */

#ifndef STUB_COMMON_H
#define STUB_COMMON_H

#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0600  /* Vista+ for DWM */
#include <windows.h>
#include <shellapi.h>
#include <dwmapi.h>
#include <commdlg.h>
#include <shlobj.h>
#include <wincrypt.h>
#include <wincodec.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ---- Colours (dark theme — palette from gui_colors.txt) ---- */
#define COL_BG          RGB(0x12, 0x12, 0x18)  /* #121218  main bg          */
#define COL_BG_LIGHT    RGB(0x20, 0x20, 0x2c)  /* #20202c  input/surface bg */
#define COL_LOG_BG      RGB(0x18, 0x18, 0x20)  /* #181820  log area bg      */
#define COL_HOVER       RGB(0x2c, 0x2c, 0x3c)  /* #2c2c3c  hover surface    */
#define COL_PRESSED     RGB(0x3a, 0x3a, 0x55)  /* #3a3a55  pressed surface  */
#define COL_ACCENT      RGB(0x42, 0x87, 0xf5)  /* #4287f5  accent blue      */
#define COL_ACCENT_HOV  RGB(0x58, 0x97, 0xff)  /* #5897ff  accent hover     */
#define COL_TEXT        RGB(0xd7, 0xd7, 0xe1)  /* #d7d7e1  body text        */
#define COL_TEXT_DIM    RGB(0xa0, 0xa0, 0xb9)  /* #a0a0b9  muted text       */
#define COL_SUCCESS     RGB(0x3c, 0xb9, 0x69)  /* #3cb969  green            */
#define COL_ERROR       RGB(0xe6, 0x46, 0x46)  /* #e64646  red              */
#define COL_BORDER      RGB(0x2a, 0x2a, 0x3a)  /* #2a2a3a  border           */
#define COL_PROGRESS_BG RGB(0x1a, 0x1a, 0x24)  /* #1a1a24  disabled bg      */

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
#define IDC_CHK_BACKUP  1008
#define IDC_CHK_VERIFY  1009

/* ---- Thread messages ---- */
#define WM_PATCH_DONE  (WM_USER + 1)
#define WM_PATCH_PROG  (WM_USER + 2)
#define WM_LOG_MSG     (WM_USER + 3)

/* ---- Extra-file metadata (one per bundled file) ---- */
#define MAX_EXTRA_FILES 64
typedef struct {
    char    dest[512];    /* destination path relative to game folder */
    int64_t offset;       /* byte offset in this exe */
    int64_t size;         /* byte length */
} ExtraFileMeta;

/* ---- Patch metadata (populated from JSON at startup) ---- */
typedef struct {
    char app_name[256];
    char app_note[256];         /* short subtitle */
    char version[64];
    char description[512];
    char copyright[256];
    char contact[256];
    char company_info[256];
    char window_title[256];     /* title bar text; falls back to app_name */
    char patch_exe_version[64]; /* informational version of the patch exe */
    char engine[32];        /* "xdelta3", "jojodiff", "hdiffpatch" */
    char compression[32];
    char verify_method[32]; /* "crc32c", "md5", "filesize" */
    char find_method[32];   /* "manual", "registry", "ini" */
    char registry_key[512];
    char registry_value[256];
    char ini_path[512];
    char ini_section[128];
    char ini_key[128];
    char *checksums;        /* malloc'd or NULL — target file checksums (post-patch) */
    char *source_checksums; /* malloc'd or NULL — source file checksums (pre-patch) */
    int64_t patch_data_offset;
    int64_t patch_data_size;

    /* New: patching behaviour */
    int  delete_extra_files;    /* 1 = delete files absent from target (default) */
    char run_on_startup[512];   /* command run when patcher window opens (async) */
    char run_before[512];       /* command to run before patching */
    char run_after[512];        /* command to run after patching */
    char run_on_finish[512];    /* command run after successful patch + dialog */
    char detect_running_exe[256]; /* warn if this process name is running */
    char backup_at[32];         /* "disabled"|"same_folder"|"custom" */
    char backup_path[512];      /* used when backup_at == "custom" */

    /* Patcher UX options */
    int    close_delay;             /* seconds to auto-close after success; 0 = stay open */
    double required_free_space_gb;  /* minimum free GB before patching; 0 = no check */
    int    preserve_timestamps;     /* 1 = restore original file mtimes after patching */

    /* Change summary (set at build time) */
    int files_modified;
    int files_added;
    int files_removed;

    /* New: backdrop image */
    int64_t backdrop_offset;
    int64_t backdrop_size;

    /* New: extra bundled files */
    int          num_extra_files;
    ExtraFileMeta extra_files_meta[MAX_EXTRA_FILES];
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
extern HBRUSH g_brush_log;
extern HFONT g_font_normal;
extern HFONT g_font_title;
extern PatchMeta g_meta;
extern char g_exe_path[MAX_PATH];

/* Pre-selected game folder path (set from argv[1] on elevated relaunch) */
static char g_preset_path[MAX_PATH] = {0};

/* Cached backdrop bitmap (NULL if no backdrop) */
static HBITMAP g_backdrop_bmp = NULL;

/* ---- Image-band sizing (matches installer/uninstaller chrome) ---- */
#define BACKDROP_ASPECT_W 616
#define BACKDROP_ASPECT_H 353
#define IMG_MAX_H         480

/* Layout state used by patcher chrome: image band height + footer
 * separator y. Set by pfg_build_patcher_gui / pfg_compute_img_h, read
 * by pfg_paint_band_background. */
static int g_img_h      = 0;
static int g_foot_sep_y = 0;

/* Patcher checkbox handles (set by pfg_build_patcher_gui, read by
 * each patcher stub's WM_COMMAND when launching the patch thread). */
static HWND g_hwnd_chk_backup = NULL;
static HWND g_hwnd_chk_verify = NULL;

/* ---- Forward declarations ---- */
LRESULT CALLBACK WndProc(HWND, UINT, WPARAM, LPARAM);
void log_message(const char *fmt, ...);
void set_status(const char *msg, COLORREF col);
void set_progress(int pct);
int read_patch_meta(PatchMeta *meta, char **patch_data_out, size_t *patch_size_out);
int browse_for_file(HWND owner, char *out_path, int out_len, const char *filter);
int find_target_file(char *out_path, int out_len);
int do_patch(const char *target_path, const char *patch_data, size_t patch_size);

/* ---- Path safety: reject absolute paths, drive letters, UNC, .. components.
 * Returned paths are always relative-and-contained; the caller can safely
 * snprintf("%s\\%s", base_dir, path) without escaping base_dir. */
static int pfg_path_is_safe(const char *path)
{
    if (!path || !path[0]) return 0;
    /* Reject absolute paths (leading separator) */
    if (path[0] == '/' || path[0] == '\\') return 0;
    /* Reject drive-letter paths "X:..." */
    if (path[1] == ':') return 0;
    /* Reject UNC paths "\\server\..." (the leading-backslash check above
     * already catches this, but the explicit second-char test guards
     * against any future loosening.) */
    if (path[0] == '\\' && path[1] == '\\') return 0;
    /* Reject any ".." path component */
    const char *p = path;
    while (*p) {
        if (p[0] == '.' && p[1] == '.' &&
            (p[2] == '\0' || p[2] == '/' || p[2] == '\\'))
            return 0;
        while (*p && *p != '/' && *p != '\\') p++;
        if (*p) p++;
    }
    return 1;
}

/* ---- Simple JSON key extraction (no external deps) ---- */
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
    while (*p == ' ' || *p == ':') p++;
    return (int64_t)_atoi64(p);
}

static double json_get_double(const char *json, const char *key)
{
    char search[128];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return 0.0;
    p += strlen(search);
    while (*p == ' ' || *p == ':') p++;
    return strtod(p, NULL);
}

/* Like json_get_str but malloc's the result — caller must free(). */
static char *json_get_str_alloc(const char *json, const char *key)
{
    char search[128];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return NULL;
    p += strlen(search);
    while (*p == ' ' || *p == ':') p++;
    if (*p != '"') return NULL;
    p++;
    const char *start = p;
    size_t len = 0;
    while (*p && *p != '"') {
        if (*p == '\\' && *(p+1)) { p++; }
        len++; p++;
    }
    char *out = (char *)malloc(len + 1);
    if (!out) return NULL;
    p = start; size_t i = 0;
    while (*p && *p != '"' && i < len) {
        if (*p == '\\' && *(p+1)) { p++; }
        out[i++] = *p++;
    }
    out[i] = '\0';
    return out;
}

/* ---- Parse extra_files array from JSON ---- */
static void json_parse_extra_files(const char *json, PatchMeta *meta)
{
    meta->num_extra_files = 0;
    const char *p = strstr(json, "\"extra_files\"");
    if (!p) return;
    p += strlen("\"extra_files\"");
    while (*p == ' ' || *p == ':') p++;
    if (*p != '[') return;
    p++;

    int n = 0;
    while (*p && *p != ']' && n < MAX_EXTRA_FILES) {
        while (*p == ' ' || *p == ',') p++;
        if (*p != '{') break;
        /* Find matching closing brace */
        const char *obj_start = p;
        int depth = 0;
        const char *q = p;
        while (*q) {
            if (*q == '{') depth++;
            else if (*q == '}') { depth--; if (depth == 0) break; }
            q++;
        }
        if (depth != 0) break;
        /* Copy object into a temp buffer for simple key extraction */
        size_t obj_len = (size_t)(q - obj_start + 1);
        char *obj = (char *)malloc(obj_len + 1);
        if (!obj) break;
        memcpy(obj, obj_start, obj_len);
        obj[obj_len] = '\0';

        char dest[512] = {0};
        json_get_str(obj, "dest", dest, sizeof(dest));
        int64_t offset = json_get_int(obj, "offset");
        int64_t size   = json_get_int(obj, "size");
        free(obj);

        /* Reject any extra-file entry whose dest would escape the game dir.
         * The Python builder validates this too, but a tampered .exe could
         * carry malicious dest values. */
        if (dest[0] && size > 0 && pfg_path_is_safe(dest)) {
            strncpy(meta->extra_files_meta[n].dest, dest, 511);
            meta->extra_files_meta[n].dest[511] = '\0';
            meta->extra_files_meta[n].offset = offset;
            meta->extra_files_meta[n].size   = size;
            n++;
        }
        p = q + 1;
    }
    meta->num_extra_files = n;
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
    if (!ReadFile(f, magic, PATCH_MAGIC_LEN, &rd, NULL) || rd != PATCH_MAGIC_LEN) {
        CloseHandle(f); return 0;
    }
    if (memcmp(magic, PATCH_MAGIC, PATCH_MAGIC_LEN) != 0) {
        CloseHandle(f); return 0;
    }

    /* Read metadata length (4 bytes LE, before magic) */
    uint32_t meta_len;
    pos.QuadPart = file_size - PATCH_MAGIC_LEN - 4;
    SetFilePointerEx(f, pos, NULL, FILE_BEGIN);
    if (!ReadFile(f, &meta_len, 4, &rd, NULL) || rd != 4) { CloseHandle(f); return 0; }
    if (meta_len == 0 || meta_len > (1 << 20)) { CloseHandle(f); return 0; }

    /* Read JSON metadata */
    char *json = (char *)malloc(meta_len + 1);
    if (!json) { CloseHandle(f); return 0; }
    pos.QuadPart = file_size - PATCH_MAGIC_LEN - 4 - meta_len;
    SetFilePointerEx(f, pos, NULL, FILE_BEGIN);
    if (!ReadFile(f, json, meta_len, &rd, NULL) || rd != meta_len) {
        free(json); CloseHandle(f); return 0;
    }
    json[meta_len] = '\0';

    /* Read patch data */
    int64_t data_start = json_get_int(json, "patch_data_offset");
    int64_t data_size  = json_get_int(json, "patch_data_size");
    if (data_size <= 0 || data_start < 0) { free(json); CloseHandle(f); return 0; }
    /* Subtract instead of add — guards against int64 overflow when a
     * tampered metadata blob supplies enormous offsets/sizes. */
    if (data_start > file_size || data_size > file_size - data_start) {
        free(json); CloseHandle(f); return 0;
    }

    char *data = (char *)malloc((size_t)data_size);
    if (!data) { free(json); CloseHandle(f); return 0; }
    pos.QuadPart = data_start;
    SetFilePointerEx(f, pos, NULL, FILE_BEGIN);

    size_t remaining = (size_t)data_size;
    char *dst = data;
    while (remaining > 0) {
        DWORD chunk = (DWORD)(remaining > 65536 ? 65536 : remaining);
        if (!ReadFile(f, dst, chunk, &rd, NULL) || rd == 0) break;
        dst += rd; remaining -= rd;
    }
    CloseHandle(f);

    if (remaining > 0) { free(data); free(json); return 0; }

    /* Parse JSON into meta struct */
    memset(meta, 0, sizeof(*meta));
    json_get_str(json, "app_name",          meta->app_name,          sizeof(meta->app_name));
    json_get_str(json, "app_note",          meta->app_note,          sizeof(meta->app_note));
    json_get_str(json, "version",           meta->version,           sizeof(meta->version));
    json_get_str(json, "description",       meta->description,       sizeof(meta->description));
    json_get_str(json, "copyright",         meta->copyright,         sizeof(meta->copyright));
    json_get_str(json, "contact",           meta->contact,           sizeof(meta->contact));
    json_get_str(json, "company_info",      meta->company_info,      sizeof(meta->company_info));
    json_get_str(json, "window_title",      meta->window_title,      sizeof(meta->window_title));
    json_get_str(json, "patch_exe_version", meta->patch_exe_version, sizeof(meta->patch_exe_version));
    json_get_str(json, "engine",         meta->engine,        sizeof(meta->engine));
    json_get_str(json, "compression",    meta->compression,   sizeof(meta->compression));
    json_get_str(json, "verify_method",  meta->verify_method, sizeof(meta->verify_method));
    json_get_str(json, "find_method",    meta->find_method,   sizeof(meta->find_method));
    json_get_str(json, "registry_key",   meta->registry_key,  sizeof(meta->registry_key));
    json_get_str(json, "registry_value", meta->registry_value,sizeof(meta->registry_value));
    json_get_str(json, "ini_path",       meta->ini_path,      sizeof(meta->ini_path));
    json_get_str(json, "ini_section",    meta->ini_section,   sizeof(meta->ini_section));
    json_get_str(json, "ini_key",        meta->ini_key,       sizeof(meta->ini_key));
    meta->checksums         = json_get_str_alloc(json, "checksums");
    meta->source_checksums  = json_get_str_alloc(json, "source_checksums");
    meta->files_modified    = (int)json_get_int(json, "files_modified");
    meta->files_added       = (int)json_get_int(json, "files_added");
    meta->files_removed     = (int)json_get_int(json, "files_removed");
    meta->patch_data_offset = data_start;
    meta->patch_data_size   = data_size;

    /* Patching-behaviour fields (default delete_extra_files=1) */
    meta->delete_extra_files = (int)json_get_int(json, "delete_extra_files");
    if (meta->delete_extra_files == 0) {
        /* Explicitly set to 0 — honour it.  JSON default absent means 1. */
        char tmp[8] = {0};
        if (!json_get_str(json, "delete_extra_files", tmp, sizeof(tmp)) || !tmp[0])
            meta->delete_extra_files = 1;
    }
    json_get_str(json, "run_on_startup",    meta->run_on_startup,    sizeof(meta->run_on_startup));
    json_get_str(json, "run_before",        meta->run_before,        sizeof(meta->run_before));
    json_get_str(json, "run_after",         meta->run_after,         sizeof(meta->run_after));
    json_get_str(json, "run_on_finish",     meta->run_on_finish,     sizeof(meta->run_on_finish));
    json_get_str(json, "detect_running_exe",meta->detect_running_exe,sizeof(meta->detect_running_exe));
    json_get_str(json, "backup_at",         meta->backup_at,         sizeof(meta->backup_at));
    if (!meta->backup_at[0]) { strncpy(meta->backup_at, "same_folder", sizeof(meta->backup_at) - 1); meta->backup_at[sizeof(meta->backup_at) - 1] = '\0'; }
    json_get_str(json, "backup_path", meta->backup_path, sizeof(meta->backup_path));

    /* Patcher UX options */
    meta->close_delay            = (int)json_get_int(json, "close_delay");
    meta->required_free_space_gb = json_get_double(json, "required_free_space_gb");
    meta->preserve_timestamps    = (int)json_get_int(json, "preserve_timestamps");

    /* Backdrop */
    meta->backdrop_offset = json_get_int(json, "backdrop_offset");
    meta->backdrop_size   = json_get_int(json, "backdrop_size");

    /* Extra files */
    json_parse_extra_files(json, meta);

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
    BOOL dark = TRUE;
    DwmSetWindowAttribute(hwnd, 20, &dark, sizeof(dark));
    DwmSetWindowAttribute(hwnd, 19, &dark, sizeof(dark));
}

/* ---- Owner-draw button paint (rounded, modern) ---- */
static void paint_button(DRAWITEMSTRUCT *dis, COLORREF bg, COLORREF text_col)
{
    HDC    dc      = dis->hDC;
    RECT   r       = dis->rcItem;
    BOOL   hover   = (dis->itemState & ODS_HOTLIGHT) != 0;
    BOOL   pressed = (dis->itemState & ODS_SELECTED) != 0;
    BOOL   focused = (dis->itemState & ODS_FOCUS)    != 0;

    COLORREF fill   = pressed ? COL_PRESSED : hover ? COL_HOVER : bg;
    COLORREF border = (hover || focused) ? COL_ACCENT : COL_BORDER;

    /* Erase behind rounded corners with window background */
    HBRUSH win_br = CreateSolidBrush(COL_BG);
    FillRect(dc, &r, win_br);
    DeleteObject(win_br);

    HBRUSH btn_br  = CreateSolidBrush(fill);
    HPEN   btn_pen = CreatePen(PS_SOLID, 1, border);
    HPEN   old_p   = (HPEN)SelectObject(dc, btn_pen);
    HBRUSH old_b   = (HBRUSH)SelectObject(dc, btn_br);
    RoundRect(dc, r.left, r.top, r.right, r.bottom, 6, 6);
    SelectObject(dc, old_p);
    SelectObject(dc, old_b);
    DeleteObject(btn_br);
    DeleteObject(btn_pen);

    char buf[128] = {0};
    GetWindowTextA(dis->hwndItem, buf, sizeof(buf));
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, text_col);
    SelectObject(dc, g_font_normal);
    DrawTextA(dc, buf, -1, &r, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
}

/* ---- CRC32C (Castagnoli) implementation ---- */
static uint32_t _pfg_crc32c(const char *path)
{
    static uint32_t tbl[256];
    static int tbl_ready = 0;
    if (!tbl_ready) {
        for (int i = 0; i < 256; i++) {
            uint32_t c = (uint32_t)i;
            for (int j = 0; j < 8; j++)
                c = (c >> 1) ^ (0x82F63B78u & (uint32_t)(-(int)(c & 1)));
            tbl[i] = c;
        }
        tbl_ready = 1;
    }
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    uint32_t crc = 0xFFFFFFFFu;
    unsigned char buf[65536];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
        for (size_t i = 0; i < n; i++)
            crc = (crc >> 8) ^ tbl[(crc ^ buf[i]) & 0xFF];
    fclose(f);
    return crc ^ 0xFFFFFFFFu;
}

/* ---- MD5 via Windows CryptAPI ---- */
static int _pfg_md5(const char *path, char out_hex[33])
{
    HCRYPTPROV prov = 0;
    HCRYPTHASH hash = 0;
    int ok = 0;

    FILE *f = fopen(path, "rb");
    if (!f) return 0;

    if (!CryptAcquireContextA(&prov, NULL, NULL, PROV_RSA_FULL, CRYPT_VERIFYCONTEXT))
        goto md5_done;
    if (!CryptCreateHash(prov, CALG_MD5, 0, 0, &hash))
        goto md5_done;

    unsigned char buf[65536];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
        if (!CryptHashData(hash, buf, (DWORD)n, 0)) goto md5_done;

    BYTE digest[16];
    DWORD dlen = 16;
    if (!CryptGetHashParam(hash, HP_HASHVAL, digest, &dlen, 0))
        goto md5_done;

    for (int i = 0; i < 16; i++)
        snprintf(out_hex + i * 2, 3, "%02x", (unsigned)digest[i]);
    ok = 1;

md5_done:
    if (hash) CryptDestroyHash(hash);
    if (prov) CryptReleaseContext(prov, 0);
    fclose(f);
    return ok;
}

static int verify_file(const char *path, const char *method, const char *expected)
{
    if (!method[0] || !expected[0]) return 1;

    char computed[64] = {0};

    if (_stricmp(method, "crc32c") == 0) {
        uint32_t crc = _pfg_crc32c(path);
        snprintf(computed, sizeof(computed), "%08x", (unsigned)crc);
    } else if (_stricmp(method, "md5") == 0) {
        char hex[33] = {0};
        if (!_pfg_md5(path, hex)) return 0;
        strncpy(computed, hex, sizeof(computed) - 1);
    } else if (_stricmp(method, "filesize") == 0) {
        WIN32_FILE_ATTRIBUTE_DATA fa;
        if (!GetFileAttributesExA(path, GetFileExInfoStandard, &fa)) return 0;
        int64_t sz = ((int64_t)fa.nFileSizeHigh << 32) | fa.nFileSizeLow;
        snprintf(computed, sizeof(computed), "%lld", (long long)sz);
    } else {
        return 1;
    }

    return _stricmp(computed, expected) == 0;
}

static int verify_all_checksums(const char *game_dir, const PatchMeta *meta)
{
    if (!meta->checksums || !meta->checksums[0]) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("No verification data in this patch."));
        return 1;
    }
    if (!meta->verify_method[0]) return 1;

    char *buf = _strdup(meta->checksums);
    if (!buf) return 0;

    int all_pass = 1, total = 0, passed = 0;
    char *entry = buf;

    while (entry && *entry) {
        char *next = strchr(entry, ';');
        if (next) *next = '\0';

        char *pipe = strchr(entry, '|');
        if (pipe) {
            *pipe = '\0';
            const char *rel_path = entry;
            const char *expected = pipe + 1;

            if (!pfg_path_is_safe(rel_path)) {
                all_pass = 0;
                entry = next ? next + 1 : NULL;
                continue;
            }

            char full_path[MAX_PATH];
            snprintf(full_path, MAX_PATH, "%s\\%s", game_dir, rel_path);
            for (char *p = full_path; *p; p++)
                if (*p == '/') *p = '\\';

            total++;
            int ok = verify_file(full_path, meta->verify_method, expected);
            if (ok) passed++;
            else    all_pass = 0;

            size_t rlen = strlen(rel_path);
            char *log_line = (char *)malloc(rlen + 10);
            if (log_line) {
                snprintf(log_line, rlen + 10, "  %s: %s",
                         ok ? "OK" : "FAIL", rel_path);
                PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)log_line);
            }
        }

        entry = next ? next + 1 : NULL;
    }
    free(buf);

    char *summary = (char *)malloc(64);
    if (summary) {
        snprintf(summary, 64, "Verification: %d/%d passed.", passed, total);
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)summary);
    }
    return all_pass;
}

/* Pre-patch: verify source files match expected checksums.
   Returns 1 if all match (or no source_checksums in meta), 0 if any fail.
   On failure, fills err_msg (size err_size) with a human-readable reason. */
static int verify_source_files(const char *game_dir, const PatchMeta *meta,
                                char *err_msg, int err_size)
{
    if (!meta->source_checksums || !meta->source_checksums[0]) return 1;
    if (!meta->verify_method[0]) return 1;

    char *buf = _strdup(meta->source_checksums);
    if (!buf) return 1;  /* can't verify, allow through */

    int failed = 0;
    char first_bad[MAX_PATH] = {0};
    char *entry = buf;

    while (entry && *entry) {
        char *next = strchr(entry, ';');
        if (next) *next = '\0';

        char *pipe = strchr(entry, '|');
        if (pipe) {
            *pipe = '\0';
            const char *rel_path = entry;
            const char *expected = pipe + 1;

            if (!pfg_path_is_safe(rel_path)) {
                failed++;
                if (!first_bad[0])
                    strncpy(first_bad, rel_path, MAX_PATH - 1);
                entry = next ? next + 1 : NULL;
                continue;
            }

            char full_path[MAX_PATH];
            snprintf(full_path, MAX_PATH, "%s\\%s", game_dir, rel_path);
            for (char *p = full_path; *p; p++)
                if (*p == '/') *p = '\\';

            /* Missing file counts as wrong version */
            DWORD attr = GetFileAttributesA(full_path);
            int exists = (attr != INVALID_FILE_ATTRIBUTES &&
                          !(attr & FILE_ATTRIBUTE_DIRECTORY));
            int ok = exists && verify_file(full_path, meta->verify_method, expected);

            if (!ok) {
                failed++;
                if (!first_bad[0])
                    strncpy(first_bad, rel_path, MAX_PATH - 1);
            }
        }
        entry = next ? next + 1 : NULL;
    }
    free(buf);

    if (failed == 0) return 1;

    if (err_msg && err_size > 0) {
        if (meta->version[0])
            snprintf(err_msg, err_size,
                "Wrong game version.\n\n"
                "This patch requires version %s.\n\n"
                "%d file(s) did not match, e.g.:\n  %s",
                meta->version, failed, first_bad);
        else
            snprintf(err_msg, err_size,
                "Wrong game version.\n\n"
                "%d file(s) did not match the expected source, e.g.:\n  %s",
                failed, first_bad);
    }
    return 0;
}

/* ---- Recursive directory copy (used for backup) ---- */
static int pfg_copy_dir(const char *src, const char *dst)
{
    if (!CreateDirectoryA(dst, NULL) && GetLastError() != ERROR_ALREADY_EXISTS)
        return 0;

    char search[MAX_PATH];
    snprintf(search, MAX_PATH, "%s\\*", src);

    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(search, &fd);
    if (h == INVALID_HANDLE_VALUE) return 1;

    int ok = 1;
    do {
        if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..")) continue;
        char s[MAX_PATH], d[MAX_PATH];
        snprintf(s, MAX_PATH, "%s\\%s", src, fd.cFileName);
        snprintf(d, MAX_PATH, "%s\\%s", dst, fd.cFileName);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (!pfg_copy_dir(s, d)) ok = 0;
        } else {
            if (!CopyFileA(s, d, FALSE)) ok = 0;
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return ok;
}

/* ---- Create parent directories for a full path ---- */
static void pfg_ensure_parent_dirs(const char *full_path)
{
    char tmp[MAX_PATH];
    strncpy(tmp, full_path, MAX_PATH - 1);
    tmp[MAX_PATH - 1] = '\0';
    /* Skip drive root, e.g. "C:\" */
    for (char *p = tmp + 3; *p; p++) {
        if (*p == '\\') {
            *p = '\0';
            CreateDirectoryA(tmp, NULL);
            *p = '\\';
        }
    }
}

/* ---- Run a command and wait for it to exit ---- */
static void pfg_run_and_wait(const char *cmd)
{
    if (!cmd || !cmd[0]) return;
    char buf[1024];
    strncpy(buf, cmd, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof(si)); si.cb = sizeof(si);
    memset(&pi, 0, sizeof(pi));
    if (CreateProcessA(NULL, buf, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        WaitForSingleObject(pi.hProcess, INFINITE);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    }
}

/* ---- Perform backup using the configured backup_at setting ---- */
static int pfg_do_backup(const char *game_dir, const PatchMeta *meta)
{
    if (_stricmp(meta->backup_at, "disabled") == 0) return 1;

    char backup[MAX_PATH];
    if (_stricmp(meta->backup_at, "custom") == 0 && meta->backup_path[0]) {
        snprintf(backup, MAX_PATH, "%s\\%s_backup",
                 meta->backup_path,
                 meta->app_name[0] ? meta->app_name : "game");
    } else {
        /* same_folder: place sibling of game_dir */
        snprintf(backup, MAX_PATH, "%s_pfg_backup", game_dir);
    }

    int ok = pfg_copy_dir(game_dir, backup);
    if (ok) {
        char msg[MAX_PATH + 32];
        snprintf(msg, sizeof(msg), "Backup saved: %s", backup);
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup(msg));
    } else {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("WARNING: backup incomplete, continuing anyway."));
    }
    return ok;
}

/* ---- Write extra bundled files into the game folder ---- */
static void pfg_write_extra_files(const char *game_dir, const PatchMeta *meta)
{
    if (meta->num_extra_files == 0) return;

    HANDLE exe = CreateFileA(g_exe_path, GENERIC_READ, FILE_SHARE_READ,
                             NULL, OPEN_EXISTING, 0, NULL);
    if (exe == INVALID_HANDLE_VALUE) {
        PostMessageA(g_hwnd, WM_LOG_MSG, 0,
            (LPARAM)_strdup("WARNING: could not open exe to extract extra files."));
        return;
    }

    for (int i = 0; i < meta->num_extra_files && i < MAX_EXTRA_FILES; i++) {
        const ExtraFileMeta *ef = &meta->extra_files_meta[i];
        if (ef->size <= 0 || !ef->dest[0]) continue;

        /* Build full destination path */
        char full[MAX_PATH];
        /* If dest starts with drive letter or backslash, use as absolute path */
        if ((ef->dest[1] == ':') || (ef->dest[0] == '\\')) {
            strncpy(full, ef->dest, MAX_PATH - 1);
        } else {
            snprintf(full, MAX_PATH, "%s\\%s", game_dir, ef->dest);
        }
        /* Ensure parent directories exist */
        pfg_ensure_parent_dirs(full);

        /* Seek to file data in exe */
        LARGE_INTEGER pos;
        pos.QuadPart = ef->offset;
        SetFilePointerEx(exe, pos, NULL, FILE_BEGIN);

        /* Write output file */
        HANDLE out = CreateFileA(full, GENERIC_WRITE, 0, NULL,
                                 CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (out == INVALID_HANDLE_VALUE) {
            char msg[MAX_PATH + 64];
            snprintf(msg, sizeof(msg), "WARNING: could not create: %s", ef->dest);
            PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup(msg));
            continue;
        }

        int64_t rem = ef->size;
        char buf[65536];
        int write_ok = 1;
        while (rem > 0) {
            DWORD chunk = (DWORD)(rem > (int64_t)sizeof(buf) ? sizeof(buf) : rem);
            DWORD rd, wr;
            if (!ReadFile(exe, buf, chunk, &rd, NULL) || rd == 0) { write_ok = 0; break; }
            if (!WriteFile(out, buf, rd, &wr, NULL) || wr != rd) { write_ok = 0; break; }
            rem -= rd;
        }
        CloseHandle(out);
        if (!write_ok) {
            DeleteFileA(full);
            char msg[MAX_PATH + 64];
            snprintf(msg, sizeof(msg), "WARNING: failed to write extra file: %s", ef->dest);
            PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup(msg));
            continue;
        }

        char msg[MAX_PATH + 32];
        snprintf(msg, sizeof(msg), "  Installed: %s", ef->dest);
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup(msg));
    }
    CloseHandle(exe);
}

/* ---- Load backdrop image from exe using WIC ---- */
static void pfg_load_backdrop(void)
{
    if (g_meta.backdrop_size <= 0 || g_meta.backdrop_offset <= 0) return;

    /* Read backdrop blob from exe */
    HANDLE f = CreateFileA(g_exe_path, GENERIC_READ, FILE_SHARE_READ,
                           NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return;

    size_t bd_size = (size_t)g_meta.backdrop_size;
    char *blob = (char *)malloc(bd_size);
    if (!blob) { CloseHandle(f); return; }

    LARGE_INTEGER pos;
    pos.QuadPart = g_meta.backdrop_offset;
    SetFilePointerEx(f, pos, NULL, FILE_BEGIN);
    DWORD rd;
    ReadFile(f, blob, (DWORD)bd_size, &rd, NULL);
    CloseHandle(f);

    /* Create IStream over the memory blob */
    HGLOBAL hmem = GlobalAlloc(GMEM_MOVEABLE, bd_size);
    if (!hmem) { free(blob); return; }
    void *mp = GlobalLock(hmem);
    memcpy(mp, blob, bd_size);
    GlobalUnlock(hmem);
    free(blob);

    IStream *stream = NULL;
    if (CreateStreamOnHGlobal(hmem, TRUE, &stream) != S_OK) {
        GlobalFree(hmem);
        return;
    }

    /* Create WIC factory via COM */
    IWICImagingFactory *factory = NULL;
    if (CoCreateInstance(&CLSID_WICImagingFactory, NULL, CLSCTX_INPROC_SERVER,
                         &IID_IWICImagingFactory, (void **)&factory) != S_OK) {
        stream->lpVtbl->Release(stream);
        return;
    }

    IWICBitmapDecoder *decoder = NULL;
    factory->lpVtbl->CreateDecoderFromStream(
        factory, stream, NULL, WICDecodeMetadataCacheOnDemand, &decoder);
    stream->lpVtbl->Release(stream);

    if (!decoder) { factory->lpVtbl->Release(factory); return; }

    IWICBitmapFrameDecode *frame = NULL;
    decoder->lpVtbl->GetFrame(decoder, 0, &frame);
    decoder->lpVtbl->Release(decoder);

    if (!frame) { factory->lpVtbl->Release(factory); return; }

    /* Convert to 32bpp BGR */
    IWICFormatConverter *conv = NULL;
    factory->lpVtbl->CreateFormatConverter(factory, &conv);
    conv->lpVtbl->Initialize(conv, (IWICBitmapSource *)frame,
        &GUID_WICPixelFormat32bppBGR, WICBitmapDitherTypeNone,
        NULL, 0.0, WICBitmapPaletteTypeCustom);
    frame->lpVtbl->Release(frame);

    UINT w = 0, h = 0;
    conv->lpVtbl->GetSize(conv, &w, &h);

    if (w == 0 || h == 0) {
        conv->lpVtbl->Release(conv);
        factory->lpVtbl->Release(factory);
        return;
    }

    BITMAPINFO bmi;
    memset(&bmi, 0, sizeof(bmi));
    bmi.bmiHeader.biSize        = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth       = (LONG)w;
    bmi.bmiHeader.biHeight      = -(LONG)h; /* top-down */
    bmi.bmiHeader.biPlanes      = 1;
    bmi.bmiHeader.biBitCount    = 32;
    bmi.bmiHeader.biCompression = BI_RGB;

    void *bits = NULL;
    HDC screen = GetDC(NULL);
    g_backdrop_bmp = CreateDIBSection(screen, &bmi, DIB_RGB_COLORS, &bits, NULL, 0);
    ReleaseDC(NULL, screen);

    if (g_backdrop_bmp && bits) {
        UINT stride = w * 4;
        conv->lpVtbl->CopyPixels(conv, NULL, stride, stride * h, (BYTE *)bits);
    }

    conv->lpVtbl->Release(conv);
    factory->lpVtbl->Release(factory);
}

/* ---- Render progress bar (pill-shaped, rounded) ---- */
static void pfg_draw_progress(HDC dc, RECT r, int pct)
{
    int rnd = r.bottom - r.top; /* full pill radius */

    /* Background track */
    HBRUSH bg_br  = CreateSolidBrush(COL_PROGRESS_BG);
    HPEN   bg_pen = CreatePen(PS_SOLID, 1, COL_BORDER);
    HPEN   old_p  = (HPEN)SelectObject(dc, bg_pen);
    HBRUSH old_b  = (HBRUSH)SelectObject(dc, bg_br);
    RoundRect(dc, r.left, r.top, r.right, r.bottom, rnd, rnd);
    SelectObject(dc, old_p);
    SelectObject(dc, old_b);
    DeleteObject(bg_br);
    DeleteObject(bg_pen);

    if (pct <= 0) return;

    /* Filled portion (inset by 1 px all sides) */
    RECT f  = r;
    f.left  += 1; f.top += 1; f.bottom -= 1;
    f.right  = r.left + 1 + (int)((r.right - r.left - 2) * pct / 100);
    if (f.right > r.right - 1) f.right = r.right - 1;
    if (f.right <= f.left) return;

    HBRUSH ac_br  = CreateSolidBrush(COL_ACCENT);
    HPEN   ac_pen = CreatePen(PS_SOLID, 1, COL_ACCENT);
    old_p = (HPEN)SelectObject(dc, ac_pen);
    old_b = (HBRUSH)SelectObject(dc, ac_br);
    int frnd = f.bottom - f.top;
    RoundRect(dc, f.left, f.top, f.right, f.bottom, frnd, frnd);
    SelectObject(dc, old_p);
    SelectObject(dc, old_b);
    DeleteObject(ac_br);
    DeleteObject(ac_pen);
}

/* ---- Compute image-band height from backdrop aspect ratio ----
 * Mirrors installer_stub.c so patcher chrome matches installer chrome.
 * Call after pfg_load_backdrop. */
static void pfg_compute_img_h(void)
{
    if (!g_backdrop_bmp) { g_img_h = 0; return; }
    g_img_h = (int)((720 * BACKDROP_ASPECT_H + BACKDROP_ASPECT_W / 2)
                    / BACKDROP_ASPECT_W);
    if (g_img_h > IMG_MAX_H) g_img_h = IMG_MAX_H;
    if (g_img_h < 60)        g_img_h = 60;
}

/* ---- Installer-style background paint ----
 * Solid bg + image band (height g_img_h) + accent separator under
 * the image + 1px footer separator at g_foot_sep_y. Drive from
 * WM_ERASEBKGND. Returns 1 (handled). */
static int pfg_paint_band_background(HWND hwnd, HDC dc)
{
    RECT r; GetClientRect(hwnd, &r);
    FillRect(dc, &r, g_brush_bg);

    if (g_backdrop_bmp && g_img_h > 0) {
        HDC mdc = CreateCompatibleDC(dc);
        if (mdc) {
            SelectObject(mdc, g_backdrop_bmp);
            BITMAP bm = {0};
            GetObjectA(g_backdrop_bmp, sizeof(bm), &bm);
            SetStretchBltMode(dc, HALFTONE);
            SetBrushOrgEx(dc, 0, 0, NULL);
            StretchBlt(dc, 0, 0, r.right, g_img_h,
                       mdc, 0, 0, bm.bmWidth, bm.bmHeight, SRCCOPY);
            DeleteDC(mdc);
            HBRUSH sep = CreateSolidBrush(COL_ACCENT);
            RECT   sep_r = {0, g_img_h, r.right, g_img_h + 2};
            FillRect(dc, &sep_r, sep);
            DeleteObject(sep);
        }
    }

    if (g_foot_sep_y > 0) {
        HBRUSH fsep = CreateSolidBrush(COL_BORDER);
        RECT   fsep_r = {20, g_foot_sep_y, r.right - 20, g_foot_sep_y + 1};
        FillRect(dc, &fsep_r, fsep);
        DeleteObject(fsep);
    }
    return 1;
}

/* ---- Smart UAC elevation ---- */
/* Returns 1 if we have write access (proceed), 0 if we relaunched elevated
   (the current window will close; do NOT start the patch thread). */
static int pfg_check_elevate(const char *path)
{
    /* Write-test: create a temp file in the target directory */
    char probe[MAX_PATH];
    snprintf(probe, MAX_PATH, "%s\\.pfg_uac_probe", path);
    HANDLE h = CreateFileA(probe, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        CloseHandle(h);
        return 1; /* have write access */
    }
    if (GetLastError() != ERROR_ACCESS_DENIED) return 1; /* not a permission issue */

    /* Need elevation — relaunch as administrator, passing path as argument */
    char params[MAX_PATH + 2];
    snprintf(params, sizeof(params), "\"%s\"", path);

    SHELLEXECUTEINFOA sei;
    memset(&sei, 0, sizeof(sei));
    sei.cbSize       = sizeof(sei);
    sei.fMask        = SEE_MASK_NOCLOSEPROCESS;
    sei.hwnd         = g_hwnd;
    sei.lpVerb       = "runas";
    sei.lpFile       = g_exe_path;
    sei.lpParameters = params;
    sei.nShow        = SW_SHOWNORMAL;

    if (ShellExecuteExA(&sei)) {
        if (sei.hProcess) CloseHandle(sei.hProcess);
        /* Self-close so the elevated instance is the only one running */
        PostMessageA(g_hwnd, WM_DESTROY, 0, 0);
        return 0;
    }
    /* User declined UAC or ShellExecuteEx failed — proceed without elevation */
    return 1;
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

/* ---- Detect running game process ---- */
/* Returns 1 if ok to proceed, 0 if user cancelled. */
static int pfg_check_running_exe(HWND hwnd, const char *exe_name)
{
    if (!exe_name || !exe_name[0]) return 1;
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 1;
    PROCESSENTRY32 pe;
    pe.dwSize = sizeof(pe);
    int found = 0;
    if (Process32First(snap, &pe)) {
        do {
            if (_stricmp(pe.szExeFile, exe_name) == 0) { found = 1; break; }
        } while (Process32Next(snap, &pe));
    }
    CloseHandle(snap);
    if (!found) return 1;
    char msg[512];
    snprintf(msg, sizeof(msg),
        "The game appears to be running.\n\n"
        "Process detected: %s\n\n"
        "Please close the game before applying this patch.\n\n"
        "Continue anyway?",
        exe_name);
    return (MessageBoxA(hwnd, msg, "Game Is Running",
                        MB_YESNO | MB_ICONWARNING) == IDYES) ? 1 : 0;
}

/* ---- Run startup command asynchronously (fire-and-forget thread) ---- */
static DWORD WINAPI pfg_run_async_thread(LPVOID arg)
{
    pfg_run_and_wait((const char *)arg);
    free(arg);
    return 0;
}
static void pfg_run_async(const char *cmd)
{
    if (!cmd || !cmd[0]) return;
    char *copy = _strdup(cmd);
    if (copy) CloseHandle(CreateThread(NULL, 0, pfg_run_async_thread, copy, 0, NULL));
}

/* ---- Disk space check ---- */
/* Returns 1 if ok to proceed, 0 if user cancelled. */
static int pfg_check_free_space(HWND hwnd, const char *game_path, double required_gb)
{
    if (required_gb <= 0.0) return 1;
    /* Use the drive root of the game path */
    char root[4] = {0};
    if (game_path[0] && game_path[1] == ':') {
        root[0] = game_path[0]; root[1] = ':'; root[2] = '\\';
    } else {
        return 1; /* Can't determine drive — skip check */
    }
    ULARGE_INTEGER free_bytes;
    if (!GetDiskFreeSpaceExA(root, &free_bytes, NULL, NULL)) return 1;
    double free_gb = (double)free_bytes.QuadPart / (1024.0 * 1024.0 * 1024.0);
    if (free_gb >= required_gb) return 1;
    char msg[320];
    snprintf(msg, sizeof(msg),
        "Low disk space warning\n\n"
        "Required:  %.1f GB\n"
        "Available: %.1f GB\n\n"
        "Continue patching anyway?",
        required_gb, free_gb);
    return (MessageBoxA(hwnd, msg, "Disk Space Warning",
                        MB_YESNO | MB_ICONWARNING) == IDYES) ? 1 : 0;
}

/* ---- Timestamp preservation ---- */
typedef struct { char path[MAX_PATH]; FILETIME mtime; } FileStamp;

static void pfg_snapshot_dir(const char *dir, FileStamp **arr, int *cnt, int *cap)
{
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof(pattern), "%s\\*", dir);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..")) continue;
        char full[MAX_PATH];
        snprintf(full, sizeof(full), "%s\\%s", dir, fd.cFileName);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            pfg_snapshot_dir(full, arr, cnt, cap);
        } else {
            if (*cnt >= *cap) {
                *cap = (*cap == 0) ? 64 : (*cap * 2);
                *arr = (FileStamp *)realloc(*arr, (size_t)(*cap) * sizeof(FileStamp));
                if (!*arr) { *cnt = 0; *cap = 0; FindClose(h); return; }
            }
            strncpy((*arr)[*cnt].path, full, MAX_PATH - 1);
            (*arr)[*cnt].path[MAX_PATH - 1] = '\0';
            (*arr)[*cnt].mtime = fd.ftLastWriteTime;
            (*cnt)++;
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
}

/* Snapshot all file mtimes under dir. Returns malloc'd array; caller frees. */
static FileStamp *pfg_snapshot_timestamps(const char *dir, int *out_count)
{
    FileStamp *arr = NULL; int cnt = 0, cap = 0;
    pfg_snapshot_dir(dir, &arr, &cnt, &cap);
    *out_count = cnt;
    return arr;
}

/* Restore mtimes from a snapshot. */
static void pfg_restore_timestamps(FileStamp *snap, int count)
{
    for (int i = 0; i < count; i++) {
        HANDLE h = CreateFileA(snap[i].path, FILE_WRITE_ATTRIBUTES,
                               FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                               OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
        if (h != INVALID_HANDLE_VALUE) {
            SetFileTime(h, NULL, NULL, &snap[i].mtime);
            CloseHandle(h);
        }
    }
}

/* ---- Build patcher window contents (matches installer chrome) ----
 * Creates every static/control inside the patcher window: title row,
 * subtitle / description / change-summary lines, "Game folder:" path
 * row, SETTINGS section header, backup + verify checkboxes, log,
 * progress bar, status, footer separator (via g_foot_sep_y), footer
 * info label, and Cancel / Patch buttons. Pre-populates the path edit
 * from preset/UAC arg or registry/ini auto-detect, and kicks off the
 * run_on_startup command. Returns the y of the bottom of the last
 * control so WinMain can size the window to fit. */
static int pfg_build_patcher_gui(HWND hwnd)
{
    const int lx   = 20;
    const int crw  = 680;
    const int rmax = 700;

    int cy = g_img_h + 2;
    int title_y = cy + 14;

    HWND lbl_title = CreateWindowExA(0, "STATIC",
        g_meta.app_name[0] ? g_meta.app_name : "PatchForge Patcher",
        WS_CHILD | WS_VISIBLE | SS_LEFT,
        lx, title_y, 500, 28, hwnd, NULL, NULL, NULL);
    SendMessageA(lbl_title, WM_SETFONT, (WPARAM)g_font_title, TRUE);

    if (g_meta.version[0]) {
        HWND lbl_ver = CreateWindowExA(0, "STATIC", g_meta.version,
            WS_CHILD | WS_VISIBLE | SS_RIGHT,
            rmax - 150, title_y + 8, 150, 16, hwnd, NULL, NULL, NULL);
        SendMessageA(lbl_ver, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
    }

    int subtitle_h = 0;
    if (g_meta.app_note[0]) {
        HWND s = CreateWindowExA(0, "STATIC", g_meta.app_note,
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            lx, title_y + 30, crw, 16, hwnd, NULL, NULL, NULL);
        SendMessageA(s, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        subtitle_h = 18;
    }
    int desc_h = 0;
    if (g_meta.description[0]) {
        HWND d = CreateWindowExA(0, "STATIC", g_meta.description,
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            lx, title_y + 30 + subtitle_h, crw, 16, hwnd, NULL, NULL, NULL);
        SendMessageA(d, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        desc_h = 18;
    }

    int summary_h = 0;
    {
        int m = g_meta.files_modified;
        int a = g_meta.files_added;
        int rem = g_meta.files_removed;
        if (m + a + rem > 0) {
            char cbuf[128] = {0};
            int pos = 0;
            if (m) pos += snprintf(cbuf + pos, sizeof(cbuf) - pos,
                                   "%d modified", m);
            if (a) pos += snprintf(cbuf + pos, sizeof(cbuf) - pos,
                                   "%s%d added", pos ? "  \xB7  " : "", a);
            if (rem) pos += snprintf(cbuf + pos, sizeof(cbuf) - pos,
                                     "%s%d removed", pos ? "  \xB7  " : "", rem);
            HWND sum = CreateWindowExA(0, "STATIC", cbuf,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                lx, title_y + 30 + subtitle_h + desc_h, crw, 16,
                hwnd, NULL, NULL, NULL);
            SendMessageA(sum, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            summary_h = 18;
        }
    }

    int path_y = title_y + 30 + subtitle_h + desc_h + summary_h + 12;

    HWND lbl_path = CreateWindowExA(0, "STATIC", "Game folder:",
        WS_CHILD | WS_VISIBLE | SS_LEFT,
        lx, path_y, 100, 16, hwnd, NULL, NULL, NULL);
    SendMessageA(lbl_path, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

    g_hwnd_filepath = CreateWindowExA(0, "EDIT", "",
        WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
        lx, path_y + 18, 568, 26, hwnd, (HMENU)IDC_FILEPATH, NULL, NULL);
    SendMessageA(g_hwnd_filepath, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

    CreateWindowExA(0, "BUTTON", "Browse...",
        WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
        lx + 572, path_y + 18, 108, 26, hwnd,
        (HMENU)IDC_BTN_BROWSE, NULL, NULL);

    /* SETTINGS section */
    int sec_y = path_y + 18 + 26 + 10;
    HWND sec = CreateWindowExA(0, "STATIC", "SETTINGS",
        WS_CHILD | WS_VISIBLE | SS_LEFT,
        lx, sec_y, crw, 16, hwnd, NULL, NULL, NULL);
    SendMessageA(sec, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

    int chk_y = sec_y + 20;
    g_hwnd_chk_backup = CreateWindowExA(0, "BUTTON",
        "Create backup before patching",
        WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
        lx, chk_y, crw, 20, hwnd, (HMENU)IDC_CHK_BACKUP, NULL, NULL);
    SendMessageA(g_hwnd_chk_backup, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
    SendMessageA(g_hwnd_chk_backup, BM_SETCHECK, BST_CHECKED, 0);
    chk_y += 24;

    g_hwnd_chk_verify = CreateWindowExA(0, "BUTTON",
        "Verify after patching",
        WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
        lx, chk_y, crw, 20, hwnd, (HMENU)IDC_CHK_VERIFY, NULL, NULL);
    SendMessageA(g_hwnd_chk_verify, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
    SendMessageA(g_hwnd_chk_verify, BM_SETCHECK, BST_CHECKED, 0);
    chk_y += 24;

    int log_y = chk_y + 6;
    g_hwnd_log = CreateWindowExA(0, "EDIT", "",
        WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL |
        ES_READONLY | WS_VSCROLL,
        lx, log_y, crw, 120, hwnd, (HMENU)IDC_LOG, NULL, NULL);
    SendMessageA(g_hwnd_log, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
    SendMessageA(g_hwnd_log, EM_SETLIMITTEXT, 0, 0);

    int prog_y = log_y + 124;
    g_hwnd_progress = CreateWindowExA(0, "STATIC", "",
        WS_CHILD | WS_VISIBLE | SS_OWNERDRAW,
        lx, prog_y, crw, 10, hwnd, (HMENU)IDC_PROGRESS, NULL, NULL);
    SetWindowLongA(g_hwnd_progress, GWLP_USERDATA, 0);

    int stat_y = prog_y + 14;
    g_hwnd_status = CreateWindowExA(0, "STATIC",
        "Select the game folder and click Patch.",
        WS_CHILD | WS_VISIBLE | SS_LEFT,
        lx, stat_y, 500, 16, hwnd, (HMENU)IDC_STATUS, NULL, NULL);
    SendMessageA(g_hwnd_status, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

    g_foot_sep_y = stat_y + 20;

    int foot_y = g_foot_sep_y + 8;
    {
        char info[512] = {0};
        int pos = 0;
        const char *parts[] = {
            g_meta.company_info[0] ? g_meta.company_info : NULL,
            g_meta.copyright[0]    ? g_meta.copyright    : NULL,
            g_meta.contact[0]      ? g_meta.contact      : NULL,
        };
        for (int i = 0; i < 3; i++) {
            if (!parts[i]) continue;
            if (pos > 0) pos += snprintf(info + pos, sizeof(info) - pos,
                                         "  \xB7  ");
            pos += snprintf(info + pos, sizeof(info) - pos, "%s", parts[i]);
        }
        if (pos > 0) {
            HWND infolbl = CreateWindowExA(0, "STATIC", info,
                WS_CHILD | WS_VISIBLE | SS_LEFT | SS_ENDELLIPSIS,
                lx, foot_y + 7, 400, 14, hwnd, NULL, NULL, NULL);
            SendMessageA(infolbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        }
    }

    g_hwnd_btn_patch = CreateWindowExA(0, "BUTTON", "Patch",
        WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
        rmax - 88, foot_y, 88, 28, hwnd,
        (HMENU)IDC_BTN_PATCH, NULL, NULL);
    CreateWindowExA(0, "BUTTON", "Cancel",
        WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
        rmax - 88 - 8 - 80, foot_y, 80, 28, hwnd,
        (HMENU)IDC_BTN_CANCEL, NULL, NULL);

    /* Run on-startup command async */
    pfg_run_async(g_meta.run_on_startup);

    /* Pre-populate path: preset (UAC relaunch) > auto-detect */
    char auto_path[MAX_PATH] = {0};
    if (strcmp(g_meta.find_method, "registry") == 0)
        find_via_registry(&g_meta, auto_path, MAX_PATH);
    else if (strcmp(g_meta.find_method, "ini") == 0)
        find_via_ini(&g_meta, auto_path, MAX_PATH);
    {
        const char *init = g_preset_path[0] ? g_preset_path : auto_path;
        if (init[0]) SetWindowTextA(g_hwnd_filepath, init);
    }

    return foot_y + 28;
}

#endif /* STUB_COMMON_H */

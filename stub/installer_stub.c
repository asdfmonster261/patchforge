/*
 * installer_stub.c — PatchForge self-extracting game installer (Win32)
 *
 * File layout (appended to this exe):
 *   [XPACK01 blob: file table + XZ/LZMA2-compressed data]
 *   [backdrop image bytes]  (zero if none)
 *   [JSON metadata, UTF-8 ]
 *   [4B LE: metadata_len  ]
 *   [8B magic: "XPACK01\0"]
 */

#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0600
#include <windows.h>
#include <shellapi.h>
#include <dwmapi.h>
#include <commdlg.h>
#include <shlobj.h>
#include <wincodec.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <lzma.h>
#include "third_party/zstd/zstddeclib.c"

/* ---- Colours (dark theme, same palette as patcher stubs) ---- */
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
#define COL_WARN        RGB(0xe8, 0xa0, 0x30)

/* ---- Control IDs ---- */
#define IDC_STATUS       1001
#define IDC_PROGRESS     1002
#define IDC_BTN_BROWSE   1003
#define IDC_BTN_INSTALL  1004
#define IDC_BTN_CANCEL   1005
#define IDC_FILEPATH     1006
#define IDC_LOG          1007
#define IDC_CHK_LOWLOAD  1010
#define IDC_SPACE_LBL    1011
#define IDC_CHK_VERIFY        1012
#define IDC_CHK_SC_STARTMENU  1013
#define IDC_CHK_SC_DESKTOP    1014
#define IDC_COMP_BASE    1020   /* component checkboxes/radios: 1020, 1021, ... */
#define IDC_GROUP_BASE   1040   /* group-enable checkboxes:    1040, 1041, ... */

/* ---- Component limits ---- */
#define MAX_COMPONENTS   16

/* ---- Backdrop layout ---- */
#define BACKDROP_ASPECT_W  616   /* reference aspect ratio width  */
#define BACKDROP_ASPECT_H  353   /* reference aspect ratio height */
#define IMG_MAX_H          480   /* hard ceiling (px) */

/* ---- Thread messages ---- */
#define WM_INSTALL_DONE  (WM_USER + 1)
#define WM_INSTALL_PROG  (WM_USER + 2)
#define WM_LOG_MSG       (WM_USER + 3)

/* ---- Timer ---- */
#define TIMER_CLOSE 1

/* ---- Installer metadata ---- */
typedef struct {
    char   app_name[256];
    char   app_note[256];
    char   version[64];
    char   description[512];
    char   copyright[256];
    char   contact[256];
    char   company_info[256];
    char   window_title[256];
    char   installer_exe_version[64];
    int    total_files;
    int64_t total_uncompressed_size;
    char   install_subdir[256];       /* base name of the source game folder */
    char   install_registry_key[512];
    char   run_after_install[512];
    char   detect_running_exe[256];
    int    close_delay;
    double required_free_space_gb;
    int64_t pack_data_offset;
    int64_t pack_data_size;
    int64_t backdrop_offset;
    int64_t backdrop_size;
    int64_t uninstaller_offset;
    int64_t uninstaller_size;
    char    arp_subkey[256];
    int     include_uninstaller;
    int     verify_crc32;
    char    shortcut_target[512];
    char    shortcut_name[256];
    int     shortcut_create_desktop;
    int     shortcut_create_startmenu;
    char    codec[16];   /* "lzma" (default) or "zstd" */
    int     bin_parts;        /* 1 = single file; >1 = split into .001, .002 ... */
    int64_t bin_part_size;    /* fixed size of each part except the last */
} InstallMeta;

/* ---- Per-file table entry ---- */
typedef struct {
    char     path[512];
    uint64_t offset;
    uint64_t size;
    uint32_t component;
    uint32_t crc32;
} PackEntry;

/* ---- Global state ---- */
static HWND       g_hwnd              = NULL;
static HWND       g_hwnd_filepath     = NULL;
static HWND       g_hwnd_status       = NULL;
static HWND       g_hwnd_progress     = NULL;
static HWND       g_hwnd_log          = NULL;
static HWND       g_hwnd_btn_install  = NULL;
static HWND       g_hwnd_chk_lowload  = NULL;
static HWND       g_hwnd_chk_verify        = NULL;
static HWND       g_hwnd_chk_sc_startmenu  = NULL;
static HWND       g_hwnd_chk_sc_desktop    = NULL;
static HWND       g_hwnd_space_lbl    = NULL;
static HBRUSH     g_brush_bg          = NULL;
static HBRUSH     g_brush_light       = NULL;
static HBRUSH     g_brush_log         = NULL;
static HFONT      g_font_normal       = NULL;
static HFONT      g_font_title        = NULL;
static InstallMeta g_meta             = {0};
static char       g_exe_path[MAX_PATH]= {0};
static char       g_bin_path[MAX_PATH]= {0};  /* same as g_exe_path unless split_bin */
/* External component sidecar data, indexed by component index (1-based).
 * g_ext_bin[i] non-empty means the stream lives in <exe_dir>/<name>. */
static char    g_ext_bin[MAX_COMPONENTS + 1][64]    = {{0}};
static int64_t g_ext_offset[MAX_COMPONENTS + 1]     = {0};
static int64_t g_ext_csize[MAX_COMPONENTS + 1]      = {0};
static PackEntry *g_entries           = NULL;
static uint32_t   g_num_entries       = 0;
static int        g_close_countdown   = 0;
static HBITMAP    g_backdrop_bmp      = NULL;
static int        g_btn_hover_install = 0;
static int        g_btn_hover_cancel  = 0;
static int        g_silent            = 0;
static char       g_silent_dir[MAX_PATH] = {0};
static char       g_user_desktop[MAX_PATH]  = {0}; /* per-user Desktop, captured pre-elevation */
static char       g_user_programs[MAX_PATH] = {0}; /* per-user Start Menu\Programs, same */
static int        g_img_h             = 0;   /* rendered backdrop height in window */
static int        g_foot_sep_y        = 0;   /* y of footer separator line */
static HWND       g_hwnd_subtitle     = NULL; /* app_note label (dim colour) */
static HWND       g_hwnd_desc        = NULL; /* description label (dim colour) */
static HWND       g_hwnd_summary      = NULL; /* files·size label (dim colour) */
static HWND       g_hwnd_sec_settings = NULL; /* "SETTINGS" section header (dim) */
static HWND       g_hwnd_sec_comps    = NULL; /* "OPTIONAL COMPONENTS" section header (dim) */
static HWND       g_hwnd_sac_warn     = NULL; /* SAC/AV warning label (hidden when irrelevant) */

/* ---- Optional components ---- */
typedef struct {
    int  index;
    char label[256];
    char group[64];
    int  default_checked;
    int  requires[MAX_COMPONENTS];  /* 1-based indices of components this one depends on */
    int  num_requires;
    char shortcut_target[512];      /* overrides g_meta.shortcut_target if non-empty */
    int  sac_warning;               /* show SAC/AV warning when this component is checked */
    uint64_t size_bytes;            /* total uncompressed size of this component's files */
    HWND hwnd_ctrl;
} ComponentInfo;

static ComponentInfo g_components[MAX_COMPONENTS];
static int           g_num_components = 0;

typedef struct {
    char group[64];
    HWND hwnd_hdr;  /* the group-enable checkbox */
} GroupInfo;

static GroupInfo g_groups[MAX_COMPONENTS];
static int       g_num_groups = 0;

/* ---- Forward declarations ---- */
LRESULT CALLBACK WndProc(HWND, UINT, WPARAM, LPARAM);
static void refresh_component_states(void);

/* ==================================================================== */
/* CRC32 (IEEE 802.3 polynomial, same as zlib)                          */
/* ==================================================================== */

static uint32_t g_crc32_table[256];

static void init_crc32_table(void)
{
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320u & (uint32_t)(-(int)(c & 1)));
        g_crc32_table[i] = c;
    }
}

/* Feed bytes into a running CRC32. Call with crc=0xFFFFFFFF to start (matches zlib.crc32).
 * XOR with 0xFFFFFFFF to finalize: final = crc32_update(running, ...) ^ 0xFFFFFFFFu */
static uint32_t crc32_update(uint32_t crc, const uint8_t *buf, size_t len)
{
    while (len--) crc = g_crc32_table[(crc ^ *buf++) & 0xFF] ^ (crc >> 8);
    return crc;
}

/* ==================================================================== */
/* JSON helpers (same lightweight approach as patcher stubs)             */
/* ==================================================================== */

/* Format a byte count as "X.X B/KB/MB/GB/TB" into a caller-provided buffer. */
static void format_size_bytes(uint64_t n, char *out, size_t out_len)
{
    static const char *units[] = {"B", "KB", "MB", "GB", "TB"};
    double v = (double)n;
    int u = 0;
    while (v >= 1024.0 && u < 4) { v /= 1024.0; u++; }
    snprintf(out, out_len, "%.1f %s", v, units[u]);
}

/* ---- Multi-part file reader (for base_game.bin.001, .002, ...) ------- */
/* Presents N on-disk parts as one logical stream. Supports read + seek. */
#define MPF_MAX_PARTS 999
typedef struct {
    FILE    *fp;             /* currently open part (NULL = needs open) */
    int      cur_idx;        /* 0-based index of fp */
    int      num_parts;
    int64_t  part_size;      /* fixed size of parts[0..num_parts-2]; last part ≤ this */
    int64_t  pos;            /* logical position in the virtual stream */
    char     base_path[MAX_PATH];   /* path prefix; part N is "base_path.%03d" */
} MPF;

/* If num_parts == 1, base_path is the raw file (no .001 suffix). */
static int mpf_open(MPF *m, const char *base_path, int num_parts, int64_t part_size)
{
    memset(m, 0, sizeof(*m));
    m->num_parts = num_parts;
    m->part_size = part_size;
    size_t bp_len = strlen(base_path);
    if (bp_len >= MAX_PATH) bp_len = MAX_PATH - 1;
    memcpy(m->base_path, base_path, bp_len);
    m->base_path[bp_len] = '\0';
    m->cur_idx = 0;
    char path[MAX_PATH + 8];
    if (num_parts > 1)
        snprintf(path, sizeof(path), "%s.%03d", base_path, 1);
    else
        snprintf(path, sizeof(path), "%s", base_path);
    m->fp = fopen(path, "rb");
    return m->fp != NULL;
}

static void mpf_close(MPF *m)
{
    if (m->fp) { fclose(m->fp); m->fp = NULL; }
}

/* Move to part index idx and position within it. Caller must ensure idx is valid. */
static int mpf_seek_to_part(MPF *m, int idx, int64_t within)
{
    if (idx != m->cur_idx || !m->fp) {
        if (m->fp) { fclose(m->fp); m->fp = NULL; }
        char path[MAX_PATH + 8];
        if (m->num_parts > 1)
            snprintf(path, sizeof(path), "%s.%03d", m->base_path, idx + 1);
        else
            snprintf(path, sizeof(path), "%s", m->base_path);
        m->fp = fopen(path, "rb");
        if (!m->fp) return 0;
        m->cur_idx = idx;
    }
    return _fseeki64(m->fp, within, SEEK_SET) == 0;
}

/* SEEK_SET and SEEK_CUR are supported; SEEK_END is not used here. */
static int mpf_seek(MPF *m, int64_t off, int whence)
{
    int64_t target;
    if (whence == SEEK_SET)      target = off;
    else if (whence == SEEK_CUR) target = m->pos + off;
    else                         return 0;
    if (target < 0) return 0;
    m->pos = target;
    if (m->num_parts <= 1) {
        return m->fp && _fseeki64(m->fp, target, SEEK_SET) == 0;
    }
    int idx = (int)(target / m->part_size);
    int64_t within = target - (int64_t)idx * m->part_size;
    if (idx >= m->num_parts) {
        /* Past the end of the last part — position beyond EOF is fine; reads will
           simply return 0. Don't treat this as an error since it matches fseek. */
        return 1;
    }
    return mpf_seek_to_part(m, idx, within);
}

static size_t mpf_read(MPF *m, void *buf, size_t n)
{
    if (!m->fp) return 0;
    uint8_t *out = (uint8_t *)buf;
    size_t total = 0;
    while (n > 0) {
        size_t got = fread(out + total, 1, n, m->fp);
        total  += got;
        n      -= got;
        m->pos += (int64_t)got;
        if (n == 0) break;
        /* Current part exhausted — advance if more parts remain. */
        if (m->num_parts <= 1 || m->cur_idx + 1 >= m->num_parts) break;
        if (!mpf_seek_to_part(m, m->cur_idx + 1, 0)) break;
    }
    return total;
}

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
    while (*p && i < out_len - 1) {
        if (*p == '\\' && *(p + 1)) {
            p++;                   /* skip backslash, copy escaped char */
        } else if (*p == '"') {
            break;                 /* unescaped quote = end of string */
        }
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

static int json_parse_int_array(const char *json, const char *key, int *out, int max)
{
    char search[128];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return 0;
    p += strlen(search);
    while (*p == ' ' || *p == ':') p++;
    if (*p != '[') return 0;
    p++;
    int count = 0;
    while (count < max) {
        while (*p == ' ' || *p == ',') p++;
        if (*p == ']' || !*p) break;
        if (*p >= '0' && *p <= '9')
            out[count++] = (int)_atoi64(p);
        while (*p && *p != ',' && *p != ']') p++;
    }
    return count;
}

/* Parse "components" JSON array from metadata into g_components[]. */
static void json_parse_components(const char *json)
{
    const char *p = strstr(json, "\"components\"");
    if (!p) return;
    p = strchr(p, '[');
    if (!p) return;
    p++;

    g_num_components = 0;
    while (g_num_components < MAX_COMPONENTS) {
        while (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t' || *p == ',') p++;
        if (*p == ']' || !*p) break;
        if (*p != '{') break;

        /* Find the closing brace for this object */
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

        ComponentInfo *c = &g_components[g_num_components];
        memset(c, 0, sizeof(*c));
        c->index           = (int)json_get_int(tmp, "index");
        c->default_checked = json_get_bool(tmp, "default_checked", 1);
        json_get_str(tmp, "label", c->label, sizeof(c->label));
        json_get_str(tmp, "group", c->group, sizeof(c->group));
        c->num_requires    = json_parse_int_array(tmp, "requires",
                                                  c->requires, MAX_COMPONENTS);
        c->size_bytes      = (uint64_t)json_get_int(tmp, "size_bytes");
        json_get_str(tmp, "shortcut_target", c->shortcut_target, sizeof(c->shortcut_target));
        c->sac_warning     = json_get_bool(tmp, "sac_warning", 0);
        free(tmp);

        if (c->index > 0 && c->label[0])
            g_num_components++;

        p = q;
    }
}

/* Parse a {"1": N, "2": M, ...} JSON object into an int64_t array. */
static void json_parse_ext_int64s(const char *json, const char *key,
                                   int64_t *arr, int max)
{
    char search[128];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return;
    p = strchr(p, '{');
    if (!p) return;
    p++;
    while (*p) {
        while (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t' || *p == ',') p++;
        if (*p == '}' || !*p) break;
        if (*p != '"') break;
        p++;
        int idx = 0;
        while (*p >= '0' && *p <= '9') { idx = idx * 10 + (*p - '0'); p++; }
        if (*p != '"') break;
        p++;
        while (*p == ' ' || *p == ':') p++;
        if (idx >= 0 && idx <= max)
            arr[idx] = (int64_t)_atoi64(p);
        while (*p && *p != ',' && *p != '}') p++;
    }
}

/* Parse "external_components" JSON object {"1":"crack.bin","2":"dlc.bin",...}
 * into g_ext_bin[comp_idx]. */
static void json_parse_external_components(const char *json)
{
    memset(g_ext_bin, 0, sizeof(g_ext_bin));
    const char *p = strstr(json, "\"external_components\"");
    if (!p) return;
    p = strchr(p, '{');
    if (!p) return;
    p++;
    while (*p) {
        while (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t' || *p == ',') p++;
        if (*p == '}' || !*p) break;
        if (*p != '"') break;
        p++;
        int idx = 0;
        while (*p >= '0' && *p <= '9') { idx = idx * 10 + (*p - '0'); p++; }
        if (*p != '"') break;
        p++;
        while (*p == ' ' || *p == ':') p++;
        if (*p != '"') break;
        p++;
        if (idx > 0 && idx <= MAX_COMPONENTS) {
            int i = 0;
            while (*p && *p != '"' && i < (int)sizeof(g_ext_bin[0]) - 1)
                g_ext_bin[idx][i++] = *p++;
            g_ext_bin[idx][i] = '\0';
        } else {
            while (*p && *p != '"') p++;
        }
        if (*p == '"') p++;
    }
}

/* Build the full path to a sidecar file that lives next to the installer. */
static void build_sidecar_path(const char *filename, char *out)
{
    char exe_dir[MAX_PATH];
    strncpy(exe_dir, g_exe_path, MAX_PATH - 1);
    exe_dir[MAX_PATH - 1] = '\0';
    char *sep = strrchr(exe_dir, '\\');
    if (sep) *(sep + 1) = '\0';
    else     exe_dir[0] = '\0';
    snprintf(out, MAX_PATH, "%s%s", exe_dir, filename);
}

/* ==================================================================== */
/* Metadata reading                                                      */
/* ==================================================================== */

static int read_install_meta(void)
{
    FILE *f = fopen(g_exe_path, "rb");
    if (!f) return 0;

    /* Last 12 bytes: [4B meta_len][8B magic] */
    _fseeki64(f, -12, SEEK_END);
    uint32_t meta_len = 0;
    char magic[9] = {0};
    fread(&meta_len, 4, 1, f);
    fread(magic, 8, 1, f);

    if (memcmp(magic, "XPACK01\x00", 8) != 0) {
        fclose(f);
        return 0;
    }

    _fseeki64(f, -((int64_t)12 + (int64_t)meta_len), SEEK_END);
    char *buf = (char *)malloc(meta_len + 1);
    if (!buf) { fclose(f); return 0; }
    fread(buf, 1, meta_len, f);
    buf[meta_len] = '\0';
    fclose(f);

    json_get_str(buf, "app_name",               g_meta.app_name,              sizeof(g_meta.app_name));
    json_get_str(buf, "app_note",               g_meta.app_note,              sizeof(g_meta.app_note));
    json_get_str(buf, "version",                g_meta.version,               sizeof(g_meta.version));
    json_get_str(buf, "description",            g_meta.description,           sizeof(g_meta.description));
    json_get_str(buf, "copyright",              g_meta.copyright,             sizeof(g_meta.copyright));
    json_get_str(buf, "contact",                g_meta.contact,               sizeof(g_meta.contact));
    json_get_str(buf, "company_info",           g_meta.company_info,          sizeof(g_meta.company_info));
    json_get_str(buf, "window_title",           g_meta.window_title,          sizeof(g_meta.window_title));
    json_get_str(buf, "installer_exe_version",  g_meta.installer_exe_version, sizeof(g_meta.installer_exe_version));
    json_get_str(buf, "install_subdir",          g_meta.install_subdir,        sizeof(g_meta.install_subdir));
    json_get_str(buf, "install_registry_key",   g_meta.install_registry_key,  sizeof(g_meta.install_registry_key));
    json_get_str(buf, "run_after_install",      g_meta.run_after_install,     sizeof(g_meta.run_after_install));
    json_get_str(buf, "detect_running_exe",     g_meta.detect_running_exe,    sizeof(g_meta.detect_running_exe));

    g_meta.total_files              = (int)json_get_int(buf, "total_files");
    g_meta.total_uncompressed_size  = json_get_int(buf, "total_uncompressed_size");
    g_meta.close_delay              = (int)json_get_int(buf, "close_delay");
    g_meta.required_free_space_gb   = json_get_double(buf, "required_free_space_gb");
    g_meta.pack_data_offset         = json_get_int(buf, "pack_data_offset");
    g_meta.pack_data_size           = json_get_int(buf, "pack_data_size");
    g_meta.backdrop_offset          = json_get_int(buf, "backdrop_offset");
    g_meta.backdrop_size            = json_get_int(buf, "backdrop_size");
    g_meta.uninstaller_offset       = json_get_int(buf, "uninstaller_offset");
    g_meta.uninstaller_size         = json_get_int(buf, "uninstaller_size");
    g_meta.include_uninstaller      = json_get_bool(buf, "include_uninstaller", 0);
    g_meta.verify_crc32             = json_get_bool(buf, "verify_crc32", 0);
    json_get_str(buf, "shortcut_target", g_meta.shortcut_target, sizeof(g_meta.shortcut_target));
    json_get_str(buf, "shortcut_name",   g_meta.shortcut_name,   sizeof(g_meta.shortcut_name));
    g_meta.shortcut_create_desktop   = json_get_bool(buf, "shortcut_create_desktop",   0);
    g_meta.shortcut_create_startmenu = json_get_bool(buf, "shortcut_create_startmenu", 0);
    json_get_str(buf, "arp_subkey", g_meta.arp_subkey, sizeof(g_meta.arp_subkey));
    json_get_str(buf, "codec",     g_meta.codec,      sizeof(g_meta.codec));
    g_meta.bin_parts               = (int)json_get_int(buf, "bin_parts");
    if (g_meta.bin_parts < 1) g_meta.bin_parts = 1;
    g_meta.bin_part_size           = json_get_int(buf, "bin_part_size");
    if (!g_meta.codec[0]) strncpy(g_meta.codec, "lzma", sizeof(g_meta.codec) - 1);

    /* Resolve path to pack data: either self (single-file) or base_game.bin. */
    char bin_file[64] = {0};
    json_get_str(buf, "bin_file", bin_file, sizeof(bin_file));
    if (bin_file[0]) {
        char exe_dir[MAX_PATH];
        strncpy(exe_dir, g_exe_path, MAX_PATH - 1);
        exe_dir[MAX_PATH - 1] = '\0';
        char *last_sep = strrchr(exe_dir, '\\');
        if (last_sep) *(last_sep + 1) = '\0';
        else exe_dir[0] = '\0';
        snprintf(g_bin_path, MAX_PATH, "%s%s", exe_dir, bin_file);
    } else {
        strncpy(g_bin_path, g_exe_path, MAX_PATH - 1);
        g_bin_path[MAX_PATH - 1] = '\0';
    }

    json_parse_components(buf);
    json_parse_external_components(buf);
    json_parse_ext_int64s(buf, "external_offsets", g_ext_offset, MAX_COMPONENTS);
    json_parse_ext_int64s(buf, "external_csizes",  g_ext_csize,  MAX_COMPONENTS);

    free(buf);
    return 1;
}

/* Read the XPACK01 file table from the embedded blob. */
static int read_pack_entries(void)
{
    MPF mf;
    if (!mpf_open(&mf, g_bin_path, g_meta.bin_parts, g_meta.bin_part_size)) return 0;

    mpf_seek(&mf, g_meta.pack_data_offset, SEEK_SET);
    uint32_t n = 0;
    if (mpf_read(&mf, &n, 4) != 4) { mpf_close(&mf); return 0; }

    g_entries = (PackEntry *)malloc(n * sizeof(PackEntry));
    if (!g_entries) { mpf_close(&mf); return 0; }
    g_num_entries = n;

#define READ_PACK_FAIL { free(g_entries); g_entries = NULL; g_num_entries = 0; mpf_close(&mf); return 0; }
    for (uint32_t i = 0; i < n; i++) {
        uint16_t plen = 0;
        if (mpf_read(&mf, &plen, 2) != 2) READ_PACK_FAIL
        if (plen >= (uint16_t)sizeof(g_entries[i].path))
            plen = (uint16_t)(sizeof(g_entries[i].path) - 1);
        if (mpf_read(&mf, g_entries[i].path, plen) != plen) READ_PACK_FAIL
        g_entries[i].path[plen] = '\0';
        if (mpf_read(&mf, &g_entries[i].offset,    8) != 8) READ_PACK_FAIL
        if (mpf_read(&mf, &g_entries[i].size,      8) != 8) READ_PACK_FAIL
        if (mpf_read(&mf, &g_entries[i].component, 4) != 4) READ_PACK_FAIL
        if (mpf_read(&mf, &g_entries[i].crc32,     4) != 4) READ_PACK_FAIL
    }
#undef READ_PACK_FAIL

    mpf_close(&mf);
    return 1;
}

/* ==================================================================== */
/* UI helpers                                                            */
/* ==================================================================== */

static void set_status(const char *msg, COLORREF col)
{
    SetWindowTextA(g_hwnd_status, msg);
    InvalidateRect(g_hwnd_status, NULL, TRUE);
    (void)col;
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

static void set_progress(int pct)
{
    if (pct < 0)   pct = 0;
    if (pct > 100) pct = 100;
    SetWindowLongA(g_hwnd_progress, GWLP_USERDATA, pct);
    InvalidateRect(g_hwnd_progress, NULL, FALSE);
}

static void paint_button(DRAWITEMSTRUCT *dis, COLORREF bg, COLORREF fg)
{
    HDC dc = dis->hDC;
    RECT r = dis->rcItem;
    HBRUSH br = CreateSolidBrush(
        (dis->itemState & ODS_SELECTED) ? COL_PRESSED :
        ((dis->CtlID == IDC_BTN_INSTALL ? g_btn_hover_install : g_btn_hover_cancel)
         ? COL_HOVER : bg));
    FillRect(dc, &r, br);
    DeleteObject(br);
    SetTextColor(dc, fg);
    SetBkMode(dc, TRANSPARENT);
    SelectObject(dc, g_font_normal);
    DrawTextA(dc, dis->itemState & ODS_SELECTED ? "▸" : "", -1, &r,
              DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    char txt[128] = {0};
    GetWindowTextA(dis->hwndItem, txt, sizeof(txt));
    DrawTextA(dc, txt, -1, &r, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
}

static void enable_dark_titlebar(HWND hwnd)
{
    BOOL dark = TRUE;
    DwmSetWindowAttribute(hwnd, 20 /*DWMWA_USE_IMMERSIVE_DARK_MODE*/, &dark, sizeof(dark));
}

/* ==================================================================== */
/* Browse for folder                                                     */
/* ==================================================================== */

static int browse_for_folder(HWND owner, char *out, int out_len)
{
    BROWSEINFOA bi = {0};
    bi.hwndOwner  = owner;
    bi.lpszTitle  = "Select install folder:";
    bi.ulFlags    = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX;
    bi.pszDisplayName = out;
    LPITEMIDLIST pidl = SHBrowseForFolderA(&bi);
    if (!pidl) return 0;
    SHGetPathFromIDListA(pidl, out);
    CoTaskMemFree(pidl);
    return 1;
}

/* ==================================================================== */
/* Disk space label                                                      */
/* ==================================================================== */

static void update_space_label(void)
{
    char path[MAX_PATH] = {0};
    GetWindowTextA(g_hwnd_filepath, path, MAX_PATH);
    if (!path[0]) {
        SetWindowTextA(g_hwnd_space_lbl, "");
        return;
    }
    char root[4] = {path[0], ':', '\\', '\0'};
    ULARGE_INTEGER avail;
    if (!GetDiskFreeSpaceExA(root, &avail, NULL, NULL)) {
        SetWindowTextA(g_hwnd_space_lbl, "");
        return;
    }
    double avail_gb = (double)avail.QuadPart / (1024.0 * 1024.0 * 1024.0);
    char buf[128];
    if (g_meta.total_uncompressed_size > 0) {
        double req_gb = (double)g_meta.total_uncompressed_size / (1024.0 * 1024.0 * 1024.0);
        snprintf(buf, sizeof(buf), "Required: %.1f GB  |  Available: %.1f GB", req_gb, avail_gb);
    } else {
        snprintf(buf, sizeof(buf), "Available: %.1f GB", avail_gb);
    }
    SetWindowTextA(g_hwnd_space_lbl, buf);
}

/* ==================================================================== */
/* Running-exe check                                                     */
/* ==================================================================== */

static int check_running_exe(HWND hwnd, const char *exe_name)
{
    if (!exe_name || !exe_name[0]) return 1;
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 1;
    PROCESSENTRY32 pe; pe.dwSize = sizeof(pe);
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
        "Please close the game before installing.\n\n"
        "Continue anyway?", exe_name);
    return MessageBoxA(hwnd, msg, "Game Running",
                       MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) == IDYES;
}

/* ==================================================================== */
/* Free space check                                                      */
/* ==================================================================== */

static int check_free_space(HWND hwnd, const char *path, double required_gb)
{
    if (required_gb <= 0.0) return 1;
    char root[4] = {path[0], ':', '\\', '\0'};
    ULARGE_INTEGER avail;
    if (!GetDiskFreeSpaceExA(root, &avail, NULL, NULL)) return 1;
    double avail_gb = (double)avail.QuadPart / (1024.0 * 1024.0 * 1024.0);
    if (avail_gb >= required_gb) return 1;
    char msg[256];
    snprintf(msg, sizeof(msg),
        "Not enough free disk space.\n\n"
        "Required: %.1f GB\nAvailable: %.1f GB\n\n"
        "Continue anyway?", required_gb, avail_gb);
    return MessageBoxA(hwnd, msg, "Low Disk Space",
                       MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) == IDYES;
}

/* ==================================================================== */
/* Run async (fire-and-forget)                                           */
/* ==================================================================== */

static DWORD WINAPI run_async_thread(LPVOID param)
{
    char *cmd = (char *)param;
    if (cmd && cmd[0]) {
        STARTUPINFOA si = {0}; si.cb = sizeof(si);
        PROCESS_INFORMATION pi = {0};
        if (CreateProcessA(NULL, cmd, NULL, NULL, FALSE,
                           CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) {
            CloseHandle(pi.hProcess);
            CloseHandle(pi.hThread);
        }
    }
    free(cmd);
    return 0;
}

static void run_async(const char *cmd)
{
    if (!cmd || !cmd[0]) return;
    char *copy = _strdup(cmd);
    if (!copy) return;
    HANDLE t = CreateThread(NULL, 0, run_async_thread, copy, 0, NULL);
    if (t) CloseHandle(t);
    else   free(copy);
}

/* ==================================================================== */
/* Backdrop rendering                                                    */
/* ==================================================================== */

static HBITMAP load_backdrop(void)
{
    if (g_meta.backdrop_size <= 0) return NULL;
    FILE *f = fopen(g_exe_path, "rb");
    if (!f) return NULL;
    _fseeki64(f, g_meta.backdrop_offset, SEEK_SET);
    BYTE *raw = (BYTE *)malloc((size_t)g_meta.backdrop_size);
    if (!raw) { fclose(f); return NULL; }
    if (fread(raw, 1, (size_t)g_meta.backdrop_size, f) != (size_t)g_meta.backdrop_size) {
        fclose(f); free(raw); return NULL;
    }
    fclose(f);

    IWICImagingFactory *wic = NULL;
    CoInitialize(NULL);
    if (FAILED(CoCreateInstance(&CLSID_WICImagingFactory, NULL, CLSCTX_INPROC_SERVER,
                                &IID_IWICImagingFactory, (void **)&wic))) {
        CoUninitialize();
        free(raw); return NULL;
    }
    IWICStream *stream = NULL;
    if (FAILED(wic->lpVtbl->CreateStream(wic, &stream)) || !stream) {
        wic->lpVtbl->Release(wic);
        CoUninitialize();
        free(raw); return NULL;
    }
    stream->lpVtbl->InitializeFromMemory(stream, raw, (DWORD)g_meta.backdrop_size);
    IWICBitmapDecoder *dec = NULL;
    wic->lpVtbl->CreateDecoderFromStream(wic, (IStream *)stream, NULL,
                                          WICDecodeMetadataCacheOnLoad, &dec);
    IWICBitmapFrameDecode *frame = NULL;
    if (dec) dec->lpVtbl->GetFrame(dec, 0, &frame);
    IWICFormatConverter *conv = NULL;
    if (frame) {
        wic->lpVtbl->CreateFormatConverter(wic, &conv);
        conv->lpVtbl->Initialize(conv, (IWICBitmapSource *)frame,
                                  &GUID_WICPixelFormat32bppBGRA, WICBitmapDitherTypeNone,
                                  NULL, 0.0, WICBitmapPaletteTypeCustom);
    }
    HBITMAP hbm = NULL;
    if (conv) {
        UINT w = 0, h = 0;
        ((IWICBitmapSource *)conv)->lpVtbl->GetSize((IWICBitmapSource *)conv, &w, &h);
        BITMAPINFO bi = {0};
        bi.bmiHeader.biSize        = sizeof(bi.bmiHeader);
        bi.bmiHeader.biWidth       = (LONG)w;
        bi.bmiHeader.biHeight      = -(LONG)h;
        bi.bmiHeader.biPlanes      = 1;
        bi.bmiHeader.biBitCount    = 32;
        bi.bmiHeader.biCompression = BI_RGB;
        void *bits = NULL;
        HDC dc = GetDC(NULL);
        hbm = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, NULL, 0);
        ReleaseDC(NULL, dc);
        if (hbm && bits && w <= 65535 && h <= 65535) {
            UINT stride = w * 4;  /* safe: w <= 65535, so w*4 <= 262140 */
            ((IWICBitmapSource *)conv)->lpVtbl->CopyPixels(
                (IWICBitmapSource *)conv, NULL, stride, stride * h, (BYTE *)bits);
        }
        conv->lpVtbl->Release(conv);
    }
    if (frame) frame->lpVtbl->Release(frame);
    if (dec)   dec->lpVtbl->Release(dec);
    if (stream) stream->lpVtbl->Release(stream);
    wic->lpVtbl->Release(wic);
    CoUninitialize();
    free(raw);
    return hbm;
}

/* ==================================================================== */
/* UAC elevation helper                                                  */
/* ==================================================================== */

static int check_elevate(const char *path)
{
    HANDLE token = NULL;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return 1;
    TOKEN_ELEVATION elev = {0};
    DWORD sz = sizeof(elev);
    GetTokenInformation(token, TokenElevation, &elev, sz, &sz);
    CloseHandle(token);
    if (elev.TokenIsElevated) return 1;

    /* Walk up to the deepest existing ancestor — the target dir may not exist
       yet, so probing it directly would always fail even on writable paths. */
    char probe_dir[MAX_PATH];
    strncpy(probe_dir, path, MAX_PATH - 1);
    probe_dir[MAX_PATH - 1] = '\0';
    while (probe_dir[0]) {
        DWORD attr = GetFileAttributesA(probe_dir);
        if (attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_DIRECTORY))
            break;
        char *last = strrchr(probe_dir, '\\');
        if (!last) { probe_dir[0] = '\0'; break; }
        *last = '\0';
    }
    if (!probe_dir[0]) return 1; /* can't determine — assume ok */

    char probe[MAX_PATH];
    snprintf(probe, MAX_PATH, "%s\\~pfg_probe.tmp", probe_dir);
    HANDLE fh = CreateFileA(probe, GENERIC_WRITE, 0, NULL,
                            CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE,
                            NULL);
    if (fh != INVALID_HANDLE_VALUE) {
        CloseHandle(fh);
        return 1; /* write access OK without elevation */
    }

    int answer = MessageBoxA(g_hwnd,
        "Administrator privileges may be required to install to this folder.\n\n"
        "Restart as Administrator?",
        "Elevation Required", MB_YESNO | MB_ICONQUESTION);
    if (answer != IDYES) return 0;

    char args[MAX_PATH * 3 + 16];
    snprintf(args, sizeof(args), "\"%s\" \"%s\" \"%s\"",
             path, g_user_desktop, g_user_programs);
    SHELLEXECUTEINFOA sei = {0};
    sei.cbSize       = sizeof(sei);
    sei.lpVerb       = "runas";
    sei.lpFile       = g_exe_path;
    sei.lpParameters = args;
    sei.nShow        = SW_SHOWNORMAL;
    ShellExecuteExA(&sei);
    PostQuitMessage(0);
    return 0;
}

/* ==================================================================== */
/* Directory creation                                                    */
/* ==================================================================== */

static void ensure_dir(const char *path)
{
    if (!path || !path[0]) return;
    if (GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES) return;
    char parent[MAX_PATH];
    strncpy(parent, path, MAX_PATH - 1);
    parent[MAX_PATH - 1] = '\0';
    char *last = strrchr(parent, '\\');
    if (last && last != parent) {
        *last = '\0';
        ensure_dir(parent);
    }
    CreateDirectoryA(path, NULL);
}

static void ensure_dir_for_file(const char *filepath)
{
    char dir[MAX_PATH];
    strncpy(dir, filepath, MAX_PATH - 1);
    dir[MAX_PATH - 1] = '\0';
    char *last = strrchr(dir, '\\');
    if (last) { *last = '\0'; ensure_dir(dir); }
}

/* ==================================================================== */
/* Shortcut creation (COM / IShellLink)                                  */
/* ==================================================================== */

static void create_shortcuts(const char *install_dir, int do_desktop, int do_startmenu)
{
    if (!g_meta.shortcut_target[0]) return;
    if (!do_desktop && !do_startmenu) return;

    char target[MAX_PATH];
    snprintf(target, MAX_PATH, "%s\\%s", install_dir, g_meta.shortcut_target);
    for (char *p = target; *p; p++) if (*p == '/') *p = '\\';

    const char *sname = g_meta.shortcut_name[0] ? g_meta.shortcut_name : g_meta.app_name;
    if (!sname || !sname[0]) sname = "Game";

    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);

    IShellLinkA *psl = NULL;
    if (FAILED(CoCreateInstance(&CLSID_ShellLink, NULL, CLSCTX_INPROC_SERVER,
                                &IID_IShellLinkA, (void **)&psl))) {
        CoUninitialize();
        return;
    }

    if (FAILED(psl->lpVtbl->SetPath(psl, target)) ||
        FAILED(psl->lpVtbl->SetWorkingDirectory(psl, install_dir))) {
        psl->lpVtbl->Release(psl);
        CoUninitialize();
        return;
    }

    /* Prefer the uninstaller exe as icon source — it has the custom icon
       PE-injected from the Python GUI. Fall back to the target exe. */
    char icon_src[MAX_PATH];
    if (g_meta.include_uninstaller && g_meta.uninstaller_size > 0)
        snprintf(icon_src, MAX_PATH, "%s\\uninstall.exe", install_dir);
    else
        snprintf(icon_src, MAX_PATH, "%s", target);
    psl->lpVtbl->SetIconLocation(psl, icon_src, 0);

    IPersistFile *ppf = NULL;
    if (SUCCEEDED(psl->lpVtbl->QueryInterface(psl, &IID_IPersistFile, (void **)&ppf))) {
        char lnk[MAX_PATH];
        WCHAR wlnk[MAX_PATH];

        if (do_startmenu && g_user_programs[0]) {
            char subdir[MAX_PATH];
            const char *folder = g_meta.app_name[0] ? g_meta.app_name : sname;
            snprintf(subdir, MAX_PATH, "%s\\%s", g_user_programs, folder);
            CreateDirectoryA(subdir, NULL);
            snprintf(lnk, MAX_PATH, "%s\\%s.lnk", subdir, sname);
            MultiByteToWideChar(CP_ACP, 0, lnk, -1, wlnk, MAX_PATH);
            ppf->lpVtbl->Save(ppf, wlnk, TRUE);
        }

        if (do_desktop && g_user_desktop[0]) {
            snprintf(lnk, MAX_PATH, "%s\\%s.lnk", g_user_desktop, sname);
            MultiByteToWideChar(CP_ACP, 0, lnk, -1, wlnk, MAX_PATH);
            ppf->lpVtbl->Save(ppf, wlnk, TRUE);
        }

        ppf->lpVtbl->Release(ppf);
    }

    psl->lpVtbl->Release(psl);
    CoUninitialize();
}

/* ==================================================================== */
/* Existing-install detection                                            */
/* ==================================================================== */

static int detect_existing_install(const char *install_dir)
{
    /* Primary sentinel: uninstall.exe placed by a previous install */
    char uninst[MAX_PATH];
    snprintf(uninst, MAX_PATH, "%s\\uninstall.exe", install_dir);
    if (GetFileAttributesA(uninst) != INVALID_FILE_ATTRIBUTES) return 1;

    /* Fallback: non-empty directory */
    DWORD attr = GetFileAttributesA(install_dir);
    if (attr == INVALID_FILE_ATTRIBUTES || !(attr & FILE_ATTRIBUTE_DIRECTORY)) return 0;
    char pattern[MAX_PATH];
    snprintf(pattern, MAX_PATH, "%s\\*", install_dir);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    int found = 0;
    do {
        if (strcmp(fd.cFileName, ".") && strcmp(fd.cFileName, ".."))
            { found = 1; break; }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return found;
}

/* Reject archive paths that could escape the install directory.
 * Returns 1 if the path is safe (relative, no .. components, no drive spec). */
static int archive_path_is_safe(const char *path)
{
    if (!path || !path[0]) return 0;
    /* Reject absolute paths (leading slash, backslash, or drive letter) */
    if (path[0] == '/' || path[0] == '\\') return 0;
    if (path[1] == ':') return 0;
    /* Reject UNC paths */
    if (path[0] == '\\' && path[1] == '\\') return 0;
    /* Reject any .. component */
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

/* CRC32 of a file already on disk (for repair-mode skip check) */
static int file_crc32_matches(const char *path, uint32_t expected)
{
    if (!expected) return 0;
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    uint32_t crc = 0xFFFFFFFF;
    uint8_t buf[65536];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
        crc = crc32_update(crc, buf, n);
    fclose(f);
    return (crc ^ 0xFFFFFFFFu) == expected;
}

/* ==================================================================== */
/* Install thread                                                        */
/* ==================================================================== */

struct InstallArgs {
    char install_dir[MAX_PATH];
    int  low_load;
    int  verify_crc32;
    int  repair_mode;   /* 0 = fresh/reinstall, 1 = skip files whose CRC32 matches */
    int  shortcut_desktop;
    int  shortcut_startmenu;
    int  selected_comps[MAX_COMPONENTS];
    int  num_components;
};

struct InstallResult {
    int      verify_passed;
    int      repair_mode;
    uint32_t files_skipped;   /* files whose on-disk CRC32 already matched */
    uint32_t files_replaced;  /* files actually written */
};

/* ---- Shared file-dispatch state (used by both LZMA and zstd branches) ---- */
typedef struct {
    const char  *install_dir;
    int          repair_mode;
    int          verify_crc32;
    int          low_load;
    uint32_t     first_entry;
    uint32_t     last_entry;
    uint32_t     total_to_install;
    /* mutable */
    int         *success;
    uint32_t    *files_done;
    uint32_t    *files_skipped;
    uint32_t    *files_written;
    /* per-file state */
    uint32_t     cur_file;
    uint64_t     cur_file_written;
    uint32_t     cur_crc32;
    int          skip_cur;
    HANDLE       hf;
} DispatchState;

/* Consume up to `len` bytes from `ptr`, writing them to the current file.
 * Call with len=0 after a stream finishes to flush trailing zero-size entries.
 * Sets *ds->success = 0 on error. */
static void dispatch_chunk(DispatchState *ds, const uint8_t *ptr, size_t len)
{
    while (ds->cur_file < ds->last_entry) {
        PackEntry *e = &g_entries[ds->cur_file];

        /* Zero-size files need no stream data — create them immediately so they
         * are not silently dropped regardless of where they appear in the stream. */
        if (e->size == 0) {
            if (!ds->skip_cur) {
                if (!archive_path_is_safe(e->path)) { *ds->success = 0; return; }
                if (strlen(ds->install_dir) + 1 + strlen(e->path) >= MAX_PATH)
                    { *ds->success = 0; return; }
                char fpath[MAX_PATH];
                snprintf(fpath, MAX_PATH, "%s\\%s", ds->install_dir, e->path);
                for (char *fp = fpath; *fp; fp++) if (*fp == '/') *fp = '\\';
                ensure_dir_for_file(fpath);
                HANDLE hf = CreateFileA(fpath, GENERIC_WRITE, 0, NULL,
                                        CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
                if (hf != INVALID_HANDLE_VALUE) CloseHandle(hf);
            }
            int was_skipped      = ds->skip_cur;
            ds->skip_cur         = 0;
            ds->cur_crc32        = 0xFFFFFFFF;
            ds->cur_file++;
            (*ds->files_done)++;
            if (was_skipped) (*ds->files_skipped)++;
            else             (*ds->files_written)++;
            int pct = (int)(*ds->files_done * 100 / ds->total_to_install);
            if (g_hwnd) {
                PostMessageA(g_hwnd, WM_INSTALL_PROG, (WPARAM)pct, 0);
                if (!was_skipped) {
                    char *log_msg = (char *)malloc(MAX_PATH + 32);
                    if (log_msg) {
                        snprintf(log_msg, MAX_PATH + 32,
                                 ds->repair_mode ? "Repairing %u / %u: %s"
                                                 : "Extracting %u / %u: %s",
                                 *ds->files_done, ds->total_to_install, e->path);
                        PostMessageA(g_hwnd, WM_LOG_MSG, (WPARAM)log_msg, 0);
                    }
                }
            }
            if (ds->low_load) Sleep(1);
            continue;
        }

        /* Non-zero files require decompressed stream data. */
        if (len == 0) break;

        uint64_t need = e->size - ds->cur_file_written;
        size_t write  = (size_t)(len < need ? len : need);

        if (ds->hf == INVALID_HANDLE_VALUE && !ds->skip_cur) {
            if (!archive_path_is_safe(e->path)) { *ds->success = 0; break; }
            /* Reject if the combined path would overflow MAX_PATH */
            if (strlen(ds->install_dir) + 1 + strlen(e->path) >= MAX_PATH)
                { *ds->success = 0; break; }
            char fpath[MAX_PATH];
            snprintf(fpath, MAX_PATH, "%s\\%s", ds->install_dir, e->path);
            for (char *fp = fpath; *fp; fp++) if (*fp == '/') *fp = '\\';
            if (ds->repair_mode && e->crc32 && file_crc32_matches(fpath, e->crc32)) {
                ds->skip_cur = 1;
            } else {
                ensure_dir_for_file(fpath);
                ds->hf = CreateFileA(fpath, GENERIC_WRITE, 0, NULL,
                                     CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
            }
        }

        if (ds->hf != INVALID_HANDLE_VALUE && write > 0) {
            DWORD written = 0;
            if (!WriteFile(ds->hf, ptr, (DWORD)write, &written, NULL))
                *ds->success = 0;
            ds->cur_crc32 = crc32_update(ds->cur_crc32, ptr, write);
        }

        ptr                  += write;
        len                  -= write;
        ds->cur_file_written += write;

        if (ds->cur_file_written >= e->size) {
            if (ds->hf != INVALID_HANDLE_VALUE) {
                CloseHandle(ds->hf);
                ds->hf = INVALID_HANDLE_VALUE;
            }
            if (!ds->skip_cur && ds->verify_crc32 && e->crc32
                && (ds->cur_crc32 ^ 0xFFFFFFFFu) != e->crc32) {
                *ds->success = 0;
                if (g_hwnd) {
                    char *log_msg = (char *)malloc(MAX_PATH);
                    if (log_msg) {
                        snprintf(log_msg, MAX_PATH, "CRC32 MISMATCH: %s", e->path);
                        PostMessageA(g_hwnd, WM_LOG_MSG, (WPARAM)log_msg, 0);
                    }
                }
            }
            int was_skipped      = ds->skip_cur;
            ds->skip_cur         = 0;
            ds->cur_crc32        = 0xFFFFFFFF;
            ds->cur_file_written = 0;
            ds->cur_file++;
            (*ds->files_done)++;
            if (was_skipped) (*ds->files_skipped)++;
            else             (*ds->files_written)++;

            int pct = (int)(*ds->files_done * 100 / ds->total_to_install);
            if (g_hwnd) {
                PostMessageA(g_hwnd, WM_INSTALL_PROG, (WPARAM)pct, 0);
                if (!was_skipped) {
                    char *log_msg = (char *)malloc(MAX_PATH + 32);
                    if (log_msg) {
                        snprintf(log_msg, MAX_PATH + 32,
                                 ds->repair_mode ? "Repairing %u / %u: %s"
                                                 : "Extracting %u / %u: %s",
                                 *ds->files_done, ds->total_to_install, e->path);
                        PostMessageA(g_hwnd, WM_LOG_MSG, (WPARAM)log_msg, 0);
                    }
                }
            }
            if (ds->low_load) Sleep(1);
        }
    }
}

static int do_install(const char *install_dir, int low_load, int verify_crc32,
                      int repair_mode, const int *selected_comps, int num_components,
                      int shortcut_desktop, int shortcut_startmenu,
                      uint32_t *out_skipped, uint32_t *out_replaced)
{
    MPF base;
    if (!mpf_open(&base, g_bin_path, g_meta.bin_parts, g_meta.bin_part_size)) return 0;

    /* Seek past the file table */
    mpf_seek(&base, g_meta.pack_data_offset, SEEK_SET);
    uint32_t n = 0;
    if (mpf_read(&base, &n, 4) != 4) { mpf_close(&base); return 0; }
    for (uint32_t i = 0; i < n; i++) {
        uint16_t plen = 0;
        if (mpf_read(&base, &plen, 2) != 2) { mpf_close(&base); return 0; }
        mpf_seek(&base, (int64_t)(plen + 8 + 8 + 4 + 4), SEEK_CUR);
    }

    /* Read number of compressed streams */
    uint32_t num_streams = 0;
    if (mpf_read(&base, &num_streams, 4) != 4) { mpf_close(&base); return 0; }

    /* Count how many files will actually be installed (for progress %) */
    uint32_t total_to_install = 0;
    for (uint32_t i = 0; i < g_num_entries; i++) {
        uint32_t cidx = g_entries[i].component;
        int install_this = (cidx == 0);
        if (!install_this && (int)cidx <= num_components)
            install_this = selected_comps[cidx - 1];
        if (install_this) total_to_install++;
    }
    if (total_to_install == 0) total_to_install = 1;

    int success = 1;
    uint32_t files_done    = 0;
    uint32_t files_skipped = 0;
    uint32_t files_written = 0;

    const size_t IN_SZ  = 65536;
    const size_t OUT_SZ = low_load ? 65536 : 262144;
    uint8_t *inbuf  = (uint8_t *)malloc(IN_SZ);
    uint8_t *outbuf = (uint8_t *)malloc(OUT_SZ);
    if (!inbuf || !outbuf) {
        free(inbuf); free(outbuf);
        mpf_close(&base);
        return 0;
    }

    for (uint32_t s = 0; s < num_streams && success; s++) {
        uint32_t comp_idx = 0;
        uint64_t csize    = 0;
        mpf_read(&base, &comp_idx, 4);
        mpf_read(&base, &csize,    8);

        /* csize == 0 is the sentinel for an external-sidecar stream. */
        int is_external = (csize == 0 &&
                           comp_idx > 0 && comp_idx <= MAX_COMPONENTS &&
                           g_ext_bin[comp_idx][0] != '\0');

        /* Decide whether to extract this stream */
        int install_this = (comp_idx == 0);
        if (!install_this && (int)comp_idx <= num_components)
            install_this = selected_comps[comp_idx - 1];

        if (!install_this) {
            /* csize == 0 for external streams, so this seek is always a no-op;
             * keep it for embedded streams that aren't selected. */
            if (!is_external) mpf_seek(&base, (int64_t)csize, SEEK_CUR);
            continue;
        }

        /* Find file entries belonging to this component (contiguous in table) */
        uint32_t first_entry = g_num_entries, last_entry = g_num_entries;
        for (uint32_t i = 0; i < g_num_entries; i++) {
            if (g_entries[i].component == comp_idx) {
                if (first_entry == g_num_entries) first_entry = i;
                last_entry = i + 1;
            }
        }
        if (first_entry == g_num_entries) {
            if (!is_external) mpf_seek(&base, (int64_t)csize, SEEK_CUR);
            continue;
        }

        /* For external streams, open the sidecar .bin file. */
        MPF  side;
        MPF *src           = &base;
        uint64_t src_csize = csize;
        int opened_sidecar = 0;

        if (is_external) {
            char sidecar_path[MAX_PATH];
            build_sidecar_path(g_ext_bin[comp_idx], sidecar_path);
            if (!mpf_open(&side, sidecar_path, 1, 0)) {
                if (g_hwnd) {
                    char *msg = (char *)malloc(MAX_PATH + 80);
                    if (msg) {
                        snprintf(msg, MAX_PATH + 80,
                                 "ERROR: cannot find %s — component skipped",
                                 g_ext_bin[comp_idx]);
                        PostMessageA(g_hwnd, WM_LOG_MSG, (WPARAM)msg, 0);
                    }
                }
                continue;
            }
            src = &side;
            mpf_seek(src, g_ext_offset[comp_idx], SEEK_SET);
            src_csize      = (uint64_t)g_ext_csize[comp_idx];
            opened_sidecar = 1;
        }

        /* Build shared dispatch state for this stream */
        DispatchState ds = {
            .install_dir     = install_dir,
            .repair_mode     = repair_mode,
            .verify_crc32    = verify_crc32,
            .low_load        = low_load,
            .first_entry     = first_entry,
            .last_entry      = last_entry,
            .total_to_install= total_to_install,
            .success         = &success,
            .files_done      = &files_done,
            .files_skipped   = &files_skipped,
            .files_written   = &files_written,
            .cur_file        = first_entry,
            .cur_file_written= 0,
            .cur_crc32       = 0xFFFFFFFF,
            .skip_cur        = 0,
            .hf              = INVALID_HANDLE_VALUE,
        };

        int use_zstd = (strcmp(g_meta.codec, "zstd") == 0);

        if (use_zstd) {
            /* ---- zstd decompression ---- */
            ZSTD_DStream *zds = ZSTD_createDStream();
            if (!zds) {
                success = 0; if (opened_sidecar) mpf_close(&side); break;
            }
            if (ZSTD_isError(ZSTD_initDStream(zds))) {
                ZSTD_freeDStream(zds); success = 0;
                if (opened_sidecar) { mpf_close(&side); } break;
            }

            uint64_t total_read = 0;
            while (total_read < src_csize && success) {
                size_t to_read = IN_SZ < (src_csize - total_read)
                                 ? IN_SZ : (size_t)(src_csize - total_read);
                size_t got = mpf_read(src, inbuf, to_read);
                if (got == 0) break;
                total_read += got;

                ZSTD_inBuffer  zin  = { inbuf, got, 0 };
                while (zin.pos < zin.size && success) {
                    ZSTD_outBuffer zout = { outbuf, OUT_SZ, 0 };
                    size_t ret = ZSTD_decompressStream(zds, &zout, &zin);
                    if (ZSTD_isError(ret)) { success = 0; break; }
                    if (zout.pos > 0)
                        dispatch_chunk(&ds, (const uint8_t *)outbuf, zout.pos);
                }
                if (low_load) Sleep(1);
            }
            dispatch_chunk(&ds, NULL, 0); /* flush trailing zero-size entries */
            if (ds.hf != INVALID_HANDLE_VALUE) { CloseHandle(ds.hf); ds.hf = INVALID_HANDLE_VALUE; }
            ZSTD_freeDStream(zds);
        } else {
            /* ---- LZMA decompression (multi-threaded when low_load is off) ---- */
            lzma_stream strm = LZMA_STREAM_INIT;
            {
                SYSTEM_INFO si = {0};
                GetSystemInfo(&si);
                uint32_t nthreads = low_load ? 1 : (uint32_t)si.dwNumberOfProcessors;
                if (nthreads < 1) nthreads = 1;
                lzma_mt mt_opts = {0};
                mt_opts.threads            = nthreads;
                mt_opts.memlimit_threading = UINT64_MAX;
                mt_opts.memlimit_stop      = UINT64_MAX;
                if (lzma_stream_decoder_mt(&strm, &mt_opts) != LZMA_OK) {
                    success = 0;
                    if (opened_sidecar) mpf_close(&side);
                    break; /* strm uninitialised — lzma_end() not needed */
                }
            }

            uint64_t    total_read = 0;
            lzma_action action     = LZMA_RUN;

            strm.next_in  = NULL;
            strm.avail_in = 0;

            while (1) {
                if (strm.avail_in == 0 && total_read < src_csize) {
                    size_t to_read = IN_SZ < (src_csize - total_read)
                                     ? IN_SZ : (size_t)(src_csize - total_read);
                    size_t got = mpf_read(src, inbuf, to_read);
                    strm.next_in  = inbuf;
                    strm.avail_in = (uint32_t)got;
                    total_read   += got;
                    if (total_read >= src_csize) action = LZMA_FINISH;
                }

                strm.next_out  = outbuf;
                strm.avail_out = (uint32_t)OUT_SZ;

                lzma_ret ret    = lzma_code(&strm, action);
                size_t produced = OUT_SZ - strm.avail_out;

                if (produced > 0)
                    dispatch_chunk(&ds, outbuf, produced);

                if (low_load) Sleep(1);

                if (ret == LZMA_STREAM_END) break;
                if (ret != LZMA_OK) { success = 0; break; }
            }

            dispatch_chunk(&ds, NULL, 0); /* flush trailing zero-size entries */
            if (ds.hf != INVALID_HANDLE_VALUE) { CloseHandle(ds.hf); ds.hf = INVALID_HANDLE_VALUE; }
            lzma_end(&strm);
        }

        if (opened_sidecar) { mpf_close(&side); src = &base; }
    }

    free(inbuf);
    free(outbuf);
    mpf_close(&base);

    /* Write game registry key */
    if (success && g_meta.install_registry_key[0]) {
        HKEY hkey = NULL;
        if (RegCreateKeyExA(HKEY_CURRENT_USER, g_meta.install_registry_key,
                            0, NULL, 0, KEY_SET_VALUE, NULL, &hkey, NULL) == ERROR_SUCCESS) {
            RegSetValueExA(hkey, "InstallPath", 0, REG_SZ,
                           (const BYTE *)install_dir,
                           (DWORD)(strlen(install_dir) + 1));
            RegCloseKey(hkey);
        }
    }

    /* Extract uninstaller and register with Add/Remove Programs */
    if (success && g_meta.include_uninstaller && g_meta.uninstaller_size > 0) {
        char uninst_path[MAX_PATH];
        snprintf(uninst_path, MAX_PATH, "%s\\uninstall.exe", install_dir);

        /* Write uninstall.exe */
        FILE *uf = fopen(g_exe_path, "rb");
        if (uf) {
            _fseeki64(uf, g_meta.uninstaller_offset, SEEK_SET);
            BYTE *ubuf = (BYTE *)malloc((size_t)g_meta.uninstaller_size);
            if (ubuf) {
                fread(ubuf, 1, (size_t)g_meta.uninstaller_size, uf);
                FILE *of = fopen(uninst_path, "wb");
                if (of) { fwrite(ubuf, 1, (size_t)g_meta.uninstaller_size, of); fclose(of); }
                free(ubuf);
            }
            fclose(uf);
        }

        /* Build InstalledComponents string: "0" or "0,1,2" etc. */
        char comp_str[256];
        int  cpos = (int)snprintf(comp_str, sizeof(comp_str), "0");
        for (int ci = 0; ci < num_components; ci++) {
            if (selected_comps[ci])
                cpos += snprintf(comp_str + cpos, (int)sizeof(comp_str) - cpos, ",%d", ci + 1);
        }

        /* Write A/RP registry entry — try HKLM, fall back to HKCU */
        char arp_key[512];
        snprintf(arp_key, sizeof(arp_key),
            "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\%s",
            g_meta.arp_subkey);

        HKEY hives[2] = {HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER};
        for (int h = 0; h < 2; h++) {
            HKEY hkey = NULL;
            if (RegCreateKeyExA(hives[h], arp_key, 0, NULL, 0,
                                KEY_SET_VALUE, NULL, &hkey, NULL) == ERROR_SUCCESS) {
                char uninst_str[MAX_PATH + 4];
                snprintf(uninst_str, sizeof(uninst_str), "\"%s\"", uninst_path);
                DWORD est_kb = (DWORD)(g_meta.total_uncompressed_size / 1024);
                DWORD one    = 1;

                RegSetValueExA(hkey, "DisplayName",       0, REG_SZ,
                    (BYTE *)g_meta.app_name, (DWORD)(strlen(g_meta.app_name) + 1));
                if (g_meta.version[0])
                    RegSetValueExA(hkey, "DisplayVersion", 0, REG_SZ,
                        (BYTE *)g_meta.version, (DWORD)(strlen(g_meta.version) + 1));
                if (g_meta.company_info[0])
                    RegSetValueExA(hkey, "Publisher", 0, REG_SZ,
                        (BYTE *)g_meta.company_info,
                        (DWORD)(strlen(g_meta.company_info) + 1));
                RegSetValueExA(hkey, "UninstallString",  0, REG_SZ,
                    (BYTE *)uninst_str, (DWORD)(strlen(uninst_str) + 1));
                RegSetValueExA(hkey, "InstallLocation",  0, REG_SZ,
                    (BYTE *)install_dir, (DWORD)(strlen(install_dir) + 1));
                RegSetValueExA(hkey, "DisplayIcon",      0, REG_SZ,
                    (BYTE *)uninst_str, (DWORD)(strlen(uninst_str) + 1));
                RegSetValueExA(hkey, "EstimatedSize",    0, REG_DWORD,
                    (BYTE *)&est_kb, sizeof(est_kb));
                RegSetValueExA(hkey, "NoModify",         0, REG_DWORD,
                    (BYTE *)&one, sizeof(one));
                RegSetValueExA(hkey, "NoRepair",         0, REG_DWORD,
                    (BYTE *)&one, sizeof(one));
                RegSetValueExA(hkey, "InstalledComponents", 0, REG_SZ,
                    (BYTE *)comp_str, (DWORD)(strlen(comp_str) + 1));

                RegCloseKey(hkey);
                break;
            }
        }
    }

    /* If any installed component specifies a shortcut_target, the last one wins */
    if (success) {
        for (int ci = 0; ci < num_components; ci++) {
            if (selected_comps[ci] && g_components[ci].shortcut_target[0])
                snprintf(g_meta.shortcut_target, sizeof(g_meta.shortcut_target),
                         "%s", g_components[ci].shortcut_target);
        }
    }

    /* Create shortcuts */
    if (success)
        create_shortcuts(install_dir, shortcut_desktop, shortcut_startmenu);

    if (out_skipped)  *out_skipped  = files_skipped;
    if (out_replaced) *out_replaced = files_written;
    return success;
}

static DWORD WINAPI install_thread(LPVOID param)
{
    struct InstallArgs *args = (struct InstallArgs *)param;
    int verify      = args->verify_crc32;
    int repair      = args->repair_mode;

    struct InstallResult *res = (struct InstallResult *)malloc(sizeof(struct InstallResult));
    uint32_t skipped = 0, replaced = 0;
    int ok = do_install(args->install_dir, args->low_load, verify, repair,
                        args->selected_comps, args->num_components,
                        args->shortcut_desktop, args->shortcut_startmenu,
                        &skipped, &replaced);
    if (res) {
        res->verify_passed  = verify && ok;
        res->repair_mode    = repair;
        res->files_skipped  = skipped;
        res->files_replaced = replaced;
    }
    PostMessageA(g_hwnd, WM_INSTALL_DONE, (WPARAM)ok, (LPARAM)res);
    free(args);
    return 0;
}

/* ==================================================================== */
/* Component dependency enforcement                                      */
/* ==================================================================== */

/* After any component checkbox change: disable components whose required
   dependencies are not checked, re-enable them when requirements are met.
   Runs a full recompute so cascades (A needs B needs C) resolve correctly. */
static void refresh_component_states(void)
{
    for (int j = 0; j < g_num_components; j++) {
        ComponentInfo *cj = &g_components[j];
        if (!cj->hwnd_ctrl) continue;

        /* Group-enable takes priority: if the group header is unchecked, disable. */
        if (cj->group[0]) {
            int grp_on = 1;
            for (int gi = 0; gi < g_num_groups; gi++) {
                if (strcmp(g_groups[gi].group, cj->group) == 0) {
                    grp_on = (SendMessageA(g_groups[gi].hwnd_hdr,
                                           BM_GETCHECK, 0, 0) == BST_CHECKED);
                    break;
                }
            }
            if (!grp_on) {
                EnableWindow(cj->hwnd_ctrl, FALSE);
                continue;
            }
        }

        /* Dependency check */
        if (cj->num_requires == 0) {
            EnableWindow(cj->hwnd_ctrl, TRUE);
            continue;
        }
        int enabled = 1;
        for (int r = 0; r < cj->num_requires && enabled; r++) {
            int ri = cj->requires[r] - 1;
            if (ri < 0 || ri >= g_num_components) continue;
            ComponentInfo *cr = &g_components[ri];
            if (!cr->hwnd_ctrl) continue;
            if (SendMessageA(cr->hwnd_ctrl, BM_GETCHECK, 0, 0) != BST_CHECKED)
                enabled = 0;
        }
        if (!enabled) {
            SendMessageA(cj->hwnd_ctrl, BM_SETCHECK, BST_UNCHECKED, 0);
            EnableWindow(cj->hwnd_ctrl, FALSE);
        } else {
            EnableWindow(cj->hwnd_ctrl, TRUE);
        }
    }

    /* Show SAC warning if any flagged component is currently checked */
    if (g_hwnd_sac_warn) {
        int show = 0;
        for (int j = 0; j < g_num_components && !show; j++) {
            if (g_components[j].sac_warning && g_components[j].hwnd_ctrl &&
                SendMessageA(g_components[j].hwnd_ctrl, BM_GETCHECK, 0, 0) == BST_CHECKED)
                show = 1;
        }
        ShowWindow(g_hwnd_sac_warn, show ? SW_SHOW : SW_HIDE);
    }
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

        const int lx   = 20;    /* left margin */
        const int crw  = 680;   /* control row width */
        const int rmax = 700;   /* right edge (lx + crw) */

        /* cy: first pixel below the image separator */
        int cy = g_img_h + 2;

        /* ── Title + version ─────────────────────────────────────── */
        int title_y = cy + 14;
        HWND lbl_title = CreateWindowExA(0, "STATIC",
            g_meta.app_name[0] ? g_meta.app_name : "PatchForge Installer",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            lx, title_y, 500, 28, hwnd, NULL, NULL, NULL);
        SendMessageA(lbl_title, WM_SETFONT, (WPARAM)g_font_title, TRUE);

        if (g_meta.version[0]) {
            char verbuf[80];
            snprintf(verbuf, sizeof(verbuf), "%s", g_meta.version);
            HWND lbl_ver = CreateWindowExA(0, "STATIC", verbuf,
                WS_CHILD | WS_VISIBLE | SS_RIGHT,
                rmax - 150, title_y + 8, 150, 16, hwnd, NULL, NULL, NULL);
            SendMessageA(lbl_ver, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        }

        /* ── Subtitle: app_note (dim), then description (normal) ─── */
        int subtitle_h = 0;
        if (g_meta.app_note[0]) {
            g_hwnd_subtitle = CreateWindowExA(0, "STATIC", g_meta.app_note,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                lx, title_y + 30, crw, 16, hwnd, NULL, NULL, NULL);
            SendMessageA(g_hwnd_subtitle, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            subtitle_h = 18;
        }
        int desc_h = 0;
        if (g_meta.description[0]) {
            g_hwnd_desc = CreateWindowExA(0, "STATIC", g_meta.description,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                lx, title_y + 30 + subtitle_h, crw, 16, hwnd, NULL, NULL, NULL);
            SendMessageA(g_hwnd_desc, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            desc_h = 18;
        }

        /* ── Summary: "N files  ·  X GB installed" (dim text) ───── */
        int summary_h = 0;
        if (g_meta.total_files > 0) {
            char cbuf[128] = {0};
            double gb = (double)g_meta.total_uncompressed_size / (1024.0 * 1024.0 * 1024.0);
            if (gb >= 1.0)
                snprintf(cbuf, sizeof(cbuf), "%d files  \xB7  %.1f GB installed",
                         g_meta.total_files, gb);
            else {
                double mb = (double)g_meta.total_uncompressed_size / (1024.0 * 1024.0);
                snprintf(cbuf, sizeof(cbuf), "%d files  \xB7  %.0f MB installed",
                         g_meta.total_files, mb);
            }
            g_hwnd_summary = CreateWindowExA(0, "STATIC", cbuf,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                lx, title_y + 30 + subtitle_h + desc_h, crw, 16, hwnd, NULL, NULL, NULL);
            SendMessageA(g_hwnd_summary, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            summary_h = 18;
        }

        /* ── Install path ────────────────────────────────────────── */
        int path_y = title_y + 30 + subtitle_h + desc_h + summary_h + 12;

        HWND lbl_path = CreateWindowExA(0, "STATIC", "Install to:",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            lx, path_y, 100, 16, hwnd, NULL, NULL, NULL);
        SendMessageA(lbl_path, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        g_hwnd_filepath = CreateWindowExA(0, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
            lx, path_y + 18, 568, 26, hwnd, (HMENU)IDC_FILEPATH, NULL, NULL);
        SendMessageA(g_hwnd_filepath, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        CreateWindowExA(0, "BUTTON", "Browse...",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            lx + 572, path_y + 18, 108, 26, hwnd, (HMENU)IDC_BTN_BROWSE, NULL, NULL);

        /* ── Settings (left) + Optional Components (right) ─────────── */
        const int col1_x = lx;          /* 20 */
        const int col1_w = 320;
        const int col2_x = 360;
        const int col2_w = rmax - col2_x; /* 340 */

        int opt_y  = path_y + 18 + 26 + 10;
        int left_y = opt_y;
        int right_y = opt_y;

        /* Settings header (left column) */
        g_hwnd_sec_settings = CreateWindowExA(0, "STATIC", "SETTINGS",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            col1_x, left_y, col1_w, 16, hwnd, NULL, NULL, NULL);
        SendMessageA(g_hwnd_sec_settings, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        left_y += 20;

        g_hwnd_chk_lowload = CreateWindowExA(0, "BUTTON",
            "Reduce system load during install (slower)",
            WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
            col1_x, left_y, col1_w, 20, hwnd, (HMENU)IDC_CHK_LOWLOAD, NULL, NULL);
        SendMessageA(g_hwnd_chk_lowload, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        left_y += 24;

        if (g_meta.verify_crc32) {
            g_hwnd_chk_verify = CreateWindowExA(0, "BUTTON",
                "Verify file integrity after installation",
                WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                col1_x, left_y, col1_w, 20, hwnd, (HMENU)IDC_CHK_VERIFY, NULL, NULL);
            SendMessageA(g_hwnd_chk_verify, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            SendMessageA(g_hwnd_chk_verify, BM_SETCHECK, BST_CHECKED, 0);
            left_y += 24;
        }

        if (g_meta.shortcut_target[0]) {
            g_hwnd_chk_sc_startmenu = CreateWindowExA(0, "BUTTON",
                "Create Start Menu shortcut",
                WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                col1_x, left_y, col1_w, 20, hwnd, (HMENU)IDC_CHK_SC_STARTMENU, NULL, NULL);
            SendMessageA(g_hwnd_chk_sc_startmenu, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            SendMessageA(g_hwnd_chk_sc_startmenu, BM_SETCHECK,
                         g_meta.shortcut_create_startmenu ? BST_CHECKED : BST_UNCHECKED, 0);
            left_y += 24;
            g_hwnd_chk_sc_desktop = CreateWindowExA(0, "BUTTON",
                "Create Desktop shortcut",
                WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                col1_x, left_y, col1_w, 20, hwnd, (HMENU)IDC_CHK_SC_DESKTOP, NULL, NULL);
            SendMessageA(g_hwnd_chk_sc_desktop, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            SendMessageA(g_hwnd_chk_sc_desktop, BM_SETCHECK,
                         g_meta.shortcut_create_desktop ? BST_CHECKED : BST_UNCHECKED, 0);
            left_y += 24;
        }

        /* Optional Components header + checkboxes (right column, same top y) */
        if (g_num_components > 0) {
            g_hwnd_sec_comps = CreateWindowExA(0, "STATIC", "OPTIONAL COMPONENTS",
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                col2_x, right_y, col2_w, 16, hwnd, NULL, NULL, NULL);
            SendMessageA(g_hwnd_sec_comps, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            right_y += 20;

            char prev_group[64] = {0};
            for (int ci = 0; ci < g_num_components; ci++) {
                ComponentInfo *c = &g_components[ci];
                char size_str[32];
                format_size_bytes(c->size_bytes, size_str, sizeof(size_str));
                char disp[320];
                if (c->group[0]) {
                    /* New group: emit a group-enable checkbox header */
                    if (strcmp(c->group, prev_group) != 0) {
                        strncpy(prev_group, c->group, sizeof(prev_group) - 1);
                        int grp_on = 0;
                        uint64_t grp_size = 0;
                        for (int j = ci; j < g_num_components; j++) {
                            if (strcmp(g_components[j].group, c->group) != 0) break;
                            if (g_components[j].default_checked) { grp_on = 1; }
                            if (g_components[j].size_bytes > grp_size)
                                grp_size = g_components[j].size_bytes;  /* radio: largest option */
                        }
                        char grp_size_str[32];
                        format_size_bytes(grp_size, grp_size_str, sizeof(grp_size_str));
                        char grp_disp[128];
                        snprintf(grp_disp, sizeof(grp_disp), "%s  (up to %s)",
                                 c->group, grp_size_str);
                        int gi = g_num_groups;
                        strncpy(g_groups[gi].group, c->group, sizeof(g_groups[gi].group) - 1);
                        g_groups[gi].hwnd_hdr = CreateWindowExA(0, "BUTTON", grp_disp,
                            WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX | WS_GROUP,
                            col2_x, right_y, col2_w, 20, hwnd,
                            (HMENU)(LONG_PTR)(IDC_GROUP_BASE + gi), NULL, NULL);
                        SendMessageA(g_groups[gi].hwnd_hdr, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
                        SendMessageA(g_groups[gi].hwnd_hdr, BM_SETCHECK,
                                     grp_on ? BST_CHECKED : BST_UNCHECKED, 0);
                        g_num_groups++;
                        right_y += 24;
                    }
                    /* Radio button, indented; first in group gets WS_GROUP to bound
                       the auto-uncheck set */
                    BOOL first_in_grp = (ci == 0 ||
                        strcmp(g_components[ci - 1].group, c->group) != 0);
                    DWORD btn_style = WS_CHILD | WS_VISIBLE | BS_AUTORADIOBUTTON;
                    if (first_in_grp) btn_style |= WS_GROUP;
                    snprintf(disp, sizeof(disp), "%s  (%s)", c->label, size_str);
                    c->hwnd_ctrl = CreateWindowExA(0, "BUTTON", disp,
                        btn_style,
                        col2_x + 16, right_y, col2_w - 16, 20, hwnd,
                        (HMENU)(LONG_PTR)(IDC_COMP_BASE + ci), NULL, NULL);
                    SendMessageA(c->hwnd_ctrl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
                    right_y += 24;
                } else {
                    /* Standalone checkbox */
                    prev_group[0] = '\0';
                    snprintf(disp, sizeof(disp), "%s  (%s)", c->label, size_str);
                    c->hwnd_ctrl = CreateWindowExA(0, "BUTTON", disp,
                        WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX | WS_GROUP,
                        col2_x, right_y, col2_w, 20, hwnd,
                        (HMENU)(LONG_PTR)(IDC_COMP_BASE + ci), NULL, NULL);
                    SendMessageA(c->hwnd_ctrl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
                    right_y += 24;
                }
            }

            /* Set initial checked states (first default per radio group) */
            char checked_grps[MAX_COMPONENTS][64];
            int  n_checked_grps = 0;
            for (int ci = 0; ci < g_num_components; ci++) {
                ComponentInfo *c = &g_components[ci];
                if (!c->default_checked) continue;
                if (c->group[0]) {
                    int already = 0;
                    for (int gi = 0; gi < n_checked_grps; gi++) {
                        if (strcmp(checked_grps[gi], c->group) == 0) { already = 1; break; }
                    }
                    if (already) continue;
                    strncpy(checked_grps[n_checked_grps++], c->group,
                            sizeof(checked_grps[0]) - 1);
                }
                SendMessageA(c->hwnd_ctrl, BM_SETCHECK, BST_CHECKED, 0);
            }
        }

        /* SAC warning label — only created when at least one component carries the flag */
        int any_sac = 0;
        for (int ci = 0; ci < g_num_components; ci++)
            if (g_components[ci].sac_warning) { any_sac = 1; break; }
        if (any_sac) {
            g_hwnd_sac_warn = CreateWindowExA(0, "STATIC",
                "! One or more selected components may be flagged by Windows Defender "
                "or Smart App Control.",
                WS_CHILD | SS_LEFT,
                col2_x, right_y, col2_w, 28, hwnd, NULL, NULL, NULL);
            SendMessageA(g_hwnd_sac_warn, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            right_y += 32;
        }

        refresh_component_states();

        /* ── Disk space label (below whichever column is taller) ──── */
        int space_y = (left_y > right_y ? left_y : right_y) + 6;
        g_hwnd_space_lbl = CreateWindowExA(0, "STATIC", "",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            lx, space_y, crw, 14, hwnd, (HMENU)IDC_SPACE_LBL, NULL, NULL);
        SendMessageA(g_hwnd_space_lbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* ── Log area ────────────────────────────────────────────── */
        int log_y = space_y + 16;
        g_hwnd_log = CreateWindowExA(0, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL |
            ES_READONLY | WS_VSCROLL,
            lx, log_y, crw, 120, hwnd, (HMENU)IDC_LOG, NULL, NULL);
        SendMessageA(g_hwnd_log, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        SendMessageA(g_hwnd_log, EM_SETLIMITTEXT, 0, 0);

        /* ── Progress bar ────────────────────────────────────────── */
        int prog_y = log_y + 124;
        g_hwnd_progress = CreateWindowExA(0, "STATIC", "",
            WS_CHILD | WS_VISIBLE | SS_OWNERDRAW,
            lx, prog_y, crw, 10, hwnd, (HMENU)IDC_PROGRESS, NULL, NULL);
        SetWindowLongA(g_hwnd_progress, GWLP_USERDATA, 0);

        /* ── Status ──────────────────────────────────────────────── */
        int stat_y = prog_y + 14;
        g_hwnd_status = CreateWindowExA(0, "STATIC",
            "Select an install folder and click Install.",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            lx, stat_y, 500, 16, hwnd, (HMENU)IDC_STATUS, NULL, NULL);
        SendMessageA(g_hwnd_status, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* ── Footer separator y (painted in WM_ERASEBKGND) ──────── */
        g_foot_sep_y = stat_y + 20;

        /* ── Footer: info left, Cancel + Install right ───────────── */
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
                if (pos > 0) pos += snprintf(info + pos, sizeof(info) - pos, "  \xB7  ");
                pos += snprintf(info + pos, sizeof(info) - pos, "%s", parts[i]);
            }
            if (pos > 0) {
                HWND infolbl = CreateWindowExA(0, "STATIC", info,
                    WS_CHILD | WS_VISIBLE | SS_LEFT,
                    lx, foot_y + 7, 400, 14, hwnd, NULL, NULL, NULL);
                SendMessageA(infolbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            }
        }

        g_hwnd_btn_install = CreateWindowExA(0, "BUTTON", "Install",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            rmax - 88, foot_y, 88, 28, hwnd, (HMENU)IDC_BTN_INSTALL, NULL, NULL);
        CreateWindowExA(0, "BUTTON", "Cancel",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            rmax - 88 - 8 - 80, foot_y, 80, 28, hwnd, (HMENU)IDC_BTN_CANCEL, NULL, NULL);

        /* ── Pre-populate install path ───────────────────────────── */
        {
            char default_path[MAX_PATH] = {0};
            int argc = 0;
            LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
            if (argv && argc >= 2) {
                WideCharToMultiByte(CP_ACP, 0, argv[1], -1,
                                    default_path, MAX_PATH, NULL, NULL);
            } else {
                snprintf(default_path, MAX_PATH, "%s", g_exe_path);
                char *last = strrchr(default_path, '\\');
                if (last) *last = '\0';
                if (g_meta.install_subdir[0]) {
                    size_t plen = strlen(default_path);
                    snprintf(default_path + plen, MAX_PATH - plen,
                             "\\%s", g_meta.install_subdir);
                }
            }
            if (argv) LocalFree(argv);
            if (default_path[0]) SetWindowTextA(g_hwnd_filepath, default_path);
        }

        update_space_label();
        break;
    }

    case WM_CTLCOLORSTATIC: {
        HDC  dc  = (HDC)wp;
        HWND ctl = (HWND)lp;
        if (ctl == g_hwnd_sac_warn) {
            SetTextColor(dc, COL_WARN);
            SetBkColor(dc, COL_BG);
            return (LRESULT)g_brush_bg;
        }
        if (ctl == g_hwnd_log) {
            SetTextColor(dc, COL_TEXT_DIM);
            SetBkColor(dc, COL_LOG_BG);
            return (LRESULT)g_brush_log;
        }
        if (ctl == g_hwnd_subtitle || ctl == g_hwnd_desc || ctl == g_hwnd_summary
                || ctl == g_hwnd_sec_settings || ctl == g_hwnd_sec_comps)
            SetTextColor(dc, COL_TEXT_DIM);
        else
            SetTextColor(dc, COL_TEXT);
        SetBkColor(dc, COL_BG);
        return (LRESULT)g_brush_bg;
    }

    case WM_CTLCOLOREDIT: {
        HDC dc = (HDC)wp;
        SetTextColor(dc, COL_TEXT);
        SetBkColor(dc, COL_BG_LIGHT);
        return (LRESULT)g_brush_light;
    }

    case WM_CTLCOLORBTN: {
        HDC dc = (HDC)wp;
        SetBkColor(dc, COL_BG);
        return (LRESULT)g_brush_bg;
    }

    case WM_ERASEBKGND: {
        HDC dc = (HDC)wp;
        RECT r; GetClientRect(hwnd, &r);
        FillRect(dc, &r, g_brush_bg);
        if (g_backdrop_bmp && g_img_h > 0) {
            HDC mdc = CreateCompatibleDC(dc);
            if (!mdc) break;
            SelectObject(mdc, g_backdrop_bmp);
            BITMAP bm = {0};
            GetObjectA(g_backdrop_bmp, sizeof(bm), &bm);
            SetStretchBltMode(dc, HALFTONE);
            SetBrushOrgEx(dc, 0, 0, NULL);
            StretchBlt(dc, 0, 0, r.right, g_img_h,
                       mdc, 0, 0, bm.bmWidth, bm.bmHeight, SRCCOPY);
            DeleteDC(mdc);
            /* 2 px accent separator between image and controls */
            HBRUSH sep = CreateSolidBrush(COL_ACCENT);
            RECT   sep_r = {0, g_img_h, r.right, g_img_h + 2};
            FillRect(dc, &sep_r, sep);
            DeleteObject(sep);
        }
        /* Footer separator */
        if (g_foot_sep_y > 0) {
            HBRUSH fsep = CreateSolidBrush(COL_BORDER);
            RECT   fsep_r = {20, g_foot_sep_y, r.right - 20, g_foot_sep_y + 1};
            FillRect(dc, &fsep_r, fsep);
            DeleteObject(fsep);
        }
        return 1;
    }

    case WM_DRAWITEM: {
        DRAWITEMSTRUCT *dis = (DRAWITEMSTRUCT *)lp;
        if (dis->CtlID == IDC_PROGRESS) {
            int pct = (int)GetWindowLongA(dis->hwndItem, GWLP_USERDATA);
            RECT r = dis->rcItem;
            HBRUSH bg = CreateSolidBrush(COL_PROGRESS_BG);
            FillRect(dis->hDC, &r, bg);
            DeleteObject(bg);
            if (pct > 0) {
                RECT fill = r;
                fill.right = r.left + (r.right - r.left) * pct / 100;
                HBRUSH fg = CreateSolidBrush(COL_ACCENT);
                FillRect(dis->hDC, &fill, fg);
                DeleteObject(fg);
            }
        } else {
            COLORREF bg = (dis->CtlID == IDC_BTN_INSTALL) ? COL_ACCENT : COL_BG_LIGHT;
            paint_button(dis, bg, COL_TEXT);
        }
        return TRUE;
    }

    case WM_MOUSEMOVE: {
        int x = LOWORD(lp), y = HIWORD(lp);
        RECT ri, rc;
        GetWindowRect(g_hwnd_btn_install, &ri);
        GetWindowRect(GetDlgItem(hwnd, IDC_BTN_CANCEL), &rc);
        POINT pt = {x, y}; ClientToScreen(hwnd, &pt);
        int nh = PtInRect(&ri, pt);
        int nc = PtInRect(&rc, pt);
        if (nh != g_btn_hover_install || nc != g_btn_hover_cancel) {
            g_btn_hover_install = nh;
            g_btn_hover_cancel  = nc;
            InvalidateRect(g_hwnd_btn_install, NULL, FALSE);
            InvalidateRect(GetDlgItem(hwnd, IDC_BTN_CANCEL), NULL, FALSE);
        }
        break;
    }

    case WM_COMMAND: {
        int id    = LOWORD(wp);
        int notif = HIWORD(wp);
        if (id >= IDC_GROUP_BASE && id < IDC_GROUP_BASE + g_num_groups) {
            if (notif == BN_CLICKED)
                refresh_component_states();
        } else if (id >= IDC_COMP_BASE && id < IDC_COMP_BASE + g_num_components) {
            if (notif == BN_CLICKED) {
                int ci = id - IDC_COMP_BASE;
                ComponentInfo *cc = &g_components[ci];
                /* If just checked, auto-check all transitive deps until stable.
                   Single-pass only catches direct deps; iterate to handle chains
                   like A→B→C where checking A must also pull in C. */
                if (SendMessageA(cc->hwnd_ctrl, BM_GETCHECK, 0, 0) == BST_CHECKED) {
                    int changed = 1;
                    while (changed) {
                        changed = 0;
                        for (int j = 0; j < g_num_components; j++) {
                            ComponentInfo *cj = &g_components[j];
                            if (!cj->hwnd_ctrl) continue;
                            if (SendMessageA(cj->hwnd_ctrl, BM_GETCHECK, 0, 0) != BST_CHECKED) continue;
                            for (int r = 0; r < cj->num_requires; r++) {
                                int ri = cj->requires[r] - 1;
                                if (ri < 0 || ri >= g_num_components || !g_components[ri].hwnd_ctrl) continue;
                                if (SendMessageA(g_components[ri].hwnd_ctrl, BM_GETCHECK, 0, 0) != BST_CHECKED) {
                                    SendMessageA(g_components[ri].hwnd_ctrl, BM_SETCHECK, BST_CHECKED, 0);
                                    changed = 1;
                                }
                            }
                        }
                    }
                }
                refresh_component_states();
            }
        } else if (id == IDC_FILEPATH && notif == EN_CHANGE) {
            update_space_label();
        } else if (id == IDC_BTN_BROWSE) {
            char path[MAX_PATH] = {0};
            GetWindowTextA(g_hwnd_filepath, path, MAX_PATH);
            /* If the current path already ends with \<subdir>, strip it so the
               user browses to the parent folder — not inside it. */
            if (g_meta.install_subdir[0]) {
                size_t plen = strlen(path);
                size_t slen = strlen(g_meta.install_subdir);
                if (plen > slen + 1 &&
                    path[plen - slen - 1] == '\\' &&
                    _stricmp(path + plen - slen, g_meta.install_subdir) == 0)
                    path[plen - slen - 1] = '\0';
            }
            if (browse_for_folder(hwnd, path, MAX_PATH)) {
                /* Always append \<subdir> after the user picks a folder */
                if (g_meta.install_subdir[0]) {
                    size_t plen = strlen(path);
                    size_t slen = strlen(g_meta.install_subdir);
                    int already = (plen > slen + 1 &&
                                   path[plen - slen - 1] == '\\' &&
                                   _stricmp(path + plen - slen, g_meta.install_subdir) == 0);
                    if (!already)
                        snprintf(path + plen, MAX_PATH - plen, "\\%s", g_meta.install_subdir);
                }
                SetWindowTextA(g_hwnd_filepath, path);
            }
            update_space_label();
        } else if (id == IDC_BTN_INSTALL) {
            char path[MAX_PATH] = {0};
            GetWindowTextA(g_hwnd_filepath, path, MAX_PATH);
            if (!path[0]) {
                set_status("Please select an install folder first.", COL_ERROR);
                return 0;
            }
            DWORD attr = GetFileAttributesA(path);
            if (attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY)) {
                set_status("Path is not a directory. Please select a valid folder.", COL_ERROR);
                return 0;
            }
            if (g_meta.detect_running_exe[0] &&
                !check_running_exe(hwnd, g_meta.detect_running_exe)) return 0;
            if (!check_free_space(hwnd, path, g_meta.required_free_space_gb)) return 0;
            if (!check_elevate(path)) return 0;

            /* Detect existing install and ask the user what to do */
            int repair_mode = 0;
            if (detect_existing_install(path)) {
                char det_msg[MAX_PATH + 256];
                snprintf(det_msg, sizeof(det_msg),
                    "An existing installation was detected at:\n%s\n\n"
                    "Yes  \x97  Reinstall (overwrite all files)\n"
                    "No   \x97  Repair (replace only missing or changed files)\n"
                    "Cancel  \x97  Go back",
                    path);
                int answer = MessageBoxA(hwnd, det_msg,
                    g_meta.app_name[0] ? g_meta.app_name : "PatchForge",
                    MB_YESNOCANCEL | MB_ICONQUESTION);
                if (answer == IDCANCEL) return 0;
                repair_mode = (answer == IDNO) ? 1 : 0;
            }

            /* Create install dir if it doesn't exist */
            ensure_dir(path);

            EnableWindow(g_hwnd_btn_install, FALSE);
            set_status(repair_mode ? "Repairing..." : "Installing...", COL_TEXT);

            int low_load = (SendMessageA(g_hwnd_chk_lowload, BM_GETCHECK, 0, 0) == BST_CHECKED);
            int do_verify = g_meta.verify_crc32
                            && g_hwnd_chk_verify
                            && (SendMessageA(g_hwnd_chk_verify, BM_GETCHECK, 0, 0) == BST_CHECKED);
            int do_startmenu = g_hwnd_chk_sc_startmenu &&
                (SendMessageA(g_hwnd_chk_sc_startmenu, BM_GETCHECK, 0, 0) == BST_CHECKED);
            int do_desktop = g_hwnd_chk_sc_desktop &&
                (SendMessageA(g_hwnd_chk_sc_desktop, BM_GETCHECK, 0, 0) == BST_CHECKED);

            struct InstallArgs *args =
                (struct InstallArgs *)malloc(sizeof(struct InstallArgs));
            strncpy(args->install_dir, path, MAX_PATH - 1);
            args->install_dir[MAX_PATH - 1] = '\0';
            args->low_load           = low_load;
            args->verify_crc32       = do_verify;
            args->repair_mode        = repair_mode;
            args->shortcut_desktop   = do_desktop;
            args->shortcut_startmenu = do_startmenu;
            args->num_components     = g_num_components;
            memset(args->selected_comps, 0, sizeof(args->selected_comps));
            for (int ci = 0; ci < g_num_components; ci++) {
                int checked = (SendMessageA(g_components[ci].hwnd_ctrl,
                                             BM_GETCHECK, 0, 0) == BST_CHECKED);
                /* A radio button keeps its check mark even when disabled; skip if
                   its group header is off. */
                if (checked && g_components[ci].group[0]) {
                    for (int gi = 0; gi < g_num_groups; gi++) {
                        if (strcmp(g_groups[gi].group, g_components[ci].group) == 0) {
                            if (SendMessageA(g_groups[gi].hwnd_hdr,
                                             BM_GETCHECK, 0, 0) != BST_CHECKED)
                                checked = 0;
                            break;
                        }
                    }
                }
                args->selected_comps[ci] = checked;
            }
            CloseHandle(CreateThread(NULL, 0, install_thread, args, 0, NULL));
        } else if (id == IDC_BTN_CANCEL) {
            DestroyWindow(hwnd);
        }
        break;
    }

    case WM_INSTALL_PROG:
        set_progress((int)wp);
        break;

    case WM_LOG_MSG: {
        char *msg = (char *)wp;
        if (msg) { log_append(msg); free(msg); }
        break;
    }

    case WM_INSTALL_DONE: {
        struct InstallResult *res = (struct InstallResult *)lp;
        if (wp) {
            if (res && res->verify_passed)
                log_append("Integrity check passed.");
            if (res && res->repair_mode) {
                char rbuf[128];
                snprintf(rbuf, sizeof(rbuf),
                    "Repair complete: %u file(s) replaced, %u already up to date.",
                    res->files_replaced, res->files_skipped);
                log_append(rbuf);
            }
            const char *done_label = (res && res->repair_mode)
                                     ? "Repair complete." : "Installation complete.";
            log_append(done_label);
            set_progress(100);
            const char *popup_msg  = (res && res->repair_mode)
                                     ? "Repair complete!\nAll files have been verified."
                                     : "Installation complete!\nThe game has been installed successfully.";
            MessageBoxA(hwnd, popup_msg,
                        g_meta.app_name[0] ? g_meta.app_name : "PatchForge",
                        MB_OK | MB_ICONINFORMATION);
            run_async(g_meta.run_after_install);
            const char *status_label = (res && res->repair_mode)
                                       ? "Repair complete!" : "Installation complete!";
            if (g_meta.close_delay > 0) {
                g_close_countdown = g_meta.close_delay;
                char buf[64];
                snprintf(buf, sizeof(buf), "Done! Closing in %d seconds...",
                         g_close_countdown);
                set_status(buf, COL_SUCCESS);
                SetTimer(hwnd, TIMER_CLOSE, 1000, NULL);
            } else {
                set_status(status_label, COL_SUCCESS);
            }
        } else {
            set_status("Installation failed. See log for details.", COL_ERROR);
            log_append("ERROR: Installation failed.");
            MessageBoxA(hwnd, "Installation failed.\n\nSome files may not have been written.",
                        "Error", MB_OK | MB_ICONERROR);
        }
        free(res);
        EnableWindow(g_hwnd_btn_install, TRUE);
        break;
    }

    case WM_TIMER:
        if (wp == TIMER_CLOSE) {
            g_close_countdown--;
            if (g_close_countdown <= 0) {
                KillTimer(hwnd, TIMER_CLOSE);
                DestroyWindow(hwnd);
            } else {
                char buf[64];
                snprintf(buf, sizeof(buf), "Done! Closing in %d seconds...",
                         g_close_countdown);
                set_status(buf, COL_SUCCESS);
            }
        }
        break;

    case WM_PAINT: {
        PAINTSTRUCT ps;
        BeginPaint(hwnd, &ps);
        EndPaint(hwnd, &ps);
        break;
    }

    case WM_DESTROY:
        if (g_backdrop_bmp) DeleteObject(g_backdrop_bmp);
        PostQuitMessage(0);
        break;
    }

    return DefWindowProcA(hwnd, msg, wp, lp);
}

/* ==================================================================== */
/* WinMain                                                               */
/* ==================================================================== */

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR lpCmd, int nShow)
{
    (void)hPrev;

    init_crc32_table();
    GetModuleFileNameA(NULL, g_exe_path, MAX_PATH);

    /* Capture per-user shell folder paths before any UAC elevation.
       If this is an elevated relaunch, argv[2]/argv[3] carry the original
       user's paths (set by check_elevate before relaunching). */
    SHGetFolderPathA(NULL, CSIDL_DESKTOPDIRECTORY, NULL, 0, g_user_desktop);
    SHGetFolderPathA(NULL, CSIDL_PROGRAMS,         NULL, 0, g_user_programs);
    {
        int argc_w = 0;
        LPWSTR *argv_w = CommandLineToArgvW(GetCommandLineW(), &argc_w);
        if (argv_w) {
            if (argc_w >= 3 && argv_w[2][0])
                WideCharToMultiByte(CP_ACP, 0, argv_w[2], -1,
                                    g_user_desktop, MAX_PATH, NULL, NULL);
            if (argc_w >= 4 && argv_w[3][0])
                WideCharToMultiByte(CP_ACP, 0, argv_w[3], -1,
                                    g_user_programs, MAX_PATH, NULL, NULL);
            LocalFree(argv_w);
        }
    }

    int g_noverify = 0;  /* /NOVERIFY flag for silent mode */

    /* Parse /S (silent) and /D=<path> (install directory override) */
    if (lpCmd) {
        if (strstr(lpCmd, "/S") || strstr(lpCmd, "-S"))
            g_silent = 1;
        if (strstr(lpCmd, "/NOVERIFY"))
            g_noverify = 1;
        const char *darg = strstr(lpCmd, "/D=");
        if (darg) {
            darg += 3;
            if (*darg == '"') {
                darg++;
                int i = 0;
                while (*darg && *darg != '"' && i < MAX_PATH - 1)
                    g_silent_dir[i++] = *darg++;
                g_silent_dir[i] = '\0';
            } else {
                int i = 0;
                while (*darg && *darg != ' ' && i < MAX_PATH - 1)
                    g_silent_dir[i++] = *darg++;
                g_silent_dir[i] = '\0';
            }
        }
    }

    if (!read_install_meta()) {
        if (!g_silent)
            MessageBoxA(NULL,
                "This installer is incomplete or corrupted.\n"
                "Please re-download the installer.",
                "PatchForge Installer", MB_OK | MB_ICONERROR);
        return 1;
    }

    /* When pack data lives in a separate file, verify it is present. */
    if (strcmp(g_bin_path, g_exe_path) != 0) {
        /* Check that every expected data file is present. For multi-part
           splits, verify each part individually so the user gets told which
           file is missing rather than a vague "data file not found". */
        char first_path[MAX_PATH + 8];
        int  num_parts   = g_meta.bin_parts > 0 ? g_meta.bin_parts : 1;
        int  missing_part = 0;
        for (int i = 0; i < num_parts; i++) {
            if (num_parts > 1)
                snprintf(first_path, sizeof(first_path), "%s.%03d", g_bin_path, i + 1);
            else
                snprintf(first_path, sizeof(first_path), "%s", g_bin_path);
            FILE *bf = fopen(first_path, "rb");
            if (!bf) { missing_part = i + 1; break; }
            fclose(bf);
        }
        if (missing_part) {
            if (!g_silent) {
                char msg[MAX_PATH + 200];
                if (num_parts > 1) {
                    snprintf(msg, sizeof(msg),
                        "Cannot find data file part %d of %d:\n  %s.%03d\n\n"
                        "Place all base_game.bin.NNN parts in the same folder as this installer and try again.",
                        missing_part, num_parts, g_bin_path, missing_part);
                } else {
                    snprintf(msg, sizeof(msg),
                        "Cannot find the data file:\n  %s\n\n"
                        "Place base_game.bin in the same folder as this installer and try again.",
                        g_bin_path);
                }
                MessageBoxA(NULL, msg, "PatchForge Installer", MB_OK | MB_ICONERROR);
            }
            return 1;
        }
    }

    if (!read_pack_entries()) {
        if (!g_silent)
            MessageBoxA(NULL,
                "Failed to read the package file table.\n"
                "The installer may be corrupted.",
                "PatchForge Installer", MB_OK | MB_ICONERROR);
        return 1;
    }

    /* ── Silent install ──────────────────────────────────────────────────
       No UI — use /D= path or derive default from installer's own dir.
       Component defaults are applied. Exits with 0 on success, 1 on fail. */
    if (g_silent) {
        char install_dir[MAX_PATH] = {0};
        if (g_silent_dir[0]) {
            snprintf(install_dir, MAX_PATH, "%s", g_silent_dir);
        } else {
            snprintf(install_dir, MAX_PATH, "%s", g_exe_path);
            char *last = strrchr(install_dir, '\\');
            if (last) *last = '\0';
            if (g_meta.install_subdir[0]) {
                size_t plen = strlen(install_dir);
                snprintf(install_dir + plen, MAX_PATH - plen,
                         "\\%s", g_meta.install_subdir);
            }
        }
        ensure_dir(install_dir);
        int verify = g_noverify ? 0 : g_meta.verify_crc32;
        int selected_comps[MAX_COMPONENTS] = {0};
        for (int i = 0; i < g_num_components; i++)
            selected_comps[i] = g_components[i].default_checked;
        int ok = do_install(install_dir, 0, verify, 0 /* fresh */,
                            selected_comps, g_num_components,
                            g_meta.shortcut_create_desktop,
                            g_meta.shortcut_create_startmenu,
                            NULL, NULL);
        free(g_entries);
        return ok ? 0 : 1;
    }

    /* Resources */
    g_brush_bg    = CreateSolidBrush(COL_BG);
    g_brush_light = CreateSolidBrush(COL_BG_LIGHT);
    g_brush_log   = CreateSolidBrush(COL_LOG_BG);

    g_font_normal = CreateFontA(-13, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                                DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                                CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                DEFAULT_PITCH | FF_DONTCARE, "Segoe UI");
    g_font_title  = CreateFontA(-22, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE,
                                DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                                CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                DEFAULT_PITCH | FF_DONTCARE, "Segoe UI");

    /* Pre-load backdrop so we know the image height before sizing the window */
    g_backdrop_bmp = load_backdrop();
    if (g_backdrop_bmp) {
        /* Fix display height to the 616:353 reference aspect ratio at window width 720,
         * using rounded integer arithmetic to avoid off-by-one clipping. */
        g_img_h = (int)((720 * BACKDROP_ASPECT_H + BACKDROP_ASPECT_W / 2) / BACKDROP_ASPECT_W);
        if (g_img_h > IMG_MAX_H) g_img_h = IMG_MAX_H;
        if (g_img_h < 60)        g_img_h = 60;
    }

    /* Window class */
    WNDCLASSEXA wc = {0};
    wc.cbSize        = sizeof(wc);
    wc.lpfnWndProc   = WndProc;
    wc.hInstance     = hInst;
    wc.hCursor       = LoadCursorA(NULL, IDC_ARROW);
    wc.hbrBackground = g_brush_bg;
    wc.lpszClassName = "PFGInstaller";
    wc.hIcon = LoadIconA(hInst, MAKEINTRESOURCEA(1));
    RegisterClassExA(&wc);

    const char *title = g_meta.window_title[0] ? g_meta.window_title
                      : g_meta.app_name[0]      ? g_meta.app_name
                      : "PatchForge Installer";

    /* Compute client height from layout constants (mirrors WM_CREATE positions).
       Base 300 px covers: separator + header + path row + low-load chk +
       space label + log + progress + status + footer separator + footer + padding.
       Each optional element adds its own height. */
    DWORD wstyle = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    int verify_offset   = g_meta.verify_crc32       ? 24 : 0;
    int shortcut_offset = g_meta.shortcut_target[0] ? 48 : 0;
    int hdr_extra       = (g_meta.app_note[0]    ? 18 : 0)
                        + (g_meta.description[0] ? 18 : 0);
    int sum_extra       = (g_meta.total_files > 0)  ? 18 : 0;
    /* Count distinct groups so each header row is included in the right-column height. */
    int num_distinct_groups = 0;
    {
        char seen[MAX_COMPONENTS][64];
        int nseen = 0;
        for (int ci = 0; ci < g_num_components; ci++) {
            if (!g_components[ci].group[0]) continue;
            int found = 0;
            for (int j = 0; j < nseen; j++) {
                if (strcmp(seen[j], g_components[ci].group) == 0) { found = 1; break; }
            }
            if (!found && nseen < MAX_COMPONENTS)
                strncpy(seen[nseen++], g_components[ci].group, 63);
        }
        num_distinct_groups = nseen;
    }
    /* Settings and Optional Components columns sit side-by-side; height = max of the two. */
    int left_col_h  = 20 + 24 + verify_offset + shortcut_offset; /* header + low-load + extras */
    int right_col_h = g_num_components > 0
        ? (20 + (g_num_components + num_distinct_groups) * 24) : 0;
    int two_col_h   = left_col_h > right_col_h ? left_col_h : right_col_h;
    /* base 300 already budgets one low-load row (24px); subtract to avoid double-counting */
    int client_h = g_img_h + 372 + hdr_extra + sum_extra + (two_col_h - 24);
    RECT wr = {0, 0, 720, client_h};
    AdjustWindowRect(&wr, wstyle, FALSE);
    HWND hwnd = CreateWindowExA(
        0, "PFGInstaller", title, wstyle,
        CW_USEDEFAULT, CW_USEDEFAULT,
        wr.right - wr.left, wr.bottom - wr.top,
        NULL, NULL, hInst, NULL);

    ShowWindow(hwnd, nShow);
    UpdateWindow(hwnd);

    MSG m;
    while (GetMessageA(&m, NULL, 0, 0)) {
        TranslateMessage(&m);
        DispatchMessageA(&m);
    }

    /* Cleanup */
    DeleteObject(g_brush_bg);
    DeleteObject(g_brush_light);
    DeleteObject(g_brush_log);
    DeleteObject(g_font_normal);
    DeleteObject(g_font_title);
    free(g_entries);

    return (int)m.wParam;
}

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
    char   install_registry_key[512];
    char   run_after_install[512];
    char   detect_running_exe[256];
    int    close_delay;
    double required_free_space_gb;
    int64_t pack_data_offset;
    int64_t pack_data_size;
    int64_t backdrop_offset;
    int64_t backdrop_size;
} InstallMeta;

/* ---- Per-file table entry ---- */
typedef struct {
    char     path[512];
    uint64_t offset;     /* byte offset in decompressed stream */
    uint64_t size;       /* uncompressed byte count */
    uint32_t component;  /* 0 = required; reserved for future optional components */
} PackEntry;

/* ---- Global state ---- */
static HWND       g_hwnd              = NULL;
static HWND       g_hwnd_filepath     = NULL;
static HWND       g_hwnd_status       = NULL;
static HWND       g_hwnd_progress     = NULL;
static HWND       g_hwnd_log          = NULL;
static HWND       g_hwnd_btn_install  = NULL;
static HWND       g_hwnd_chk_lowload  = NULL;
static HWND       g_hwnd_space_lbl    = NULL;
static HBRUSH     g_brush_bg          = NULL;
static HBRUSH     g_brush_light       = NULL;
static HBRUSH     g_brush_log         = NULL;
static HFONT      g_font_normal       = NULL;
static HFONT      g_font_title        = NULL;
static InstallMeta g_meta             = {0};
static char       g_exe_path[MAX_PATH]= {0};
static PackEntry *g_entries           = NULL;
static uint32_t   g_num_entries       = 0;
static int        g_close_countdown   = 0;
static HBITMAP    g_backdrop_bmp      = NULL;
static int        g_btn_hover_install = 0;
static int        g_btn_hover_cancel  = 0;

/* ---- Forward declarations ---- */
LRESULT CALLBACK WndProc(HWND, UINT, WPARAM, LPARAM);

/* ==================================================================== */
/* JSON helpers (same lightweight approach as patcher stubs)             */
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
        if (*p == '\\' && *(p + 1)) { p++; }
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

    _fseeki64(f, -(int64_t)(12 + meta_len), SEEK_END);
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

    free(buf);
    return 1;
}

/* Read the XPACK01 file table from the embedded blob. */
static int read_pack_entries(void)
{
    FILE *f = fopen(g_exe_path, "rb");
    if (!f) return 0;

    _fseeki64(f, g_meta.pack_data_offset, SEEK_SET);
    uint32_t n = 0;
    fread(&n, 4, 1, f);

    g_entries = (PackEntry *)malloc(n * sizeof(PackEntry));
    if (!g_entries) { fclose(f); return 0; }
    g_num_entries = n;

    for (uint32_t i = 0; i < n; i++) {
        uint16_t plen = 0;
        fread(&plen, 2, 1, f);
        if (plen >= (uint16_t)sizeof(g_entries[i].path))
            plen = (uint16_t)(sizeof(g_entries[i].path) - 1);
        fread(g_entries[i].path, 1, plen, f);
        g_entries[i].path[plen] = '\0';
        fread(&g_entries[i].offset,    8, 1, f);
        fread(&g_entries[i].size,      8, 1, f);
        fread(&g_entries[i].component, 4, 1, f);
    }

    fclose(f);
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
    fread(raw, 1, (size_t)g_meta.backdrop_size, f);
    fclose(f);

    IWICImagingFactory *wic = NULL;
    CoInitialize(NULL);
    if (FAILED(CoCreateInstance(&CLSID_WICImagingFactory, NULL, CLSCTX_INPROC_SERVER,
                                &IID_IWICImagingFactory, (void **)&wic))) {
        free(raw); return NULL;
    }
    IWICStream *stream = NULL;
    wic->lpVtbl->CreateStream(wic, &stream);
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
        if (hbm && bits) {
            UINT stride = w * 4;
            ((IWICBitmapSource *)conv)->lpVtbl->CopyPixels(
                (IWICBitmapSource *)conv, NULL, stride, stride * h, (BYTE *)bits);
        }
        conv->lpVtbl->Release(conv);
    }
    if (frame) frame->lpVtbl->Release(frame);
    if (dec)   dec->lpVtbl->Release(dec);
    if (stream) stream->lpVtbl->Release(stream);
    wic->lpVtbl->Release(wic);
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

    /* Try to write a probe file */
    char probe[MAX_PATH];
    snprintf(probe, MAX_PATH, "%s\\~pfg_probe.tmp", path);
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

    char args[MAX_PATH + 4];
    snprintf(args, sizeof(args), "\"%s\"", path);
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
/* Install thread                                                        */
/* ==================================================================== */

struct InstallArgs {
    char install_dir[MAX_PATH];
    int  low_load;
};

static int do_install(const char *install_dir, int low_load)
{
    FILE *f = fopen(g_exe_path, "rb");
    if (!f) return 0;

    /* Seek past the file table to reach the compressed data size field */
    _fseeki64(f, g_meta.pack_data_offset, SEEK_SET);
    uint32_t n = 0;
    fread(&n, 4, 1, f);
    for (uint32_t i = 0; i < n; i++) {
        uint16_t plen = 0;
        fread(&plen, 2, 1, f);
        _fseeki64(f, (int64_t)(plen + 8 + 8 + 4), SEEK_CUR);
    }

    uint64_t csize = 0;
    fread(&csize, 8, 1, f);
    /* f is now positioned at the first byte of compressed data */

    lzma_stream strm = LZMA_STREAM_INIT;
    if (lzma_stream_decoder(&strm, UINT64_MAX, 0) != LZMA_OK) {
        fclose(f);
        return 0;
    }

    const size_t IN_SZ  = 65536;
    const size_t OUT_SZ = low_load ? 65536 : 262144;
    uint8_t *inbuf  = (uint8_t *)malloc(IN_SZ);
    uint8_t *outbuf = (uint8_t *)malloc(OUT_SZ);
    if (!inbuf || !outbuf) {
        free(inbuf); free(outbuf);
        lzma_end(&strm);
        fclose(f);
        return 0;
    }

    uint64_t total_read     = 0;
    uint32_t cur_file       = 0;
    uint64_t cur_file_written = 0;
    HANDLE   hf             = INVALID_HANDLE_VALUE;
    int      success        = 1;
    lzma_action action      = LZMA_RUN;

    strm.next_in  = NULL;
    strm.avail_in = 0;

    while (1) {
        /* Feed more compressed input when the buffer is empty */
        if (strm.avail_in == 0 && total_read < csize) {
            size_t to_read = IN_SZ < (csize - total_read)
                             ? IN_SZ : (size_t)(csize - total_read);
            size_t got = fread(inbuf, 1, to_read, f);
            strm.next_in  = inbuf;
            strm.avail_in = (uint32_t)got;
            total_read   += got;
            if (total_read >= csize) action = LZMA_FINISH;
        }

        strm.next_out  = outbuf;
        strm.avail_out = (uint32_t)OUT_SZ;

        lzma_ret ret = lzma_code(&strm, action);
        size_t produced = OUT_SZ - strm.avail_out;

        /* Distribute decoded bytes across output files */
        uint8_t *ptr = outbuf;
        size_t   rem = produced;

        while (rem > 0 && cur_file < g_num_entries) {
            PackEntry *e = &g_entries[cur_file];
            uint64_t need  = e->size - cur_file_written;
            size_t   write = (size_t)(rem < need ? rem : need);

            if (hf == INVALID_HANDLE_VALUE && e->size > 0) {
                char fpath[MAX_PATH];
                snprintf(fpath, MAX_PATH, "%s\\%s", install_dir, e->path);
                /* Forward slashes → backslashes */
                for (char *p = fpath; *p; p++) if (*p == '/') *p = '\\';
                ensure_dir_for_file(fpath);
                hf = CreateFileA(fpath, GENERIC_WRITE, 0, NULL,
                                 CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
            }

            if (hf != INVALID_HANDLE_VALUE && write > 0) {
                DWORD written = 0;
                if (!WriteFile(hf, ptr, (DWORD)write, &written, NULL))
                    success = 0;
            }

            ptr             += write;
            rem             -= write;
            cur_file_written += write;

            if (cur_file_written >= e->size || e->size == 0) {
                if (hf != INVALID_HANDLE_VALUE) {
                    CloseHandle(hf);
                    hf = INVALID_HANDLE_VALUE;
                }
                cur_file_written = 0;
                cur_file++;

                /* Progress + log */
                int pct = (int)(cur_file * 100 / g_num_entries);
                PostMessageA(g_hwnd, WM_INSTALL_PROG, (WPARAM)pct, 0);

                char *log_msg = (char *)malloc(128);
                if (log_msg) {
                    snprintf(log_msg, 128, "Extracting file %u / %u: %s",
                             cur_file, g_num_entries, e->path);
                    PostMessageA(g_hwnd, WM_LOG_MSG, (WPARAM)log_msg, 0);
                }
            }
        }

        if (low_load) Sleep(1);

        if (ret == LZMA_STREAM_END) break;
        if (ret != LZMA_OK) { success = 0; break; }
    }

    if (hf != INVALID_HANDLE_VALUE) CloseHandle(hf);
    lzma_end(&strm);
    free(inbuf);
    free(outbuf);
    fclose(f);

    /* Write registry key */
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

    return success;
}

static DWORD WINAPI install_thread(LPVOID param)
{
    struct InstallArgs *args = (struct InstallArgs *)param;
    int ok = do_install(args->install_dir, args->low_load);
    PostMessageA(g_hwnd, WM_INSTALL_DONE, (WPARAM)ok, 0);
    free(args);
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
        HWND lbl = CreateWindowExA(0, "STATIC",
            g_meta.app_name[0] ? g_meta.app_name : "PatchForge Installer",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 16, 680, 30, hwnd, NULL, NULL, NULL);
        SendMessageA(lbl, WM_SETFONT, (WPARAM)g_font_title, TRUE);

        /* Change summary line: "X files · Y GB" */
        {
            char cbuf[128] = {0};
            if (g_meta.total_files > 0) {
                double gb = (double)g_meta.total_uncompressed_size
                            / (1024.0 * 1024.0 * 1024.0);
                if (gb >= 1.0)
                    snprintf(cbuf, sizeof(cbuf), "%d files  \xB7  %.1f GB installed",
                             g_meta.total_files, gb);
                else {
                    double mb = (double)g_meta.total_uncompressed_size
                                / (1024.0 * 1024.0);
                    snprintf(cbuf, sizeof(cbuf), "%d files  \xB7  %.1f MB installed",
                             g_meta.total_files, mb);
                }
                HWND clbl = CreateWindowExA(0, "STATIC", cbuf,
                    WS_CHILD | WS_VISIBLE | SS_LEFT,
                    20, 50, 680, 16, hwnd, NULL, NULL, NULL);
                SendMessageA(clbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            }
        }

        /* Description */
        if (g_meta.description[0]) {
            HWND dlbl = CreateWindowExA(0, "STATIC", g_meta.description,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                20, 68, 680, 16, hwnd, NULL, NULL, NULL);
            SendMessageA(dlbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        }

        /* Install location label */
        HWND flbl = CreateWindowExA(0, "STATIC", "Install location:",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 102, 110, 18, hwnd, NULL, NULL, NULL);
        SendMessageA(flbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Install path edit */
        g_hwnd_filepath = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
            135, 100, 479, 22, hwnd, (HMENU)IDC_FILEPATH, NULL, NULL);
        SendMessageA(g_hwnd_filepath, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        CreateWindowExA(0, "BUTTON", "Browse...",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            622, 100, 78, 22, hwnd, (HMENU)IDC_BTN_BROWSE, NULL, NULL);

        /* Reduce system load checkbox */
        g_hwnd_chk_lowload = CreateWindowExA(0, "BUTTON",
            "Reduce system load during install (slower, uses less CPU)",
            WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
            20, 130, 460, 20, hwnd, (HMENU)IDC_CHK_LOWLOAD, NULL, NULL);
        SendMessageA(g_hwnd_chk_lowload, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Disk space label */
        g_hwnd_space_lbl = CreateWindowExA(0, "STATIC", "",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 154, 680, 16, hwnd, (HMENU)IDC_SPACE_LBL, NULL, NULL);
        SendMessageA(g_hwnd_space_lbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Log area */
        g_hwnd_log = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL |
            ES_READONLY | WS_VSCROLL,
            20, 180, 680, 122, hwnd, (HMENU)IDC_LOG, NULL, NULL);
        SendMessageA(g_hwnd_log, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Progress bar */
        g_hwnd_progress = CreateWindowExA(0, "STATIC", "",
            WS_CHILD | WS_VISIBLE | SS_OWNERDRAW,
            20, 310, 680, 8, hwnd, (HMENU)IDC_PROGRESS, NULL, NULL);
        SetWindowLongA(g_hwnd_progress, GWLP_USERDATA, 0);

        /* Status */
        g_hwnd_status = CreateWindowExA(0, "STATIC",
            "Select an install folder and click Install.",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 326, 510, 16, hwnd, (HMENU)IDC_STATUS, NULL, NULL);
        SendMessageA(g_hwnd_status, WM_SETFONT, (WPARAM)g_font_normal, TRUE);

        /* Install / Cancel buttons */
        g_hwnd_btn_install = CreateWindowExA(0, "BUTTON", "Install",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            530, 354, 80, 28, hwnd, (HMENU)IDC_BTN_INSTALL, NULL, NULL);
        CreateWindowExA(0, "BUTTON", "Cancel",
            WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
            620, 354, 72, 28, hwnd, (HMENU)IDC_BTN_CANCEL, NULL, NULL);

        /* Bottom-left info: company · copyright · contact */
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
                    20, 358, 500, 16, hwnd, NULL, NULL, NULL);
                SendMessageA(infolbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
            }
        }

        /* Version line */
        if (g_meta.version[0]) {
            char verbuf[80];
            snprintf(verbuf, sizeof(verbuf), "Version %s", g_meta.version);
            HWND verlbl = CreateWindowExA(0, "STATIC", verbuf,
                WS_CHILD | WS_VISIBLE | SS_LEFT,
                20, 378, 500, 14, hwnd, NULL, NULL, NULL);
            SendMessageA(verlbl, WM_SETFONT, (WPARAM)g_font_normal, TRUE);
        }

        /* Backdrop */
        g_backdrop_bmp = load_backdrop();

        /* Pre-populate install path from argv[1] (elevated relaunch) */
        {
            int argc = 0;
            LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
            if (argv && argc >= 2) {
                char path8[MAX_PATH] = {0};
                WideCharToMultiByte(CP_ACP, 0, argv[1], -1, path8, MAX_PATH, NULL, NULL);
                if (path8[0]) SetWindowTextA(g_hwnd_filepath, path8);
            }
            if (argv) LocalFree(argv);
        }

        update_space_label();
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
        if (g_backdrop_bmp) {
            HDC mdc = CreateCompatibleDC(dc);
            SelectObject(mdc, g_backdrop_bmp);
            BITMAP bm = {0};
            GetObjectA(g_backdrop_bmp, sizeof(bm), &bm);
            SetStretchBltMode(dc, HALFTONE);
            StretchBlt(dc, 0, 0, r.right, r.bottom,
                       mdc, 0, 0, bm.bmWidth, bm.bmHeight, SRCCOPY);
            DeleteDC(mdc);
        }
        return 1;
    }

    case WM_DRAWITEM: {
        DRAWITEMSTRUCT *dis = (DRAWITEMSTRUCT *)lp;
        COLORREF bg = (dis->CtlID == IDC_BTN_INSTALL) ? COL_ACCENT : COL_BG_LIGHT;
        paint_button(dis, bg, COL_TEXT);
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
        if (id == IDC_FILEPATH && notif == EN_CHANGE) {
            update_space_label();
        } else if (id == IDC_BTN_BROWSE) {
            char path[MAX_PATH] = {0};
            GetWindowTextA(g_hwnd_filepath, path, MAX_PATH);
            if (browse_for_folder(hwnd, path, MAX_PATH))
                SetWindowTextA(g_hwnd_filepath, path);
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

            /* Create install dir if it doesn't exist */
            ensure_dir(path);

            EnableWindow(g_hwnd_btn_install, FALSE);
            set_status("Installing…", COL_TEXT);

            int low_load = (SendMessageA(g_hwnd_chk_lowload, BM_GETCHECK, 0, 0) == BST_CHECKED);
            struct InstallArgs *args =
                (struct InstallArgs *)malloc(sizeof(struct InstallArgs));
            strncpy(args->install_dir, path, MAX_PATH - 1);
            args->install_dir[MAX_PATH - 1] = '\0';
            args->low_load = low_load;
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

    case WM_INSTALL_DONE:
        if (wp) {
            log_append("Installation complete.");
            set_progress(100);
            MessageBoxA(hwnd, "Installation complete!\nThe game has been installed successfully.",
                        g_meta.app_name[0] ? g_meta.app_name : "PatchForge",
                        MB_OK | MB_ICONINFORMATION);
            run_async(g_meta.run_after_install);
            if (g_meta.close_delay > 0) {
                g_close_countdown = g_meta.close_delay;
                char buf[64];
                snprintf(buf, sizeof(buf), "Done! Closing in %d seconds\xe2\x80\xa6",
                         g_close_countdown);
                set_status(buf, COL_SUCCESS);
                SetTimer(hwnd, TIMER_CLOSE, 1000, NULL);
            } else {
                set_status("Installation complete!", COL_SUCCESS);
            }
        } else {
            set_status("Installation failed. See log for details.", COL_ERROR);
            log_append("ERROR: Installation failed.");
            MessageBoxA(hwnd, "Installation failed.\n\nSome files may not have been written.",
                        "Error", MB_OK | MB_ICONERROR);
        }
        EnableWindow(g_hwnd_btn_install, TRUE);
        break;

    case WM_TIMER:
        if (wp == TIMER_CLOSE) {
            g_close_countdown--;
            if (g_close_countdown <= 0) {
                KillTimer(hwnd, TIMER_CLOSE);
                DestroyWindow(hwnd);
            } else {
                char buf[64];
                snprintf(buf, sizeof(buf), "Done! Closing in %d seconds\xe2\x80\xa6",
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

    /* Custom progress bar drawing */
    if (msg == WM_DRAWITEM) {
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
            return TRUE;
        }
    }

    return DefWindowProcA(hwnd, msg, wp, lp);
}

/* ==================================================================== */
/* WinMain                                                               */
/* ==================================================================== */

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR lpCmd, int nShow)
{
    (void)hPrev; (void)lpCmd;

    GetModuleFileNameA(NULL, g_exe_path, MAX_PATH);

    if (!read_install_meta()) {
        MessageBoxA(NULL,
            "This installer is incomplete or corrupted.\n"
            "Please re-download the installer.",
            "PatchForge Installer", MB_OK | MB_ICONERROR);
        return 1;
    }

    if (!read_pack_entries()) {
        MessageBoxA(NULL,
            "Failed to read the package file table.\n"
            "The installer may be corrupted.",
            "PatchForge Installer", MB_OK | MB_ICONERROR);
        return 1;
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

    /* Compute outer window size from desired client area so the non-client
       frame (title bar + borders) never clips controls at the bottom. */
    DWORD wstyle = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    RECT wr = {0, 0, 720, 412};
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

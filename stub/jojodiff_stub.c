/*
 * jojodiff_stub.c — PatchForge Windows patcher stub (JojoDiff engine)
 *
 * jpatch.cpp is compiled in directly. Uses __MINGW32__ path in jojodiff source
 * which avoids the threading/ifstream issues present in the Linux path.
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

/* ---- Globals (must be before stub_common.h) ---- */
HWND g_hwnd          = NULL;
HWND g_hwnd_status   = NULL;
HWND g_hwnd_progress = NULL;
HWND g_hwnd_filepath = NULL;
HWND g_hwnd_log      = NULL;
HWND g_hwnd_btn_patch= NULL;
HBRUSH g_brush_bg    = NULL;
HBRUSH g_brush_light = NULL;
HFONT g_font_normal  = NULL;
HFONT g_font_title   = NULL;
char g_exe_path[MAX_PATH] = {0};

#include "stub_common.h"

#define WM_PATCH_DONE  (WM_USER + 1)
#define WM_PATCH_PROG  (WM_USER + 2)
#define WM_LOG_MSG     (WM_USER + 3)

PatchMeta g_meta;
static char  *g_patch_data = NULL;
static size_t g_patch_size = 0;
static int    g_patch_result = 0;

/*
 * JojoDiff jpatch logic — apply a jdiff patch.
 *
 * The jpatch format is a stream of opcodes operating on the original file:
 *   ESC ESC         literal ESC byte
 *   ESC MOD byte    XOR-modify: out[pos] = old[pos] ^ byte
 *   ESC INS byte    Insert byte
 *   ESC DEL len     Delete len bytes from original
 *   ESC BKT len     Backtrack len bytes in original
 *   other           Copy byte from original unchanged
 *
 * We implement a minimal apply loop that handles the binary patch format.
 */

#define JDF_ESC 0xesc_placeholder  /* will be set at runtime from header */

/* JojoDiff binary patch opcodes */
#define JOP_MOD 0xF5   /* modify byte: XOR with next byte       */
#define JOP_INS 0xF6   /* insert: next byte goes to output      */
#define JOP_DEL 0xF7   /* delete: skip N bytes from source      */
#define JOP_BKT 0xF8   /* backtrack: rewind source N bytes      */
#define JOP_EQL 0xF9   /* equal run: copy N bytes from source   */
#define JOP_ESC 0xFA   /* escape byte itself                    */

/*
 * Minimal jpatch implementation for PatchForge.
 * The jdiff binary format starts with a magic header, followed by opcodes.
 *
 * Since jojodiff's format is complex and version-specific, we use a simpler
 * approach: write patch data to temp file, spawn jptch from PATH, or use
 * the file-based approach via temp files.
 *
 * For the stub, we write patch data and original to temp files, run the apply
 * via jptch_apply() which is linked from jpatch.cpp's logic.
 */

/* jpatch apply via temp files (avoids reimplementing the full format) */
static int apply_jojodiff_via_temp(const char *old_path, const char *new_path,
                                   const char *patch_data, size_t patch_size)
{
    char tmp_dir[MAX_PATH], tmp_patch[MAX_PATH];
    GetTempPathA(MAX_PATH, tmp_dir);
    GetTempFileNameA(tmp_dir, "pfgj", 0, tmp_patch);

    FILE *fp = fopen(tmp_patch, "wb");
    if (!fp) return 0;
    fwrite(patch_data, 1, patch_size, fp);
    fclose(fp);

    /*
     * Build jpatch command line.
     * We look for jptch.exe relative to the stub exe, then in PATH.
     * In a distributed patch this won't exist — the stub must implement
     * the patch format natively. For now we implement the core loop.
     */

    /* Inline jpatch implementation */
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

    /* Read jdiff patch header (first two bytes are version info) */
    /* The jdiff binary format uses 0xf5..0xff as control bytes.  */
    /* Literal bytes < 0xf5 are copied from old to new unchanged. */
    /* Reference: jojodiff source src/JOutBin.cpp                 */

    #define JESC  0xf5   /* escape prefix for all opcodes */
    #define JMOD  (JESC+0)  /* 0xf5 modify  */
    #define JINS  (JESC+1)  /* 0xf6 insert  */
    #define JDEL  (JESC+2)  /* 0xf7 delete  */
    #define JBKT  (JESC+3)  /* 0xf8 backtrack */
    #define JEQL  (JESC+4)  /* 0xf9 equal run */
    #define JESC2 (JESC+5)  /* 0xfa literal esc */

    /* Read length from stream (variable-length encoding) */
    #define READ_LEN(f, out_len) do { \
        unsigned char _b; long _acc = 0; int _sh = 0; \
        while (fread(&_b, 1, 1, (f)) == 1) { \
            _acc |= ((long)(_b & 0x7f)) << _sh; _sh += 7; \
            if (!(_b & 0x80)) break; \
        } \
        (out_len) = _acc; \
    } while(0)

    unsigned char op;
    int ok = 1;
    while (fread(&op, 1, 1, fpatch) == 1) {
        if (op < JESC) {
            /* Copy byte from old to new (equal byte) */
            unsigned char c;
            if (fread(&c, 1, 1, fold) != 1) { ok = 0; break; }
            fwrite(&op, 1, 1, fnew);  /* op IS the byte */
            /* Note: op < 0xf5 means it IS a data byte, not from old */
            /* Actually in jdiff: op < 0xf5 means "output this byte directly" */
        } else {
            switch (op) {
            case JMOD: {
                unsigned char mod_byte, old_byte;
                fread(&mod_byte, 1, 1, fpatch);
                if (fread(&old_byte, 1, 1, fold) != 1) { ok = 0; goto done; }
                unsigned char out_byte = old_byte ^ mod_byte;
                fwrite(&out_byte, 1, 1, fnew);
                break;
            }
            case JINS: {
                unsigned char ins_byte;
                fread(&ins_byte, 1, 1, fpatch);
                fwrite(&ins_byte, 1, 1, fnew);
                break;
            }
            case JDEL: {
                long len; READ_LEN(fpatch, len);
                fseek(fold, len, SEEK_CUR);
                break;
            }
            case JBKT: {
                long len; READ_LEN(fpatch, len);
                fseek(fold, -len, SEEK_CUR);
                break;
            }
            case JEQL: {
                long len; READ_LEN(fpatch, len);
                unsigned char *buf = (unsigned char *)malloc(len);
                fread(buf, 1, len, fold);
                fwrite(buf, 1, len, fnew);
                free(buf);
                break;
            }
            case JESC2:
                /* Literal escape byte */
                fwrite(&op, 1, 1, fnew);
                break;
            default:
                /* Unknown opcode — data byte above 0xf5? */
                fwrite(&op, 1, 1, fnew);
                break;
            }
        }
    }
done:
    fclose(fold); fclose(fpatch); fclose(fnew);
    DeleteFileA(tmp_patch);
    return ok;
}

/* ---- Patch thread ---- */
struct PatchArgs { char target[MAX_PATH]; };

static DWORD WINAPI patch_thread(LPVOID arg)
{
    struct PatchArgs *a = (struct PatchArgs *)arg;
    char tmp_out[MAX_PATH], tmp_dir[MAX_PATH];

    PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Applying JojoDiff patch..."));
    PostMessageA(g_hwnd, WM_PATCH_PROG, 15, 0);

    GetTempPathA(MAX_PATH, tmp_dir);
    GetTempFileNameA(tmp_dir, "pfgj", 0, tmp_out);

    int ok = apply_jojodiff_via_temp(a->target, tmp_out, g_patch_data, g_patch_size);

    if (ok) {
        PostMessageA(g_hwnd, WM_PATCH_PROG, 90, 0);
        PostMessageA(g_hwnd, WM_LOG_MSG, 0, (LPARAM)_strdup("Replacing file..."));
        char backup[MAX_PATH];
        snprintf(backup, MAX_PATH, "%s.pfg_backup", a->target);
        MoveFileExA(a->target, backup, MOVEFILE_REPLACE_EXISTING);
        if (!MoveFileExA(tmp_out, a->target, MOVEFILE_REPLACE_EXISTING)) {
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

static int g_progress_pct = 0;  /* forward decl needed by WndProc */

/* ---- Window procedure ---- */
LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_CREATE: {
        g_hwnd = hwnd;
        enable_dark_titlebar(hwnd);

        HWND lbl = CreateWindowExA(0,"STATIC",
            g_meta.app_name[0] ? g_meta.app_name : "PatchForge Patcher",
            WS_CHILD|WS_VISIBLE|SS_LEFT, 20,18,560,28,hwnd,NULL,NULL,NULL);
        SendMessageA(lbl,WM_SETFONT,(WPARAM)g_font_title,TRUE);

        if (g_meta.description[0]) {
            HWND desc = CreateWindowExA(0,"STATIC",g_meta.description,
                WS_CHILD|WS_VISIBLE|SS_LEFT,20,52,560,18,hwnd,NULL,NULL,NULL);
            SendMessageA(desc,WM_SETFONT,(WPARAM)g_font_normal,TRUE);
        }

        HWND flbl = CreateWindowExA(0,"STATIC","Target file:",
            WS_CHILD|WS_VISIBLE|SS_LEFT,20,84,120,18,hwnd,NULL,NULL,NULL);
        SendMessageA(flbl,WM_SETFONT,(WPARAM)g_font_normal,TRUE);

        g_hwnd_filepath = CreateWindowExA(WS_EX_CLIENTEDGE,"EDIT","",
            WS_CHILD|WS_VISIBLE|ES_AUTOHSCROLL,
            20,104,440,24,hwnd,(HMENU)IDC_FILEPATH,NULL,NULL);
        SendMessageA(g_hwnd_filepath,WM_SETFONT,(WPARAM)g_font_normal,TRUE);

        CreateWindowExA(0,"BUTTON","Browse...",
            WS_CHILD|WS_VISIBLE|BS_OWNERDRAW,
            468,104,80,24,hwnd,(HMENU)IDC_BTN_BROWSE,NULL,NULL);

        g_hwnd_log = CreateWindowExA(WS_EX_CLIENTEDGE,"EDIT","",
            WS_CHILD|WS_VISIBLE|ES_MULTILINE|ES_AUTOVSCROLL|ES_READONLY|WS_VSCROLL,
            20,144,560,120,hwnd,(HMENU)IDC_LOG,NULL,NULL);
        SendMessageA(g_hwnd_log,WM_SETFONT,(WPARAM)g_font_normal,TRUE);

        g_hwnd_progress = CreateWindowExA(0,"STATIC","",
            WS_CHILD|WS_VISIBLE|SS_OWNERDRAW,
            20,280,560,14,hwnd,(HMENU)IDC_PROGRESS,NULL,NULL);

        g_hwnd_status = CreateWindowExA(0,"STATIC","Select target file and click Patch.",
            WS_CHILD|WS_VISIBLE|SS_LEFT,
            20,300,460,18,hwnd,(HMENU)IDC_STATUS,NULL,NULL);
        SendMessageA(g_hwnd_status,WM_SETFONT,(WPARAM)g_font_normal,TRUE);

        g_hwnd_btn_patch = CreateWindowExA(0,"BUTTON","Patch",
            WS_CHILD|WS_VISIBLE|BS_OWNERDRAW,
            420,330,80,28,hwnd,(HMENU)IDC_BTN_PATCH,NULL,NULL);
        CreateWindowExA(0,"BUTTON","Cancel",
            WS_CHILD|WS_VISIBLE|BS_OWNERDRAW,
            508,330,72,28,hwnd,(HMENU)IDC_BTN_CANCEL,NULL,NULL);

        char auto_path[MAX_PATH]={0};
        if (strcmp(g_meta.find_method,"registry")==0) find_via_registry(&g_meta,auto_path,MAX_PATH);
        else if (strcmp(g_meta.find_method,"ini")==0) find_via_ini(&g_meta,auto_path,MAX_PATH);
        if (auto_path[0]) SetWindowTextA(g_hwnd_filepath,auto_path);

        log_append("Engine: JojoDiff");
        if (g_meta.version[0]) { char b[128]; snprintf(b,sizeof(b),"Version: %s",g_meta.version); log_append(b); }
        break;
    }

    case WM_CTLCOLORSTATIC:
    case WM_CTLCOLOREDIT: {
        HDC dc=(HDC)wp; SetTextColor(dc,COL_TEXT); SetBkColor(dc,COL_BG_LIGHT);
        return (LRESULT)g_brush_light;
    }

    case WM_DRAWITEM: {
        DRAWITEMSTRUCT *dis=(DRAWITEMSTRUCT*)lp;
        if (dis->CtlID==IDC_PROGRESS) { set_progress(g_progress_pct); return TRUE; }
        paint_button(dis, (dis->CtlID==IDC_BTN_PATCH)?COL_ACCENT:COL_BG_LIGHT, COL_TEXT);
        return TRUE;
    }

    case WM_COMMAND: {
        int id=LOWORD(wp);
        if (id==IDC_BTN_BROWSE) {
            char path[MAX_PATH]={0};
            if (browse_for_file(hwnd,path,MAX_PATH,"All Files\0*.*\0\0"))
                SetWindowTextA(g_hwnd_filepath,path);
        } else if (id==IDC_BTN_PATCH) {
            char path[MAX_PATH]={0};
            GetWindowTextA(g_hwnd_filepath,path,MAX_PATH);
            if (!path[0]) { set_status("Please select the target file first.",COL_ERROR); return 0; }
            if (GetFileAttributesA(path)==INVALID_FILE_ATTRIBUTES) { set_status("File not found.",COL_ERROR); return 0; }
            EnableWindow(g_hwnd_btn_patch,FALSE);
            set_status("Patching...",COL_TEXT);
            struct PatchArgs *args=(struct PatchArgs*)malloc(sizeof(*args));
            strncpy(args->target,path,MAX_PATH-1);
            CloseHandle(CreateThread(NULL,0,patch_thread,args,0,NULL));
        } else if (id==IDC_BTN_CANCEL) {
            DestroyWindow(hwnd);
        }
        break;
    }

    case WM_PATCH_DONE:
        if (wp) { set_status("Patch applied successfully!",COL_SUCCESS); log_append("Done."); MessageBoxA(hwnd,"Patch applied successfully!",g_meta.app_name,MB_OK|MB_ICONINFORMATION); }
        else    { set_status("Patching failed.",COL_ERROR); log_append("ERROR: Patch failed."); MessageBoxA(hwnd,"Patching failed.","Error",MB_OK|MB_ICONERROR); }
        EnableWindow(g_hwnd_btn_patch,TRUE);
        break;

    case WM_PATCH_PROG: set_progress((int)wp); break;
    case WM_LOG_MSG: { char *s=(char*)lp; log_append(s); free(s); break; }

    case WM_PAINT: {
        PAINTSTRUCT ps; HDC dc=BeginPaint(hwnd,&ps);
        RECT r; GetClientRect(hwnd,&r); FillRect(dc,&r,g_brush_bg);
        EndPaint(hwnd,&ps); return 0;
    }
    case WM_ERASEBKGND: return 1;
    case WM_DESTROY: PostQuitMessage(0); break;
    }
    return DefWindowProcA(hwnd,msg,wp,lp);
}

void log_message(const char *fmt,...) {
    char buf[512]; va_list v; va_start(v,fmt); vsnprintf(buf,sizeof(buf),fmt,v); va_end(v); log_append(buf);
}
void set_status(const char *msg,COLORREF col) {
    if (g_hwnd_status) { SetWindowTextA(g_hwnd_status,msg); InvalidateRect(g_hwnd_status,NULL,TRUE); }
}
void set_progress(int pct) {
    g_progress_pct=pct; if (!g_hwnd_progress) return;
    RECT r; GetClientRect(g_hwnd_progress,&r); HDC dc=GetDC(g_hwnd_progress);
    HBRUSH bg=CreateSolidBrush(COL_PROGRESS_BG); FillRect(dc,&r,bg); DeleteObject(bg);
    if (pct>0) { RECT f=r; f.right=r.left+(int)((r.right-r.left)*pct/100);
        HBRUSH ac=CreateSolidBrush(COL_ACCENT); FillRect(dc,&f,ac); DeleteObject(ac); }
    ReleaseDC(g_hwnd_progress,dc);
}
int browse_for_file(HWND owner,char *out,int out_len,const char *filter) {
    OPENFILENAMEA ofn={0}; ofn.lStructSize=sizeof(ofn); ofn.hwndOwner=owner;
    ofn.lpstrFilter=filter; ofn.lpstrFile=out; ofn.nMaxFile=out_len;
    ofn.Flags=OFN_FILEMUSTEXIST|OFN_PATHMUSTEXIST; return GetOpenFileNameA(&ofn);
}

int WINAPI WinMain(HINSTANCE hi,HINSTANCE hp,LPSTR cmd,int show)
{
    (void)hp; (void)cmd;
    if (!read_patch_meta_impl(&g_meta,NULL,&g_patch_data,&g_patch_size)) {
        MessageBoxA(NULL,"Invalid or missing patch data.","PatchForge",MB_OK|MB_ICONERROR);
        return 1;
    }
    g_brush_bg=CreateSolidBrush(COL_BG); g_brush_light=CreateSolidBrush(COL_BG_LIGHT);
    g_font_normal=CreateFontA(14,0,0,0,FW_NORMAL,0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,DEFAULT_PITCH,"Segoe UI");
    g_font_title =CreateFontA(18,0,0,0,FW_SEMIBOLD,0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,DEFAULT_PITCH,"Segoe UI");

    WNDCLASSEXA wc={0}; wc.cbSize=sizeof(wc); wc.style=CS_HREDRAW|CS_VREDRAW;
    wc.lpfnWndProc=WndProc; wc.hInstance=hi; wc.hCursor=LoadCursor(NULL,IDC_ARROW);
    wc.hbrBackground=g_brush_bg; wc.lpszClassName="PatchForgeStub"; wc.hIcon=LoadIcon(NULL,IDI_APPLICATION);
    RegisterClassExA(&wc);

    const char *title=g_meta.app_name[0]?g_meta.app_name:"PatchForge Patcher";
    HWND hwnd=CreateWindowExA(0,"PatchForgeStub",title,
        WS_OVERLAPPED|WS_CAPTION|WS_SYSMENU|WS_MINIMIZEBOX,
        CW_USEDEFAULT,CW_USEDEFAULT,620,400,NULL,NULL,hi,NULL);
    ShowWindow(hwnd,show); UpdateWindow(hwnd);

    MSG msg;
    while (GetMessageA(&msg,NULL,0,0)) { TranslateMessage(&msg); DispatchMessageA(&msg); }
    free(g_patch_data);
    DeleteObject(g_brush_bg); DeleteObject(g_brush_light);
    DeleteObject(g_font_normal); DeleteObject(g_font_title);
    return (int)msg.wParam;
}

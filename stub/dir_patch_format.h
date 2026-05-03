/*
 * dir_patch_format.h — PFMD container parser for JojoDiff directory patches.
 *
 * Wire format (version 2):
 *   4 bytes  magic "PFMD"
 *   1 byte   version = 2
 *   4 bytes  LE uint32 num_entries
 *   entries:
 *     1 byte   op  (0=delete, 1=patch, 2=new-file)
 *     2 bytes  LE uint16 path_len
 *     N bytes  path (UTF-8 encoded, forward slashes, no leading slash)
 *     8 bytes  LE uint64 data_len (0 for delete)
 *     N bytes  data (patch bytes for op=1, raw content for op=2)
 *
 * Version 2 widened data_len from uint32 to uint64 so OP_NEW entries for
 * files larger than 4 GB no longer overflow.  This parser refuses earlier
 * versions; rebuild the prebuilt stubs whenever you bump the format.
 */
#ifndef DIR_PATCH_FORMAT_H
#define DIR_PATCH_FORMAT_H

#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <windows.h>

#define PFMD_OP_DELETE  0
#define PFMD_OP_PATCH   1
#define PFMD_OP_NEW     2
#define PFMD_VERSION    2

static inline uint64_t pfmd_u64le(const unsigned char *p) {
    return (uint64_t)p[0]
         | ((uint64_t)p[1] <<  8)
         | ((uint64_t)p[2] << 16)
         | ((uint64_t)p[3] << 24)
         | ((uint64_t)p[4] << 32)
         | ((uint64_t)p[5] << 40)
         | ((uint64_t)p[6] << 48)
         | ((uint64_t)p[7] << 56);
}
static inline uint32_t pfmd_u32le(const unsigned char *p) {
    return (uint32_t)p[0]
         | ((uint32_t)p[1] <<  8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}
static inline uint16_t pfmd_u16le(const unsigned char *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

/*
 * pfmd_entry_fn — callback invoked once per container entry.
 *
 *   op       — PFMD_OP_DELETE / PFMD_OP_PATCH / PFMD_OP_NEW
 *   rel_path — relative Windows path (forward slashes already converted to backslashes)
 *   data     — patch/content bytes; NULL if data_len == 0
 *   data_len — length of data
 *   userdata — caller-supplied context
 *
 * Return 1 to continue iterating, 0 to stop with failure.
 */
typedef int (*pfmd_entry_fn)(int op, const char *rel_path,
                              const unsigned char *data, uint64_t data_len,
                              void *userdata);

/* Iterate over a PFMD container in memory, invoking cb for each entry. */
static int pfmd_iterate(const unsigned char *buf, size_t buf_len,
                         pfmd_entry_fn cb, void *userdata)
{
    if (buf_len < 9) return 0;
    if (memcmp(buf, "PFMD", 4) != 0) return 0;
    if (buf[4] != PFMD_VERSION) return 0;
    const unsigned char *p   = buf + 5;
    const unsigned char *end = buf + buf_len;
    if (p + 4 > end) return 0;
    uint32_t n = pfmd_u32le(p); p += 4;

    char rel_path[MAX_PATH];

    for (uint32_t i = 0; i < n; i++) {
        if (p + 3 > end) return 0;
        int op = (int)*p++;
        uint16_t path_len = pfmd_u16le(p); p += 2;
        if (p + path_len > end || path_len >= MAX_PATH) return 0;
        memcpy(rel_path, p, path_len);
        rel_path[path_len] = '\0';
        /* Convert forward slashes to backslashes */
        for (int j = 0; j < (int)path_len; j++)
            if (rel_path[j] == '/') rel_path[j] = '\\';
        p += path_len;
        if (p + 8 > end) return 0;
        uint64_t data_len = pfmd_u64le(p); p += 8;
        /* Bounds-check additively to avoid overflow in (p + data_len). */
        if (data_len > (uint64_t)(end - p)) return 0;
        const unsigned char *data = (data_len > 0) ? p : NULL;
        p += data_len;

        if (!cb(op, rel_path, data, data_len, userdata)) return 0;
    }
    return 1;
}

/* Create all intermediate directories for the parent of full_path. */
static void pfmd_ensure_parent_dirs(const char *full_path)
{
    char tmp[MAX_PATH];
    strncpy(tmp, full_path, MAX_PATH - 1);
    tmp[MAX_PATH - 1] = '\0';
    /* Start after drive letter + colon + first separator (e.g. "C:\") */
    for (char *p = tmp + 3; *p; p++) {
        if (*p == '\\') {
            *p = '\0';
            CreateDirectoryA(tmp, NULL);  /* silently ignore ERROR_ALREADY_EXISTS */
            *p = '\\';
        }
    }
}

/* Write the parent directory of full_path into out_parent (MAX_PATH). */
static void pfmd_parent_dir(const char *full_path, char *out_parent)
{
    strncpy(out_parent, full_path, MAX_PATH - 1);
    out_parent[MAX_PATH - 1] = '\0';
    char *sep = strrchr(out_parent, '\\');
    /* Don't strip the root backslash (e.g. "C:\") */
    if (sep && sep > out_parent + 2)
        *sep = '\0';
}

/* Recursively copy src directory to dst (used for backup). */
static int pfmd_copy_dir(const char *src, const char *dst)
{
    if (!CreateDirectoryA(dst, NULL) && GetLastError() != ERROR_ALREADY_EXISTS)
        return 0;

    char search[MAX_PATH];
    snprintf(search, MAX_PATH, "%s\\*", src);

    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(search, &fd);
    if (h == INVALID_HANDLE_VALUE) return 1; /* empty dir is fine */

    int ok = 1;
    do {
        if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..")) continue;
        char s[MAX_PATH], d[MAX_PATH];
        snprintf(s, MAX_PATH, "%s\\%s", src, fd.cFileName);
        snprintf(d, MAX_PATH, "%s\\%s", dst, fd.cFileName);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (!pfmd_copy_dir(s, d)) ok = 0;
        } else {
            if (!CopyFileA(s, d, FALSE)) ok = 0;
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return ok;
}

#endif /* DIR_PATCH_FORMAT_H */

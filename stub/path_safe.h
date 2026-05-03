/*
 * path_safe.h — relative-path validation shared between PatchForge stubs.
 *
 * Reject anything that could escape the install / uninstall root:
 *   - empty paths
 *   - absolute paths (leading separator)
 *   - drive-letter paths "X:..."
 *   - UNC paths "\\server\..."
 *   - any ".." path component
 *
 * Intentionally tiny + dependency-free so both stub_common.h (which is
 * full of Win32 UI machinery) and uninstaller_stub.c (which deliberately
 * does not include stub_common.h) can share one definition.
 */
#ifndef PATH_SAFE_H
#define PATH_SAFE_H

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

#endif /* PATH_SAFE_H */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>
#include "package_pin.h"

/* Linked only into the deployable, before the Dawn entry can open a database. */
static void refuse(const char *why) {
    fprintf(stderr, "native package refused: %s\n", why);
    exit(78);
}
static void *symbol(void *library, const char *name) {
    void *p = dlsym(library, name);
    if (!p) refuse(name);
    return p;
}
static int same_file(const char *a, const char *b) {
    struct stat x, y;
    return !stat(a, &x) && !stat(b, &y) && x.st_dev == y.st_dev && x.st_ino == y.st_ino;
}
__attribute__((constructor)) static void package_start(int argc, char **argv, char **envp) {
    (void)envp;
    char exe[PATH_MAX], expected[PATH_MAX], actual[PATH_MAX], extension[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    if (n < 0 || n >= (ssize_t)sizeof(exe) - 1) refuse("executable path");
    exe[n] = 0;
    char *slash = strrchr(exe, '/');
    if (!slash) refuse("release directory");
    *slash = 0;
    if (snprintf(expected, sizeof(expected), "%s/lib/libsqlite3.so.0", exe) >= (int)sizeof(expected)) refuse("library path length");
    if (!realpath(expected, actual) || strcmp(actual, expected)) refuse("private SQLite missing or symlinked");
    void *h = dlopen(expected, RTLD_NOW | RTLD_LOCAL);
    if (!h) refuse(dlerror());
    Dl_info info;
    void *version_symbol = symbol(h, "sqlite3_libversion");
    if (!dladdr(version_symbol, &info) || !realpath(info.dli_fname, actual) || strcmp(actual, expected)) refuse("SQLite outside private directory");
    /* The pinned package later dlopens the SONAME. Prove that resolves to this handle. */
    void *by_name = dlopen("libsqlite3.so.0", RTLD_NOW | RTLD_LOCAL);
    if (!by_name || symbol(by_name, "sqlite3_libversion") != version_symbol || !same_file(actual, expected)) refuse("SQLite SONAME fallback");
    int (*number)(void) = symbol(h, "sqlite3_libversion_number");
    const char *(*source)(void) = symbol(h, "sqlite3_sourceid");
    if (number() < 3051003) refuse("SQLite below 3.51.3");
    if (number() != 3054001 || strcmp(source(), PACKAGE_SQLITE_SOURCE_ID)) refuse("SQLite provenance mismatch");
    int (*option)(const char *) = symbol(h, "sqlite3_compileoption_used");
    if (!option("ENABLE_FTS5") || !option("THREADSAFE=1") || option("OMIT_LOAD_EXTENSION")) refuse("SQLite compile options");
    int (*open_db)(const char *, void **) = symbol(h, "sqlite3_open");
    int (*close_db)(void *) = symbol(h, "sqlite3_close");
    int (*prepare)(void *, const char *, int, void **, const char **) = symbol(h, "sqlite3_prepare_v2");
    int (*step)(void *) = symbol(h, "sqlite3_step");
    const unsigned char *(*text)(void *, int) = symbol(h, "sqlite3_column_text");
    int (*finalize)(void *) = symbol(h, "sqlite3_finalize");
    int (*enable)(void *, int) = symbol(h, "sqlite3_enable_load_extension");
    int (*load)(void *, const char *, const char *, char **) = symbol(h, "sqlite3_load_extension");
    void *db = NULL, *stmt = NULL;
    if (open_db(":memory:", &db)) refuse("memory probe open");
    if (prepare(db, "SELECT sqlite_version(), sqlite_source_id()", -1, &stmt, NULL) || step(stmt) != 100) refuse("engine SQL probe");
    fprintf(stderr, "sqlite_path=%s sqlite_version=%s sqlite_source_id=%s\n", actual, text(stmt, 0), text(stmt, 1));
    finalize(stmt);
    if (snprintf(extension, sizeof(extension), "%s/extensions/libsimple.so", exe) >= (int)sizeof(extension)) refuse("extension path length");
    if (enable(db, 1) || load(db, extension, "sqlite3_simple_init", NULL) || enable(db, 0)) refuse("pinned simple extension");
    if (prepare(db, "SELECT simple_query('编译器')", -1, &stmt, NULL) || step(stmt) != 100 || !strstr((const char *)text(stmt, 0), "编")) refuse("simple query probe");
    finalize(stmt);
    if (close_db(db)) refuse("memory probe close");
    /* No loader path is exported: curl starts with its normal system dependencies. */
    unsetenv("LD_LIBRARY_PATH");
    unsetenv("LD_PRELOAD");
    unsetenv("LD_AUDIT");
    const char *idle = getenv("DAWNOP_SOCKET_IDLE_SEC");
    long seconds = idle ? 0 : 60;
    if (idle) for (const char *p = idle; *p; ++p) {
        if (*p < '0' || *p > '9' || seconds > 3600) refuse("socket idle timeout");
        seconds = seconds * 10 + *p - '0';
    }
    if (seconds < 1 || seconds > 3600) refuse("socket idle timeout");
    struct timeval timeout = { .tv_sec = seconds, .tv_usec = 0 };
    if (setsockopt(0, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) && isatty(0)) refuse("socket input");
    (void)setsockopt(1, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    if (argc == 2 && !strcmp(argv[1], "--engine-info")) exit(0);
}

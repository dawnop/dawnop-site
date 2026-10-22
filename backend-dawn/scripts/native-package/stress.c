#include "sqlite3.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static void required(int rc, sqlite3 *db) {
    if (rc != SQLITE_OK) {
        fprintf(stderr, "sqlite rc=%d %s\n", rc, sqlite3_errmsg(db));
        exit(1);
    }
}
int main(int argc, char **argv) {
    if (argc != 6 || strcmp(sqlite3_libversion(), "3.54.1")) return 2;
    const char *path = argv[1], *mode = argv[2], *worker = argv[3];
    fprintf(stderr, "sqlite_source_id=%s\n", sqlite3_sourceid());
    int iterations = atoi(argv[4]), busy = 0, writes = 0, reads = 0, complete = 0, resets = 0, opens = 0;
    for (int i = 0; i < iterations; ++i) {
        sqlite3 *db = NULL;
        required(sqlite3_open(path, &db), db);
        ++opens;
        required(sqlite3_busy_timeout(db, 30), db);
        required(sqlite3_exec(db, "PRAGMA wal_autocheckpoint=0", NULL, NULL, NULL), db);
        /* Load the schema: checkpoint on an untouched C connection is a no-op. */
        required(sqlite3_exec(db, "SELECT count(*) FROM sqlite_schema", NULL, NULL, NULL), db);
        if (!strcmp(mode, "setup")) {
            required(sqlite3_exec(db, "PRAGMA journal_mode=WAL; CREATE TABLE ledger(id TEXT PRIMARY KEY, payload BLOB NOT NULL);", NULL, NULL, NULL), db);
        } else if (!strcmp(mode, "hold")) {
            required(sqlite3_exec(db, "BEGIN IMMEDIATE", NULL, NULL, NULL), db);
            FILE *ready = fopen(argv[5], "w");
            if (!ready) return 3;
            fputs("ready", ready);
            fclose(ready);
            usleep(700000);
            required(sqlite3_exec(db, "ROLLBACK", NULL, NULL, NULL), db);
        } else if (!strcmp(mode, "write")) {
            int rc = sqlite3_exec(db, "BEGIN IMMEDIATE", NULL, NULL, NULL);
            if (rc == SQLITE_BUSY || rc == SQLITE_LOCKED) { ++busy; --i; }
            else {
                required(rc, db);
                char id[128];
                snprintf(id, sizeof(id), "%s-%d", worker, i);
                sqlite3_stmt *s = NULL;
                required(sqlite3_prepare_v2(db, "INSERT INTO ledger VALUES(?, zeroblob(4096))", -1, &s, NULL), db);
                required(sqlite3_bind_text(s, 1, id, -1, SQLITE_TRANSIENT), db);
                if (sqlite3_step(s) != SQLITE_DONE) return 4;
                required(sqlite3_finalize(s), db);
                required(sqlite3_exec(db, "COMMIT", NULL, NULL, NULL), db);
                ++writes;
            }
        } else if (!strcmp(mode, "read")) {
            sqlite3_stmt *s = NULL;
            int rc = sqlite3_prepare_v2(db, "SELECT count(*) FROM ledger WHERE length(payload)!=4096", -1, &s, NULL);
            if (rc == SQLITE_BUSY || rc == SQLITE_LOCKED) ++busy;
            else {
                required(rc, db);
                rc = sqlite3_step(s);
                if (rc == SQLITE_BUSY || rc == SQLITE_LOCKED) ++busy;
                else if (rc != SQLITE_ROW || sqlite3_column_int(s, 0) != 0) return 5;
                else ++reads;
            }
            if (s) sqlite3_finalize(s);
        } else if (!strcmp(mode, "verify")) {
            sqlite3_stmt *s = NULL;
            required(sqlite3_prepare_v2(db, "PRAGMA integrity_check", -1, &s, NULL), db);
            if (sqlite3_step(s) != SQLITE_ROW || strcmp((const char *)sqlite3_column_text(s, 0), "ok")) return 6;
            required(sqlite3_finalize(s), db);
            required(sqlite3_prepare_v2(db, "SELECT count(*) FROM ledger", -1, &s, NULL), db);
            if (sqlite3_step(s) != SQLITE_ROW || sqlite3_column_int(s, 0) != atoi(argv[5])) return 7;
            required(sqlite3_finalize(s), db);
        } else {
            /* Two complete FULL attempts, then a WAL reset, on every new connection. */
            for (int j = 0; j < 3; ++j) {
                int log = -1, copied = -1;
                int rc = sqlite3_wal_checkpoint_v2(db, NULL, j == 2 ? SQLITE_CHECKPOINT_TRUNCATE : SQLITE_CHECKPOINT_FULL, &log, &copied);
                if (rc == SQLITE_BUSY || rc == SQLITE_LOCKED) ++busy;
                else {
                    required(rc, db);
                    if (j < 2 && log >= 0 && log == copied) ++complete;
                    if (j == 2 && log == 0 && copied == 0) ++resets;
                }
            }
        }
        required(sqlite3_close(db), db);
    }
    printf("{\"engine\":\"3.54.1\",\"worker\":\"%s\",\"opens\":%d,\"writes\":%d,\"reads\":%d,\"busy\":%d,\"complete\":%d,\"resets\":%d}\n", worker, opens, writes, reads, busy, complete, resets);
    return 0;
}

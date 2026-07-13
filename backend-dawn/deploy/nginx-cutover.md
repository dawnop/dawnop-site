# nginx cutover snippets (M6 strangler)

The Dawn backend runs on `127.0.0.1:8001` alongside the FastAPI backend on
`127.0.0.1:8000`. Cut individual `/api` locations over one at a time by adding a
more-specific `location` block **before** the catch-all `location /api/`. Each
cut is one block; rollback is deleting it and `nginx -s reload`.

```nginx
# ---- ported to the Dawn backend (M6) ----
# health first (smoke)
location = /api/health {
    proxy_pass http://127.0.0.1:8001;
}
# public article reads (共库只读，安全)
location = /api/articles              { proxy_pass http://127.0.0.1:8001; }
location ~ ^/api/articles/[^/]+$      { proxy_pass http://127.0.0.1:8001; }

# ---- everything else still on FastAPI ----
location /api/ {
    proxy_pass http://127.0.0.1:8000;
}
```

Order matters: nginx picks exact (`=`) and regex (`~`) matches over the prefix
`location /api/`. Keep the ported blocks above the catch-all. Verify each cut
with `curl -s https://dawnop.com/api/... | diff` against the FastAPI response
before moving to the next.

Prereqs on the server:
- `openjdk-21-jre-headless` (already present for the playground runner).
- `lib/sqlite-jdbc-*.jar` next to `backend-dawn.jar` (rsynced with the app).
- `DAWN_SIMPLE_EXT` pointing at the linux `libsimple.so` already deployed at
  `/opt/dawnop/backend/extensions/libsimple.so` (writes to `articles` need it —
  the FTS triggers use tokenize='simple').
- `DAWN_DB_PATH=/opt/dawnop/data/dawnop.db` (the same file FastAPI writes; WAL
  mode lets both processes share it).

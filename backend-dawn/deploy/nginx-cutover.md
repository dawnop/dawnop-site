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

## Phase 2 — full cutover (knives 8–12 done)

The Dawn backend now serves **every** `/api` route FastAPI does, verified by a
route-table diff, with a single exception: `POST /api/fm/upload` (the backend
proxy-upload backup path, deferred to M6.5 with WebDAV — both need binary
request bodies). So the final state inverts the default: `/api/` catch-all goes
to Dawn, with one carve-out kept on Python.

```nginx
# fm proxy-upload (multipart binary request body) stays on FastAPI until M6.5;
# the browser never hits it (it direct-uploads via /upload-token + /register)
location = /api/fm/upload {
    proxy_pass http://127.0.0.1:8000;
}

# everything else on /api → Dawn backend (M6)
location /api/ {
    proxy_pass http://127.0.0.1:8001;
}
```

`dav.dawnop.com` is a separate vhost and is untouched — WebDAV stays on Python
(M6.5). Once this is live and observed clean, `dawnop-backend` (uvicorn) can be
scaled down to serve only `/api/fm/upload` + `/dav`, or left as-is until M6.5
retires it entirely.

> **Status: executed on 2026-07-14** (this exact block is live). Verified by
> framing signature (all `/api` → Dawn's chunked responses, `/api/fm/upload` →
> uvicorn's `Content-Length`) and a self-cleaning `login → create → read →
> update → monitor → delete → 404` round-trip over HTTPS on the live store
> (article count returned to its starting value; FTS `simple` triggers fired on
> create/update/delete). Post-flip: `NRestarts=0`, ~150 MiB, zero 5xx/panic.
> uvicorn stays up for `/api/fm/upload` + one-line rollback. Only remaining step
> is retiring uvicorn after a clean observation window (blocked on M6.5 for the
> `/api/fm/upload` endpoint, or keep FastAPI running solely for it).

**Rollback** is a one-line revert: point `location /api/` back at `:8000` and
`nginx -s reload`. Because both backends share the same SQLite file (WAL) and
qiniu bucket, there is no data migration to undo — cutover and rollback are pure
routing changes.

### Recommended rollout order (each = one `nginx -s reload`, diffable)

1. `= /api/health` — smoke.
2. Public reads — `GET /api/articles*`, `/api/pages*`, `/api/tags*`, `/api/viz`,
   `/api/search`. Read-only on the shared DB, zero risk.
3. `/api/auth/*` — confirm the JWT issued by Dawn is accepted by FastAPI and
   vice-versa (same `SECRET_KEY`; already cross-verified locally).
4. Admin writes — `/api/articles` (POST/PUT/DELETE), `/api/pages`, `/api/tags`,
   `/api/viz`, `/api/settings`. Writes to `articles` need `libsimple` loaded
   (see prereqs) — the Dawn `db.connect` loads it per connection.
5. `/api/fm/*` (except `upload`) and `/api/monitor`.
6. Flip the catch-all to Dawn + keep the `= /api/fm/upload` carve-out (the block
   above). Delete the now-redundant per-endpoint blocks.

Prereqs on the server:
- `openjdk-21-jre-headless` (already present for the playground runner).
- `lib/sqlite-jdbc-*.jar` next to `backend-dawn.jar` — `deploy.sh` puts both there
  from the CI artifact (the jar's manifest Class-Path is relative to its own dir).
- `DAWN_SIMPLE_EXT` pointing at the linux `libsimple.so` already deployed at
  `/opt/dawnop/backend/extensions/libsimple.so` (writes to `articles` need it —
  the FTS triggers use tokenize='simple').
- `DAWN_DB_PATH=/opt/dawnop/data/dawnop.db` (the same file FastAPI writes; WAL
  mode lets both processes share it).

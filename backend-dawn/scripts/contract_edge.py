#!/usr/bin/env python3
"""Boundary / edge-case contract diff between the FastAPI and Dawn backends.

Fires a battery of *edge* requests (bad pagination, non-existent slugs,
malformed and injection-ish queries, auth failures, method-not-allowed, and
write requests that are *guaranteed to be rejected before any mutation*) at
both backends and diffs the HTTP status + normalized JSON body.

SAFE AGAINST THE LIVE SHARED STORE: every write case here is engineered to be
refused by validation/auth *before* the DB row or qiniu object is touched
(missing/blank title, invalid slug, dangling page_id, non-existent id, no
token, ...). No case is expected to create/update/delete real data. A case
that unexpectedly returns 2xx on a write path is flagged loudly.

Usage (run on the server so both ports are localhost):
    TOKEN=$(...form login...) python3 contract_edge.py \
        --dawn http://127.0.0.1:8001 --fast http://127.0.0.1:8000

TOKEN is an admin JWT (fetch it in the caller so creds never touch this file).
Exit code is non-zero if any case is a hard mismatch (different status, or a
non-message body difference, or an unexpected write success).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Fields whose values are wall-clock / live-metric / view-counter and therefore
# legitimately differ between two independently-serving backends. Scrubbed
# recursively before comparing bodies.
VOLATILE = {
    "views",
    "updated_at",
    "cached_at",
    "latency_ms",
    "cpu_percent",
    "load",
    "uptime",
    "mem",
    "memory",
    "disk",
    "net",
    "used",
    "used_bytes",
    "remote_space",
    "space_used",
    "generated_at",
    "now",
    "timestamp",
}


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [scrub(v) for v in x]
    return x


def req(base, method, path, headers=None, body=None, timeout=20):
    url = base + path
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - network/other, surface as sentinel
        return -1, f"__ERR__ {type(e).__name__}: {e}"


def as_json(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def is_detail_only(obj):
    return isinstance(obj, dict) and set(obj.keys()) == {"detail"}


def form_login(base, user, pw):
    data = urllib.parse.urlencode({"username": user, "password": pw}).encode()
    r = urllib.request.Request(base + "/api/auth/login", data=data, method="POST")
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read())["access_token"]


# Cases that create a REAL row on both backends (valid title, slug merely
# slugified) — they mutate the store and must only run against an isolated
# sandbox DB (--allow-mutations), never prod. Everything else here is
# non-mutating: reads, 401s (rejected before logic), validation-rejects (422/400
# before insert), and PUT/DELETE to a guaranteed-absent id (touches 0 rows).
MUTATING = {"art.create.badSlug", "art.create.upperSlug", "art.create.dashSlug"}


def build_cases(token, content_page_id):
    AUTH = {"Authorization": f"Bearer {token}"}
    # A structurally-valid JWT with a corrupted signature -> must fail verify.
    parts = token.split(".")
    tampered = (
        parts[0]
        + "."
        + parts[1]
        + "."
        + ("A" if parts[2][:1] != "A" else "B")
        + parts[2][1:]
    )
    BADSIG = {"Authorization": f"Bearer {tampered}"}
    GARBAGE = {"Authorization": "Bearer not.a.jwt"}
    LONGQ = "x" * 500

    # (name, method, path, headers, body, compare)
    #   compare: "full" (status+scrubbed body) | "status" (status only) | "write"
    #   "write": like full, but 2xx is treated as an unexpected mutation and flagged.
    C = []

    def add(*a):
        C.append(a)

    # ---- public reads: pagination bounds ----
    add("articles.ok", "GET", "/api/articles", None, None, "full")
    add("articles.page0", "GET", "/api/articles?page=0", None, None, "full")
    add("articles.pageNeg", "GET", "/api/articles?page=-1", None, None, "full")
    add("articles.pageHuge", "GET", "/api/articles?page=99999", None, None, "full")
    add("articles.size0", "GET", "/api/articles?size=0", None, None, "full")
    add("articles.sizeBig", "GET", "/api/articles?size=1000", None, None, "full")
    add("articles.sizeNeg", "GET", "/api/articles?size=-5", None, None, "full")
    add("articles.pageNaN", "GET", "/api/articles?page=abc", None, None, "full")
    add("articles.sizeNaN", "GET", "/api/articles?size=abc", None, None, "full")
    # ---- public reads: not-found ----
    add(
        "articles.slug404",
        "GET",
        "/api/articles/zzz-does-not-exist",
        None,
        None,
        "full",
    )
    add("pages.slug404", "GET", "/api/pages/zzz-nope", None, None, "full")
    add("pages.slug404.arts", "GET", "/api/pages/zzz-nope/articles", None, None, "full")
    add("tags.slug404", "GET", "/api/tags/zzz-nope", None, None, "full")
    add("viz.slug404", "GET", "/api/viz/zzz-nope", None, None, "full")
    # ---- list-page article pagination bounds ----
    add("pageArts.page0", "GET", "/api/pages/blog/articles?page=0", None, None, "full")
    add(
        "pageArts.sizeBig",
        "GET",
        "/api/pages/blog/articles?size=999",
        None,
        None,
        "full",
    )
    # ---- search: missing/empty/clamp/injection/cjk/long ----
    add("search.noQ", "GET", "/api/search", None, None, "full")
    add("search.emptyQ", "GET", "/api/search?q=", None, None, "full")
    add("search.sizeClamp", "GET", "/api/search?q=dawn&size=999", None, None, "full")
    add("search.page0", "GET", "/api/search?q=dawn&page=0", None, None, "full")
    add(
        "search.xss",
        "GET",
        "/api/search?" + urllib.parse.urlencode({"q": "<script>alert(1)</script>"}),
        None,
        None,
        "full",
    )
    add(
        "search.sqli",
        "GET",
        "/api/search?" + urllib.parse.urlencode({"q": "' OR '1'='1"}),
        None,
        None,
        "full",
    )
    add(
        "search.cjk",
        "GET",
        "/api/search?" + urllib.parse.urlencode({"q": "编译器"}),
        None,
        None,
        "full",
    )
    add(
        "search.long",
        "GET",
        "/api/search?" + urllib.parse.urlencode({"q": LONGQ}),
        None,
        None,
        "full",
    )
    add(
        "search.pct",
        "GET",
        "/api/search?" + urllib.parse.urlencode({"q": "100% done"}),
        None,
        None,
        "full",
    )
    # ---- routing / method ----
    add("route.404", "GET", "/api/nope-endpoint", None, None, "full")
    add("method.health.POST", "POST", "/api/health", None, None, "full")
    add("method.nav.DELETE", "DELETE", "/api/pages/nav", None, None, "full")

    # ---- auth failures ----
    add("me.noTok", "GET", "/api/auth/me", None, None, "full")
    add("me.garbage", "GET", "/api/auth/me", GARBAGE, None, "full")
    add("me.badsig", "GET", "/api/auth/me", BADSIG, None, "full")
    add("me.ok", "GET", "/api/auth/me", AUTH, None, "full")
    # (login is application/x-www-form-urlencoded, not JSON; the happy/interop
    #  path is covered by the auth knife, so no JSON-body login case here.)
    add("settings.noTok", "GET", "/api/settings", None, None, "full")
    add("monitor.noTok", "GET", "/api/monitor", None, None, "full")
    add("articles.admin.noTok", "GET", "/api/articles/admin", None, None, "full")
    add("pages.admin.noTok", "GET", "/api/pages/admin", None, None, "full")
    add("tags.admin.noTok", "GET", "/api/tags/admin", None, None, "full")
    add("viz.list.noTok", "GET", "/api/viz", None, None, "full")

    # ---- write REJECTION paths (must never mutate) ----
    add("art.create.noTok", "POST", "/api/articles", None, {"title": "x"}, "write")
    add("art.create.empty", "POST", "/api/articles", AUTH, {}, "write")
    add("art.create.blank", "POST", "/api/articles", AUTH, {"title": "   "}, "write")
    add(
        "art.create.badSlug",
        "POST",
        "/api/articles",
        AUTH,
        {"title": "x", "slug": "BAD SLUG"},
        "write",
    )
    add(
        "art.create.upperSlug",
        "POST",
        "/api/articles",
        AUTH,
        {"title": "x", "slug": "Upper"},
        "write",
    )
    add(
        "art.create.dashSlug",
        "POST",
        "/api/articles",
        AUTH,
        {"title": "x", "slug": "-lead"},
        "write",
    )
    add(
        "art.create.pageMissing",
        "POST",
        "/api/articles",
        AUTH,
        {"title": "x", "slug": "bad slug so also rejected", "page_id": 99999999},
        "write",
    )
    add(
        "art.update.404", "PUT", "/api/articles/99999999", AUTH, {"title": "x"}, "write"
    )
    add("art.delete.404", "DELETE", "/api/articles/99999999", AUTH, None, "write")
    if content_page_id is not None:
        # page_id points at a content page (not article_list) -> 400; slug also
        # invalid as a belt-and-suspenders guard so nothing can insert.
        add(
            "art.create.pageWrongType",
            "POST",
            "/api/articles",
            AUTH,
            {"title": "x", "slug": "BAD SLUG", "page_id": content_page_id},
            "write",
        )

    add(
        "viz.create.noTok",
        "POST",
        "/api/viz",
        None,
        {"slug": "x", "name": "n", "source": "s"},
        "write",
    )
    add("viz.create.empty", "POST", "/api/viz", AUTH, {}, "write")
    add(
        "viz.create.badSlug",
        "POST",
        "/api/viz",
        AUTH,
        {"slug": "BAD SLUG", "name": "n", "source": "s"},
        "write",
    )
    add("viz.update.404", "PUT", "/api/viz/99999999", AUTH, {"name": "n"}, "write")
    add("viz.delete.404", "DELETE", "/api/viz/99999999", AUTH, None, "write")

    add("page.create.noTok", "POST", "/api/pages", None, {"title": "x"}, "write")
    add("page.create.empty", "POST", "/api/pages", AUTH, {}, "write")
    add("page.update.404", "PUT", "/api/pages/99999999", AUTH, {"title": "x"}, "write")
    add("page.delete.404", "DELETE", "/api/pages/99999999", AUTH, None, "write")
    # reorder with only non-existent ids -> no existing row is touched; both
    # backends return the unchanged admin list. Compared as a plain no-op diff.
    add(
        "page.reorder.ghosts",
        "POST",
        "/api/pages/reorder",
        AUTH,
        {"ids": [99999999, 88888888]},
        "full",
    )

    add("tag.merge.noTok", "POST", "/api/tags/merge", None, {}, "write")
    add("tag.cleanup.noTok", "POST", "/api/tags/cleanup", None, {}, "write")
    add("tag.update.404", "PUT", "/api/tags/99999999", AUTH, {"name": "x"}, "write")
    add("tag.delete.404", "DELETE", "/api/tags/99999999", AUTH, None, "write")

    # ---- fm reads (safe) + fm write rejections ----
    add("fm.list.noTok", "GET", "/api/fm", None, None, "full")
    add("fm.list.ok", "GET", "/api/fm?path=qiniu://", AUTH, None, "full")
    add(
        "fm.search.ok",
        "GET",
        "/api/fm/search?" + urllib.parse.urlencode({"q": "x"}),
        AUTH,
        None,
        "full",
    )
    add(
        "fm.sign.404",
        "GET",
        "/api/fm/sign?" + urllib.parse.urlencode({"path": "qiniu://zzz-nope.bin"}),
        AUTH,
        None,
        "full",
    )
    add(
        "fm.stats.ok", "GET", "/api/fm/stats", AUTH, None, "status"
    )  # live qiniu usage -> status-only
    add(
        "fm.delete.noTok",
        "POST",
        "/api/fm/delete",
        None,
        {"path": "qiniu://x"},
        "write",
    )
    add(
        "fm.mkfolder.noTok",
        "POST",
        "/api/fm/create-folder",
        None,
        {"path": "qiniu://x"},
        "write",
    )
    add(
        "fm.uptok.noTok",
        "POST",
        "/api/fm/upload-token",
        None,
        {"path": "qiniu://x"},
        "write",
    )
    return C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dawn", required=True)
    ap.add_argument("--fast", required=True)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--allow-mutations",
        action="store_true",
        help="run row-creating cases too — ONLY against a sandbox DB, never prod",
    )
    args = ap.parse_args()

    token = os.environ.get("TOKEN", "").strip()
    if not token:
        print("FATAL: TOKEN env not set (admin JWT required)", file=sys.stderr)
        return 2

    # find a content-type page id (for the wrong-page-type rejection case)
    content_page_id = None
    st, body = req(
        args.fast, "GET", "/api/pages/admin", {"Authorization": f"Bearer {token}"}
    )
    j = as_json(body)
    if isinstance(j, list):
        for p in j:
            if p.get("type") == "content" or p.get("kind") == "content":
                content_page_id = p.get("id")
                break

    cases = build_cases(token, content_page_id)

    tallies = {
        "EXACT": 0,
        "SCRUBBED": 0,
        "ACCEPTED": 0,
        "STATUS_MISMATCH": 0,
        "BODY_MISMATCH": 0,
        "UNEXPECTED_WRITE": 0,
        "ERROR": 0,
    }
    problems = []
    skipped = 0

    for name, method, path, headers, body, mode in cases:
        if name in MUTATING and not args.allow_mutations:
            skipped += 1
            continue
        ds, dt = req(args.dawn, method, path, headers, body)
        fs, ft = req(args.fast, method, path, headers, body)
        dj, fj = as_json(dt), as_json(ft)

        if ds == -1 or fs == -1:
            verdict = "ERROR"
        elif mode == "status":
            verdict = "EXACT" if ds == fs else "STATUS_MISMATCH"
        else:
            if ds != fs:
                verdict = "STATUS_MISMATCH"
            elif dt == ft:
                verdict = "EXACT"
            elif dj is not None and fj is not None and scrub(dj) == scrub(fj):
                verdict = "SCRUBBED"
            elif is_detail_only(dj) and is_detail_only(fj):
                # same status, both {"detail": ...} but different message -> the
                # accepted error-message rationalization (frontend keys on status)
                verdict = "ACCEPTED"
            else:
                verdict = "BODY_MISMATCH"
            # a write path that returned 2xx means it may have mutated: flag hard
            if mode == "write" and 200 <= (ds if ds > 0 else 0) < 300:
                verdict = "UNEXPECTED_WRITE"
            if mode == "write" and 200 <= (fs if fs > 0 else 0) < 300:
                verdict = "UNEXPECTED_WRITE"

        tallies[verdict] += 1
        flag = verdict in (
            "STATUS_MISMATCH",
            "BODY_MISMATCH",
            "UNEXPECTED_WRITE",
            "ERROR",
        )
        mark = {"EXACT": "OK ", "SCRUBBED": "OK~", "ACCEPTED": "OK≈"}.get(
            verdict, "XX "
        )
        line = f"{mark} {verdict:16s} {name:24s} {method:6s} {path[:48]:48s} dawn={ds} fast={fs}"
        if flag or args.verbose:
            print(line)
        if flag:
            problems.append((name, method, path, ds, dt, fs, ft, verdict))

    print("\n=== tally ===")
    for k, v in tallies.items():
        print(f"  {k:18s} {v}")
    if skipped:
        print(
            f"  {'SKIPPED(mut)':18s} {skipped}  (row-creating; run with --allow-mutations on a sandbox)"
        )

    if problems:
        print("\n=== problem detail ===")
        for name, method, path, ds, dt, fs, ft, verdict in problems:
            print(f"\n[{verdict}] {name}  {method} {path}")
            print(f"  dawn  {ds}: {dt[:400]}")
            print(f"  fast  {fs}: {ft[:400]}")

    hard = (
        tallies["STATUS_MISMATCH"]
        + tallies["BODY_MISMATCH"]
        + tallies["UNEXPECTED_WRITE"]
        + tallies["ERROR"]
    )
    print(f"\nhard mismatches: {hard}")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())

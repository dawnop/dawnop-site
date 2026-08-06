#!/usr/bin/env python3
"""Boundary / edge-case contract: bad input, auth failures, rejected writes.

Fires the edge battery — pagination out of range, non-existent slugs, malformed
and injection-ish queries, missing/garbage/tampered tokens, method-not-allowed,
and write requests that must be refused before anything is touched — at ONE
backend and compares status + body with golden/edge.json.

Against the pinned fixture (contract_fixture.py) every answer here is
deterministic, including the three cases that DO create a row: they run against
a disposable fixture database, and their recorded value drops only the two
wall-clock stamps SQLite fills in. This script is no longer safe to point at a
live store, and no longer needs to be — the env fingerprint would reject the
golden anyway.

Normally driven by contract_run.py. Standalone:

    TOKEN=... python3 contract_edge.py --base http://127.0.0.1:18001 [--record]
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from contract_golden import TRANSPORT_STATUS, Golden, transport_error

# urllib's no_proxy matching is suffix-based and does not exempt 127.0.0.1 from
# a `no_proxy=127.*` export; go direct.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Nothing is scrubbed globally. The two stamps below are dropped from the three
# row-creating cases only: SQLite fills created_at/updated_at with the wall
# clock at insert time, which is the one value in this script that a golden
# cannot own. Everything else in those responses — the generated id, the
# slugified slug, published, page_id — is pinned.
ROW_STAMPS = {"created_at", "updated_at"}


def req(base, method, path, headers=None, body=None, timeout=30):
    url = base + path
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with OPENER.open(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - transport failure is a case failure
        return TRANSPORT_STATUS, transport_error(e)


def as_json(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


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

    C = []

    def add(name, method, path, headers=None, body=None, kind="read"):
        """kind: read | reject (must not answer 2xx) | create (drops row stamps)"""
        C.append((name, method, path, headers, body, kind))

    # ---- public reads: pagination bounds ----
    add("articles.ok", "GET", "/api/articles")
    add("articles.page0", "GET", "/api/articles?page=0")
    add("articles.pageNeg", "GET", "/api/articles?page=-1")
    add("articles.pageHuge", "GET", "/api/articles?page=99999")
    add("articles.size0", "GET", "/api/articles?size=0")
    add("articles.sizeBig", "GET", "/api/articles?size=1000")
    add("articles.sizeNeg", "GET", "/api/articles?size=-5")
    add("articles.pageNaN", "GET", "/api/articles?page=abc")
    add("articles.sizeNaN", "GET", "/api/articles?size=abc")
    # ---- public reads: not-found ----
    add("articles.slug404", "GET", "/api/articles/zzz-does-not-exist")
    add("pages.slug404", "GET", "/api/pages/zzz-nope")
    add("pages.slug404.arts", "GET", "/api/pages/zzz-nope/articles")
    add("tags.slug404", "GET", "/api/tags/zzz-nope")
    add("viz.slug404", "GET", "/api/viz/zzz-nope")
    # ---- list-page article pagination bounds (fixture page slug: blog) ----
    add("pageArts.page0", "GET", "/api/pages/blog/articles?page=0")
    add("pageArts.sizeBig", "GET", "/api/pages/blog/articles?size=999")
    # ---- search: missing/empty/clamp/injection/cjk/long ----
    add("search.noQ", "GET", "/api/search")
    add("search.emptyQ", "GET", "/api/search?q=")
    add("search.sizeClamp", "GET", "/api/search?q=dawn&size=999")
    add("search.page0", "GET", "/api/search?q=dawn&page=0")
    add(
        "search.xss",
        "GET",
        "/api/search?" + urllib.parse.urlencode({"q": "<script>alert(1)</script>"}),
    )
    add(
        "search.sqli",
        "GET",
        "/api/search?" + urllib.parse.urlencode({"q": "' OR '1'='1"}),
    )
    add("search.cjk", "GET", "/api/search?" + urllib.parse.urlencode({"q": "编译器"}))
    add("search.long", "GET", "/api/search?" + urllib.parse.urlencode({"q": LONGQ}))
    add(
        "search.pct", "GET", "/api/search?" + urllib.parse.urlencode({"q": "100% done"})
    )
    # ---- routing / method ----
    add("route.404", "GET", "/api/nope-endpoint")
    add("method.health.POST", "POST", "/api/health")
    add("method.nav.DELETE", "DELETE", "/api/pages/nav")

    # ---- auth failures ----
    add("me.noTok", "GET", "/api/auth/me")
    add("me.garbage", "GET", "/api/auth/me", GARBAGE)
    add("me.badsig", "GET", "/api/auth/me", BADSIG)
    add("me.ok", "GET", "/api/auth/me", AUTH)
    # (login is form-encoded, not JSON; the happy path is covered by contract_run
    #  logging in before any of this runs.)
    add("settings.noTok", "GET", "/api/settings")
    add("monitor.noTok", "GET", "/api/monitor")
    add("articles.admin.noTok", "GET", "/api/articles/admin")
    add("pages.admin.noTok", "GET", "/api/pages/admin")
    add("tags.admin.noTok", "GET", "/api/tags/admin")
    add("viz.list.noTok", "GET", "/api/viz")
    # 编辑页取单行的两个端点：鉴权、缺行、非整数 id
    add("pages.adminGet.noTok", "GET", "/api/pages/admin/1")
    add("pages.adminGet.404", "GET", "/api/pages/admin/99999999", AUTH)
    add("pages.adminGet.nan", "GET", "/api/pages/admin/abc", AUTH)
    add("viz.adminGet.noTok", "GET", "/api/viz/admin/1")
    add("viz.adminGet.404", "GET", "/api/viz/admin/99999999", AUTH)
    add("viz.adminGet.nan", "GET", "/api/viz/admin/abc", AUTH)

    # ---- write REJECTION paths (must never answer 2xx) ----
    add("art.create.noTok", "POST", "/api/articles", None, {"title": "x"}, "reject")
    add("art.create.empty", "POST", "/api/articles", AUTH, {}, "reject")
    add("art.create.blank", "POST", "/api/articles", AUTH, {"title": "   "}, "reject")
    # These three DO create a row. In the differential era they were skipped by
    # default (they mutated the shared production store); against a disposable
    # fixture they are the only cases that pin slugify's answer, so they run.
    add(
        "art.create.badSlug",
        "POST",
        "/api/articles",
        AUTH,
        {"title": "x", "slug": "BAD SLUG"},
        "create",
    )
    add(
        "art.create.upperSlug",
        "POST",
        "/api/articles",
        AUTH,
        {"title": "x", "slug": "Upper"},
        "create",
    )
    add(
        "art.create.dashSlug",
        "POST",
        "/api/articles",
        AUTH,
        {"title": "x", "slug": "-lead"},
        "create",
    )
    add(
        "art.create.pageMissing",
        "POST",
        "/api/articles",
        AUTH,
        {"title": "x", "slug": "no-such-page", "page_id": 99999999},
        "reject",
    )
    add(
        "art.update.404",
        "PUT",
        "/api/articles/99999999",
        AUTH,
        {"title": "x"},
        "reject",
    )
    add("art.delete.404", "DELETE", "/api/articles/99999999", AUTH, None, "reject")
    if content_page_id is not None:
        # page_id points at a content page, not an article_list -> 400
        add(
            "art.create.pageWrongType",
            "POST",
            "/api/articles",
            AUTH,
            {"title": "x", "slug": "wrong-page-type", "page_id": content_page_id},
            "reject",
        )

    add(
        "viz.create.noTok",
        "POST",
        "/api/viz",
        None,
        {"slug": "x", "name": "n", "source": "s"},
        "reject",
    )
    add("viz.create.empty", "POST", "/api/viz", AUTH, {}, "reject")
    add(
        "viz.create.badSlug",
        "POST",
        "/api/viz",
        AUTH,
        {"slug": "BAD SLUG", "name": "n", "source": "s"},
        "reject",
    )
    add("viz.update.404", "PUT", "/api/viz/99999999", AUTH, {"name": "n"}, "reject")
    # slug already worn by viz #1 -> 409, and nothing else in the patch applies
    add(
        "viz.update.slugTaken",
        "PUT",
        "/api/viz/2",
        AUTH,
        {"slug": "counter-demo"},
        "reject",
    )
    add("viz.delete.404", "DELETE", "/api/viz/99999999", AUTH, None, "reject")

    add("page.create.noTok", "POST", "/api/pages", None, {"title": "x"}, "reject")
    add("page.create.empty", "POST", "/api/pages", AUTH, {}, "reject")
    add("page.update.404", "PUT", "/api/pages/99999999", AUTH, {"title": "x"}, "reject")
    add("page.delete.404", "DELETE", "/api/pages/99999999", AUTH, None, "reject")
    # reorder with only non-existent ids -> no existing row is touched; the
    # backend returns the unchanged admin list. Compared as a plain no-op.
    add(
        "page.reorder.ghosts",
        "POST",
        "/api/pages/reorder",
        AUTH,
        {"ids": [99999999, 88888888]},
    )

    add("tag.merge.noTok", "POST", "/api/tags/merge", None, {}, "reject")
    add("tag.cleanup.noTok", "POST", "/api/tags/cleanup", None, {}, "reject")
    add("tag.update.404", "PUT", "/api/tags/99999999", AUTH, {"name": "x"}, "reject")
    add("tag.delete.404", "DELETE", "/api/tags/99999999", AUTH, None, "reject")

    # ---- fm reads (no object storage touched) + fm write rejections ----
    add("fm.list.noTok", "GET", "/api/fm")
    add("fm.list.ok", "GET", "/api/fm?path=qiniu://", AUTH)
    # filter，不是 q——发 q 会被忽略成「列全部」，测不到过滤路径。
    add(
        "fm.search.ok",
        "GET",
        "/api/fm/search?" + urllib.parse.urlencode({"filter": "x"}),
        AUTH,
    )
    add(
        "fm.sign.404",
        "GET",
        "/api/fm/sign?" + urllib.parse.urlencode({"path": "qiniu://zzz-nope.bin"}),
        AUTH,
    )
    # path omitted entirely. Dawn's qparam() defaults a missing param to "" and
    # falls through to the not-found path (404); FastAPI declares it a required
    # Query and answers 422 before the handler. The differential harness carried
    # a written rationale for accepting that pair; the golden simply records the
    # backend under test, so pointing this script at FastAPI will show these four
    # as diffs. Both refuse, nothing mutates, the frontend keys on status.
    add("fm.sign.noPath", "GET", "/api/fm/sign", AUTH)
    add("fm.content.noPath", "GET", "/api/fm/content", AUTH)
    add("fm.preview.noPath", "GET", "/api/fm/preview", AUTH)
    add("fm.download.noPath", "GET", "/api/fm/download", AUTH)
    add(
        "fm.delete.noTok",
        "POST",
        "/api/fm/delete",
        None,
        {"path": "qiniu://x"},
        "reject",
    )
    add(
        "fm.mkfolder.noTok",
        "POST",
        "/api/fm/create-folder",
        None,
        {"path": "qiniu://x"},
        "reject",
    )
    add(
        "fm.uptok.noTok",
        "POST",
        "/api/fm/upload-token",
        None,
        {"path": "qiniu://x"},
        "reject",
    )
    # copying a directory onto its own parent collides with itself -> 409 before
    # anything is written and before the object store is touched. This is the
    # only fm write path whose 409 the fixture can reach without credentials, and
    # it is what pins the name-collision status code (api_fm.conflict_or).
    add(
        "fm.copy.nameTaken",
        "POST",
        "/api/fm/copy",
        AUTH,
        {
            "path": "qiniu://",
            "destination": "qiniu://",
            "sources": ["qiniu://empty-dir"],
        },
        "reject",
    )
    return C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()
    B = args.base

    token = os.environ.get("TOKEN", "").strip()
    if not token:
        print("FATAL: TOKEN env not set (admin JWT required)", file=sys.stderr)
        return 2

    g = Golden("edge", record=args.record)
    if g.fatal:
        return g.finish()

    # a content-type page id, for the wrong-page-type rejection case
    content_page_id = None
    _, body = req(B, "GET", "/api/pages/admin", {"Authorization": f"Bearer {token}"})
    j = as_json(body)
    if isinstance(j, list):
        for p in j:
            if p.get("type") == "content":
                content_page_id = p.get("id")
                break

    bad_writes = []
    for name, method, path, headers, body, kind in build_cases(token, content_page_id):
        st, text = req(B, method, path, headers, body)
        parsed = as_json(text)
        value = {"method": method, "path": path, "status": st}
        if kind == "create" and isinstance(parsed, dict):
            value["body"] = {k: v for k, v in parsed.items() if k not in ROW_STAMPS}
        else:
            value["body"] = parsed if parsed is not None else text
        g.case(name, value)
        # A rejection path that answers 2xx is a failure even while recording —
        # otherwise --record would quietly bake a broken guard into the contract.
        if kind == "reject" and 200 <= st < 300:
            bad_writes.append(
                f"{name}: {method} {path} answered {st} on a rejection path"
            )

    # /api/fm/stats reports live bucket usage from qiniu; with no credentials the
    # HMAC signer refuses an empty key, and with credentials the number moves.
    g.skip("fm.stats.ok", "needs QINIU_* credentials (live bucket usage)")

    rc = g.finish()
    if bad_writes:
        print("\n=== UNEXPECTED WRITE ===")
        for line in bad_writes:
            print(f"  {line}")
        rc = rc or 1
    return rc


if __name__ == "__main__":
    sys.exit(main())

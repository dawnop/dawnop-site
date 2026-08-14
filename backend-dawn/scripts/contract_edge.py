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

The last block is a different shape: successful writes, each observed twice
(read, write, read) so the golden can hold what the stamps DID rather than
having to drop them. See `stamp_cases` below and contract_golden.stamp_facts.

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

import contract_fixture
from contract_golden import TRANSPORT_STATUS, Golden, stamp_facts, transport_error

# urllib's no_proxy matching is suffix-based and does not exempt 127.0.0.1 from
# a `no_proxy=127.*` export; go direct.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Nothing is scrubbed globally. The two stamps below are dropped from the bodies
# of the cases that write a row: SQLite fills created_at/updated_at with the wall
# clock, which is the one value in this script that a golden cannot own.
# Everything else in those responses — the generated id, the slugified slug,
# published, page_id — is pinned.
#
# Dropping them from the BODY is not the same as being blind to them: the cases
# in `stamp_cases` read the row on both sides of the write and record how the
# stamps moved. That relation is what the golden owns instead of the value.
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


# ---- one case, two observations --------------------------------------------
#
# Everything above fires one request and records the answer. That shape cannot
# see a stamp: `updated_at` is wall clock, so it has to be dropped, and dropping
# it is what left the whole updated_at family uncovered here.
#
# These cases read the row, write, and read it again, then record the RELATION
# between the two readings (contract_golden.stamp_facts) instead of either
# value. The write's own response body is still recorded — with the same two
# stamps dropped — because the rest of it (slug, tags, nav_order, page_id) is
# the other half of what a successful PUT promises.


def drop_stamps(value):
    """The response body with every wall-clock field removed, at any depth."""
    if isinstance(value, list):
        return [drop_stamps(item) for item in value]
    if isinstance(value, dict):
        return {k: drop_stamps(v) for k, v in value.items() if k not in ROW_STAMPS}
    return value


def read_row(base, path, auth):
    """One observation: the row as the admin API renders it, or None."""
    status, text = req(base, "GET", path, auth)
    parsed = as_json(text)
    return parsed if status == 200 and isinstance(parsed, dict) else None


def two_shot(g, base, auth, name, read_path, write, seeded, watch=()):
    """Read, write, read. `write` is (method, path, headers, body) or a callable
    taking the first reading and returning one — a no-op re-save has to echo
    back exactly what it just read.

    `watch` names the non-stamp fields whose before/after pair is recorded
    literally; those are ordinary values (views, nav_order, page_id) and a
    golden can own them. It is what keeps a "stamp did not move" case honest:
    without it, a write that did nothing at all would look the same.
    """
    before = read_row(base, read_path, auth)
    if before is None:
        # No first observation, so there is nothing to write against and nothing
        # to compare. Recorded as a case failure rather than raised: a run that
        # dies here would take the remaining cases with it, and the golden's own
        # "case did not run" guard is the better place for that to surface.
        g.case(name, {"read": read_path, "error": "no pre-write observation"})
        return
    method, path, headers, body = write(before) if callable(write) else write
    status, text = req(base, method, path, headers, body)
    after = read_row(base, read_path, auth)
    parsed = as_json(text)
    g.case(
        name,
        {
            "write": {"method": method, "path": path, "status": status},
            "body": drop_stamps(parsed) if parsed is not None else text,
            "read": read_path,
            "stamps": stamp_facts(before, after, seeded),
            "fields": {k: [(before or {}).get(k), (after or {}).get(k)] for k in watch},
        },
    )


def article_resave_body(before):
    """Every editable article field, exactly as it was just read.

    `created_at` is left out on purpose: the API takes it through
    `datetime(?)`, which normalizes the ISO spelling this read back into a
    different stored string, so echoing it would make the save a real edit.
    Tags arrive as objects and go back as names.
    """
    return {
        "title": before["title"],
        "slug": before["slug"],
        "summary": before["summary"],
        "content": before["content"],
        "published": before["published"],
        "auto_title": before["auto_title"],
        "page_id": before["page_id"],
        "tags": [t["name"] for t in before["tags"]],
    }


def page_resave_body(before):
    """Every editable page field, as read. `path` is derived, not stored."""
    return {
        k: before[k]
        for k in (
            "title",
            "slug",
            "type",
            "description",
            "content",
            "auto_title",
            "nav_visible",
            "nav_order",
        )
    }


def viz_resave_body(before):
    return {k: before[k] for k in ("slug", "name", "source", "compiled", "style")}


def stamp_cases(g, base, auth):
    """The write cases whose subject is the timestamp, not the status code.

    Each one names the issue it holds down. They run last because they mutate
    rows the read cases above have already been compared against, and they pick
    a different row per case so every one of them starts from the planted stamp
    rather than from whatever the previous case left behind.
    """
    print("\n== writes observed twice (updated_at semantics) ==")

    # --- a real edit advances the stamp. The control for everything below: if
    # these read "unchanged" too, the harness is measuring nothing.
    two_shot(
        g,
        base,
        auth,
        "art.put.edit",
        "/api/articles/admin/6",
        ("PUT", "/api/articles/6", auth, {"summary": "edited by the contract run"}),
        contract_fixture.seeded_article_stamps(6),
        watch=("summary",),
    )
    two_shot(
        g,
        base,
        auth,
        "page.put.edit",
        "/api/pages/admin/3",
        ("PUT", "/api/pages/3", auth, {"description": "edited by the contract run"}),
        contract_fixture.seeded_page_stamps(3),
        watch=("description",),
    )
    two_shot(
        g,
        base,
        auth,
        "viz.put.edit",
        "/api/viz/admin/2",
        ("PUT", "/api/viz/2", auth, {"name": "Chart demo, edited"}),
        contract_fixture.seeded_viz_stamps(2),
        watch=("name",),
    )

    # --- #266: opening a row and pressing save without changing anything is not
    # an edit. Without this the stamp advanced and the row jumped to the top of
    # the admin list, which is ordered by it.
    two_shot(
        g,
        base,
        auth,
        "art.put.noop",
        "/api/articles/admin/10",
        lambda before: ("PUT", "/api/articles/10", auth, article_resave_body(before)),
        contract_fixture.seeded_article_stamps(10),
        watch=("title", "slug", "tags"),
    )
    two_shot(
        g,
        base,
        auth,
        "page.put.noop",
        "/api/pages/admin/4",
        lambda before: ("PUT", "/api/pages/4", auth, page_resave_body(before)),
        contract_fixture.seeded_page_stamps(4),
        watch=("title", "slug", "nav_order"),
    )
    two_shot(
        g,
        base,
        auth,
        "viz.put.noop",
        "/api/viz/admin/1",
        lambda before: ("PUT", "/api/viz/1", auth, viz_resave_body(before)),
        contract_fixture.seeded_viz_stamps(1),
        watch=("slug", "name"),
    )
    # tags are a set: re-saving the same two in the other order is still a no-op.
    # Article 1 carries exactly two, which is the smallest case that can tell an
    # order-blind signature from an order-sensitive one.
    two_shot(
        g,
        base,
        auth,
        "art.put.tagsReordered",
        "/api/articles/admin/1",
        lambda before: (
            "PUT",
            "/api/articles/1",
            auth,
            {"tags": [t["name"] for t in reversed(before["tags"])]},
        ),
        contract_fixture.seeded_article_stamps(1),
        watch=("tags",),
    )

    # --- #262: a view is not an edit. The write here is an anonymous public
    # read (the counter only moves for one), and `views` in the watch list is
    # what proves the request did land.
    two_shot(
        g,
        base,
        auth,
        "art.view.doesNotTouchStamp",
        "/api/articles/admin/2",
        ("GET", "/api/articles/latex-in-markdown", None, None),
        contract_fixture.seeded_article_stamps(2),
        watch=("views",),
    )

    # --- #264: reordering the nav writes nav_order on every page named, and
    # nothing else. Page 5 is untouched by the cases above, so its reading
    # starts from the planted stamp.
    two_shot(
        g,
        base,
        auth,
        "page.reorder.doesNotTouchStamp",
        "/api/pages/admin/5",
        ("POST", "/api/pages/reorder", auth, {"ids": [5, 4, 3]}),
        contract_fixture.seeded_page_stamps(5),
        watch=("nav_order",),
    )

    # --- #264, the other half: deleting a list page unbinds its articles.
    # Losing a page is not an edit of the article, so article 9's stamp holds
    # while its page_id goes null. Last, because it removes page 5.
    two_shot(
        g,
        base,
        auth,
        "page.delete.unbindsWithoutTouchingStamp",
        "/api/articles/admin/9",
        ("DELETE", "/api/pages/5", auth, None),
        contract_fixture.seeded_article_stamps(9),
        watch=("page_id",),
    )


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
    # Addressing is lossless: `qiniu://<path>` names the row spelled exactly
    # that way. Every case below aims at a real fixture path plus one space,
    # which is a DIFFERENT path — so the answers are "empty directory" and
    # "not found", not `docs` and `docs/notes.txt`. paths.fm_split used to trim
    # the whole path and collapse the two spellings onto one row, which is how
    # a delete aimed at "a.txt " removed "a.txt"; these four are the wire-level
    # tripwire for that. Note that they are only recordable *because* of the
    # fix: under the old behaviour sign/download answered with a signed URL
    # carrying a wall-clock expiry, which no golden can own.
    add(
        "fm.list.trailingSpace",
        "GET",
        "/api/fm?" + urllib.parse.urlencode({"path": "qiniu://docs "}),
        AUTH,
    )
    add(
        "fm.list.prefixSpace",
        "GET",
        "/api/fm?" + urllib.parse.urlencode({"path": "qiniu:// docs"}),
        AUTH,
    )
    add(
        "fm.sign.trailingSpace",
        "GET",
        "/api/fm/sign?" + urllib.parse.urlencode({"path": "qiniu://docs/notes.txt "}),
        AUTH,
    )
    add(
        "fm.download.trailingSpace",
        "GET",
        "/api/fm/download?"
        + urllib.parse.urlencode({"path": "qiniu://docs/notes.txt "}),
        AUTH,
    )
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

    stamp_cases(g, B, {"Authorization": f"Bearer {token}"})

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

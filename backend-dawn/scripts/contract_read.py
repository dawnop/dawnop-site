#!/usr/bin/env python3
"""READ-path contract: every GET endpoint against a golden recording.

Hits the public and admin-read GETs on ONE backend and compares status + body
with the recording in golden/read.json. Detail endpoints are still driven by
slugs discovered from the list endpoints — against the pinned fixture those are
deterministic, and if a list endpoint breaks and returns nothing, the detail
cases disappear and the golden's case-set guard fails the run.

READ-ONLY except for the documented view counter: an anonymous GET of a
published article bumps `views`, so the fixture database is rebuilt before each
script (contract_run.py) and the bumped values are themselves pinned.

Normally driven by contract_run.py, which seeds the fixture, boots the backend
and passes TOKEN. Standalone:

    TOKEN=... python3 contract_read.py --base http://127.0.0.1:18001 [--record]
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from contract_golden import TRANSPORT_STATUS, Golden, transport_error

# Direct to 127.0.0.1: urllib's no_proxy matching is suffix-based, so the usual
# `no_proxy=127.*` export does not exempt localhost and every request would go
# through a local proxy.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Fields scrubbed before comparison.
#
# The old list had 26 entries. It was sized for the differential era, where two
# independent processes served the same request microseconds apart: view
# counters raced, SQLAlchemy's onupdate stamped its own wall clock, and every
# live metric differed by definition. Against a golden that reasoning is gone —
# one backend, one pinned fixture, values that come from rows this repo wrote.
#
# What was checked, one field at a time, before deleting it:
#   views          -> comes from the fixture row; the anonymous-read bump is
#                     deterministic because the DB is reseeded per script and the
#                     case order is fixed. Now PINNED (a bump that stopped
#                     happening is a real regression the old list hid).
#   updated_at,
#   created_at     -> fixture literals. api_public deliberately does not touch
#                     updated_at on a view. PINNED.
#   last_modified  -> fm entries; repo_fm derives it from files.updated_at via
#                     LocalDateTime in the *system* zone. Not wall-clock volatile,
#                     environment volatile: it differed by 28800 between this
#                     laptop (+08) and a UTC runner. Fixed by pinning TZ=UTC in
#                     contract_run.py and recording it in the env fingerprint,
#                     not by scrubbing the field. PINNED.
#   cached_at, generated_at, now, timestamp, latency_ms, cpu*, load, uptime,
#   mem*, disk, net, used*, free, available, rss, traffic, points, series,
#   remote_space, space_used
#                  -> every one of these lives under /api/monitor or /api/fm/stats.
#                     Those two are handled explicitly (a shape assertion and a
#                     named skip), so the entries scrubbed nothing anywhere else.
#                     Grepped the wire builders (jsonx/repo_*/api_*) to confirm
#                     no other endpoint emits them.
#
# Empty on purpose: a body diff is now a body diff. Adding an entry here means
# admitting a field cannot be pinned — say why, in this comment, next to it.
VOLATILE: set = set()


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [scrub(v) for v in x]
    return x


def skeleton(x):
    """Types and key names only — the shape assertion for live-metric endpoints."""
    if isinstance(x, dict):
        return {k: skeleton(v) for k, v in sorted(x.items())}
    if isinstance(x, list):
        return ["<list>", skeleton(x[0]) if x else None]
    return type(x).__name__


def req(base, path, token=None, timeout=30):
    url = base + path
    hdrs = {}
    if token:
        hdrs["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, method="GET", headers=hdrs)
    try:
        with OPENER.open(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - transport failure is a case failure
        return TRANSPORT_STATUS, transport_error(e)


def body_norm(text):
    try:
        return scrub(json.loads(text))
    except ValueError:
        return text


def ids_from(listing, key="items", limit=8):
    """行 id（整数），给按 id 取单行的 admin 端点用。"""
    return slugs_from(listing, key=key, field="id", limit=limit)


def slugs_from(listing, key="items", field="slug", limit=8):
    try:
        data = json.loads(listing)
    except ValueError:
        return []
    items = data.get(key, data) if isinstance(data, dict) else data
    out = []
    for it in items or []:
        if isinstance(it, dict) and it.get(field):
            out.append(it[field])
        if len(out) >= limit:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--base", required=True, help="backend under test, e.g. http://127.0.0.1:18001"
    )
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()
    B = args.base
    tok = os.environ.get("TOKEN")
    if not tok:
        print("FATAL: TOKEN env not set (admin JWT required)", file=sys.stderr)
        return 2

    g = Golden("read", record=args.record)
    if g.fatal:
        return g.finish()

    def case(label, path, token=None):
        st, body = req(B, path, token)
        g.case(label, {"path": path, "status": st, "body": body_norm(body)})

    print("\n== health / public lists ==")
    case("health", "/api/health")
    case("articles.list", "/api/articles?page=1&size=20")
    case("articles.p2", "/api/articles?page=2&size=5")
    case("articles.tag", "/api/articles?page=1&size=10&tag=dawn")
    case("pages.nav", "/api/pages/nav")
    case("tags.list", "/api/tags")

    print("\n== search (multiple queries) ==")
    for q in ("dawn", "后端", "latex", "zzz-no-such-term", "a"):
        case(f"search[{q}]", "/api/search?q=" + urllib.parse.quote(q) + "&size=10")

    print("\n== article details (slugs from the list endpoint) ==")
    _, list_txt = req(B, "/api/articles?page=1&size=50")
    for slug in slugs_from(list_txt, "items"):
        case("article:" + slug, "/api/articles/" + urllib.parse.quote(slug))

    print("\n== page details + list-page articles ==")
    _, nav_txt = req(B, "/api/pages/nav")
    try:
        nav = json.loads(nav_txt)
        pslugs = [p["slug"] for p in nav if isinstance(p, dict) and p.get("slug")]
    except ValueError:
        pslugs = []
    for slug in pslugs[:10]:
        case("page:" + slug, "/api/pages/" + urllib.parse.quote(slug))
        case(
            "page.articles:" + slug,
            "/api/pages/" + urllib.parse.quote(slug) + "/articles?page=1&size=10",
        )

    print("\n== tag details ==")
    _, tags_txt = req(B, "/api/tags")
    try:
        tag_rows = json.loads(tags_txt)
    except ValueError:
        tag_rows = []
    for t in tag_rows if isinstance(tag_rows, list) else []:
        if isinstance(t, dict) and t.get("slug"):
            case("tag:" + t["slug"], "/api/tags/" + urllib.parse.quote(t["slug"]))

    print("\n== authed reads (admin) ==")
    case("auth.me", "/api/auth/me", tok)
    case("viz.list", "/api/viz", tok)
    case("articles.admin", "/api/articles/admin?page=1&size=20", tok)
    case("pages.admin", "/api/pages/admin", tok)
    case("tags.admin", "/api/tags/admin", tok)
    case("settings", "/api/settings", tok)
    case("fm.root", "/api/fm?path=" + urllib.parse.quote("qiniu://"), tok)
    case("fm.dir", "/api/fm?path=" + urllib.parse.quote("qiniu://docs"), tok)
    # 参数名是 filter，不是 q（两侧一致：serve.py 的 filter=Query("")、api_fm.dawn 的
    # qparam(req, "filter")）。这里原本发的是 q=a，被两侧一并忽略，filter 取默认空串
    # → 回的是未过滤的整个列表 → 搜索过滤从未被测到。
    case(
        "fm.search",
        "/api/fm/search?path=" + urllib.parse.quote("qiniu://") + "&filter=a",
        tok,
    )
    case(
        "fm.search.deep",
        "/api/fm/search?path=" + urllib.parse.quote("qiniu://") + "&filter=a&deep=1",
        tok,
    )
    # /api/fm/stats asks qiniu how much space the bucket uses. With no
    # credentials the HMAC signer refuses an empty key before any request goes
    # out; with credentials the answer is a live number that cannot be pinned.
    # Neither state is worth recording, so the case is a named skip.
    g.skip("fm.stats", "needs QINIU_* credentials (live bucket usage)")

    print("\n== admin single-row reads (edit forms) ==")
    # 编辑页原本拉整张 admin 列表再 find 一行；这两个端点是那条路的替代品。
    # id 从上面的列表端点里取，跟其他 detail 用例同一套路（fixture 固定 → 确定）。
    _, padmin_txt = req(B, "/api/pages/admin", tok)
    for pid in ids_from(padmin_txt):
        case(f"pages.admin:{pid}", f"/api/pages/admin/{pid}", tok)

    print("\n== viz details ==")
    _, viz_txt = req(B, "/api/viz", tok)
    for vid in ids_from(viz_txt):
        case(f"viz.admin:{vid}", f"/api/viz/admin/{vid}", tok)
    try:
        vlist = json.loads(viz_txt)
        vlist = vlist if isinstance(vlist, list) else vlist.get("items", [])
    except ValueError:
        vlist = []
    for v in vlist:
        sv = v.get("slug") if isinstance(v, dict) else None
        if sv:
            case("viz:" + sv, "/api/viz/" + urllib.parse.quote(sv), tok)

    # /api/monitor aggregates live third-party metrics (CPU, bucket usage,
    # vault liveness). The values cannot be golden; the SHAPE can, and that is
    # what the differential harness compared too — only it compared the
    # top-level key set, and this compares the whole nested skeleton (key names
    # + value types, list lengths ignored). A renamed or dropped field fails.
    print("\n== monitor (shape assertion, not values) ==")
    st, body = req(B, "/api/monitor", tok)
    try:
        shape = skeleton(json.loads(body))
    except ValueError:
        shape = body
    g.case("monitor.shape", {"path": "/api/monitor", "status": st, "shape": shape})

    return g.finish()


if __name__ == "__main__":
    sys.exit(main())

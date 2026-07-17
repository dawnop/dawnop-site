#!/usr/bin/env python3
"""Comprehensive READ-path contract diff: Dawn (:8001) vs FastAPI (:8000).

Hits every GET endpoint (public + admin-read) on both backends and diffs the
HTTP status + normalized JSON body. Real content slugs/ids are discovered from
the list endpoints so detail endpoints are exercised against live data.

READ-ONLY: no case mutates the shared store. Volatile fields (view counters,
wall-clock updated_at, live monitor metrics) are scrubbed before comparison.

Usage (on the server, both ports localhost):
    TOKEN=$(...form login on :8001...) python3 contract_read.py \
        --dawn http://127.0.0.1:8001 --fast http://127.0.0.1:8000
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Fields that legitimately differ between two independently-serving backends
# (view counters bumped per-hit, SQLAlchemy onupdate wall-clock, live metrics).
VOLATILE = {
    "views",
    "view_count",
    "updated_at",
    "cached_at",
    "generated_at",
    "now",
    "timestamp",
    "latency_ms",
    "cpu_percent",
    "cpu",
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
    "free",
    "available",
    "rss",
    "traffic",
    "cpu_trend",
    "points",
    "series",
    "last_modified",  # fm entries: mtime from wall-clock-ish source
}

PASS = "\033[32mEXACT\033[0m"
DIFF = "\033[31mDIFF \033[0m"
ERRC = "\033[33mERR  \033[0m"

tally = {"exact": 0, "diff": 0, "err": 0}
diffs = []


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [scrub(v) for v in x]
    return x


def req(base, path, token=None, timeout=25):
    url = base + path
    hdrs = {}
    if token:
        hdrs["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, method="GET", headers=hdrs)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa
        return -1, repr(e)


def body_norm(text):
    try:
        return scrub(json.loads(text))
    except Exception:  # noqa
        return text


def diff(label, base_d, base_f, path, token=None):
    sd, bd = req(base_d, path, token)
    sf, bf = req(base_f, path, token)
    nd, nf = body_norm(bd), body_norm(bf)
    if sd < 0 or sf < 0:
        tally["err"] += 1
        print(f"  [{ERRC}] {label}  {path}  dawn={sd} fast={sf}")
        diffs.append((label, path, "transport", bd[:200], bf[:200]))
        return nd, nf
    if sd == sf and nd == nf:
        tally["exact"] += 1
        print(f"  [{PASS}] {label}  {path}  ({sd})")
    else:
        tally["diff"] += 1
        print(f"  [{DIFF}] {label}  {path}  dawn={sd} fast={sf}")
        diffs.append(
            (
                label,
                path,
                f"{sd}/{sf}",
                json.dumps(nd, ensure_ascii=False)[:400],
                json.dumps(nf, ensure_ascii=False)[:400],
            )
        )
    return nd, nf


def slugs_from(listing, key="items", field="slug", limit=8):
    try:
        data = json.loads(listing)
    except Exception:  # noqa
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
    ap.add_argument("--dawn", required=True)
    ap.add_argument("--fast", required=True)
    args = ap.parse_args()
    D, F = args.dawn, args.fast
    tok = os.environ.get("TOKEN")

    print("\n== health / public lists ==")
    diff("health", D, F, "/api/health")
    diff("articles.list", D, F, "/api/articles?page=1&size=20")
    diff("articles.p2", D, F, "/api/articles?page=2&size=5")
    diff("articles.tag", D, F, "/api/articles?page=1&size=10&tag=dawn")
    diff("pages.nav", D, F, "/api/pages/nav")
    diff("tags.list", D, F, "/api/tags")

    print("\n== search (multiple queries) ==")
    for q in ("dawn", "后端", "latex", "zzz-no-such-term", "a"):
        diff(
            f"search[{q}]", D, F, "/api/search?q=" + urllib.parse.quote(q) + "&size=10"
        )

    print("\n== article details (live slugs) ==")
    _, list_txt = req(D, "/api/articles?page=1&size=50"), None
    s, list_txt = req(D, "/api/articles?page=1&size=50")
    for slug in slugs_from(list_txt, "items"):
        diff("article", D, F, "/api/articles/" + urllib.parse.quote(slug))

    print("\n== page details + list-page articles (live slugs) ==")
    s, nav_txt = req(D, "/api/pages/nav")
    try:
        nav = json.loads(nav_txt)
        pslugs = [p["slug"] for p in nav if isinstance(p, dict) and p.get("slug")]
    except Exception:  # noqa
        pslugs = []
    for slug in pslugs[:10]:
        diff("page", D, F, "/api/pages/" + urllib.parse.quote(slug))
        diff(
            "page.articles",
            D,
            F,
            "/api/pages/" + urllib.parse.quote(slug) + "/articles?page=1&size=10",
        )

    print("\n== tag details (live slugs) ==")
    s, tags_txt = req(D, "/api/tags")
    for slug in slugs_from(tags_txt, "items" if '"items"' in tags_txt else None) or [
        t.get("slug")
        for t in (json.loads(tags_txt) if tags_txt.startswith("[") else [])
    ]:
        if slug:
            diff("tag", D, F, "/api/tags/" + urllib.parse.quote(slug))

    if tok:
        print("\n== authed reads (admin) ==")
        diff("auth.me", D, F, "/api/auth/me", tok)
        diff("viz.list", D, F, "/api/viz", tok)  # /api/viz requires auth
        diff("articles.admin", D, F, "/api/articles/admin?page=1&size=20", tok)
        diff("pages.admin", D, F, "/api/pages/admin", tok)
        diff("settings", D, F, "/api/settings", tok)
        diff("fm.root", D, F, "/api/fm?path=" + urllib.parse.quote("qiniu://"), tok)
        diff("fm.stats", D, F, "/api/fm/stats", tok)
        diff(
            "fm.search",
            D,
            F,
            "/api/fm/search?path=" + urllib.parse.quote("qiniu://") + "&q=a",
            tok,
        )

        print("\n== viz details (live slugs) ==")
        s, viz_txt = req(D, "/api/viz", tok)
        try:
            vlist = json.loads(viz_txt)
            vlist = vlist if isinstance(vlist, list) else vlist.get("items", [])
        except Exception:  # noqa
            vlist = []
        for v in vlist:
            sv = v.get("slug") if isinstance(v, dict) else None
            if sv:
                diff("viz", D, F, "/api/viz/" + urllib.parse.quote(sv), tok)

        # monitor: aggregates live third-party metrics (deeply volatile values);
        # assert same status + identical top-level key set, not exact bytes.
        print("\n== monitor (shape only) ==")
        sd, bd = req(D, "/api/monitor", tok)
        sf, bf = req(F, "/api/monitor", tok)
        try:
            kd = sorted(json.loads(bd).keys())
            kf = sorted(json.loads(bf).keys())
            ok = sd == sf == 200 and kd == kf
        except Exception:  # noqa
            ok = False
            kd = kf = None
        if ok:
            tally["exact"] += 1
            print(f"  [{PASS}] monitor  /api/monitor  ({sd})  keys={kd}")
        else:
            tally["diff"] += 1
            print(f"  [{DIFF}] monitor  /api/monitor  dawn={sd}/{kd} fast={sf}/{kf}")
            diffs.append(("monitor", "/api/monitor", f"{sd}/{sf}", str(kd), str(kf)))
    else:
        print("\n(no TOKEN — skipping authed reads)")

    print("\n=== tally ===")
    print(f"  EXACT {tally['exact']}   DIFF {tally['diff']}   ERR {tally['err']}")
    if diffs:
        print("\n--- differences ---")
        for label, path, code, d, f in diffs:
            print(f"\n[{label}] {path}  ({code})")
            print(f"  dawn: {d}")
            print(f"  fast: {f}")
    sys.exit(1 if (tally["diff"] or tally["err"]) else 0)


if __name__ == "__main__":
    main()

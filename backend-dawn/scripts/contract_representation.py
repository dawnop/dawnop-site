"""Assertions against actual routes, SQLite rows, final nginx wire and fake IO."""

import base64
import email.utils
import hashlib
import json
import sqlite3
import time
import xml.etree.ElementTree as ET


def run(g, base, token, auth, fake, db, fake_base):
    import contract_qiniu as q

    path = "/dav/dav-test/k2.txt"
    rel = "dav-test/k2.txt"
    content = b"representation bytes for range tests"

    def row(name=rel):
        with sqlite3.connect(db) as conn:
            return conn.execute(
                "select key,size,representation_kind,representation_key,representation_profile,representation_etag,representation_last_modified_s from files where path=?",
                (name,),
            ).fetchone()

    def request(method, headers=None, body=None):
        return q.dav(base, method, path, auth, headers, body)

    def case(name, check):
        check()
        g.case("k2." + name, {"assertions": "PASS"})

    def wire():
        assert request("PUT", body=content)[0] == 201
        metadata = row()
        assert metadata[0] == metadata[3] and metadata[1] == len(content)
        assert metadata[2] == "object-identity-v1"
        fake.clear_calls()
        st, hd, raw = request("PROPFIND", {"Depth": "0"})
        assert st == 207 and not fake.calls()
        root = ET.fromstring(raw)
        tag = root.findtext(".//{DAV:}getetag")
        date = root.findtext(".//{DAV:}getlastmodified")
        assert (
            tag == metadata[5]
            and int(email.utils.parsedate_to_datetime(date).timestamp()) == metadata[6]
        )
        assert root.findtext(".//{DAV:}getcontentlength") == str(metadata[1])
        for method in ("GET", "HEAD"):
            fake.clear_calls()
            st, hd, body = request(method)
            assert st == 200 and body == (content if method == "GET" else b"")
            assert hd["etag"] == tag and hd["last-modified"] == date
            assert int(hd["content-length"]) == len(content)
            assert len(fake.calls()) == 1 and fake.calls()[0]["op"] == (
                "download" if method == "GET" else "head"
            )
            basic = "Basic " + base64.b64encode(auth.encode()).decode()
            head, raw = q.raw_http(base, method, path, [("Authorization", basic)])
            for name in (b"content-length", b"etag", b"last-modified"):
                assert (
                    sum(
                        line.lower().startswith(name + b":")
                        for line in head.split(b"\r\n")
                    )
                    == 1
                )

    case("dav-three-methods", wire)

    def conditions_ranges():
        tag = row()[5]
        for headers, status, body in [
            ({"Range": "bytes=0-2", "If-Range": tag}, 206, content[:3]),
            ({"Range": "bytes=0-2", "If-Range": '"old-key"'}, 200, content),
            ({"Range": "bytes=0-2", "If-Range": "W/" + tag}, 200, content),
            ({"Range": "bytes=5-"}, 206, content[5:]),
            ({"Range": "bytes=-4"}, 206, content[-4:]),
            ({"Range": "bytes=99999-"}, 416, None),
            ({"Range": "bytes=1-2,5-6"}, 200, content),
            ({"Range": "broken"}, 200, content),
            ({"If-None-Match": tag}, 304, b""),
            (
                {
                    "If-None-Match": '"other"',
                    "If-Modified-Since": "Thu, 01 Jan 2037 00:00:00 GMT",
                },
                200,
                content,
            ),
            ({"If-Match": '"other"'}, 412, None),
            ({"If-Unmodified-Since": "Wed, 31 Dec 2025 23:59:59 GMT"}, 412, None),
        ]:
            fake.clear_calls()
            st, hd, raw = request("GET", headers)
            assert st == status, (headers, st)
            if body is not None:
                assert hashlib.sha256(raw).digest() == hashlib.sha256(body).digest()
            if status == 416:
                assert hd["content-range"] == f"bytes */{len(content)}"
            if status == 206:
                assert hd["content-range"].endswith("/" + str(len(content)))
                assert int(hd["content-length"]) == len(raw)
            assert len(fake.calls()) == 1

    case("conditions-and-ranges", conditions_ranges)

    def unavailable():
        for column, value in [
            ("representation_etag", None),
            ("representation_key", "wrong"),
            ("representation_profile", "wrong"),
        ]:
            with sqlite3.connect(db) as conn:
                old = conn.execute(
                    f"select {column} from files where path=?", (rel,)
                ).fetchone()[0]
                conn.execute(f"update files set {column}=? where path=?", (value, rel))
            try:
                for method in ("GET", "HEAD", "PROPFIND"):
                    fake.clear_calls()
                    assert request(method, {"Depth": "0"})[0] == 503
                    assert not fake.calls()
            finally:
                with sqlite3.connect(db) as conn:
                    conn.execute(
                        f"update files set {column}=? where path=?", (old, rel)
                    )

    case("missing-and-mismatched-metadata", unavailable)

    def failed_head():
        before, objects = row(), fake.state()
        fake.refuse("head", 500)
        try:
            assert request("PUT", body=b"unpublished new bytes")[0] == 502
        finally:
            fake.allow("head")
        assert row() == before and fake.state() == objects
        assert request("GET")[2] == content

    case("head-failure-preserves-old-row", failed_head)

    def empty():
        previous = row()
        assert request("PUT", body=b"")[0] == 204
        zero = row()
        assert (
            zero[1] == 0
            and zero[2] == "local-empty-v1"
            and zero[5] == '"dawn-empty-v1"'
        )
        assert abs(zero[6] - time.time()) < 10
        for method in ("GET", "HEAD"):
            for headers, expected in [
                ({}, 200),
                ({"Range": "bytes=0-0", "If-Range": '"old"'}, 200),
                ({"If-None-Match": "W/" + zero[5]}, 304),
                ({"If-Match": '"wrong"'}, 412),
                (
                    {
                        "If-Modified-Since": email.utils.formatdate(
                            previous[6], usegmt=True
                        )
                    },
                    200,
                ),
            ]:
                fake.clear_calls()
                st, hd, raw = request(method, headers)
                assert st == expected and not raw and not fake.calls()
                assert hd["etag"] == zero[5]
                assert (
                    int(
                        email.utils.parsedate_to_datetime(
                            hd["last-modified"]
                        ).timestamp()
                    )
                    == zero[6]
                )
                if st != 304:
                    assert hd["content-length"] == "0"
        fake.clear_calls()
        st, _, raw = request("PROPFIND", {"Depth": "0"})
        assert st == 207 and not fake.calls()
        root = ET.fromstring(raw)
        assert root.findtext(".//{DAV:}getetag") == zero[5]
        assert root.findtext(".//{DAV:}getcontentlength") == "0"
        assert request("PUT", body=content)[0] == 204
        assert request("GET", {"If-None-Match": zero[5]})[0] == 200

    case("empty-owner-and-transitions", empty)

    def immutable():
        st, raw = q.api(
            base,
            "POST",
            "/api/fm/upload-token",
            token,
            {"path": "qiniu://dav-test", "name": "k2-direct.txt"},
        )
        assert st == 200
        grant = json.loads(raw)
        assert (
            q.spend_upload_token(fake_base, grant["token"], grant["key"], b"first")[0]
            == 200
        )
        assert (
            q.spend_upload_token(fake_base, grant["token"], grant["key"], b"different")[
                0
            ]
            == 614
        )
        st, _ = q.api(
            base,
            "POST",
            "/api/fm/register",
            token,
            {"path": grant["path"], "key": grant["key"], "size": 99},
        )
        assert st == 502
        st, _ = q.api(
            base,
            "POST",
            "/api/fm/register",
            token,
            {"path": grant["path"], "key": grant["key"], "size": 5},
        )
        assert st == 200

    case("ordinary-token-replay-and-register", immutable)

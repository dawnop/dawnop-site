"""Assertions against actual routes, SQLite rows, final nginx wire and fake IO."""

import base64
import email.utils
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


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
        try:
            check()
        except BaseException:
            if getattr(g, "strict_assertions", False) or getattr(g, "record", False):
                raise AssertionError("k2." + name) from None
            g.case("k2." + name, {"assertions": "FAIL"})
            return
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
        time.sleep(1.1)
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

    def copies_and_writers():
        original = row()
        target = "/dav/dav-test/k2-copy.txt"
        fake.clear_calls()
        assert q.dav(base, "COPY", path, auth, {"Destination": target})[0] == 201
        copied = row("dav-test/k2-copy.txt")
        assert (
            copied[0] != original[0]
            and copied[1] == original[1]
            and copied[5] == original[5]
        )
        assert copied[6] == original[6] + 1
        assert [call["op"] for call in fake.calls()] == ["copy", "head"]
        fake.clear_calls()
        assert (
            q.dav(
                base,
                "MOVE",
                target,
                auth,
                {"Destination": "/dav/dav-test/k2-moved.txt"},
            )[0]
            == 201
        )
        assert row("dav-test/k2-moved.txt") == copied and not fake.calls()
        assert (
            q.api(
                base,
                "POST",
                "/api/fm/rename",
                token,
                {
                    "path": "qiniu://dav-test",
                    "item": "qiniu://dav-test/k2-moved.txt",
                    "name": "k2-renamed.txt",
                },
            )[0]
            == 200
        )
        assert row("dav-test/k2-renamed.txt") == copied and not fake.calls()
        boundary = "K2ProxyBoundary"
        data = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="path"\r\n\r\nqiniu://dav-test\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="k2-proxy.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'
            ).encode()
            + b"\x00\xffab"
            + f"\r\n--{boundary}--\r\n".encode()
        )
        upload = urllib.request.Request(
            base + "/api/fm/upload",
            data=data,
            method="POST",
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "multipart/form-data; boundary=" + boundary,
            },
        )
        with q.OPENER.open(upload, timeout=10) as response:
            assert response.status == 200
        assert (
            row("dav-test/k2-proxy.bin")[1] == 4
            and row("dav-test/k2-proxy.bin")[2] == "object-identity-v1"
        )
        with sqlite3.connect(db) as conn:
            total, complete = conn.execute(
                "select count(*),sum(representation_kind is not null and representation_key=key and representation_etag is not null and representation_last_modified_s is not null) from files where path like 'dav-test/drop-inbox/%' and is_dir=0"
            ).fetchone()
        assert total > 0 and total == complete
        assert (
            q.api(
                base,
                "POST",
                "/api/fm/create-file",
                token,
                {"path": "qiniu://dav-test", "name": "k2-empty-copy.txt"},
            )[0]
            == 200
        )
        source_empty = row("dav-test/k2-empty-copy.txt")
        time.sleep(1.1)
        assert (
            q.dav(
                base,
                "COPY",
                "/dav/dav-test/k2-empty-copy.txt",
                auth,
                {"Destination": "/dav/dav-test/k2-empty-target.txt"},
            )[0]
            == 201
        )
        empty_target = row("dav-test/k2-empty-target.txt")
        assert (
            empty_target[1] == 0
            and empty_target[2] == "local-empty-v1"
            and empty_target[6] > source_empty[6]
        )

    case("copy-move-and-writer-coverage", copies_and_writers)

    def metadata_headers():
        date = "Thu, 01 Jan 2026 00:00:00 GMT"
        good = [
            ("ETag", 'W/"exact-entity<&>"'),
            ("Last-Modified", date),
            ("Content-Length", "5"),
        ]
        cases = [
            (good, True, True),
            (good + [("ETag", '"duplicate"')], False, False),
            (good + [("Last-Modified", date)], False, False),
            (good + [("Content-Length", "5")], False, False),
            (good[1:], False, False),
            ([("ETag", "not-quoted"), *good[1:]], False, False),
            ([good[0], ("Last-Modified", "invalid"), good[2]], False, False),
            ([*good[:2], ("Content-Length", "9223372036854775808")], False, False),
            (good + [("Content-Encoding", "gzip")], False, False),
        ]
        for number, (headers, accepted, informational) in enumerate(cases):
            st, raw = q.api(
                base,
                "POST",
                "/api/fm/upload-token",
                token,
                {"path": "qiniu://dav-test", "name": f"headers-{number}.txt"},
            )
            assert st == 200
            grant = json.loads(raw)
            fake._post(
                "/__fake/put",
                {
                    "key": grant["key"],
                    "content": "bytes",
                    "etag": good[0][1],
                    "head_headers": headers,
                    "informational": informational,
                },
            )
            st, raw = q.api(
                base,
                "POST",
                "/api/fm/register",
                token,
                {"path": grant["path"], "key": grant["key"], "size": 5},
            )
            assert st == (200 if accepted else 502), (number, st)
            with sqlite3.connect(db) as conn:
                found = conn.execute(
                    "select representation_etag from files where key=?", (grant["key"],)
                ).fetchall()
                pending = conn.execute(
                    "select count(*) from pending_uploads where key=?", (grant["key"],)
                ).fetchone()[0]
            if accepted:
                assert found == [(good[0][1],)] and pending == 0
            else:
                assert found == [] and pending == 1
                assert grant["key"].encode() not in raw and b"exact-entity" not in raw
        # Missing and malformed stat lengths cannot become local empty rows.
        for number, size in enumerate((None, -1, "0")):
            st, raw = q.api(
                base,
                "POST",
                "/api/fm/upload-token",
                token,
                {"path": "qiniu://dav-test", "name": f"stat-{number}.txt"},
            )
            grant = json.loads(raw)
            fake._post(
                "/__fake/put", {"key": grant["key"], "content": "", "stat_size": size}
            )
            assert (
                q.api(
                    base,
                    "POST",
                    "/api/fm/register",
                    token,
                    {"path": grant["path"], "key": grant["key"]},
                )[0]
                == 502
            )

    case("metadata-header-validation", metadata_headers)

    def resumable():
        st, raw = q.api(
            base,
            "POST",
            "/api/fm/upload-token",
            token,
            {"path": "qiniu://dav-test", "name": "resumable.bin"},
        )
        assert st == 200
        grant = json.loads(raw)
        encoded = base64.urlsafe_b64encode(grant["key"].encode()).decode()
        target = f"{fake_base}/buckets/{q.contract_qiniu_fake.FAKE_BUCKET}/objects/{encoded}/uploads"

        def send(url, method, data):
            req = urllib.request.Request(
                url,
                data=data,
                method=method,
                headers={
                    "Authorization": "UpToken " + grant["token"],
                    "Content-Type": "application/octet-stream",
                },
            )
            try:
                with q.OPENER.open(req, timeout=10) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        for attempt in range(2):
            status, initialized = send(target, "POST", b"")
            assert status == 200
            upload = target + "/" + initialized["uploadId"]
            parts = []
            for number, data in enumerate(
                (bytes([65 + attempt]) * 1048576, b"last"), 1
            ):
                status, uploaded = send(upload + "/" + str(number), "PUT", data)
                assert status == 200
                parts.append({"partNumber": number, "etag": uploaded["etag"]})
            assert send(upload, "POST", json.dumps({"parts": parts}).encode())[0] == (
                200 if attempt == 0 else 614
            )
        assert (
            q.api(
                base,
                "POST",
                "/api/fm/register",
                token,
                {"path": grant["path"], "key": grant["key"], "size": 1048580},
            )[0]
            == 200
        )
        assert (
            base64.b64decode(fake.state()[grant["key"]]["content"])
            == b"A" * 1048576 + b"last"
        )

    case("resumable-token-replay", resumable)

    def scale():
        timings = {}
        for count in (0, 1, 100, 1000):
            directory = f"dav-test/k2-scale-{count}"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "insert into files(path,is_dir,key,content_type,size,created_at,updated_at) values (?,1,null,'',0,'2026-01-01 00:00:00','2026-01-01 00:00:00')",
                    (directory,),
                )
                metadata = row()
                for number in range(count):
                    key = f"scale-{count}-{number}"
                    conn.execute(
                        "insert into files(path,is_dir,key,content_type,size,created_at,updated_at,representation_kind,representation_key,representation_profile,representation_etag,representation_last_modified_s) values (?,0,?,'text/plain',1,'2026-01-01 00:00:00','2026-01-01 00:00:00','object-identity-v1',?,?,?,1767225600)",
                        (
                            f"{directory}/{number}.txt",
                            key,
                            key,
                            metadata[4],
                            '"scale-entity"',
                        ),
                    )
            fake.clear_calls()
            samples = []
            for _ in range(20):
                started = time.perf_counter()
                status, _, body = q.dav(
                    base, "PROPFIND", "/dav/" + directory, auth, {"Depth": "1"}
                )
                samples.append((time.perf_counter() - started) * 1000)
                root = ET.fromstring(body)
                assert (
                    status == 207 and len(root.findall("{DAV:}response")) == count + 1
                )
                assert {node.text for node in root.findall(".//{DAV:}href")} == {
                    "/dav/" + directory + "/"
                } | {f"/dav/{directory}/{number}.txt" for number in range(count)}
            assert not fake.calls()
            timings[str(count)] = {
                "samples": 20,
                "p95_ms": sorted(samples)[18],
                "object_requests": 0,
                "complete": True,
            }
        cold = []
        for _ in range(5):
            with sqlite3.connect(db) as conn:
                conn.execute("delete from runtime_cache where namespace='dav.auth.v2'")
            started = time.perf_counter()
            assert (
                q.dav(
                    base, "PROPFIND", "/dav/dav-test/k2-scale-1", auth, {"Depth": "1"}
                )[0]
                == 207
            )
            cold.append((time.perf_counter() - started) * 1000)
        assert max(cold) <= 2000
        evidence = {
            "directories": timings,
            "cold_auth_samples": 5,
            "cold_auth_max_ms": max(cold),
        }
        if os.environ.get("CONTRACT_REPRESENTATION_EVIDENCE"):
            Path(os.environ["CONTRACT_REPRESENTATION_EVIDENCE"]).write_text(
                json.dumps(evidence) + "\n"
            )

    case("propfind-scale", scale)

#!/usr/bin/env python3
"""Exercise FM and WebDAV ancestor invariants against the production jar."""

import argparse
import base64
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

import contract_fixture  # noqa: E402
import contract_qiniu_fake  # noqa: E402
import contract_run  # noqa: E402
from contract_qiniu import Fake  # noqa: E402

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class Context:
    base: str
    db: pathlib.Path
    token: str
    auth: str
    fake: Fake


class Checks:
    def __init__(self):
        self.errors = []

    def equal(self, actual, expected, label):
        if actual != expected:
            self.errors.append(f"{label}: expected {expected!r}, got {actual!r}")

    def true(self, condition, label):
        if not condition:
            self.errors.append(label)

    def finish(self):
        if self.errors:
            raise AssertionError("; ".join(self.errors))


def request(url, method, headers=None, body=None, timeout=30):
    req = urllib.request.Request(
        url, data=body, method=method, headers=dict(headers or {})
    )
    try:
        with OPENER.open(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def api(ctx, path, payload=None, method="POST"):
    headers = {"Authorization": f"Bearer {ctx.token}"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    return request(ctx.base + path, method, headers, body)


def api_upload(ctx, target, name="upload.bin", content=b"ancestor-contract"):
    boundary = "DawnFmAncestorContractBoundary"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="path"\r\n\r\n'
            f"{target}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return request(
        ctx.base + "/api/fm/upload",
        "POST",
        {
            "Authorization": f"Bearer {ctx.token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        body,
    )


def dav(ctx, method, path, headers=None, body=None):
    dav_headers = dict(headers or {})
    dav_headers["Authorization"] = (
        "Basic " + base64.b64encode(ctx.auth.encode()).decode()
    )
    raw = body if isinstance(body, (bytes, type(None))) else body.encode()
    return request(ctx.base + path, method, dav_headers, raw)


def add_file(
    ctx,
    path,
    is_dir,
    key=None,
    content_type="text/plain",
    size=1,
    content="ancestor fixture",
    plant=True,
):
    with sqlite3.connect(ctx.db) as connection:
        connection.execute(
            'INSERT INTO files (path, is_dir, "key", content_type, size, created_at, updated_at) '
            "VALUES (?, ?, ?, ?, ?, '2026-08-12 00:00:00', '2026-08-12 00:00:00')",
            (
                path,
                int(is_dir),
                key,
                "" if is_dir else content_type,
                0 if is_dir else size,
            ),
        )
    if key and plant:
        ctx.fake.plant(key, content, content_type)


def add_ledger(ctx, key, path):
    with sqlite3.connect(ctx.db) as connection:
        connection.execute(
            "INSERT INTO pending_uploads (key, path, created_at) VALUES (?, ?, '2026-08-12 00:00:00')",
            (key, path),
        )


def rows(ctx, prefix=None):
    query = 'SELECT path, is_dir, "key", content_type, size FROM files'
    params = ()
    if prefix is not None:
        query += " WHERE path = ? OR instr(path, ? || '/') = 1"
        params = (prefix, prefix)
    query += " ORDER BY path"
    with sqlite3.connect(ctx.db) as connection:
        return connection.execute(query, params).fetchall()


def db_state(ctx):
    with sqlite3.connect(ctx.db) as connection:
        files = connection.execute(
            'SELECT path, is_dir, "key", content_type, size FROM files ORDER BY path'
        ).fetchall()
        ledger = connection.execute(
            "SELECT key, path FROM pending_uploads ORDER BY key"
        ).fetchall()
    return {"files": files, "ledger": ledger}


def execute_sql(ctx, sql):
    with sqlite3.connect(ctx.db) as connection:
        connection.executescript(sql)


def file_row(ctx, path):
    with sqlite3.connect(ctx.db) as connection:
        return connection.execute(
            'SELECT path, is_dir, "key", content_type, size FROM files WHERE path = ?',
            (path,),
        ).fetchone()


def set_file_key(ctx, path, key, content_type="text/plain", size=1):
    with sqlite3.connect(ctx.db) as connection:
        cursor = connection.execute(
            'UPDATE files SET "key" = ?, content_type = ?, size = ?, '
            "updated_at = datetime('now') WHERE path = ? AND is_dir = 0",
            (key, content_type, size, path),
        )
        return cursor.rowcount


def dangling_file_paths(ctx):
    live_keys = set(ctx.fake.keys())
    return [
        row[0]
        for row in rows(ctx)
        if not row[1] and row[2] is not None and row[2] not in live_keys
    ]


def object_bytes(ctx, key):
    obj = ctx.fake.state().get(key)
    return None if obj is None else base64.b64decode(obj["content"])


def start_background(call):
    result = {}
    done = threading.Event()

    def run():
        try:
            result["response"] = call()
        except Exception as error:  # noqa: BLE001 - surfaced by the assertion
            result["error"] = error
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return result, done, thread


def finish_background(thread, done, timeout=15):
    thread.join(timeout=timeout)
    return done.is_set()


def assert_background_response(checks, result, done, label, expected_status):
    checks.true(done.is_set(), f"{label} did not finish before timeout")
    checks.true("error" not in result, f"{label} raised {result.get('error')!r}")
    checks.true("response" in result, f"{label} produced no response")
    if "response" in result:
        checks.equal(result["response"][0], expected_status, f"{label} status")


def assert_uploaded_object_collected(checks, before_state, after_state, calls, label):
    uploads = [call for call in calls if call.get("op") == "upload"]
    deletes = [call for call in calls if call.get("op") == "delete"]
    checks.equal(len(uploads), 1, f"{label} upload count")
    checks.equal(len(deletes), 1, f"{label} cleanup delete count")
    checks.equal(
        [call.get("op") for call in calls],
        ["upload", "delete"],
        f"{label} object operation order",
    )
    if uploads and deletes:
        checks.equal(
            deletes[0].get("key"),
            uploads[0].get("key"),
            f"{label} cleanup key",
        )
    checks.equal(after_state, before_state, f"{label} left a fresh upload object")


def assert_copied_objects_collected(checks, before_state, after_state, calls, label):
    copied = [
        call.get("dst")
        for call in calls
        if call.get("op") == "copy" and call.get("found") is True
    ]
    deleted = [call.get("key") for call in calls if call.get("op") == "delete"]
    checks.true(bool(copied), f"{label} did not reach Qiniu copy")
    checks.equal(after_state, before_state, f"{label} left fresh copied objects")
    checks.equal(sorted(deleted), sorted(copied), f"{label} cleanup key set")
    operations = [call.get("op") for call in calls]
    checks.true(
        all(op in ("copy", "delete") for op in operations),
        f"{label} performed an unrelated object operation: {operations}",
    )
    if "delete" in operations:
        checks.true(
            operations.index("delete") >= len(copied),
            f"{label} deleted before all copies completed: {operations}",
        )


def wait_for_pause(fake, op, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = fake.pauses().get(op, {})
        if state.get("entered", 0) > 0:
            return True
        time.sleep(0.02)
    return False


def wait_for_call(fake, op, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(call.get("op") == op for call in fake.calls()):
            return True
        time.sleep(0.02)
    return False


def seed_deep_file_ancestor(ctx, root="blocked"):
    add_file(ctx, root, False, f"{root}-object")
    add_file(ctx, f"{root}/legacy-dir", True)


def seed_dirty_subtree(ctx, root):
    add_file(ctx, root, False, f"{root}-root-object")
    add_file(ctx, f"{root}/legacy-dir", True)
    add_file(
        ctx,
        f"{root}/legacy-dir/child.txt",
        False,
        f"{root}-child-object",
    )


def assert_fm_missing_ancestors(ctx, checks):
    statuses = {}
    statuses["create-folder"] = api(
        ctx,
        "/api/fm/create-folder",
        {"path": "qiniu://auto/a", "name": "folder"},
    )[0]
    statuses["create-file"] = api(
        ctx,
        "/api/fm/create-file",
        {"path": "qiniu://create/a", "name": "new.txt"},
    )[0]
    statuses["save"] = api(
        ctx,
        "/api/fm/save",
        {"path": "qiniu://save/a/note.txt", "content": "saved"},
    )[0]

    token_status, token_raw = api(
        ctx,
        "/api/fm/upload-token",
        {"path": "qiniu://token/a", "name": "direct.bin"},
    )
    statuses["upload-token"] = token_status
    token_rows_before_register = rows(ctx, "token")
    minted = json.loads(token_raw) if token_status == 200 else {}
    register_key = minted.get("key", "fallback-register-key")
    register_path = minted.get("path", "qiniu://token/a/direct.bin")
    ctx.fake.plant(register_key, "registered", "application/octet-stream")
    statuses["register"] = api(
        ctx,
        "/api/fm/register",
        {"path": register_path, "key": register_key},
    )[0]
    statuses["upload"] = api_upload(ctx, "qiniu://upload/a")[0]
    statuses["move"] = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://move/a",
            "sources": ["qiniu://empty-dir"],
        },
    )[0]
    statuses["copy"] = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://",
            "destination": "qiniu://copy/a",
            "sources": ["qiniu://docs/notes.txt"],
        },
    )[0]

    for endpoint, status in statuses.items():
        checks.equal(status, 200, f"FM {endpoint} keeps missing-ancestor behavior")
    checks.equal(
        token_rows_before_register,
        [],
        "upload-token does not create missing directory rows",
    )
    expected_paths = {
        "auto",
        "auto/a",
        "auto/a/folder",
        "create",
        "create/a",
        "create/a/new.txt",
        "save",
        "save/a",
        "save/a/note.txt",
        "token",
        "token/a",
        "token/a/direct.bin",
        "upload",
        "upload/a",
        "upload/a/upload.bin",
        "move",
        "move/a",
        "move/a/empty-dir",
        "copy",
        "copy/a",
        "copy/a/notes.txt",
    }
    actual_paths = {row[0] for row in rows(ctx)}
    checks.true(
        expected_paths <= actual_paths, "FM writes did not create expected rows"
    )


def assert_rename_missing_ancestor_no_fill(ctx, checks):
    add_file(ctx, "rename-missing/source.txt", False, "rename-missing-object")
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()
    status, _ = api(
        ctx,
        "/api/fm/rename",
        {
            "path": "qiniu://rename-missing",
            "item": "qiniu://rename-missing/source.txt",
            "name": "renamed.txt",
        },
    )
    paths = {row[0] for row in rows(ctx)}
    checks.equal(status, 200, "rename with an absent ancestor")
    checks.true(
        "rename-missing/renamed.txt" in paths,
        "rename did not rewrite the target row",
    )
    checks.true(
        "rename-missing/source.txt" not in paths,
        "rename left the source row",
    )
    checks.true(
        "rename-missing" not in paths,
        "rename synthesized a previously absent ancestor",
    )
    checks.equal(ctx.fake.keys(), before_keys, "rename changed objects")
    checks.equal(ctx.fake.calls(), [], "rename reached Qiniu")


def add_tree(ctx, root, child, key):
    add_file(ctx, root, True)
    add_file(ctx, f"{root}/{child}", False, key)


def assert_literal_prefix_isolated(ctx, checks):
    add_tree(ctx, "search%root", "literal-hit.txt", "search-percent-object")
    add_tree(ctx, "searchXroot", "alias-hit.txt", "search-alias-object")
    query = urllib.parse.urlencode(
        {"path": "qiniu://search%root", "filter": "hit", "deep": "1"}
    )
    search_status, search_raw = api(ctx, f"/api/fm/search?{query}", method="GET")
    checks.equal(search_status, 200, "literal percent search status")
    search_body = json.loads(search_raw)
    search_paths = {entry["path"] for entry in search_body["files"]}
    checks.equal(
        search_paths,
        {"qiniu://search%root/literal-hit.txt"},
        "search expanded a literal percent prefix",
    )

    add_tree(ctx, "fm-delete%root", "literal.txt", "fm-delete-literal-object")
    add_tree(ctx, "fm-deleteXroot", "sentinel.txt", "fm-delete-sentinel-object")
    fm_delete_status, _ = api(
        ctx,
        "/api/fm/delete",
        {
            "path": "qiniu://",
            "items": [{"path": "qiniu://fm-delete%root"}],
        },
    )
    checks.equal(fm_delete_status, 200, "FM literal percent DELETE status")
    checks.true(
        "fm-deleteXroot/sentinel.txt" in {row[0] for row in rows(ctx)},
        "FM DELETE removed a percent lookalike subtree",
    )
    checks.true(
        "fm-delete-sentinel-object" in ctx.fake.keys(),
        "FM DELETE removed a percent lookalike object",
    )

    add_tree(ctx, "dav_delete_root", "literal.txt", "dav-delete-literal-object")
    add_tree(ctx, "davXdeleteXroot", "sentinel.txt", "dav-delete-sentinel-object")
    dav_delete_status, _ = dav(ctx, "DELETE", "/dav/dav_delete_root")
    checks.equal(dav_delete_status, 204, "WebDAV literal underscore DELETE status")
    checks.true(
        "davXdeleteXroot/sentinel.txt" in {row[0] for row in rows(ctx)},
        "WebDAV DELETE removed an underscore lookalike subtree",
    )
    checks.true(
        "dav-delete-sentinel-object" in ctx.fake.keys(),
        "WebDAV DELETE removed an underscore lookalike object",
    )

    add_file(ctx, "fm-copy-dest", True)
    add_file(ctx, "fm-copy%root", True)
    add_tree(ctx, "fm-copyXroot", "sentinel.txt", "fm-copy-sentinel-object")
    fm_copy_status, _ = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://",
            "destination": "qiniu://fm-copy-dest",
            "sources": ["qiniu://fm-copy%root"],
        },
    )
    checks.equal(fm_copy_status, 200, "FM literal percent COPY status")
    copied_paths = {row[0] for row in rows(ctx, "fm-copy-dest/fm-copy%root")}
    checks.equal(
        copied_paths,
        {"fm-copy-dest/fm-copy%root"},
        "FM COPY expanded a literal percent subtree",
    )
    checks.true(
        "fm-copyXroot/sentinel.txt" in {row[0] for row in rows(ctx)},
        "FM COPY changed its percent lookalike source",
    )

    add_file(ctx, "dav-copy-dest", True)
    add_file(ctx, "dav_copy_root", True)
    add_tree(ctx, "davXcopyXroot", "sentinel.txt", "dav-copy-sentinel-object")
    dav_copy_status, _ = dav(
        ctx,
        "COPY",
        "/dav/dav_copy_root",
        {"Destination": "/dav/dav-copy-dest/dav_copy_root"},
    )
    checks.equal(dav_copy_status, 201, "WebDAV literal underscore COPY status")
    checks.equal(
        {row[0] for row in rows(ctx, "dav-copy-dest/dav_copy_root")},
        {"dav-copy-dest/dav_copy_root"},
        "WebDAV COPY expanded a literal underscore subtree",
    )
    checks.true(
        "davXcopyXroot/sentinel.txt" in {row[0] for row in rows(ctx)},
        "WebDAV COPY changed its underscore lookalike source",
    )

    add_file(ctx, "fm-move-dest", True)
    add_tree(ctx, "fm-move%root", "literal.txt", "fm-move-literal-object")
    add_tree(ctx, "fm-moveXroot", "sentinel.txt", "fm-move-sentinel-object")
    fm_move_status, _ = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://fm-move-dest",
            "sources": ["qiniu://fm-move%root"],
        },
    )
    checks.equal(fm_move_status, 200, "FM literal percent MOVE status")
    checks.true(
        "fm-moveXroot/sentinel.txt" in {row[0] for row in rows(ctx)},
        "FM MOVE changed a percent lookalike subtree",
    )

    add_file(ctx, "dav-move-dest", True)
    add_tree(ctx, "dav_move_root", "literal.txt", "dav-move-literal-object")
    add_tree(ctx, "davXmoveXroot", "sentinel.txt", "dav-move-sentinel-object")
    dav_move_status, _ = dav(
        ctx,
        "MOVE",
        "/dav/dav_move_root",
        {"Destination": "/dav/dav-move-dest/dav_move_root"},
    )
    checks.equal(dav_move_status, 201, "WebDAV literal underscore MOVE status")
    checks.true(
        "davXmoveXroot/sentinel.txt" in {row[0] for row in rows(ctx)},
        "WebDAV MOVE changed an underscore lookalike subtree",
    )

    add_tree(ctx, "purge%root", "old.txt", "purge-literal-object")
    add_tree(ctx, "purgeXroot", "sentinel.txt", "purge-sentinel-object")
    purge_status, _ = dav(
        ctx,
        "COPY",
        "/dav/empty-dir",
        {"Destination": "/dav/purge%25root"},
    )
    checks.equal(purge_status, 204, "WebDAV literal percent purge status")
    checks.true(
        "purgeXroot/sentinel.txt" in {row[0] for row in rows(ctx)},
        "WebDAV purge removed a percent lookalike subtree",
    )
    checks.true(
        "purge-sentinel-object" in ctx.fake.keys(),
        "WebDAV purge removed a percent lookalike object",
    )


def assert_webdav_copy_parent_order(ctx, checks):
    seed_deep_file_ancestor(ctx, "dirty-copy-source")
    add_file(
        ctx,
        "dirty-copy-source/legacy-dir/child.txt",
        False,
        "repair-copy-child-object",
    )
    add_file(ctx, "repair-copy-destination", True)
    fm_status, _ = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://",
            "destination": "qiniu://repair-copy-destination",
            "sources": ["qiniu://dirty-copy-source/legacy-dir"],
        },
    )
    checks.equal(
        fm_status,
        200,
        "copying a clean subtree out of dirty external ancestry",
    )
    checks.true(
        "repair-copy-destination/legacy-dir/child.txt" in {row[0] for row in rows(ctx)},
        "repair copy lost its child",
    )
    checks.true(
        "dirty-copy-source/legacy-dir/child.txt" in {row[0] for row in rows(ctx)},
        "repair copy removed its source",
    )

    add_file(ctx, "ordered-destination", True)
    add_file(ctx, "ordered-source", True)
    add_file(ctx, "ordered-source/level-one", True)
    add_file(ctx, "ordered-source/level-one/level-two", True)
    add_file(
        ctx,
        "ordered-source/level-one/level-two/deep.txt",
        False,
        "ordered-deep-object",
    )
    add_file(
        ctx,
        "ordered-source/root.txt",
        False,
        "ordered-root-object",
    )
    execute_sql(
        ctx,
        """
        CREATE TRIGGER require_copy_parent_order
        BEFORE INSERT ON files
        WHEN new.path = 'ordered-destination/copied/level-one/level-two/deep.txt'
          AND NOT EXISTS (
            SELECT 1 FROM files
            WHERE path = 'ordered-destination/copied/level-one/level-two'
              AND is_dir = 1
          )
        BEGIN
          SELECT raise(abort, 'copy inserted child before parent');
        END;
        """,
    )
    status, _ = dav(
        ctx,
        "COPY",
        "/dav/ordered-source",
        {"Destination": "/dav/ordered-destination/copied"},
    )
    checks.equal(status, 201, "WebDAV multilevel COPY status")
    checks.equal(
        {row[0] for row in rows(ctx, "ordered-destination/copied")},
        {
            "ordered-destination/copied",
            "ordered-destination/copied/level-one",
            "ordered-destination/copied/level-one/level-two",
            "ordered-destination/copied/level-one/level-two/deep.txt",
            "ordered-destination/copied/root.txt",
        },
        "WebDAV COPY did not materialize parents before children",
    )


def assert_webdav_reverse_overlap(ctx, checks):
    for root in ("overlap-move", "overlap-copy", "overlap-overwrite-f"):
        add_file(ctx, root, True)
        add_tree(ctx, f"{root}/source", "child.txt", f"{root}-child-object")
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()

    move_status, _ = dav(
        ctx,
        "MOVE",
        "/dav/overlap-move/source",
        {"Destination": "/dav/overlap-move"},
    )
    copy_status, _ = dav(
        ctx,
        "COPY",
        "/dav/overlap-copy/source",
        {"Destination": "/dav/overlap-copy"},
    )
    overwrite_f_status, _ = dav(
        ctx,
        "COPY",
        "/dav/overlap-overwrite-f/source",
        {
            "Destination": "/dav/overlap-overwrite-f",
            "Overwrite": "F",
        },
    )
    checks.equal(move_status, 409, "WebDAV MOVE to source ancestor")
    checks.equal(copy_status, 409, "WebDAV COPY to source ancestor")
    checks.equal(overwrite_f_status, 412, "Overwrite:F remains first for an ancestor")
    checks.equal(db_state(ctx), before_db, "reverse overlap changed DB or ledger")
    checks.equal(ctx.fake.keys(), before_keys, "reverse overlap changed objects")
    checks.equal(ctx.fake.calls(), [], "reverse overlap reached Qiniu")


def assert_proxy_upload_final_rejection(ctx, checks):
    add_file(ctx, "proxy-final", True)
    old_content = "old proxy bytes must survive"
    add_file(
        ctx,
        "proxy-final/existing.bin",
        False,
        "proxy-final-old-object",
        content_type="application/octet-stream",
        size=len(old_content),
        content=old_content,
    )
    execute_sql(
        ctx,
        """
        CREATE TRIGGER reject_proxy_final_write
        BEFORE UPDATE ON files
        WHEN old.path = 'proxy-final/existing.bin'
        BEGIN
          SELECT raise(abort, 'reject proxy final write');
        END;
        """,
    )
    before_db = db_state(ctx)
    before_state = ctx.fake.state()
    ctx.fake.clear_calls()

    status, _ = api_upload(
        ctx,
        "qiniu://proxy-final",
        name="existing.bin",
        content=b"new proxy bytes",
    )
    after_state = ctx.fake.state()
    calls = ctx.fake.calls()

    checks.true(status >= 400, "proxy upload final metadata rejection status")
    checks.equal(db_state(ctx), before_db, "failed proxy upload changed metadata")
    checks.equal(
        after_state.get("proxy-final-old-object"),
        before_state.get("proxy-final-old-object"),
        "failed proxy upload changed old object bytes",
    )
    assert_uploaded_object_collected(
        checks,
        before_state,
        after_state,
        calls,
        "failed proxy upload",
    )


def assert_fm_copy_deep_target_preflight(ctx, checks):
    add_file(ctx, "fm-deep-source", True)
    add_file(ctx, "fm-deep-source/nested", True)
    add_file(
        ctx,
        "fm-deep-source/nested/file.txt",
        False,
        "fm-deep-source-object",
    )
    add_file(ctx, "fm-deep-destination", True)
    add_file(
        ctx,
        "fm-deep-destination/fm-deep-source/nested/file.txt",
        False,
        "fm-deep-collision-object",
    )
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()

    status, _ = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://",
            "destination": "qiniu://fm-deep-destination",
            "sources": ["qiniu://fm-deep-source"],
        },
    )
    checks.equal(status, 409, "FM deep target collision status")
    checks.equal(db_state(ctx), before_db, "FM deep collision changed DB or ledger")
    checks.equal(ctx.fake.keys(), before_keys, "FM deep collision changed objects")
    checks.equal(ctx.fake.calls(), [], "FM deep collision reached Qiniu")


def assert_copy_source_object_shape_preflight(ctx, checks):
    add_file(ctx, "shape-move-source.txt", False)
    add_file(
        ctx,
        "shape-move-target.txt",
        False,
        "shape-move-target-object",
        content="move target",
    )
    move_status, _ = dav(
        ctx,
        "MOVE",
        "/dav/shape-move-source.txt",
        {"Destination": "/dav/shape-move-target.txt"},
    )
    checks.equal(move_status, 204, "MOVE ignores copy-only object shape")
    moved_rows = rows(ctx, "shape-move-target.txt")
    checks.equal(len(moved_rows), 1, "MOVE malformed-source green row count")
    if len(moved_rows) == 1:
        checks.equal(moved_rows[0][2], None, "MOVE rewrote the malformed source key")
    ctx.fake.clear_calls()

    add_file(ctx, "shape-fm-destination", True)
    add_file(ctx, "shape-fm-source", True)
    add_file(
        ctx,
        "shape-fm-source/a-valid.txt",
        False,
        "shape-fm-valid-object",
    )
    add_file(ctx, "shape-fm-source/z-keyless.txt", False)

    add_file(ctx, "shape-dav-destination", True)
    add_file(
        ctx,
        "shape-dav-source",
        True,
        "shape-dav-directory-object",
    )
    add_file(
        ctx,
        "shape-dav-source/valid.txt",
        False,
        "shape-dav-valid-object",
    )
    add_file(ctx, "shape-overwrite-source", True)
    add_file(
        ctx,
        "shape-overwrite-source/a-valid.txt",
        False,
        "shape-overwrite-valid-object",
    )
    add_file(ctx, "shape-overwrite-source/z-keyless.txt", False)
    add_file(
        ctx,
        "shape-overwrite-target.txt",
        False,
        "shape-overwrite-target-object",
        content="overwrite target must survive",
    )
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    before_state = ctx.fake.state()
    overwrite_rows = rows(ctx, "shape-overwrite-target.txt")
    ctx.fake.clear_calls()

    fm_status, _ = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://",
            "destination": "qiniu://shape-fm-destination",
            "sources": ["qiniu://shape-fm-source"],
        },
    )
    dav_status, _ = dav(
        ctx,
        "COPY",
        "/dav/shape-dav-source",
        {"Destination": "/dav/shape-dav-destination/copied"},
    )
    overwrite_status, _ = dav(
        ctx,
        "COPY",
        "/dav/shape-overwrite-source",
        {"Destination": "/dav/shape-overwrite-target.txt"},
    )

    checks.equal(fm_status, 409, "FM malformed copy source status")
    checks.equal(dav_status, 409, "WebDAV malformed copy source status")
    checks.equal(overwrite_status, 409, "WebDAV malformed overwrite source status")
    checks.equal(
        rows(ctx, "shape-overwrite-target.txt"),
        overwrite_rows,
        "malformed overwrite removed target metadata",
    )
    checks.equal(
        ctx.fake.state().get("shape-overwrite-target-object"),
        before_state.get("shape-overwrite-target-object"),
        "malformed overwrite removed or changed the target object",
    )
    checks.equal(db_state(ctx), before_db, "malformed copy source changed metadata")
    checks.equal(ctx.fake.keys(), before_keys, "malformed copy source changed objects")
    checks.equal(ctx.fake.calls(), [], "malformed copy source reached Qiniu")


def assert_copy_blank_source_key_preflight(ctx, checks):
    blank_key = " \t "
    add_file(ctx, "blank-fm-destination", True)
    add_file(ctx, "blank-fm-source", True)
    add_file(
        ctx,
        "blank-fm-source/a-valid.txt",
        False,
        "blank-fm-valid-object",
    )
    add_file(ctx, "blank-fm-source/z-blank.txt", False, blank_key)

    add_file(ctx, "blank-dav-source", True)
    add_file(
        ctx,
        "blank-dav-source/a-valid.txt",
        False,
        "blank-dav-valid-object",
    )
    add_file(ctx, "blank-dav-source/z-blank.txt", False, blank_key)
    add_file(
        ctx,
        "blank-overwrite-target.txt",
        False,
        "blank-overwrite-target-object",
        content="blank source must not purge overwrite target",
    )

    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    before_state = ctx.fake.state()
    overwrite_rows = rows(ctx, "blank-overwrite-target.txt")
    ctx.fake.clear_calls()

    fm_status, _ = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://",
            "destination": "qiniu://blank-fm-destination",
            "sources": ["qiniu://blank-fm-source"],
        },
    )
    overwrite_status, _ = dav(
        ctx,
        "COPY",
        "/dav/blank-dav-source",
        {"Destination": "/dav/blank-overwrite-target.txt"},
    )

    checks.equal(fm_status, 409, "FM blank source key status")
    checks.equal(overwrite_status, 409, "WebDAV blank source key status")
    checks.equal(
        rows(ctx, "blank-overwrite-target.txt"),
        overwrite_rows,
        "blank source removed overwrite target metadata",
    )
    checks.equal(
        ctx.fake.state().get("blank-overwrite-target-object"),
        before_state.get("blank-overwrite-target-object"),
        "blank source removed or changed the overwrite target object",
    )
    checks.equal(db_state(ctx), before_db, "blank source key changed metadata")
    checks.equal(ctx.fake.keys(), before_keys, "blank source key changed objects")
    checks.equal(ctx.fake.calls(), [], "blank source key reached Qiniu")


def assert_destination_subtree_preflight(ctx, checks):
    add_file(ctx, "rename-green-parent", True)
    add_file(
        ctx,
        "rename-green-parent/source.txt",
        False,
        "rename-green-source",
    )
    add_file(ctx, "orphan-fm-copy-parent", True)
    add_file(ctx, "orphan-fm-copy-source.txt", False, "orphan-fm-copy-source")
    add_file(
        ctx,
        "orphan-fm-copy-parent/orphan-fm-copy-source.txt/legacy.txt",
        False,
        "orphan-fm-copy-legacy",
    )
    add_file(ctx, "orphan-fm-move-parent", True)
    add_file(ctx, "orphan-fm-move-source.txt", False, "orphan-fm-move-source")
    add_file(
        ctx,
        "orphan-fm-move-parent/orphan-fm-move-source.txt/legacy.txt",
        False,
        "orphan-fm-move-legacy",
    )
    add_file(ctx, "orphan-dav-copy-parent", True)
    add_file(ctx, "orphan-dav-copy-source.txt", False, "orphan-dav-copy-source")
    add_file(
        ctx,
        "orphan-dav-copy-parent/target/legacy.txt",
        False,
        "orphan-dav-copy-legacy",
    )
    add_file(ctx, "orphan-dav-move-parent", True)
    add_file(ctx, "orphan-dav-move-source.txt", False, "orphan-dav-move-source")
    add_file(
        ctx,
        "orphan-dav-move-parent/target/legacy.txt",
        False,
        "orphan-dav-move-legacy",
    )
    rename_status, _ = api(
        ctx,
        "/api/fm/rename",
        {
            "path": "qiniu://rename-green-parent",
            "item": "qiniu://rename-green-parent/source.txt",
            "name": "target.txt",
        },
    )
    checks.equal(rename_status, 200, "FM rename remains a final-transaction operation")
    checks.true(
        "rename-green-parent/target.txt" in {row[0] for row in rows(ctx)},
        "FM rename green control did not rewrite its source",
    )

    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()
    statuses = {
        "FM COPY": api(
            ctx,
            "/api/fm/copy",
            {
                "path": "qiniu://",
                "destination": "qiniu://orphan-fm-copy-parent",
                "sources": ["qiniu://orphan-fm-copy-source.txt"],
            },
        )[0],
        "FM MOVE": api(
            ctx,
            "/api/fm/move",
            {
                "path": "qiniu://",
                "destination": "qiniu://orphan-fm-move-parent",
                "sources": ["qiniu://orphan-fm-move-source.txt"],
            },
        )[0],
        "WebDAV COPY": dav(
            ctx,
            "COPY",
            "/dav/orphan-dav-copy-source.txt",
            {"Destination": "/dav/orphan-dav-copy-parent/target"},
        )[0],
        "WebDAV MOVE": dav(
            ctx,
            "MOVE",
            "/dav/orphan-dav-move-source.txt",
            {"Destination": "/dav/orphan-dav-move-parent/target"},
        )[0],
    }
    for label, status in statuses.items():
        checks.equal(status, 409, f"{label} unmapped destination descendant")
    checks.equal(db_state(ctx), before_db, "orphan destination changed metadata")
    checks.equal(ctx.fake.keys(), before_keys, "orphan destination changed objects")
    checks.equal(ctx.fake.calls(), [], "orphan destination reached Qiniu")


def assert_webdav_overwrite_gc_after_switch(ctx, checks):
    parent = "overwrite-gc"
    source = f"{parent}/source.txt"
    target = f"{parent}/target.txt"
    source_key = "overwrite-gc-source-object"
    old_target_key = "overwrite-gc-old-target-object"
    source_body = "source bytes copied before the switch"
    concurrent_body = b"concurrent writer survives postcommit GC"
    add_file(ctx, parent, True)
    add_file(
        ctx,
        source,
        False,
        source_key,
        size=len(source_body.encode()),
        content=source_body,
    )
    add_file(
        ctx,
        target,
        False,
        old_target_key,
        size=len("old target"),
        content="old target",
    )
    source_before = file_row(ctx, source)
    ctx.fake.clear_calls()
    ctx.fake.pause("delete")

    copy_result, copy_done, copy_thread = start_background(
        lambda: dav(
            ctx,
            "COPY",
            f"/dav/{source}",
            {"Destination": f"/dav/{target}"},
        )
    )
    entered = wait_for_pause(ctx.fake, "delete")
    copied_key = None
    switched_row = None
    put_result = None
    put_done = None
    put_thread = None
    upload_seen = False
    writer_blocked = False
    row_while_blocked = None
    state_while_paused = {}
    try:
        if entered:
            copy_calls = [call for call in ctx.fake.calls() if call.get("op") == "copy"]
            if len(copy_calls) == 1:
                copied_key = copy_calls[0].get("dst")
            switched_row = file_row(ctx, target)
            state_while_paused = ctx.fake.state()
            put_result, put_done, put_thread = start_background(
                lambda: dav(ctx, "PUT", f"/dav/{target}", body=concurrent_body)
            )
            upload_seen = wait_for_call(ctx.fake, "upload")
            if upload_seen:
                writer_blocked = not put_done.wait(timeout=0.3)
                row_while_blocked = file_row(ctx, target)
    finally:
        ctx.fake.release("delete")

    copy_finished = finish_background(copy_thread, copy_done)
    put_finished = False
    if put_thread is not None and put_done is not None:
        put_finished = finish_background(put_thread, put_done)

    checks.true(entered, "overwrite COPY did not reach postcommit destination GC")
    checks.true(copy_finished, "overwrite COPY did not finish after GC release")
    assert_background_response(checks, copy_result, copy_done, "overwrite COPY", 204)
    checks.true(upload_seen, "concurrent PUT did not finish its object upload")
    checks.true(
        writer_blocked,
        "concurrent PUT metadata switch was not blocked by the GC writer lock",
    )
    checks.true(put_finished, "concurrent PUT did not finish after GC release")
    if put_result is None or put_done is None:
        checks.true(False, "concurrent PUT was not started")
    else:
        assert_background_response(checks, put_result, put_done, "concurrent PUT", 204)

    expected_switched = None
    if source_before is not None and copied_key is not None:
        expected_switched = (
            target,
            source_before[1],
            copied_key,
            source_before[3],
            source_before[4],
        )
    checks.equal(
        switched_row,
        expected_switched,
        "COPY metadata was not fully switched before destination GC",
    )
    checks.equal(
        row_while_blocked,
        expected_switched,
        "concurrent PUT changed metadata while destination GC held the lock",
    )
    checks.true(
        old_target_key in state_while_paused,
        "destination GC removed the old object before the paused delete",
    )
    if copied_key is not None:
        checks.equal(
            base64.b64decode(state_while_paused[copied_key]["content"])
            if copied_key in state_while_paused
            else None,
            source_body.encode(),
            "switched COPY object bytes",
        )

    calls = ctx.fake.calls()
    copy_calls = [call for call in calls if call.get("op") == "copy"]
    upload_calls = [call for call in calls if call.get("op") == "upload"]
    delete_calls = [call for call in calls if call.get("op") == "delete"]
    checks.equal(len(copy_calls), 1, "overwrite COPY object-copy count")
    checks.equal(len(upload_calls), 1, "concurrent PUT upload count")
    checks.equal(len(delete_calls), 2, "postcommit GC delete count")
    checks.equal(
        [call.get("op") for call in calls],
        ["copy", "upload", "delete", "delete"],
        "postcommit GC and concurrent PUT object operation order",
    )
    if copy_calls and upload_calls:
        copied_key = copy_calls[0].get("dst")
        uploaded_key = upload_calls[0].get("key")
        checks.equal(
            {call.get("key") for call in delete_calls},
            {old_target_key, copied_key},
            "postcommit GC candidate set",
        )
        final_row = file_row(ctx, target)
        checks.true(final_row is not None, "concurrent PUT target metadata is missing")
        if final_row is not None:
            checks.equal(final_row[2], uploaded_key, "concurrent PUT final key")
        checks.equal(
            object_bytes(ctx, uploaded_key),
            concurrent_body,
            "concurrent PUT final object bytes",
        )
        checks.true(old_target_key not in ctx.fake.keys(), "old target object survived")
        checks.true(
            copied_key not in ctx.fake.keys(), "superseded COPY object survived"
        )

    checks.equal(file_row(ctx, source), source_before, "COPY changed source metadata")
    checks.equal(dangling_file_paths(ctx), [], "postcommit GC left dangling metadata")
    get_status, get_body = dav(
        ctx,
        "GET",
        f"/dav/{target}",
        {"User-Agent": "contract-proxy-client/1.0"},
    )
    checks.equal(get_status, 200, "concurrent PUT final GET status")
    checks.equal(get_body, concurrent_body, "concurrent PUT final GET bytes")


def assert_webdav_put_actual_superseded_key(ctx, checks):
    parent = "put-superseded"
    target = f"{parent}/target.txt"
    original_key = "put-superseded-k0"
    raced_key = "put-superseded-k2"
    race_body = b"uploaded K1 must replace the raced K2 row"
    add_file(ctx, parent, True)
    add_file(
        ctx,
        target,
        False,
        original_key,
        size=len("original"),
        content="original",
    )
    ctx.fake.clear_calls()
    ctx.fake.pause("upload")
    result, done, thread = start_background(
        lambda: dav(ctx, "PUT", f"/dav/{target}", body=race_body)
    )
    entered = wait_for_pause(ctx.fake, "upload")
    switched = 0
    try:
        if entered:
            switched = set_file_key(
                ctx,
                target,
                raced_key,
                size=len("raced replacement"),
            )
            ctx.fake.plant(raced_key, "raced replacement", "text/plain")
    finally:
        ctx.fake.release("upload")
    finished = finish_background(thread, done)

    checks.true(entered, "PUT did not reach the paused upload")
    checks.equal(switched, 1, "race fixture did not replace P with K2")
    checks.true(finished, "raced PUT did not finish after upload release")
    assert_background_response(checks, result, done, "raced PUT", 204)
    calls = ctx.fake.calls()
    uploads = [call for call in calls if call.get("op") == "upload"]
    deletes = [call for call in calls if call.get("op") == "delete"]
    checks.equal(
        [call.get("op") for call in calls],
        ["upload", "delete"],
        "raced PUT object operation order",
    )
    checks.equal(len(uploads), 1, "raced PUT upload count")
    checks.equal(len(deletes), 1, "raced PUT delete count")
    if uploads and deletes:
        uploaded_key = uploads[0].get("key")
        checks.equal(deletes[0].get("key"), raced_key, "raced PUT cleanup key")
        target_row = file_row(ctx, target)
        checks.true(target_row is not None, "raced PUT target row is missing")
        if target_row is not None:
            checks.equal(target_row[2], uploaded_key, "raced PUT final K1 metadata")
        checks.equal(object_bytes(ctx, uploaded_key), race_body, "raced PUT K1 bytes")
    checks.true(original_key in ctx.fake.keys(), "PUT incorrectly collected stale K0")
    checks.true(raced_key not in ctx.fake.keys(), "PUT did not collect actual K2")

    control = f"{parent}/control.txt"
    first_body = b"ordinary create control"
    second_body = b"ordinary overwrite control"
    ctx.fake.clear_calls()
    create_status, _ = dav(ctx, "PUT", f"/dav/{control}", body=first_body)
    first_row = file_row(ctx, control)
    create_calls = ctx.fake.calls()
    checks.equal(create_status, 201, "ordinary PUT create status")
    checks.equal(
        [call.get("op") for call in create_calls],
        ["upload"],
        "ordinary PUT create object operations",
    )
    checks.true(first_row is not None, "ordinary PUT create metadata")

    ctx.fake.clear_calls()
    overwrite_status, _ = dav(ctx, "PUT", f"/dav/{control}", body=second_body)
    second_row = file_row(ctx, control)
    overwrite_calls = ctx.fake.calls()
    checks.equal(overwrite_status, 204, "ordinary PUT overwrite status")
    checks.equal(
        [call.get("op") for call in overwrite_calls],
        ["upload", "delete"],
        "ordinary PUT overwrite object operations",
    )
    if first_row is not None and second_row is not None:
        checks.true(first_row[2] != second_row[2], "ordinary PUT reused its object key")
        checks.true(first_row[2] not in ctx.fake.keys(), "ordinary PUT kept old object")
        checks.equal(
            object_bytes(ctx, second_row[2]),
            second_body,
            "ordinary PUT overwrite bytes",
        )


def assert_object_gc_live_key_reference(ctx, checks):
    parent = "gc-live-reference"
    shared_key = "gc-live-shared-object"
    shared_body = "shared bytes must remain readable"
    first = f"{parent}/a.txt"
    second = f"{parent}/b.txt"
    add_file(ctx, parent, True)
    add_file(
        ctx,
        first,
        False,
        shared_key,
        size=len(shared_body.encode()),
        content=shared_body,
    )
    add_file(
        ctx,
        second,
        False,
        shared_key,
        size=len(shared_body.encode()),
        plant=False,
    )
    second_before = file_row(ctx, second)
    ctx.fake.clear_calls()
    shared_status, _ = dav(
        ctx,
        "PUT",
        f"/dav/{first}",
        body=b"A gets a private replacement",
    )
    shared_calls = ctx.fake.calls()
    checks.equal(shared_status, 204, "shared-key PUT status")
    checks.equal(
        [call.get("op") for call in shared_calls],
        ["upload"],
        "shared-key GC reached object delete",
    )
    checks.equal(file_row(ctx, second), second_before, "shared-key peer metadata")
    checks.true(shared_key in ctx.fake.keys(), "shared live key was deleted")
    get_status, get_body = dav(
        ctx,
        "GET",
        f"/dav/{second}",
        {"User-Agent": "contract-proxy-client/1.0"},
    )
    checks.equal(get_status, 200, "shared-key peer GET status")
    checks.equal(get_body, shared_body.encode(), "shared-key peer GET bytes")

    solo = f"{parent}/solo.txt"
    solo_key = "gc-live-solo-object"
    add_file(ctx, solo, False, solo_key, content="unshared old bytes")
    ctx.fake.clear_calls()
    solo_status, _ = dav(
        ctx,
        "PUT",
        f"/dav/{solo}",
        body=b"solo replacement",
    )
    solo_calls = ctx.fake.calls()
    checks.equal(solo_status, 204, "unshared-key PUT status")
    checks.equal(
        [call.get("op") for call in solo_calls],
        ["upload", "delete"],
        "unshared-key GC object operation order",
    )
    checks.true(solo_key not in ctx.fake.keys(), "unshared superseded key survived")
    if len(solo_calls) == 2:
        checks.equal(solo_calls[1].get("key"), solo_key, "unshared GC key")


def assert_webdav_delete_metadata_first_concurrent_rename(ctx, checks):
    parent = "delete-race"
    source = f"{parent}/p.txt"
    destination = f"{parent}/q.txt"
    source_key = "delete-race-source-object"
    add_file(ctx, parent, True)
    add_file(ctx, source, False, source_key, content="delete race")
    ctx.fake.clear_calls()
    ctx.fake.pause("delete")
    delete_result, delete_done, delete_thread = start_background(
        lambda: dav(ctx, "DELETE", f"/dav/{source}")
    )
    entered = wait_for_pause(ctx.fake, "delete")
    source_absent_at_pause = False
    rename_result = None
    rename_done = None
    rename_thread = None
    rename_finished_while_gc = False
    try:
        if entered:
            source_absent_at_pause = file_row(ctx, source) is None
            rename_result, rename_done, rename_thread = start_background(
                lambda: dav(
                    ctx,
                    "MOVE",
                    f"/dav/{source}",
                    {"Destination": f"/dav/{destination}"},
                )
            )
            rename_finished_while_gc = rename_done.wait(timeout=1)
    finally:
        ctx.fake.release("delete")

    delete_finished = finish_background(delete_thread, delete_done)
    rename_finished = False
    if rename_thread is not None and rename_done is not None:
        rename_finished = finish_background(rename_thread, rename_done)
    checks.true(entered, "DELETE did not reach paused object GC")
    checks.true(source_absent_at_pause, "DELETE left P metadata visible during GC")
    checks.true(
        rename_finished_while_gc,
        "concurrent rename could not observe metadata-first deletion",
    )
    checks.true(delete_finished, "DELETE did not finish after GC release")
    assert_background_response(checks, delete_result, delete_done, "DELETE", 204)
    checks.true(rename_finished, "concurrent rename did not finish")
    if rename_result is None or rename_done is None:
        checks.true(False, "concurrent rename was not started")
    else:
        assert_background_response(
            checks,
            rename_result,
            rename_done,
            "concurrent rename",
            404,
        )
    checks.equal(file_row(ctx, source), None, "DELETE source metadata survived")
    checks.equal(file_row(ctx, destination), None, "concurrent rename created Q")
    checks.true(source_key not in ctx.fake.keys(), "DELETE source object survived")
    checks.equal(
        [call.get("op") for call in ctx.fake.calls()],
        ["delete"],
        "DELETE race object operations",
    )
    checks.equal(dangling_file_paths(ctx), [], "DELETE race left dangling metadata")


def assert_webdav_delete_metadata_subtree_atomic(ctx, checks):
    root = "delete-atomic"
    first_key = "delete-atomic-first-object"
    second_key = "delete-atomic-second-object"
    add_file(ctx, root, True)
    add_file(ctx, f"{root}/a.txt", False, first_key, content="first")
    add_file(ctx, f"{root}/nested", True)
    add_file(
        ctx,
        f"{root}/nested/z.txt",
        False,
        second_key,
        content="second",
    )
    execute_sql(
        ctx,
        f"""
        CREATE TRIGGER reject_delete_atomic_child
        BEFORE DELETE ON files
        WHEN old.path = '{root}/nested/z.txt'
        BEGIN
          SELECT raise(abort, 'reject atomic subtree delete');
        END;
        """,
    )
    before_db = db_state(ctx)
    before_state = ctx.fake.state()
    ctx.fake.clear_calls()
    failed_status, _ = dav(ctx, "DELETE", f"/dav/{root}")
    checks.equal(failed_status, 500, "rejected subtree DELETE status")
    checks.equal(db_state(ctx), before_db, "rejected subtree DELETE changed metadata")
    checks.equal(
        ctx.fake.state(),
        before_state,
        "rejected subtree DELETE changed objects",
    )
    checks.equal(ctx.fake.calls(), [], "rejected subtree DELETE reached object GC")

    execute_sql(ctx, "DROP TRIGGER reject_delete_atomic_child;")
    ctx.fake.clear_calls()
    control_status, _ = dav(ctx, "DELETE", f"/dav/{root}")
    control_calls = ctx.fake.calls()
    checks.equal(control_status, 204, "subtree DELETE success control status")
    checks.equal(rows(ctx, root), [], "subtree DELETE success metadata")
    checks.true(first_key not in ctx.fake.keys(), "subtree DELETE kept first object")
    checks.true(second_key not in ctx.fake.keys(), "subtree DELETE kept second object")
    checks.equal(
        {call.get("key") for call in control_calls if call.get("op") == "delete"},
        {first_key, second_key},
        "subtree DELETE success object set",
    )
    checks.equal(
        [call.get("op") for call in control_calls],
        ["delete", "delete"],
        "subtree DELETE success object operations",
    )


def assert_webdav_overwrite_copy_failure_preserves_target(ctx, checks):
    parent = "copy-refusal-preserves"
    source = f"{parent}/source.txt"
    target = f"{parent}/target.txt"
    source_key = "copy-refusal-source-object"
    target_key = "copy-refusal-target-object"
    add_file(ctx, parent, True)
    add_file(ctx, source, False, source_key, content="source survives refusal")
    add_file(ctx, target, False, target_key, content="target survives refusal")
    before_db = db_state(ctx)
    before_state = ctx.fake.state()
    source_before = file_row(ctx, source)
    target_before = file_row(ctx, target)
    ctx.fake.clear_calls()
    ctx.fake.refuse("copy", 599, '{"error":"copy refusal seam"}')
    try:
        status, _ = dav(
            ctx,
            "COPY",
            f"/dav/{source}",
            {"Destination": f"/dav/{target}"},
        )
    finally:
        ctx.fake.allow("copy")
    calls = ctx.fake.calls()
    checks.equal(status, 502, "overwrite COPY refusal status")
    checks.equal(db_state(ctx), before_db, "overwrite COPY refusal changed metadata")
    checks.equal(
        ctx.fake.state(), before_state, "overwrite COPY refusal changed objects"
    )
    checks.equal(file_row(ctx, source), source_before, "COPY refusal changed source")
    checks.equal(file_row(ctx, target), target_before, "COPY refusal changed target")
    checks.equal(len(calls), 1, "overwrite COPY refusal object call count")
    if calls:
        checks.equal(calls[0].get("op"), "copy", "overwrite COPY refusal operation")
        checks.equal(
            calls[0].get("outcome"),
            "refused",
            "overwrite COPY refusal did not reach fake seam",
        )
    checks.true(
        all(call.get("op") != "delete" for call in calls),
        "overwrite COPY refusal deleted the old target",
    )


def assert_webdav_overwrite_move_failure_preserves_target(ctx, checks):
    parent = "move-failure-preserves"
    source = f"{parent}/source.txt"
    target = f"{parent}/target.txt"
    source_key = "move-failure-source-object"
    target_key = "move-failure-target-object"
    add_file(ctx, parent, True)
    add_file(ctx, source, False, source_key, content="source survives DB failure")
    add_file(ctx, target, False, target_key, content="target survives DB failure")
    execute_sql(
        ctx,
        f"""
        CREATE TRIGGER reject_overwrite_move_update
        BEFORE UPDATE OF path ON files
        WHEN old.path = '{source}'
        BEGIN
          SELECT raise(abort, 'reject overwrite MOVE source rewrite');
        END;
        """,
    )
    before_db = db_state(ctx)
    before_state = ctx.fake.state()
    source_before = file_row(ctx, source)
    target_before = file_row(ctx, target)
    ctx.fake.clear_calls()
    status, _ = dav(
        ctx,
        "MOVE",
        f"/dav/{source}",
        {"Destination": f"/dav/{target}"},
    )
    checks.equal(status, 500, "overwrite MOVE late DB failure status")
    checks.equal(db_state(ctx), before_db, "overwrite MOVE failure changed metadata")
    checks.equal(
        ctx.fake.state(), before_state, "overwrite MOVE failure changed objects"
    )
    checks.equal(file_row(ctx, source), source_before, "MOVE failure changed source")
    checks.equal(file_row(ctx, target), target_before, "MOVE failure changed target")
    checks.equal(ctx.fake.calls(), [], "overwrite MOVE failure reached object GC")


def assert_webdav_overwrite_metadata_switch_atomic(ctx, checks):
    copy_root = "switch-atomic-copy"
    copy_source = f"{copy_root}/source"
    copy_target = f"{copy_root}/target"
    copy_a_key = "switch-atomic-copy-a-object"
    copy_z_key = "switch-atomic-copy-z-object"
    copy_old_key = "switch-atomic-copy-old-object"
    add_file(ctx, copy_root, True)
    add_file(ctx, copy_source, True)
    add_file(ctx, f"{copy_source}/a.txt", False, copy_a_key, content="copy a")
    add_file(ctx, f"{copy_source}/z.txt", False, copy_z_key, content="copy z")
    add_file(ctx, copy_target, True)
    add_file(ctx, f"{copy_target}/old.txt", False, copy_old_key, content="copy old")
    execute_sql(
        ctx,
        f"""
        CREATE TRIGGER reject_late_copy_switch
        BEFORE INSERT ON files
        WHEN new.path = '{copy_target}/z.txt'
        BEGIN
          SELECT raise(abort, 'reject late COPY metadata switch');
        END;
        """,
    )
    before_copy_db = db_state(ctx)
    before_copy_state = ctx.fake.state()
    ctx.fake.clear_calls()
    copy_status, _ = dav(
        ctx,
        "COPY",
        f"/dav/{copy_source}",
        {"Destination": f"/dav/{copy_target}"},
    )
    copy_calls = ctx.fake.calls()
    checks.equal(copy_status, 500, "late COPY metadata switch status")
    checks.equal(
        db_state(ctx),
        before_copy_db,
        "late COPY metadata switch left partial target metadata",
    )
    assert_copied_objects_collected(
        checks,
        before_copy_state,
        ctx.fake.state(),
        copy_calls,
        "late COPY metadata switch",
    )
    checks.true(
        all(
            call.get("key") != copy_old_key
            for call in copy_calls
            if call.get("op") == "delete"
        ),
        "late COPY metadata switch deleted the old target object",
    )

    move_root = "switch-atomic-move"
    move_source = f"{move_root}/source"
    move_target = f"{move_root}/target"
    move_a_key = "switch-atomic-move-a-object"
    move_z_key = "switch-atomic-move-z-object"
    move_old_key = "switch-atomic-move-old-object"
    add_file(ctx, move_root, True)
    add_file(ctx, move_source, True)
    add_file(ctx, f"{move_source}/a.txt", False, move_a_key, content="move a")
    add_file(ctx, f"{move_source}/z.txt", False, move_z_key, content="move z")
    add_file(ctx, move_target, True)
    add_file(ctx, f"{move_target}/old.txt", False, move_old_key, content="move old")
    execute_sql(
        ctx,
        f"""
        CREATE TRIGGER reject_late_move_switch
        BEFORE UPDATE OF path ON files
        WHEN old.path = '{move_source}/z.txt'
        BEGIN
          SELECT raise(abort, 'reject late MOVE metadata switch');
        END;
        """,
    )
    before_move_db = db_state(ctx)
    before_move_state = ctx.fake.state()
    ctx.fake.clear_calls()
    move_status, _ = dav(
        ctx,
        "MOVE",
        f"/dav/{move_source}",
        {"Destination": f"/dav/{move_target}"},
    )
    checks.equal(move_status, 500, "late MOVE metadata switch status")
    checks.equal(
        db_state(ctx),
        before_move_db,
        "late MOVE metadata switch left partial source or target metadata",
    )
    checks.equal(
        ctx.fake.state(),
        before_move_state,
        "late MOVE metadata switch changed objects",
    )
    checks.equal(ctx.fake.calls(), [], "late MOVE metadata switch reached object GC")

    copy_control_root = "switch-control-copy"
    copy_control_source = f"{copy_control_root}/source"
    copy_control_target = f"{copy_control_root}/target"
    copy_control_source_key = "switch-control-copy-source-object"
    copy_control_old_key = "switch-control-copy-old-object"
    add_file(ctx, copy_control_root, True)
    add_file(ctx, copy_control_source, True)
    add_file(
        ctx,
        f"{copy_control_source}/file.txt",
        False,
        copy_control_source_key,
        content="copy control bytes",
    )
    add_file(ctx, copy_control_target, True)
    add_file(
        ctx,
        f"{copy_control_target}/old.txt",
        False,
        copy_control_old_key,
        content="old copy control",
    )
    ctx.fake.clear_calls()
    copy_control_status, _ = dav(
        ctx,
        "COPY",
        f"/dav/{copy_control_source}",
        {"Destination": f"/dav/{copy_control_target}"},
    )
    copy_control_calls = ctx.fake.calls()
    checks.equal(copy_control_status, 204, "COPY metadata-switch control status")
    checks.equal(
        {row[0] for row in rows(ctx, copy_control_target)},
        {copy_control_target, f"{copy_control_target}/file.txt"},
        "COPY metadata-switch control target tree",
    )
    checks.true(
        file_row(ctx, f"{copy_control_source}/file.txt") is not None,
        "COPY metadata-switch control removed source",
    )
    checks.true(
        copy_control_source_key in ctx.fake.keys(),
        "COPY metadata-switch control removed source object",
    )
    checks.true(
        copy_control_old_key not in ctx.fake.keys(),
        "COPY metadata-switch control kept old target object",
    )
    checks.equal(
        [call.get("op") for call in copy_control_calls],
        ["copy", "delete"],
        "COPY metadata-switch control object operations",
    )

    move_control_root = "switch-control-move"
    move_control_source = f"{move_control_root}/source"
    move_control_target = f"{move_control_root}/target"
    move_control_source_key = "switch-control-move-source-object"
    move_control_old_key = "switch-control-move-old-object"
    add_file(ctx, move_control_root, True)
    add_file(ctx, move_control_source, True)
    add_file(
        ctx,
        f"{move_control_source}/file.txt",
        False,
        move_control_source_key,
        content="move control bytes",
    )
    add_file(ctx, move_control_target, True)
    add_file(
        ctx,
        f"{move_control_target}/old.txt",
        False,
        move_control_old_key,
        content="old move control",
    )
    ctx.fake.clear_calls()
    move_control_status, _ = dav(
        ctx,
        "MOVE",
        f"/dav/{move_control_source}",
        {"Destination": f"/dav/{move_control_target}"},
    )
    move_control_calls = ctx.fake.calls()
    checks.equal(move_control_status, 204, "MOVE metadata-switch control status")
    checks.equal(rows(ctx, move_control_source), [], "MOVE control source survived")
    checks.equal(
        {row[0] for row in rows(ctx, move_control_target)},
        {move_control_target, f"{move_control_target}/file.txt"},
        "MOVE metadata-switch control target tree",
    )
    moved_row = file_row(ctx, f"{move_control_target}/file.txt")
    checks.true(moved_row is not None, "MOVE metadata-switch control target file")
    if moved_row is not None:
        checks.equal(moved_row[2], move_control_source_key, "MOVE control object key")
    checks.true(
        move_control_source_key in ctx.fake.keys(),
        "MOVE metadata-switch control removed source object",
    )
    checks.true(
        move_control_old_key not in ctx.fake.keys(),
        "MOVE metadata-switch control kept old target object",
    )
    checks.equal(
        [call.get("op") for call in move_control_calls],
        ["delete"],
        "MOVE metadata-switch control object operations",
    )


def assert_fm_copy_metadata_atomic(ctx, checks):
    add_file(ctx, "fm-atomic-source", True)
    add_file(ctx, "fm-atomic-source/nested", True)
    add_file(
        ctx,
        "fm-atomic-source/nested/file.txt",
        False,
        "fm-atomic-source-object",
    )
    execute_sql(
        ctx,
        """
        CREATE TRIGGER reject_fm_atomic_copy
        BEFORE INSERT ON files
        WHEN new.path = 'fm-atomic-parent/deep/fm-atomic-source/nested/file.txt'
        BEGIN
          SELECT raise(abort, 'reject FM atomic copy');
        END;
        """,
    )
    before_db = db_state(ctx)
    before_state = ctx.fake.state()
    ctx.fake.clear_calls()

    status, _ = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://",
            "destination": "qiniu://fm-atomic-parent/deep",
            "sources": ["qiniu://fm-atomic-source"],
        },
    )
    checks.true(status >= 400, "FM final copy failure status")
    checks.equal(
        db_state(ctx), before_db, "FM final copy failure left partial metadata"
    )
    assert_copied_objects_collected(
        checks,
        before_state,
        ctx.fake.state(),
        ctx.fake.calls(),
        "FM final copy failure",
    )


def assert_webdav_copy_metadata_atomic(ctx, checks):
    add_file(ctx, "dav-atomic-source", True)
    add_file(ctx, "dav-atomic-source/nested", True)
    add_file(
        ctx,
        "dav-atomic-source/nested/file.txt",
        False,
        "dav-atomic-source-object",
    )
    add_file(ctx, "dav-atomic-parent", True)
    execute_sql(
        ctx,
        """
        CREATE TRIGGER reject_dav_atomic_copy
        BEFORE INSERT ON files
        WHEN new.path = 'dav-atomic-parent/copied/nested/file.txt'
        BEGIN
          SELECT raise(abort, 'reject WebDAV atomic copy');
        END;
        """,
    )
    before_db = db_state(ctx)
    before_state = ctx.fake.state()
    ctx.fake.clear_calls()

    status, _ = dav(
        ctx,
        "COPY",
        "/dav/dav-atomic-source",
        {
            "Destination": "/dav/dav-atomic-parent/copied",
            "Overwrite": "F",
        },
    )
    checks.true(status >= 400, "WebDAV final copy failure status")
    checks.equal(
        db_state(ctx), before_db, "WebDAV final copy failure left partial metadata"
    )
    assert_copied_objects_collected(
        checks,
        before_state,
        ctx.fake.state(),
        ctx.fake.calls(),
        "WebDAV final copy failure",
    )


def assert_fm_external_effect_preflight(ctx, checks):
    seed_deep_file_ancestor(ctx)
    add_file(
        ctx,
        "blocked/legacy-dir/save.txt",
        False,
        "blocked-save-old-object",
    )
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()

    statuses = {
        "create-file": api(
            ctx,
            "/api/fm/create-file",
            {"path": "qiniu://blocked/legacy-dir", "name": "created.txt"},
        )[0],
        "save": api(
            ctx,
            "/api/fm/save",
            {"path": "qiniu://blocked/legacy-dir/save.txt", "content": "new"},
        )[0],
        "upload": api_upload(ctx, "qiniu://blocked/legacy-dir")[0],
        "copy": api(
            ctx,
            "/api/fm/copy",
            {
                "path": "qiniu://",
                "destination": "qiniu://blocked/legacy-dir",
                "sources": ["qiniu://docs/notes.txt"],
            },
        )[0],
    }
    for endpoint, status in statuses.items():
        checks.equal(status, 409, f"FM {endpoint} file-ancestor rejection")
    checks.equal(db_state(ctx), before_db, "FM rejected writes changed DB or ledger")
    checks.equal(ctx.fake.keys(), before_keys, "FM rejected writes changed objects")
    checks.equal(ctx.fake.calls(), [], "FM rejected writes reached Qiniu")


def assert_directory_target_preflight(ctx, checks):
    add_file(ctx, "target-dir", True)
    ctx.fake.plant("directory-register-object", "registered", "text/plain")
    add_ledger(ctx, "directory-register-object", "qiniu://target-dir")
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()

    statuses = {
        "create-file": api(
            ctx,
            "/api/fm/create-file",
            {"path": "qiniu://", "name": "target-dir"},
        )[0],
        "save": api(
            ctx,
            "/api/fm/save",
            {"path": "qiniu://target-dir", "content": "new"},
        )[0],
        "upload-token": api(
            ctx,
            "/api/fm/upload-token",
            {"path": "qiniu://", "name": "target-dir"},
        )[0],
        "register": api(
            ctx,
            "/api/fm/register",
            {"path": "qiniu://target-dir", "key": "directory-register-object"},
        )[0],
        "upload": api_upload(ctx, "qiniu://", name="target-dir")[0],
    }
    for endpoint, status in statuses.items():
        checks.equal(status, 409, f"FM {endpoint} directory-target rejection")
    checks.equal(
        db_state(ctx), before_db, "directory-target rejection changed DB or ledger"
    )
    checks.equal(
        ctx.fake.keys(), before_keys, "directory-target rejection changed objects"
    )
    checks.equal(ctx.fake.calls(), [], "directory-target rejection reached Qiniu")


def assert_file_target_descendant_preflight(ctx, checks):
    parent = "descendant-preflight"
    targets = {
        "create-file": f"{parent}/create.txt",
        "save": f"{parent}/save.txt",
        "upload-token": f"{parent}/token.bin",
        "register": f"{parent}/register.txt",
        "upload": f"{parent}/upload.bin",
        "WebDAV PUT": f"{parent}/dav.txt",
    }
    add_file(ctx, parent, True)
    for target in targets.values():
        add_file(ctx, f"{target}/legacy-child", True)
    ctx.fake.plant("descendant-register-object", "registered", "text/plain")
    add_ledger(
        ctx,
        "descendant-register-object",
        f"qiniu://{targets['register']}",
    )
    before_db = db_state(ctx)
    before_state = ctx.fake.state()
    ctx.fake.clear_calls()

    statuses = {
        "create-file": api(
            ctx,
            "/api/fm/create-file",
            {"path": f"qiniu://{parent}", "name": "create.txt"},
        )[0],
        "save": api(
            ctx,
            "/api/fm/save",
            {"path": f"qiniu://{targets['save']}", "content": "new"},
        )[0],
        "upload-token": api(
            ctx,
            "/api/fm/upload-token",
            {"path": f"qiniu://{parent}", "name": "token.bin"},
        )[0],
        "register": api(
            ctx,
            "/api/fm/register",
            {
                "path": f"qiniu://{targets['register']}",
                "key": "descendant-register-object",
            },
        )[0],
        "upload": api_upload(ctx, f"qiniu://{parent}", name="upload.bin")[0],
        "WebDAV PUT": dav(
            ctx,
            "PUT",
            f"/dav/{targets['WebDAV PUT']}",
            body=b"descendant preflight",
        )[0],
    }
    for endpoint, status in statuses.items():
        checks.equal(status, 409, f"{endpoint} descendant-target rejection")
    checks.equal(
        db_state(ctx), before_db, "descendant-target rejection changed DB or ledger"
    )
    checks.equal(
        ctx.fake.state(),
        before_state,
        "descendant-target rejection changed objects",
    )
    checks.equal(ctx.fake.calls(), [], "descendant-target rejection reached Qiniu")


def assert_file_target_descendant_final_race(ctx, checks):
    parent = "descendant-race"
    add_file(ctx, parent, True)
    cases = [
        (
            "FM upload",
            f"{parent}/fm.bin",
            lambda: api_upload(ctx, f"qiniu://{parent}", name="fm.bin"),
        ),
        (
            "WebDAV PUT",
            f"{parent}/dav.bin",
            lambda: dav(
                ctx,
                "PUT",
                f"/dav/{parent}/dav.bin",
                body=b"late descendant race",
            ),
        ),
    ]

    for label, target, request_call in cases:
        before_state = ctx.fake.state()
        ctx.fake.clear_calls()
        ctx.fake.pause("upload")
        result, done, thread = start_background(request_call)
        entered = wait_for_pause(ctx.fake, "upload")
        child = f"{target}/late-child"
        try:
            if entered:
                add_file(ctx, child, True)
        finally:
            ctx.fake.release("upload")
        finished = finish_background(thread, done)

        checks.true(entered, f"{label} did not reach the paused upload")
        checks.true(finished, f"{label} did not finish after upload release")
        assert_background_response(checks, result, done, label, 409)
        target_rows = rows(ctx, target)
        checks.equal(
            [row[0] for row in target_rows],
            [child],
            f"{label} created a file root over a late descendant",
        )
        assert_uploaded_object_collected(
            checks,
            before_state,
            ctx.fake.state(),
            ctx.fake.calls(),
            f"{label} late descendant rejection",
        )


def assert_upload_token_preflight(ctx, checks):
    seed_deep_file_ancestor(ctx)
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()
    status, _ = api(
        ctx,
        "/api/fm/upload-token",
        {"path": "qiniu://blocked/legacy-dir", "name": "direct.bin"},
    )
    checks.equal(status, 409, "upload-token file-ancestor rejection")
    checks.equal(db_state(ctx), before_db, "upload-token changed DB or ledger")
    checks.equal(ctx.fake.keys(), before_keys, "upload-token changed objects")
    checks.equal(ctx.fake.calls(), [], "upload-token reached Qiniu")


def assert_register_preflight(ctx, checks):
    seed_deep_file_ancestor(ctx)
    add_file(
        ctx,
        "blocked/legacy-dir/existing.txt",
        False,
        "register-old-object",
        content="old object must survive",
    )
    ctx.fake.plant("register-new-object", "new object", "text/plain")
    add_ledger(ctx, "register-new-object", "qiniu://blocked/legacy-dir/existing.txt")
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()
    status, _ = api(
        ctx,
        "/api/fm/register",
        {
            "path": "qiniu://blocked/legacy-dir/existing.txt",
            "key": "register-new-object",
        },
    )
    checks.equal(status, 409, "register file-ancestor rejection")
    checks.equal(db_state(ctx), before_db, "register changed DB or ledger")
    checks.equal(ctx.fake.keys(), before_keys, "register deleted or added an object")
    checks.equal(ctx.fake.calls(), [], "register reached Qiniu before preflight")


def assert_move_reparent_preflight(ctx, checks):
    seed_deep_file_ancestor(ctx, "blocked")
    add_file(ctx, "blocked/legacy-dir/child.txt", False, "repair-child-object")
    add_file(ctx, "good", True)
    repair_status, _ = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://good",
            "sources": ["qiniu://blocked/legacy-dir"],
        },
    )
    checks.equal(repair_status, 200, "moving a clean subtree out of a dirty tree")
    repair_paths = {row[0] for row in rows(ctx)}
    checks.true("good/legacy-dir/child.txt" in repair_paths, "repair move lost child")
    checks.true("blocked/legacy-dir" not in repair_paths, "repair move left source")

    seed_deep_file_ancestor(ctx, "blocked-two")
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()
    move_status, _ = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://blocked-two/legacy-dir",
            "sources": ["qiniu://archive"],
        },
    )
    checks.equal(move_status, 409, "move file-ancestor rejection")
    checks.equal(db_state(ctx), before_db, "rejected move changed DB")
    checks.equal(ctx.fake.keys(), before_keys, "rejected move changed objects")
    checks.equal(ctx.fake.calls(), [], "rejected move reached Qiniu")


def assert_fm_move_request_atomic(ctx, checks):
    add_file(ctx, "move-skip-self.txt", False, "move-skip-self-object")
    add_file(ctx, "move-skip-descendant", True)
    add_file(
        ctx,
        "move-skip-descendant/child.txt",
        False,
        "move-skip-descendant-object",
    )
    skip_state = db_state(ctx)
    empty_status, _ = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://unused",
            "sources": [],
        },
    )
    self_status, _ = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://",
            "sources": ["qiniu://move-skip-self.txt"],
        },
    )
    descendant_status, _ = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://move-skip-descendant/inside",
            "sources": ["qiniu://move-skip-descendant"],
        },
    )
    checks.equal(empty_status, 200, "empty MOVE request wire status")
    checks.equal(self_status, 200, "self MOVE skip wire status")
    checks.equal(descendant_status, 200, "descendant MOVE skip wire status")
    checks.equal(db_state(ctx), skip_state, "skipped MOVE request changed metadata")

    add_file(ctx, "move-success.txt", False, "move-success-object")
    add_file(ctx, "move-success-destination", True)
    success_status, _ = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://move-success-destination",
            "sources": ["qiniu://move-success.txt"],
        },
    )
    checks.equal(success_status, 200, "successful MOVE wire status")
    checks.equal(rows(ctx, "move-success.txt"), [], "successful MOVE left source")
    checks.equal(
        len(rows(ctx, "move-success-destination/move-success.txt")),
        1,
        "successful MOVE did not create target",
    )

    add_file(ctx, "move-atomic-left", True)
    add_file(
        ctx,
        "move-atomic-left/same.txt",
        False,
        "move-atomic-left-object",
    )
    add_file(ctx, "move-atomic-right", True)
    add_file(
        ctx,
        "move-atomic-right/same.txt",
        False,
        "move-atomic-right-object",
    )
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()

    status, _ = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://move-atomic-destination/deep",
            "sources": [
                "qiniu://move-atomic-left/same.txt",
                "qiniu://move-atomic-right/same.txt",
            ],
        },
    )

    checks.true(status >= 400, "multi-source MOVE basename collision status")
    checks.equal(db_state(ctx), before_db, "multi-source MOVE left a partial move")
    checks.equal(ctx.fake.keys(), before_keys, "metadata-only MOVE changed objects")
    checks.equal(ctx.fake.calls(), [], "metadata-only MOVE reached Qiniu")


def assert_rename_file_ancestor_preflight(ctx, checks):
    seed_deep_file_ancestor(ctx, "blocked-rename")
    add_file(
        ctx,
        "blocked-rename/legacy-dir/old.txt",
        False,
        "rename-old-object",
    )
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()
    status, _ = api(
        ctx,
        "/api/fm/rename",
        {
            "path": "qiniu://blocked-rename/legacy-dir",
            "item": "qiniu://blocked-rename/legacy-dir/old.txt",
            "name": "renamed.txt",
        },
    )
    checks.equal(status, 409, "rename file-ancestor rejection")
    checks.equal(db_state(ctx), before_db, "rejected rename changed DB")
    checks.equal(ctx.fake.keys(), before_keys, "rejected rename changed objects")
    checks.equal(ctx.fake.calls(), [], "rejected rename reached Qiniu")


def assert_conflict_mapping(ctx, checks):
    seed_deep_file_ancestor(ctx)
    ctx.fake.plant("create-folder-sentinel", "sentinel", "text/plain")
    add_ledger(
        ctx,
        "create-folder-sentinel",
        "qiniu://blocked/legacy-dir/folder",
    )
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()
    status, _ = api(
        ctx,
        "/api/fm/create-folder",
        {"path": "qiniu://blocked/legacy-dir", "name": "folder"},
    )
    checks.equal(status, 409, "create-folder file-ancestor rejection")
    checks.equal(
        db_state(ctx),
        before_db,
        "create-folder rejection changed DB or ledger",
    )
    checks.equal(
        ctx.fake.keys(), before_keys, "create-folder rejection changed objects"
    )
    checks.equal(ctx.fake.calls(), [], "create-folder rejection reached Qiniu")


def assert_fm_copy_error_classification(ctx, checks):
    add_file(ctx, "copy-errors", True)
    add_file(ctx, "copy-errors/conflict-source.txt", False, "copy-conflict-source")
    add_file(ctx, "copy-errors/conflict-destination", True)
    add_file(
        ctx,
        "copy-errors/conflict-destination/conflict-source.txt",
        False,
        "copy-conflict-target",
    )
    ctx.fake.clear_calls()
    conflict_status, _ = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://copy-errors",
            "destination": "qiniu://copy-errors/conflict-destination",
            "sources": ["qiniu://copy-errors/conflict-source.txt"],
        },
    )
    checks.equal(conflict_status, 409, "FM COPY conflict classification")
    checks.equal(ctx.fake.calls(), [], "FM COPY conflict reached Qiniu")

    add_file(ctx, "copy-errors/upstream-source.txt", False, "copy-upstream-source")
    add_file(ctx, "copy-errors/upstream-destination", True)
    ctx.fake.clear_calls()
    ctx.fake.refuse("copy", 599, '{"error":"refuse copy classification"}')
    try:
        upstream_status, _ = api(
            ctx,
            "/api/fm/copy",
            {
                "path": "qiniu://copy-errors",
                "destination": "qiniu://copy-errors/upstream-destination",
                "sources": ["qiniu://copy-errors/upstream-source.txt"],
            },
        )
    finally:
        ctx.fake.allow("copy")
    checks.equal(upstream_status, 502, "FM COPY Qiniu failure classification")
    checks.equal(
        rows(ctx, "copy-errors/upstream-destination/upstream-source.txt"),
        [],
        "FM COPY Qiniu refusal committed metadata",
    )
    checks.true(
        any(
            call.get("op") == "copy" and call.get("outcome") == "refused"
            for call in ctx.fake.calls()
        ),
        "FM COPY Qiniu refusal did not reach the fake",
    )

    add_file(ctx, "copy-errors/plain-source.txt", False, "copy-plain-source")
    add_file(ctx, "copy-errors/plain-destination", True)
    execute_sql(
        ctx,
        """
        CREATE TRIGGER reject_copy_plain_final_write
        BEFORE INSERT ON files
        WHEN new.path = 'copy-errors/plain-destination/plain-source.txt'
        BEGIN
          SELECT raise(abort, 'reject copy plain final write');
        END;
        """,
    )
    before_plain_db = db_state(ctx)
    before_plain_state = ctx.fake.state()
    ctx.fake.clear_calls()
    plain_status, _ = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://copy-errors",
            "destination": "qiniu://copy-errors/plain-destination",
            "sources": ["qiniu://copy-errors/plain-source.txt"],
        },
    )
    checks.equal(plain_status, 500, "FM COPY final DB failure classification")
    checks.equal(
        db_state(ctx), before_plain_db, "FM COPY final DB failure changed metadata"
    )
    assert_copied_objects_collected(
        checks,
        before_plain_state,
        ctx.fake.state(),
        ctx.fake.calls(),
        "FM COPY final DB failure",
    )


def assert_internal_subtree_shape(ctx, checks):
    add_file(ctx, "good", True)
    for root in (
        "fm-dirty-move",
        "fm-dirty-copy",
        "dav-dirty-move",
        "dav-dirty-copy",
    ):
        seed_dirty_subtree(ctx, root)
    add_file(
        ctx,
        "good/dav-move-target",
        False,
        "dav-move-target-object",
    )
    add_file(
        ctx,
        "good/dav-copy-target",
        False,
        "dav-copy-target-object",
    )
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()

    fm_move = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://good",
            "sources": ["qiniu://fm-dirty-move"],
        },
    )[0]
    fm_copy = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://",
            "destination": "qiniu://good",
            "sources": ["qiniu://fm-dirty-copy"],
        },
    )[0]
    dav_move = dav(
        ctx,
        "MOVE",
        "/dav/dav-dirty-move",
        {"Destination": "/dav/good/dav-move-target"},
    )[0]
    dav_copy = dav(
        ctx,
        "COPY",
        "/dav/dav-dirty-copy",
        {"Destination": "/dav/good/dav-copy-target"},
    )[0]
    for label, status in {
        "FM MOVE": fm_move,
        "FM COPY": fm_copy,
        "WebDAV MOVE": dav_move,
        "WebDAV COPY": dav_copy,
    }.items():
        checks.equal(status, 409, f"{label} internal dirty subtree rejection")
    checks.equal(db_state(ctx), before_db, "dirty subtree rejection changed DB")
    checks.equal(
        ctx.fake.keys(), before_keys, "dirty subtree rejection changed objects"
    )
    checks.equal(ctx.fake.calls(), [], "dirty subtree rejection reached Qiniu")


def assert_webdav_full_ancestor(ctx, checks):
    seed_deep_file_ancestor(ctx)
    add_file(
        ctx,
        "blocked/legacy-dir/move-target",
        False,
        "deep-move-target-object",
    )
    add_file(
        ctx,
        "blocked/legacy-dir/copy-target",
        False,
        "deep-copy-target-object",
    )
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()

    statuses = {
        "PUT": dav(ctx, "PUT", "/dav/blocked/legacy-dir/put.txt", body=b"put")[0],
        "MKCOL": dav(ctx, "MKCOL", "/dav/blocked/legacy-dir/new-col")[0],
        "MOVE": dav(
            ctx,
            "MOVE",
            "/dav/empty-dir",
            {"Destination": "/dav/blocked/legacy-dir/move-target"},
        )[0],
        "COPY": dav(
            ctx,
            "COPY",
            "/dav/docs/notes.txt",
            {"Destination": "/dav/blocked/legacy-dir/copy-target"},
        )[0],
    }
    overwrite_f = dav(
        ctx,
        "COPY",
        "/dav/empty-dir",
        {
            "Destination": "/dav/blocked/legacy-dir/copy-target",
            "Overwrite": "F",
        },
    )[0]
    for method, status in statuses.items():
        checks.equal(status, 409, f"WebDAV {method} full ancestor rejection")
    checks.equal(overwrite_f, 412, "Overwrite:F remains prior to ancestor validation")
    checks.equal(db_state(ctx), before_db, "WebDAV full-ancestor rejection changed DB")
    checks.equal(
        ctx.fake.keys(), before_keys, "WebDAV full-ancestor rejection changed objects"
    )
    checks.equal(ctx.fake.calls(), [], "WebDAV full-ancestor rejection reached Qiniu")


def assert_fm_addressing_lossless(ctx, checks):
    """`qiniu://<path>` addresses exactly the row spelled that way.

    Whitespace at the edges of a stored path is not hypothetical: multipart
    filenames arrive verbatim (util/multipart disp_filename, svc/files
    sanitize_name), WebDAV MKCOL/PUT/MOVE/COPY never trim, and every POSIX
    filesystem lets a user create " a.txt". So the store really can hold
    "a.txt" and "a.txt " at once, and paths.fm_split used to collapse the
    second onto the first — a delete of one dropped the other's row and
    collected the other's object, irreversibly. Every write here addresses the
    whitespace spelling and must leave its plain neighbour untouched.
    """
    add_file(ctx, "addr", True)
    add_file(ctx, "addr/a.txt", False, "addr-plain-object", content="plain bytes")
    add_file(
        ctx,
        "addr/a.txt ",
        False,
        "addr-trailing-object",
        content="trailing space bytes",
    )
    add_file(
        ctx,
        "addr/ b.txt",
        False,
        "addr-leading-object",
        content="leading space bytes",
    )
    # whitespace directly after the storage prefix: "qiniu:// root.txt" is the
    # row " root.txt", never "root.txt"
    add_file(ctx, "root.txt", False, "addr-root-plain-object", content="root plain")
    add_file(ctx, " root.txt", False, "addr-root-space-object", content="root spaced")

    list_status, list_raw = api(
        ctx,
        "/api/fm?" + urllib.parse.urlencode({"path": "qiniu://addr"}),
        method="GET",
    )
    checks.equal(list_status, 200, "listing status")
    checks.equal(
        {entry["path"] for entry in json.loads(list_raw)["files"]},
        {"qiniu://addr/ b.txt", "qiniu://addr/a.txt", "qiniu://addr/a.txt "},
        "listing echoes every stored spelling",
    )

    # reads: the path the listing handed out resolves to that row's object, and
    # a spelling no row carries is a 404 rather than a neighbour's bytes
    for full_path, key in (
        ("qiniu://addr/a.txt", "addr-plain-object"),
        ("qiniu://addr/a.txt ", "addr-trailing-object"),
        ("qiniu://addr/ b.txt", "addr-leading-object"),
        ("qiniu://root.txt", "addr-root-plain-object"),
        ("qiniu:// root.txt", "addr-root-space-object"),
    ):
        sign_status, sign_raw = api(
            ctx,
            "/api/fm/sign?" + urllib.parse.urlencode({"path": full_path}),
            method="GET",
        )
        checks.equal(sign_status, 200, f"sign {full_path!r} status")
        if sign_status == 200:
            checks.true(
                f"/{key}?" in json.loads(sign_raw)["url"],
                f"sign {full_path!r} addressed another row",
            )
    unstored = ("qiniu://addr/a.txt  ", "qiniu:// addr/a.txt", "qiniu://addr/b.txt")
    for missing in unstored:
        checks.equal(
            api(
                ctx,
                "/api/fm/sign?" + urllib.parse.urlencode({"path": missing}),
                method="GET",
            )[0],
            404,
            f"sign {missing!r} resolved an unstored spelling",
        )

    # delete: the reproduction. Only the addressed row and its object go.
    ctx.fake.clear_calls()
    delete_status, delete_raw = api(
        ctx,
        "/api/fm/delete",
        {"path": "qiniu://addr", "items": [{"path": "qiniu://addr/a.txt "}]},
    )
    checks.equal(delete_status, 200, "delete status")
    if delete_status == 200:
        checks.equal(
            [entry["path"] for entry in json.loads(delete_raw).get("deleted", [])],
            ["qiniu://addr/a.txt "],
            "delete reported another row",
        )
    checks.equal(
        {row[0] for row in rows(ctx, "addr")},
        {"addr", "addr/a.txt", "addr/ b.txt"},
        "delete removed the wrong metadata row",
    )
    checks.true(
        "addr-plain-object" in ctx.fake.keys(),
        "delete collected the neighbour's object",
    )
    checks.true(
        "addr-trailing-object" not in ctx.fake.keys(),
        "delete left the addressed object",
    )
    checks.equal(
        [call.get("key") for call in ctx.fake.calls() if call.get("op") == "delete"],
        ["addr-trailing-object"],
        "delete object set",
    )

    # rename: `item` is addressed the same way
    add_file(ctx, "addr-rename", True)
    add_file(ctx, "addr-rename/e.txt", False, "addr-rename-plain-object")
    add_file(ctx, "addr-rename/e.txt ", False, "addr-rename-space-object")
    rename_status, _ = api(
        ctx,
        "/api/fm/rename",
        {
            "path": "qiniu://addr-rename",
            "item": "qiniu://addr-rename/e.txt ",
            "name": "renamed.txt",
        },
    )
    checks.equal(rename_status, 200, "rename status")
    checks.equal(
        {(row[0], row[2]) for row in rows(ctx, "addr-rename")},
        {
            ("addr-rename", None),
            ("addr-rename/e.txt", "addr-rename-plain-object"),
            ("addr-rename/renamed.txt", "addr-rename-space-object"),
        },
        "rename rewrote the wrong row",
    )

    # move and copy used to appear here too, relocating "c.txt " and landing it
    # spelled that way at the destination. #245 removed the possibility rather
    # than the addressing: the path those two would write carries an edge space,
    # and a copied "c.txt " next to an existing "c.txt" is a new ambiguous pair,
    # so both are refused before they resolve anything. What they can still be
    # asked is that they refuse, and that belongs to the cases that own the
    # guard: name-guard.fm.move and name-guard.fm.copy. This case keeps the
    # operations that address a whitespace row without spelling a new name.


# The two messages util/paths require_trimmed_names answers with. Asserting the
# text, not only the 400, is what keeps these cases pinned to their own guard: a
# handler has several other ways to answer 400.
NAME_EDGE_DETAIL = "名称不能以空白开头或结尾"
NAME_BLANK_DETAIL = "名称不能只由空白组成"

# Where a probe puts its whitespace is not cosmetic. Anything an endpoint reads
# through paths.fm_split is also seen by the fm-split-trims-addressing mutant,
# which trims the whole path: a probe whose whitespace sits at either end of
# that string would stop reaching the name guard under it, and the case would
# quietly become a second owner of that mutant. So probes on fm_split inputs put
# the whitespace on a non-terminal segment (or at the start of a leaf), and the
# plain trailing-space spelling is used at the entries that do not split:
# rename's `name`, upload-token's and proxy upload's filename, and every WebDAV
# path, which arrives as decoded URL segments.


def detail_of(raw):
    try:
        return json.loads(raw).get("detail")
    except (ValueError, AttributeError):
        return None


def refuse_name(checks, response, label, detail=NAME_EDGE_DETAIL):
    status, raw = response
    checks.equal(status, 400, f"{label} status")
    checks.equal(detail_of(raw), detail, f"{label} message")


def assert_name_guard_create_folder(ctx, checks):
    """create-folder refuses a name that is not its own trim, and creates it otherwise.

    This endpoint used to run str.trim over the name and create the folder under
    the trimmed spelling, so "new " and "new" were the same request with no way
    to tell. Nothing rewrites the name now: it is either stored as typed or
    refused.
    """
    before = db_state(ctx)
    refuse_name(
        checks,
        api(ctx, "/api/fm/create-folder", {"path": "qiniu://docs", "name": "a /b"}),
        "create-folder trailing space on a parent segment",
    )
    refuse_name(
        checks,
        api(ctx, "/api/fm/create-folder", {"path": "qiniu://docs", "name": "a/ b"}),
        "create-folder leading space on the leaf",
    )
    refuse_name(
        checks,
        api(ctx, "/api/fm/create-folder", {"path": "qiniu://docs", "name": "a/   /b"}),
        "create-folder all-blank segment",
        NAME_BLANK_DETAIL,
    )
    checks.equal(db_state(ctx), before, "a refused create-folder wrote a row")
    # one new segment under a directory that already exists: the control says
    # the guard lets an ordinary create through, and nothing else. Creating
    # "a/b" here would also be asserting that missing ancestors get backfilled,
    # which is fm.missing-ancestors.auto-created's claim, and would make this
    # case a second owner of the mutant that removes the backfill.
    status, _ = api(
        ctx, "/api/fm/create-folder", {"path": "qiniu://docs", "name": "created"}
    )
    checks.equal(status, 200, "clean create-folder status")
    checks.true(
        file_row(ctx, "docs/created") is not None, "clean create-folder wrote no row"
    )


def assert_name_guard_rename(ctx, checks):
    """rename refuses a new name that is not its own trim.

    The name reaches the handler raw, so this is the endpoint where the plain
    "renamed.txt " spelling is the probe. The guard sits on the new name and
    never on `item`, which is what leaves rename as the repair path for a row
    that already carries an edge space; that half of the claim is addressing and
    belongs to fm.addressing.lossless.
    """
    before = db_state(ctx)
    for name, label, detail in (
        ("renamed.txt ", "rename trailing space", NAME_EDGE_DETAIL),
        (" renamed.txt", "rename leading space", NAME_EDGE_DETAIL),
        ("   ", "rename all-blank name", NAME_BLANK_DETAIL),
    ):
        refuse_name(
            checks,
            api(
                ctx,
                "/api/fm/rename",
                {
                    "path": "qiniu://docs",
                    "item": "qiniu://docs/notes.txt",
                    "name": name,
                },
            ),
            label,
            detail,
        )
    checks.equal(db_state(ctx), before, "a refused rename rewrote a path")
    status, _ = api(
        ctx,
        "/api/fm/rename",
        {
            "path": "qiniu://docs",
            "item": "qiniu://docs/notes.txt",
            "name": "renamed.txt",
        },
    )
    checks.equal(status, 200, "clean rename status")
    checks.true(
        file_row(ctx, "docs/renamed.txt") is not None, "clean rename lost the row"
    )


def assert_name_guard_move(ctx, checks):
    """move refuses either half of a target it would spell with an edge space.

    The path a move writes is the destination directory followed by the leaf
    name the source keeps, so both are checked. A relocated "a.txt " landing
    beside an existing "a.txt" is the ambiguous pair this rule exists to stop,
    and the source row keeping its own spelling is not a licence to write it
    somewhere new.
    """
    add_file(ctx, "dirty ", True)
    add_file(ctx, "dirty /nest", True)
    add_file(ctx, "leafs", True)
    add_file(ctx, "leafs/ a.txt", False, "move-guard-leaf-object")
    add_file(ctx, "clean-dest", True)
    before = db_state(ctx)
    refuse_name(
        checks,
        api(
            ctx,
            "/api/fm/move",
            {
                "path": "qiniu://",
                "destination": "qiniu://dirty /nest",
                "sources": ["qiniu://example.txt"],
            },
        ),
        "move into a directory whose path has an edge space",
    )
    refuse_name(
        checks,
        api(
            ctx,
            "/api/fm/move",
            {
                "path": "qiniu://",
                "destination": "qiniu://clean-dest",
                "sources": ["qiniu://leafs/ a.txt"],
            },
        ),
        "move of a leaf whose name has an edge space",
    )
    checks.equal(db_state(ctx), before, "a refused move rewrote a path")
    checks.equal(ctx.fake.calls(), [], "a refused move reached Qiniu")
    status, _ = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://clean-dest",
            "sources": ["qiniu://example.txt"],
        },
    )
    checks.equal(status, 200, "clean move status")
    checks.true(
        file_row(ctx, "clean-dest/example.txt") is not None, "clean move lost the row"
    )


def assert_name_guard_copy(ctx, checks):
    """copy refuses either half of a target it would spell with an edge space.

    Copy is the worse of the two: move at least leaves the number of dirty rows
    where it was, while a copy of "a.txt " mints a second one.
    """
    add_file(ctx, "dirty ", True)
    add_file(ctx, "dirty /nest", True)
    add_file(ctx, "leafs", True)
    add_file(ctx, "leafs/ a.txt", False, "copy-guard-leaf-object", content="leaf bytes")
    add_file(ctx, "copy-source.txt", False, "copy-guard-object", content="clean bytes")
    add_file(ctx, "clean-dest", True)
    before = db_state(ctx)
    refuse_name(
        checks,
        api(
            ctx,
            "/api/fm/copy",
            {
                "path": "qiniu://",
                "destination": "qiniu://dirty /nest",
                "sources": ["qiniu://copy-source.txt"],
            },
        ),
        "copy into a directory whose path has an edge space",
    )
    refuse_name(
        checks,
        api(
            ctx,
            "/api/fm/copy",
            {
                "path": "qiniu://",
                "destination": "qiniu://clean-dest",
                "sources": ["qiniu://leafs/ a.txt"],
            },
        ),
        "copy of a leaf whose name has an edge space",
    )
    checks.equal(db_state(ctx), before, "a refused copy wrote a row")
    checks.equal(ctx.fake.calls(), [], "a refused copy reached Qiniu")
    status, _ = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://",
            "destination": "qiniu://clean-dest",
            "sources": ["qiniu://copy-source.txt"],
        },
    )
    checks.equal(status, 200, "clean copy status")
    checks.true(
        file_row(ctx, "clean-dest/copy-source.txt") is not None,
        "clean copy wrote no row",
    )


def assert_name_guard_create_file(ctx, checks):
    """create-file refuses a name that is not its own trim, before it mints an object.

    Like create-folder it used to trim. The refusal has to land before the empty
    object is uploaded, otherwise a rejected request still leaves a key behind.
    """
    before = db_state(ctx)
    refuse_name(
        checks,
        api(ctx, "/api/fm/create-file", {"path": "qiniu://docs", "name": "a /b.txt"}),
        "create-file trailing space on a parent segment",
    )
    refuse_name(
        checks,
        api(ctx, "/api/fm/create-file", {"path": "qiniu://docs", "name": "a/ b.txt"}),
        "create-file leading space on the leaf",
    )
    checks.equal(db_state(ctx), before, "a refused create-file wrote a row")
    checks.equal(ctx.fake.calls(), [], "a refused create-file reached Qiniu")
    status, _ = api(
        ctx, "/api/fm/create-file", {"path": "qiniu://docs", "name": "created.txt"}
    )
    checks.equal(status, 200, "clean create-file status")
    checks.true(
        file_row(ctx, "docs/created.txt") is not None, "clean create-file wrote no row"
    )


def assert_name_guard_save(ctx, checks):
    """save refuses a path that is not its own trim, before it writes an object.

    save is a write even when the row exists, because it can also create one,
    and either way it uploads a fresh key before touching metadata. So the guard
    is at the top of the handler and no object is minted for a refused path.
    """
    add_file(ctx, "save-guard.txt", False, "save-guard-object", content="old text")
    before = db_state(ctx)
    refuse_name(
        checks,
        api(ctx, "/api/fm/save", {"path": "qiniu://docs /notes.txt", "content": "x"}),
        "save into a directory whose path has an edge space",
    )
    refuse_name(
        checks,
        api(ctx, "/api/fm/save", {"path": "qiniu://docs/   /x.txt", "content": "x"}),
        "save through an all-blank segment",
        NAME_BLANK_DETAIL,
    )
    checks.equal(db_state(ctx), before, "a refused save wrote a row")
    checks.equal(ctx.fake.calls(), [], "a refused save reached Qiniu")
    status, _ = api(
        ctx, "/api/fm/save", {"path": "qiniu://save-guard.txt", "content": "new text"}
    )
    checks.equal(status, 200, "clean save status")


def assert_name_guard_upload_token(ctx, checks):
    """upload-token refuses the filename before it mints a credential or a ledger row.

    The token is scoped to a key the client then spends directly against the
    bucket, and /register is handed back the path this endpoint computed. A name
    refused only at /register would already have cost an upload.
    """
    before = db_state(ctx)
    for name, label, detail in (
        ("pic.png ", "upload-token trailing space", NAME_EDGE_DETAIL),
        (" pic.png", "upload-token leading space", NAME_EDGE_DETAIL),
        ("   ", "upload-token all-blank filename", NAME_BLANK_DETAIL),
    ):
        refuse_name(
            checks,
            api(ctx, "/api/fm/upload-token", {"path": "qiniu://docs", "name": name}),
            label,
            detail,
        )
    checks.equal(db_state(ctx), before, "a refused upload-token wrote DB or ledger")
    checks.equal(ctx.fake.calls(), [], "a refused upload-token reached Qiniu")
    status, raw = api(
        ctx, "/api/fm/upload-token", {"path": "qiniu://docs", "name": "pic.png"}
    )
    checks.equal(status, 200, "clean upload-token status")
    if status == 200:
        checks.equal(
            json.loads(raw)["path"], "qiniu://docs/pic.png", "clean upload-token path"
        )


def assert_name_guard_register(ctx, checks):
    """register refuses the path before it asks Qiniu whether the object landed.

    /register is a public endpoint, not only the second half of the browser's
    direct upload, so it states the rule itself rather than trusting the path it
    is handed back.
    """
    ctx.fake.plant("register-guard-object", "registered bytes", "text/plain")
    add_ledger(ctx, "register-guard-object", "qiniu://docs/reg.txt")
    before = db_state(ctx)
    ctx.fake.clear_calls()
    refuse_name(
        checks,
        api(
            ctx,
            "/api/fm/register",
            {"path": "qiniu://docs /reg.txt", "key": "register-guard-object"},
        ),
        "register into a directory whose path has an edge space",
    )
    checks.equal(db_state(ctx), before, "a refused register wrote a row")
    checks.equal(ctx.fake.calls(), [], "a refused register reached Qiniu stat")
    status, _ = api(
        ctx,
        "/api/fm/register",
        {"path": "qiniu://docs/reg.txt", "key": "register-guard-object"},
    )
    checks.equal(status, 200, "clean register status")
    checks.true(
        file_row(ctx, "docs/reg.txt") is not None, "clean register wrote no row"
    )


def assert_name_guard_proxy_upload(ctx, checks):
    """the proxy upload refuses the multipart filename before the bytes go anywhere.

    The filename arrives verbatim from the part headers (util/multipart
    disp_filename keeps everything inside the quotes), so this door is how a
    non-browser client would otherwise plant " a.txt".
    """
    before = db_state(ctx)
    refuse_name(
        checks,
        api_upload(ctx, "qiniu://docs", name="up.bin "),
        "proxy upload trailing space in the filename",
    )
    refuse_name(
        checks,
        api_upload(ctx, "qiniu://docs", name=" up.bin"),
        "proxy upload leading space in the filename",
    )
    checks.equal(db_state(ctx), before, "a refused proxy upload wrote a row")
    checks.equal(ctx.fake.calls(), [], "a refused proxy upload reached Qiniu")
    status, _ = api_upload(ctx, "qiniu://docs", name="up.bin")
    checks.equal(status, 200, "clean proxy upload status")
    checks.true(
        file_row(ctx, "docs/up.bin") is not None, "clean proxy upload wrote no row"
    )


def assert_name_guard_webdav_put(ctx, checks):
    """PUT refuses a path segment that is not its own trim.

    WebDAV is the entry that never trimmed anything, so it is where the store
    could pick up an edge space even while the web app was still trimming. The
    path arrives as decoded URL segments, so the plain %20 spelling is the probe.
    """
    before = db_state(ctx)
    refuse_name(
        checks,
        dav(ctx, "PUT", "/dav/docs/put.txt%20", body=b"put"),
        "PUT with a trailing space in the leaf",
    )
    refuse_name(
        checks,
        dav(ctx, "PUT", "/dav/docs%20/put.txt", body=b"put"),
        "PUT through a directory segment with a trailing space",
    )
    checks.equal(db_state(ctx), before, "a refused PUT wrote a row")
    checks.equal(ctx.fake.calls(), [], "a refused PUT reached Qiniu")
    status, _ = dav(ctx, "PUT", "/dav/docs/put.txt", body=b"put")
    checks.equal(status, 201, "clean PUT status")
    checks.true(file_row(ctx, "docs/put.txt") is not None, "clean PUT wrote no row")


def assert_name_guard_webdav_mkcol(ctx, checks):
    """MKCOL refuses a collection name that is not its own trim."""
    before = db_state(ctx)
    refuse_name(
        checks,
        dav(ctx, "MKCOL", "/dav/docs/col%20"),
        "MKCOL with a trailing space in the leaf",
    )
    refuse_name(
        checks,
        dav(ctx, "MKCOL", "/dav/docs/%20col"),
        "MKCOL with a leading space in the leaf",
    )
    checks.equal(db_state(ctx), before, "a refused MKCOL wrote a row")
    status, _ = dav(ctx, "MKCOL", "/dav/docs/col")
    checks.equal(status, 201, "clean MKCOL status")
    checks.true(file_row(ctx, "docs/col") is not None, "clean MKCOL wrote no row")


def assert_name_guard_webdav_destination(ctx, checks):
    """MOVE and COPY refuse a Destination that is not its own trim.

    Both verbs resolve the Destination through one function and write what it
    names, so one guard covers the pair. It sits on the Destination and not on
    the request path: the source is being addressed, only the destination is
    being spelled.
    """
    add_file(ctx, "dav-move.txt", False, "dav-move-object", content="move bytes")
    add_file(ctx, "dav-copy.txt", False, "dav-copy-object", content="copy bytes")
    before = db_state(ctx)
    refuse_name(
        checks,
        dav(ctx, "MOVE", "/dav/dav-move.txt", {"Destination": "/dav/moved.txt%20"}),
        "MOVE to a destination with a trailing space",
    )
    refuse_name(
        checks,
        dav(ctx, "COPY", "/dav/dav-copy.txt", {"Destination": "/dav/%20copied.txt"}),
        "COPY to a destination with a leading space",
    )
    checks.equal(db_state(ctx), before, "a refused MOVE or COPY wrote a row")
    checks.equal(ctx.fake.calls(), [], "a refused MOVE or COPY reached Qiniu")
    move_status, _ = dav(
        ctx, "MOVE", "/dav/dav-move.txt", {"Destination": "/dav/moved.txt"}
    )
    checks.equal(move_status, 201, "clean MOVE status")
    copy_status, _ = dav(
        ctx, "COPY", "/dav/dav-copy.txt", {"Destination": "/dav/copied.txt"}
    )
    checks.equal(copy_status, 201, "clean COPY status")
    checks.true(file_row(ctx, "moved.txt") is not None, "clean MOVE lost the row")
    checks.true(file_row(ctx, "copied.txt") is not None, "clean COPY wrote no row")


def assert_persisted_mime_listing(ctx, checks):
    """A directory listing reports a stored content type through the outbound boundary.

    `mime_type` in a DirEntry is a stored value on its way to the browser, which
    hands it to the file manager as the type to render and preview with. Rows
    predate every check this backend runs, so the value is replaced whole rather
    than cleaned: a sanitised "text/plain\\r\\nX-Injected: 1" would still be a
    chosen type, just a different one.
    """
    add_file(ctx, "mime-list", True)
    add_file(
        ctx,
        "mime-list/dirty.txt",
        False,
        "mime-list-dirty-object",
        content_type="text/plain\r\nX-Injected: 1",
    )
    add_file(
        ctx,
        "mime-list/clean.txt",
        False,
        "mime-list-clean-object",
        content_type="text/plain",
    )
    status, raw = api(
        ctx,
        "/api/fm?" + urllib.parse.urlencode({"path": "qiniu://mime-list"}),
        method="GET",
    )
    checks.equal(status, 200, "listing status")
    if status == 200:
        listed = {
            entry["path"]: entry["mime_type"] for entry in json.loads(raw)["files"]
        }
        checks.equal(
            listed.get("qiniu://mime-list/dirty.txt"),
            "application/octet-stream",
            "listing shipped an unshippable stored type",
        )
        checks.equal(
            listed.get("qiniu://mime-list/clean.txt"),
            "text/plain",
            "listing rewrote a shippable stored type",
        )
    checks.equal(
        file_row(ctx, "mime-list/dirty.txt")[3],
        "text/plain\r\nX-Injected: 1",
        "listing rewrote the stored row",
    )


def assert_persisted_mime_copy(ctx, checks):
    """A copy stores the source's content type through the outbound boundary.

    A copy is a brand new row carrying an old row's value forward, so without
    the boundary one dirty row becomes two. The claim is about what lands in the
    database, which is why this reads the row rather than the response: the
    response would show the fallback either way, because the DirEntry has its
    own boundary.
    """
    add_file(
        ctx,
        "mime-copy.txt",
        False,
        "mime-copy-object",
        content_type="text/plain\r\nX-Injected: 1",
        content="copy me",
    )
    add_file(ctx, "mime-copy-dest", True)
    status, _ = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://",
            "destination": "qiniu://mime-copy-dest",
            "sources": ["qiniu://mime-copy.txt"],
        },
    )
    checks.equal(status, 200, "copy status")
    copied = file_row(ctx, "mime-copy-dest/mime-copy.txt")
    checks.true(copied is not None, "copy wrote no row")
    if copied is not None:
        checks.equal(
            copied[3],
            "application/octet-stream",
            "copy duplicated an unshippable stored type",
        )
    checks.equal(
        file_row(ctx, "mime-copy.txt")[3],
        "text/plain\r\nX-Injected: 1",
        "copy rewrote its source row",
    )


def assert_webdav_missing_parent(ctx, checks):
    add_file(ctx, "good", True)
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()
    statuses = {
        "PUT": dav(ctx, "PUT", "/dav/missing-put/file.txt", body=b"put")[0],
        "MKCOL": dav(ctx, "MKCOL", "/dav/missing-mkcol/child")[0],
        "MOVE": dav(
            ctx,
            "MOVE",
            "/dav/empty-dir",
            {"Destination": "/dav/missing-move/empty-dir"},
        )[0],
        "COPY": dav(
            ctx,
            "COPY",
            "/dav/docs/notes.txt",
            {"Destination": "/dav/missing-copy/notes.txt"},
        )[0],
    }
    for method, status in statuses.items():
        checks.equal(status, 409, f"WebDAV {method} missing-parent rejection")
    checks.equal(db_state(ctx), before_db, "WebDAV missing-parent rejection changed DB")
    checks.equal(
        ctx.fake.keys(), before_keys, "WebDAV missing-parent rejection changed objects"
    )
    checks.equal(ctx.fake.calls(), [], "WebDAV missing-parent rejection reached Qiniu")


def assert_json_duplicate_members(ctx, checks):
    """A body with two members of one name in one object is refused, not guessed.

    RFC 8259 leaves the repeat to the implementation, and parsing into a map
    takes the silent choice that one of the two wins. Which one is not something
    the client said: `{"name":"a","name":"b"}` created a folder called "b" here,
    and the same body with the members swapped created "a". A proxy, a buggy
    client or a request-smuggling attempt only has to append a second member to
    pick the write, and the answer looked identical either way.

    Sent as raw text rather than through json.dumps, which cannot express a
    repeat at all — the same reason the Depth probes go over a socket.

    The accepted shapes are here too. Two *independent* objects may each carry a
    "path" (that is every multi-item delete), so a rule that rejected the second
    occurrence anywhere would break the API it was meant to protect, and a probe
    that only recorded refusals could not tell that apart from working.
    """

    def post(path, body):
        return request(
            ctx.base + path,
            "POST",
            {
                "Authorization": f"Bearer {ctx.token}",
                "Content-Type": "application/json",
            },
            body.encode(),
        )

    add_file(ctx, "dup", True)
    before = db_state(ctx)

    # top level: the repeat decides the write, and used to do it silently
    status, raw = post(
        "/api/fm/create-folder",
        '{"path":"qiniu://dup","name":"first","name":"second"}',
    )
    checks.equal(status, 422, "top-level duplicate member rejected")
    checks.true(
        "duplicate JSON object member" in raw.decode("utf-8", "replace"),
        "top-level duplicate names the rule",
    )

    # the same name spelled two ways is the same name: comparing source text
    # rather than decoded names would wave this through
    escaped, _raw = post(
        "/api/fm/create-folder",
        '{"path":"qiniu://dup","name":"escaped","\\u006eame":"other"}',
    )
    checks.equal(escaped, 422, "escape-equal duplicate member rejected")

    # nested inside an object, and inside an object inside an array
    nested, _raw = post(
        "/api/fm/delete",
        '{"path":"qiniu://dup","items":[{"path":"qiniu://dup/a","path":"qiniu://dup/b"}]}',
    )
    checks.equal(nested, 422, "duplicate inside an array element rejected")

    checks.equal(db_state(ctx), before, "a rejected body still wrote")

    # ...and none of that touches the shapes the API actually uses
    accepted, _raw = post(
        "/api/fm/create-folder", '{"path":"qiniu://dup","name":"accepted"}'
    )
    checks.equal(accepted, 200, "an ordinary body still works")
    siblings, _raw = post(
        "/api/fm/delete",
        '{"path":"qiniu://dup","items":[{"path":"qiniu://dup/x"},'
        '{"path":"qiniu://dup/y"}]}',
    )
    checks.equal(siblings, 200, "sibling objects may reuse a member name")
    checks.true(
        any(row[0] == "dup/accepted" for row in rows(ctx)),
        "the accepted body created its folder",
    )

    # The per-object scope has to be a Map, because a list of names is quadratic
    # and the 2MB body limit allows about 190k members in one object: measured at
    # 11s for 50k with a list and 0.2s with a Map. That is a request that costs a
    # core for minutes, reachable by anyone who can log in. The budget below is
    # loose enough not to be measuring the machine and far enough under the
    # quadratic cost at this width (minutes) that a return to it cannot pass.
    wide = "{" + ",".join(f'"k{i}":1' for i in range(100000)) + "}"
    started = time.monotonic()
    wide_status, _raw = post("/api/fm/create-folder", wide)
    elapsed = time.monotonic() - started
    checks.equal(wide_status, 400, "a wide body is answered on its merits")
    checks.true(elapsed < 15, f"scanning 100k members took {elapsed:.1f}s")


ASSERTIONS = [
    ("fm.missing-ancestors.auto-created", assert_fm_missing_ancestors),
    ("fm.rename.missing-ancestor-no-fill", assert_rename_missing_ancestor_no_fill),
    ("tree.literal-prefix.isolated", assert_literal_prefix_isolated),
    ("webdav.copy.parent-before-child", assert_webdav_copy_parent_order),
    ("fm.external-effects.preflight", assert_fm_external_effect_preflight),
    ("fm.directory-target.preflight", assert_directory_target_preflight),
    ("file-target.descendants.preflight", assert_file_target_descendant_preflight),
    (
        "file-target.descendants.final-race",
        assert_file_target_descendant_final_race,
    ),
    ("fm.upload-token.preflight", assert_upload_token_preflight),
    ("fm.register.preflight", assert_register_preflight),
    ("fm.move.reparent-preflight", assert_move_reparent_preflight),
    ("fm.move.request-atomic", assert_fm_move_request_atomic),
    ("fm.rename.file-ancestor-preflight", assert_rename_file_ancestor_preflight),
    ("fm.conflict.maps-409", assert_conflict_mapping),
    ("fm.copy.error-classification", assert_fm_copy_error_classification),
    ("subtree.internal-shape.preflight", assert_internal_subtree_shape),
    ("webdav.full-ancestor.preflight", assert_webdav_full_ancestor),
    ("webdav.missing-parent.rejects", assert_webdav_missing_parent),
    ("webdav.reverse-overlap.rejects", assert_webdav_reverse_overlap),
    ("fm.upload.final-write-isolated", assert_proxy_upload_final_rejection),
    ("fm.copy.deep-target-preflight", assert_fm_copy_deep_target_preflight),
    ("copy.source-object-shape.preflight", assert_copy_source_object_shape_preflight),
    ("copy.blank-source-key.preflight", assert_copy_blank_source_key_preflight),
    (
        "copy-move.destination-subtree.preflight",
        assert_destination_subtree_preflight,
    ),
    (
        "webdav.overwrite.gc-after-switch",
        assert_webdav_overwrite_gc_after_switch,
    ),
    ("webdav.put.actual-superseded-key", assert_webdav_put_actual_superseded_key),
    ("object-gc.live-key-reference", assert_object_gc_live_key_reference),
    (
        "webdav.delete.metadata-first.concurrent-rename",
        assert_webdav_delete_metadata_first_concurrent_rename,
    ),
    (
        "webdav.delete.metadata-subtree-atomic",
        assert_webdav_delete_metadata_subtree_atomic,
    ),
    (
        "webdav.overwrite.copy-failure-preserves-target",
        assert_webdav_overwrite_copy_failure_preserves_target,
    ),
    (
        "webdav.overwrite.move-failure-preserves-target",
        assert_webdav_overwrite_move_failure_preserves_target,
    ),
    (
        "webdav.overwrite.metadata-switch-atomic",
        assert_webdav_overwrite_metadata_switch_atomic,
    ),
    ("fm.copy.metadata-atomic", assert_fm_copy_metadata_atomic),
    ("webdav.copy.metadata-atomic", assert_webdav_copy_metadata_atomic),
    ("fm.addressing.lossless", assert_fm_addressing_lossless),
    ("fm.json.duplicate-members", assert_json_duplicate_members),
    ("name-guard.fm.create-folder", assert_name_guard_create_folder),
    ("name-guard.fm.rename", assert_name_guard_rename),
    ("name-guard.fm.move", assert_name_guard_move),
    ("name-guard.fm.copy", assert_name_guard_copy),
    ("name-guard.fm.create-file", assert_name_guard_create_file),
    ("name-guard.fm.save", assert_name_guard_save),
    ("name-guard.fm.upload-token", assert_name_guard_upload_token),
    ("name-guard.fm.register", assert_name_guard_register),
    ("name-guard.fm.proxy-upload", assert_name_guard_proxy_upload),
    ("name-guard.webdav.put", assert_name_guard_webdav_put),
    ("name-guard.webdav.mkcol", assert_name_guard_webdav_mkcol),
    ("name-guard.webdav.destination", assert_name_guard_webdav_destination),
    ("persisted-mime.fm.listing", assert_persisted_mime_listing),
    ("persisted-mime.fm.copy", assert_persisted_mime_copy),
]


def run_assertions(ctx):
    failures = []
    for name, assertion in ASSERTIONS:
        contract_fixture.build(str(ctx.db))
        ctx.fake.reset()
        checks = Checks()
        try:
            assertion(ctx, checks)
            checks.finish()
        except Exception as error:  # noqa: BLE001 - report the whole red set
            failures.append(name)
            print(f"FAIL  {name}: {error}")
        else:
            print(f"PASS  {name}")
    return failures


def write_report(path, failures):
    if path is None:
        return
    payload = {
        "schema": "dawnop.fm-ancestor-contract.v1",
        "complete": True,
        "total": len(ASSERTIONS),
        "failures": failures,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--jar", type=pathlib.Path)
    parser.add_argument("--port", type=int, default=18320)
    parser.add_argument("--report", type=pathlib.Path)
    args = parser.parse_args()
    if args.list:
        if args.jar is not None or args.report is not None:
            parser.error("--list does not accept --jar or --report")
        print("\n".join(name for name, _ in ASSERTIONS))
        return 0
    if args.jar is None:
        parser.error("--jar is required unless --list is used")
    if not args.jar.is_file():
        parser.error(f"jar does not exist: {args.jar}")

    work = pathlib.Path(tempfile.mkdtemp(prefix="dawnop-fm-ancestor-contract-"))
    db = work / "contract.db"
    env_path = work / "contract.env"
    log_path = work / "backend.log"
    fake_base = f"http://127.0.0.1:{args.port + 1}"
    base = f"http://127.0.0.1:{args.port}"
    contract_fixture.build(str(db))
    contract_run.env_file(env_path, db, args.port, fake_base)
    fake_server = contract_qiniu_fake.serve(args.port + 1)
    java = shutil.which("java") or "java"
    child_env = dict(os.environ)
    child_env.update(
        {
            "DAWNOP_ENV": str(env_path),
            "TZ": "UTC",
            "no_proxy": "*",
            "NO_PROXY": "*",
        }
    )
    for stale in (
        "SECRET_KEY",
        "DAWN_DB_PATH",
        "DAWN_PORT",
        "QINIU_ACCESS_KEY",
        "QINIU_SECRET_KEY",
        "QINIU_RS_HOST",
        "QINIU_UP_HOST",
        "QINIU_DOMAIN",
    ):
        child_env.pop(stale, None)

    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [java, "-Duser.timezone=UTC", "-jar", str(args.jar)],
            env=child_env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        failures = None
        try:
            contract_run.wait_health(base, process)
            token = contract_run.login(base)
            ctx = Context(
                base=base,
                db=db,
                token=token,
                auth=f"{contract_fixture.FIXTURE_USER}:{contract_fixture.FIXTURE_PW}",
                fake=Fake(fake_base),
            )
            failures = run_assertions(ctx)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
            fake_server.shutdown()

    if failures is None:
        return 1
    write_report(args.report, failures)
    print(
        f"SUMMARY  FM ancestor contract: {len(failures)} of "
        f"{len(ASSERTIONS)} assertion(s) failed"
    )
    if failures:
        print(f"backend log: {log_path}", file=sys.stderr)
        return 1
    shutil.rmtree(work, ignore_errors=True)
    print(f"PASS  FM ancestor contract: {len(ASSERTIONS)} reset-isolated assertions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

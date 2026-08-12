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
import urllib.error
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
    if key:
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
        query += " WHERE path = ? OR path LIKE ?"
        params = (prefix, f"{prefix}/%")
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

    add_file(ctx, "rename-missing/source.txt", False, "rename-missing-object")
    statuses["rename"] = api(
        ctx,
        "/api/fm/rename",
        {
            "path": "qiniu://rename-missing",
            "item": "qiniu://rename-missing/source.txt",
            "name": "renamed.txt",
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
        "rename-missing/renamed.txt",
    }
    actual_paths = {row[0] for row in rows(ctx)}
    checks.true(
        expected_paths <= actual_paths, "FM writes did not create expected rows"
    )
    checks.true(
        "rename-missing" not in actual_paths,
        "rename unexpectedly created an ordinary missing ancestor",
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
    add_file(ctx, "target-dir/child.txt", False, "target-dir-child-object")
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


def assert_reparent_preflight(ctx, checks):
    seed_deep_file_ancestor(ctx, "blocked")
    add_file(ctx, "blocked/legacy-dir/child.txt", False, "repair-child-object")
    seed_deep_file_ancestor(ctx, "blocked-copy")
    add_file(
        ctx,
        "blocked-copy/legacy-dir/child.txt",
        False,
        "repair-copy-child-object",
    )
    add_file(ctx, "good", True)
    add_file(ctx, "good-copy", True)
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
    repair_copy_status, _ = api(
        ctx,
        "/api/fm/copy",
        {
            "path": "qiniu://",
            "destination": "qiniu://good-copy",
            "sources": ["qiniu://blocked-copy/legacy-dir"],
        },
    )
    checks.equal(
        repair_copy_status,
        200,
        "copying a clean subtree out of a dirty tree",
    )
    repair_copy_paths = {row[0] for row in rows(ctx)}
    checks.true(
        "good-copy/legacy-dir/child.txt" in repair_copy_paths,
        "repair copy lost child",
    )
    checks.true(
        "blocked-copy/legacy-dir/child.txt" in repair_copy_paths,
        "repair copy removed source",
    )

    seed_deep_file_ancestor(ctx, "blocked-two")
    add_file(
        ctx,
        "blocked-two/legacy-dir/old.txt",
        False,
        "reparent-old-object",
    )
    before_db = db_state(ctx)
    before_keys = ctx.fake.keys()
    ctx.fake.clear_calls()
    rename_status, _ = api(
        ctx,
        "/api/fm/rename",
        {
            "path": "qiniu://blocked-two/legacy-dir",
            "item": "qiniu://blocked-two/legacy-dir/old.txt",
            "name": "renamed.txt",
        },
    )
    move_status, _ = api(
        ctx,
        "/api/fm/move",
        {
            "path": "qiniu://",
            "destination": "qiniu://blocked-two/legacy-dir",
            "sources": ["qiniu://archive"],
        },
    )
    checks.equal(rename_status, 409, "rename file-ancestor rejection")
    checks.equal(move_status, 409, "move file-ancestor rejection")
    checks.equal(db_state(ctx), before_db, "rejected reparent changed DB")
    checks.equal(ctx.fake.keys(), before_keys, "rejected reparent changed objects")
    checks.equal(ctx.fake.calls(), [], "rejected reparent reached Qiniu")


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


ASSERTIONS = [
    ("fm.missing-ancestors.auto-created", assert_fm_missing_ancestors),
    ("fm.external-effects.preflight", assert_fm_external_effect_preflight),
    ("fm.directory-target.preflight", assert_directory_target_preflight),
    ("fm.upload-token.preflight", assert_upload_token_preflight),
    ("fm.register.preflight", assert_register_preflight),
    ("fm.reparent.preflight", assert_reparent_preflight),
    ("fm.conflict.maps-409", assert_conflict_mapping),
    ("subtree.internal-shape.preflight", assert_internal_subtree_shape),
    ("webdav.full-ancestor.preflight", assert_webdav_full_ancestor),
    ("webdav.missing-parent.rejects", assert_webdav_missing_parent),
]


def run_assertions(ctx):
    failures = 0
    for name, assertion in ASSERTIONS:
        contract_fixture.build(str(ctx.db))
        ctx.fake.reset()
        checks = Checks()
        try:
            assertion(ctx, checks)
            checks.finish()
        except Exception as error:  # noqa: BLE001 - report the whole red set
            failures += 1
            print(f"FAIL  {name}: {error}")
        else:
            print(f"PASS  {name}")
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jar", type=pathlib.Path, required=True)
    parser.add_argument("--port", type=int, default=18320)
    args = parser.parse_args()
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
        failures = 1
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

    if failures:
        print(f"backend log: {log_path}", file=sys.stderr)
        return 1
    shutil.rmtree(work, ignore_errors=True)
    print(f"PASS  FM ancestor contract: {len(ASSERTIONS)} reset-isolated assertions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

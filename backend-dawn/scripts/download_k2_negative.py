"""Compile real isolated mutations and require their semantic assertions to fail."""

import argparse
import concurrent.futures
import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import contract_download
import contract_fixture
import contract_qiniu
import contract_qiniu_fake
import contract_representation
import contract_run
from mutant_workdir import require_workdir

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
ROOT = BACKEND.parent

# These are separate compiling mutations, not altered expected values.
SPECS = {
    "logical-etag": ("qiniu", "core/src/api/webdav.dawn", "k2.dav-three-methods"),
    "logical-modified": ("qiniu", "core/src/api/webdav.dawn", "k2.dav-three-methods"),
    "stat-oracle": (
        "qiniu",
        "core/src/util/representation.dawn",
        "k2.dav-three-methods",
    ),
    "copy-source-date": (
        "qiniu",
        "core/src/svc/files.dawn",
        "k2.copy-move-and-writer-coverage",
    ),
    "drop-missing-publication": (
        "qiniu",
        "core/src/repo/repo_drop.dawn",
        "k2.copy-move-and-writer-coverage",
    ),
    "zero-hint-fallback": (
        "unit",
        "core/src/util/representation.dawn",
        "zero remains authoritative",
    ),
    "negative-size": (
        "unit",
        "core/src/util/representation.dawn",
        "zero remains authoritative",
    ),
    "ordinary-overwrite": (
        "qiniu",
        "core/src/qiniu/sign.dawn",
        "k2.ordinary-token-replay-and-register",
    ),
    "copy-force": (
        "qiniu",
        "core/src/qiniu/rs.dawn",
        "k2.copy-move-and-writer-coverage",
    ),
    "propfind-head": ("qiniu", "core/src/api/webdav.dawn", "k2.dav-three-methods"),
    "get-prehead": ("qiniu", "core/src/api/webdav.dawn", "k2.dav-three-methods"),
    "half-capability": ("qiniu", "core/src/api/webdav.dawn", "k2.dav-three-methods"),
    "empty-old-date": (
        "qiniu",
        "core/src/util/representation.dawn",
        "k2.empty-owner-and-transitions",
    ),
    "empty-read-clock": (
        "qiniu",
        "core/src/util/local_empty.dawn",
        "k2.empty-owner-and-transitions",
    ),
    "empty-head-object": (
        "qiniu",
        "core/src/api/webdav.dawn",
        "k2.empty-owner-and-transitions",
    ),
    "scrub-hides-validator": (
        "qiniu",
        "core/src/api/webdav.dawn",
        "k2.dav-three-methods",
    ),
    "backfill-no-cas": ("native", "core/src/repo/repo_fm.dawn", "backfill acceptance"),
    "backfill-no-rescan": (
        "native",
        "core/src/representation_backfill.dawn",
        "backfill acceptance",
    ),
    "backfill-network-lock": (
        "native",
        "core/src/representation_backfill.dawn",
        "backfill acceptance",
    ),
    "backfill-false-ready": (
        "native",
        "core/src/representation_backfill.dawn",
        "backfill acceptance",
    ),
    "backfill-raw-log": (
        "native",
        "core/src/representation_backfill.dawn",
        "backfill acceptance",
    ),
    "backfill-no-alias-guard": (
        "python",
        "scripts/representation_backfill.py",
        "backfill acceptance",
    ),
    "backfill-no-replica-guard": (
        "python",
        "scripts/representation_backfill.py",
        "backfill acceptance",
    ),
    "backfill-default-apply": (
        "python",
        "scripts/representation_backfill.py",
        "backfill acceptance",
    ),
}


def replace(path, before, after):
    text = path.read_text()
    if text.count(before) != 1:
        raise RuntimeError("mutation anchor mismatch")
    path.write_text(text.replace(before, after, 1))


def mutate(name, backend):
    require_workdir(backend)
    p = backend / SPECS[name][1]
    if name in {"logical-etag", "scrub-hides-validator"}:
        replace(
            p,
            "logical_time, v.etag))",
            'logical_time, "\\"" ++ unwrap_or(o.key, "") ++ "\\""))',
        )
    elif name == "logical-modified":
        replace(
            p,
            "o.content_type, v.last_modified_s, logical_time, v.etag",
            "o.content_type, logical_time, logical_time, v.etag",
        )
    elif name == "stat-oracle":
        replace(p, "HeadResp, fetch_head", "HeadResp")
        replace(
            p, "use qiniu/creds.{Qiniu}", "use qiniu/creds.{Qiniu}\nuse qiniu/rs.{stat}"
        )
        replace(
            p,
            "use util/time.{parse_http_date}",
            "use util/time.{parse_http_date, http_date}",
        )
        replace(p, "use json/value.{JObj, JInt}", "use json/value.{JObj, JInt, JStr}")
        replace(
            p,
            "  let head = fetch_head(url)?",
            """  let _url = url
  let r = stat(q, key)?
  let m = match json_parser.parse(r.body) { Ok(JObj(m)) -> m
    _ -> return Err(upstream("stat invalid")) }
  let hash = match map.get(m, "hash") { Some(JStr(v)) -> v
    _ -> "invalid" }
  let date = match map.get(m, "putTime") { Some(JInt(v)) -> v / 10000000
    _ -> 0 }
  let head = HeadResp { status = r.status, headers = [("etag", "\\\"" ++ hash ++ "\\\""), ("last-modified", http_date(date)), ("content-length", to_string(expected))] }""",
        )
    elif name == "copy-source-date":
        replace(
            p,
            "use util/representation.{Published, collect}",
            "use util/representation.{Published, collect, fields, from_head}\nuse util/time.{http_date}",
        )
        replace(
            p,
            "use util/http.{Upstream, as_outbound}",
            "use util/http.{Upstream, as_outbound, HeadResp}",
        )
        replace(
            p,
            "Ok(v) -> Some(v)",
            """Ok(v) -> {
              let f = fields(v)
              let stale = from_head(HeadResp { status = 200, headers = [("etag", f.etag), ("last-modified", http_date(unwrap_or(row.source.representation.last_modified_s, 0))), ("content-length", to_string(f.size))] }, key, f.size, f.profile, now_s())?
              Some(stale)
            }""",
        )
    elif name == "drop-missing-publication":
        replace(
            p,
            "    [PStr(rel), PStr(key), PStr(content_type), PInt(size)] ++ representation_params(representation, key, size)?)?",
            "    [PStr(rel), PStr(key), PStr(content_type), PInt(size), PNull, PNull, PNull, PNull, PNull])?",
        )
        replace(p, "PInt, PStr,", "PInt, PNull, PStr,")
        replace(
            p,
            '  let _t = require_tx("drop file insert ${full(rel)}")?',
            '  let _t = require_tx("drop file insert ${full(rel)}")?\n  let _checked = representation_params(representation, key, size)?',
        )
    elif name == "zero-hint-fallback":
        replace(
            p,
            "  if size < 0 { return Err(bad) }",
            "  if size == 0 { return Ok(unwrap_or(hint, 0)) }\n  if size < 0 { return Err(bad) }",
        )
    elif name == "negative-size":
        replace(
            p, "  if size < 0 { return Err(bad) }", "  if size < 0 { return Ok(0) }"
        )
    elif name == "ordinary-overwrite":
        replace(
            p,
            'let policy = "{\\"scope\\":\\"${q.bucket}:${key}\\",\\"deadline\\":${deadline},\\"insertOnly\\":1}"',
            'let policy = "{\\"scope\\":\\"${q.bucket}:${key}\\",\\"deadline\\":${deadline}}"',
        )
    elif name == "copy-force":
        replace(
            p,
            'let path = "/copy/${src}/${dst}/force/false"',
            'let path = "/copy/${src}/${dst}/force/true"',
        )
    elif name == "propfind-head":
        replace(
            p,
            '"PROPFIND" -> propfind(a, req, rel)',
            """"PROPFIND" -> {
                match with_db(a.db, () => get_row(rel)) { Ok(Some(o)) -> match o.key { Some(key) -> { let _ = representation.collect(a.qiniu, key, o.size) }
                    None -> () }
                  _ -> () }
                propfind(a, req, rel)
              }""",
        )
    elif name == "get-prehead":
        replace(
            p,
            "representation.ObjectIdentity(v) -> handoff(a.qiniu, origin, req.method, representation.fields(v).key, o.content_type)",
            """representation.ObjectIdentity(v) -> {
          let _ = as_http(representation.collect(a.qiniu, representation.fields(v).key, o.size), 502)?
          handoff(a.qiniu, origin, req.method, representation.fields(v).key, o.content_type)
        }""",
        )
        replace(
            p,
            "origin: String) -> Result[Response, HttpError] !io",
            "origin: String) -> Result[Response, HttpError] !Upstream !io",
        )
    elif name == "half-capability":
        replace(
            p,
            '"PROPFIND" -> propfind(a, req, rel)',
            '"PROPFIND" -> Err(http_error(503, UNAVAILABLE))',
        )
    elif name == "empty-old-date":
        replace(
            p,
            "last_modified_s = if size == 0 { published_s } else { lm }",
            "last_modified_s = lm",
        )
    elif name == "empty-read-clock":
        replace(p, "use std/str", "use util/clock.{system_s}\nuse std/str")
        replace(p, "http_date(v.last_modified_s)", "http_date(system_s())")
        replace(
            p,
            "mime: String) -> Result[Response, HttpError] =",
            "mime: String) -> Result[Response, HttpError] !io =",
        )
        replace(
            backend / "core/src/api/api_fm.dawn",
            "mime: String) -> Result[Response, HttpError] =",
            "mime: String) -> Result[Response, HttpError] !io =",
        )
    elif name == "empty-head-object":
        replace(
            p,
            "representation.LocalEmpty(v) -> local_empty.respond(req, v, safe_persisted_mime(o.content_type))",
            'representation.LocalEmpty(v) -> if req.method == "HEAD" { handoff(a.qiniu, origin, req.method, representation.fields(v).key, o.content_type) } else { local_empty.respond(req, v, safe_persisted_mime(o.content_type)) }',
        )
    elif name == "backfill-no-cas":
        text = p.read_text()
        start, end = (
            text.index("pub fn representation_cas("),
            text.index("pub fn representation_duplicate_keys()"),
        )
        p.write_text(
            text[:start]
            + """pub fn representation_cas(snapshot: RepresentationSnapshot, value: Published) -> Result[Bool, String] !Db !io = {
  let v = fields(value)
  with_immediate_tx(() => {
    let n = exec("update files set representation_kind=?,representation_key=?,representation_profile=?,representation_etag=?,representation_last_modified_s=?,size=? where id=?", representation_params(value, v.key, v.size)? ++ [PInt(v.size), PInt(snapshot.id)])?
    Ok(n == 1)
  })
}

"""
            + text[end:]
        )
    elif name == "backfill-no-rescan":
        replace(
            p,
            "for pass in range(0, if apply { 2 } else { 1 })",
            "for pass in range(0, 1)",
        )
    elif name == "backfill-network-lock":
        replace(p, "with_tx, scalar_int", "with_tx, scalar_int, with_immediate_tx")
        replace(
            p,
            "let collected = collect(q, key, size)",
            "let collected = with_db(db, () => with_immediate_tx(() => collect(q, key, size)))",
        )
    elif name == "backfill-false-ready":
        text = p.read_text()
        start, end = (
            text.index("  let ready ="),
            text.index("  Ok(obj([", text.index("  let ready =")),
        )
        p.write_text(text[:start] + "  let ready = true\n" + text[end:])
    elif name == "backfill-raw-log":
        replace(
            p,
            "          let row = snapshot.row",
            "          let row = snapshot.row\n          println(row.path)",
        )
    elif name == "backfill-no-alias-guard":
        replace(p, 'if args.role == "replica" and (', "if False and (")
    elif name == "backfill-no-replica-guard":
        text = p.read_text().replace('if args.role == "live"', "if False")
        p.write_text(text)
    elif name == "backfill-default-apply":
        replace(p, 'default="dry-run"', 'default="apply"')


class Observation:
    strict_assertions = True

    def case(self, name, value):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def business(jar, work, scrub=False):
    work.mkdir(mode=0o700)
    port, fake_port = free_port(), free_port()
    fake_base = f"http://127.0.0.1:{fake_port}"
    fake = contract_qiniu_fake.serve(fake_port)
    db, envf = work / "fixture.db", work / "fixture.env"
    contract_fixture.build(str(db))
    origin = fake_base.replace("http://127.0.0.1", "https://localhost")
    contract_download.prepare_rows(db, origin)
    contract_run.env_file(envf, db, port, fake_base)
    with envf.open("a") as output:
        output.write(f"DAWN_DOWNLOAD_HANDOFF=on\nDAWN_DOWNLOAD_ORIGIN={origin}\n")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DAWN_", "QINIU_", "TENCENT_", "VAULT_", "LIGHTHOUSE_"))
        and key not in {"SECRET_KEY", "DAWNOP_ENV"}
    }
    env["DAWNOP_ENV"] = str(envf)
    log = (work / "application.log").open("wb")
    process = subprocess.Popen(
        ["java", "-Xmx256m", "-jar", str(jar)], env=env, stdout=log, stderr=log
    )
    proxy = None
    try:
        contract_run.wait_health(f"http://127.0.0.1:{port}", process)
        proxy = contract_download.Proxy(work / "nginx", port, fake_base)
        base = f"http://127.0.0.1:{proxy.port}"
        token = contract_run.login(base)
        auth = f"{contract_fixture.FIXTURE_USER}:{contract_fixture.FIXTURE_PW}"
        control = contract_qiniu.Fake(fake_base)
        g = Observation()
        contract_qiniu.drop_cases(g, base, token, auth, control, fake_base, db)
        if scrub:
            # Restore the rejected broad test normalization as a real harness
            # mutation. The independent XML/DB/wire assertions must still fail.
            contract_qiniu.dav_normalize = lambda text: __import__("re").sub(
                r"<D:(getetag|getlastmodified)>.*?</D:\1>", "", text
            )
        contract_representation.run(g, base, token, auth, control, db, fake_base)
    finally:
        if proxy:
            proxy.close()
        process.terminate()
        process.wait(timeout=10)
        log.close()
        fake.shutdown()
        fake.server_close()


def worker(args):
    kind, location, expected = SPECS[args.worker]
    work = Path(args.work)
    backend = work / "backend-dawn"
    backend.mkdir()
    for relative in ("core", "app/jvm", "app/native"):
        shutil.copytree(
            BACKEND / relative,
            backend / relative,
            ignore=shutil.ignore_patterns("*.jar", "lib", "__pycache__"),
        )
    scripts = backend / "scripts"
    scripts.mkdir()
    shutil.copy2(HERE / "representation_backfill.py", scripts)
    (backend / ".mutant-workdir").touch()
    mutate(args.worker, backend)
    compiled = kind == "python"
    observed = ""
    if kind == "qiniu":
        jar = work / "mutant.jar"
        built = subprocess.run(
            [args.dawn, "build", str(backend / "app/jvm"), "-o", str(jar)],
            capture_output=True,
            timeout=120,
        )
        compiled = built.returncode == 0
        if compiled:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    business(jar, work / "wire", args.worker == "scrub-hides-validator")
            except AssertionError as error:
                observed = str(error)
    elif kind == "unit":
        consumer = work / "consumer"
        (consumer / "src").mkdir(parents=True)
        (consumer / "dawn.toml").write_text(
            'schema = 1\nname = "k2_probe"\n[deps]\nsite_core = "'
            + str(backend / "core")
            + '"\n'
        )
        (consumer / "src/main.dawn").write_text(
            'use site_core/util/representation.{stat_size}\ntest "zero remains authoritative" { assert stat_size("{\\"fsize\\":0}", Some(9)) != Ok(9)\n assert stat_size("{\\"fsize\\":-1}", None) != Ok(0) }\n'
        )
        built = subprocess.run(
            [args.dawn, "check", str(consumer)], capture_output=True, timeout=120
        )
        compiled = built.returncode == 0
        if compiled:
            tested = subprocess.run(
                [args.dawn, "test", str(consumer)], capture_output=True, timeout=120
            )
            if (
                tested.returncode
                and b"FAIL" in tested.stdout
                and expected.encode() in tested.stdout
            ):
                observed = expected
    else:
        binary = Path(args.binary)
        if kind == "native":
            binary = work / "mutant"
            built = subprocess.run(
                [args.dawnc, "build", str(backend / "app/native"), "-o", str(binary)],
                capture_output=True,
                timeout=180,
            )
            compiled = built.returncode == 0
        if compiled:
            tested = subprocess.run(
                [
                    sys.executable,
                    str(HERE / "backfill_acceptance.py"),
                    "--binary",
                    str(binary),
                    "--tool",
                    str(scripts / "representation_backfill.py"),
                ]
                + (
                    ["--sqlite-library", str(args.sqlite_library.resolve())]
                    if args.sqlite_library
                    else []
                ),
                capture_output=True,
                timeout=180,
            )
            if tested.returncode and (
                b"AssertionError" in tested.stderr
                or (
                    args.worker == "backfill-network-lock"
                    and b"database is locked" in tested.stderr
                )
            ):
                observed = expected
    result = {
        "mutant": args.worker,
        "location": location,
        "compiled": compiled,
        "assertion": observed,
        "expected": expected,
    }
    print(json.dumps(result))
    return 1 if compiled and observed == expected else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jar", type=Path)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--sqlite-library", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--worker", choices=SPECS)
    parser.add_argument("--dawn")
    parser.add_argument("--dawnc")
    args = parser.parse_args()
    if args.worker:
        return worker(args)
    args.work.mkdir(mode=0o700)
    dawn = subprocess.check_output([ROOT / "scripts/fetch-dawn.sh"], text=True).strip()
    dawnc = subprocess.check_output(
        [ROOT / "scripts/fetch-dawn-native.sh"], text=True
    ).strip()
    with contextlib.redirect_stdout(io.StringIO()):
        business(args.jar.resolve(), args.work / "positive-before")

    def one(name):
        directory = args.work / name
        directory.mkdir(mode=0o700)
        result = subprocess.run(
            [
                sys.executable,
                __file__,
                "--worker",
                name,
                "--work",
                str(directory),
                "--binary",
                str(args.binary.resolve()),
                "--dawn",
                dawn,
                "--dawnc",
                dawnc,
            ]
            + (
                ["--sqlite-library", str(args.sqlite_library.resolve())]
                if args.sqlite_library
                else []
            ),
            capture_output=True,
            timeout=300,
        )
        try:
            payload = json.loads(result.stdout.splitlines()[-1])
        except (ValueError, TypeError):
            payload = {"mutant": name, "compiled": False, "assertion": "worker failure"}
        payload["exit_code"] = result.returncode
        shutil.rmtree(directory)
        return payload

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(one, SPECS))
    with contextlib.redirect_stdout(io.StringIO()):
        business(args.jar.resolve(), args.work / "positive-after")
    passed = all(item["exit_code"] == 1 and item["compiled"] for item in results)
    evidence = {
        "positive_before": "PASS",
        "positive_after": "PASS",
        "negative_controls": results,
        "passed": passed,
    }
    shutil.rmtree(args.work)
    if args.summary:
        args.summary.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

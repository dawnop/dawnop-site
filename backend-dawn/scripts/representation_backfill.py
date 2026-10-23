"""Launch shared Dawn maintenance with explicit identities and aggregate output.

No SQL or object requests are implemented here. Identity checks, receipts,
private inputs and bounded subprocess orchestration surround the Dawn owner.
"""

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

VERSION = "download-k2-backfill-v1"
COUNTS = {
    "stat_ok",
    "stat_rejected",
    "head_ok",
    "head_rejected",
    "head_le_100_ms",
    "head_le_1000_ms",
    "head_gt_1000_ms",
    "profile_mismatch",
    "key_mismatch",
    "rows",
    "directories",
    "files",
    "complete",
    "unresolved",
    "initial_missing",
    "missing_key",
    "negative_size",
    "duplicate_keys",
    "unscanned",
    "requests",
    "planned",
    "updated",
    "conflicts",
    "metadata_failures",
    "object_missing",
    "database_failures",
    "budget_exhausted",
    "elapsed_ms",
    "internal_failures",
}
FIELDS = COUNTS | {
    "version",
    "phase",
    "profile",
    "schema_fingerprint",
    "sqlite_version",
    "engine_fingerprint",
    "ready",
}


class Refusal(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refusal("arguments")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def private_json(path):
    p = Path(path)
    info = p.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o077
        or info.st_uid != os.getuid()
    ):
        raise Refusal("permissions")
    if info.st_size > 65536:
        raise Refusal("input_limit")
    return json.loads(p.read_text())


def write_private(path, value):
    path = Path(path)
    if path.exists() and path.is_symlink():
        raise Refusal("permissions")
    fd, temporary = tempfile.mkstemp(prefix=".summary-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, sort_keys=True)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def identity(path):
    p = Path(path)
    info = p.stat()
    if not stat.S_ISREG(info.st_mode):
        raise Refusal("identity")
    return {"path": str(p.resolve()), "device": info.st_dev, "inode": info.st_ino}


def summary(raw):
    result = json.loads(raw)
    if (
        not isinstance(result, dict)
        or set(result) - FIELDS
        or result.get("version") != VERSION
    ):
        raise Refusal("output_schema")
    for name, value in result.items():
        if name in COUNTS and (type(value) is not int or value < 0):
            raise Refusal("output_schema")
        if name in {"schema_fingerprint", "engine_fingerprint"} and (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise Refusal("output_schema")
        if name == "sqlite_version" and (
            not isinstance(value, str)
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value)
        ):
            raise Refusal("output_schema")
        if name == "profile" and (
            not isinstance(value, str)
            or not value.startswith("identity-v1:")
            or len(value) != 76
            or any(c not in "0123456789abcdef" for c in value[12:])
        ):
            raise Refusal("output_schema")
    if type(result.get("ready")) is not bool:
        raise Refusal("output_schema")
    if result.get("internal_failures"):
        if result["ready"]:
            raise Refusal("output_schema")
    elif not (FIELDS - {"internal_failures"}) <= set(result):
        raise Refusal("output_schema")
    if result["ready"] and (
        result.get("rows") != result.get("files", -1) + result.get("directories", -1)
        or result.get("complete") != result.get("files")
        or any(
            result.get(name, -1) != 0
            for name in (
                "unresolved",
                "unscanned",
                "missing_key",
                "negative_size",
                "duplicate_keys",
                "conflicts",
                "metadata_failures",
                "object_missing",
                "database_failures",
                "budget_exhausted",
            )
        )
    ):
        raise Refusal("output_schema")
    if "phase" in result and result["phase"] not in {
        "inventory",
        "dry-run",
        "apply",
        "verify",
        "resume",
    }:
        raise Refusal("output_schema")
    return result


def child(binary, work, payload, ca, sqlite_library=None):
    with tempfile.TemporaryDirectory(prefix="request-", dir=work) as directory:
        runtime = Path(directory)
        input_file = runtime / "input.json"
        write_private(input_file, {**payload, "temp_parent": str(runtime.resolve())})
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TZ": "UTC",
            "NO_PROXY": "*",
            "no_proxy": "*",
            "TMPDIR": str(runtime),
            "LANG": "C.UTF-8",
        }
        if ca:
            environment["CURL_CA_BUNDLE"] = str(Path(ca).resolve(strict=True))
        if sqlite_library:
            library = Path(sqlite_library).resolve(strict=True)
            if library.name != "libsqlite3.so.0":
                raise Refusal("configuration")
            environment["LD_LIBRARY_PATH"] = str(library.parent)
        with (
            (runtime / "stdout").open("wb") as out,
            (runtime / "stderr").open("wb") as err,
        ):
            process = subprocess.Popen(
                [str(binary), "--representation-backfill", str(input_file)],
                stdout=out,
                stderr=err,
                env=environment,
                start_new_session=True,
            )
            try:
                process.wait(timeout=payload["max_seconds"] + 12)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
            if process.returncode:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise Refusal("child_failure")
        if (runtime / "stderr").stat().st_size or (
            runtime / "stdout"
        ).stat().st_size > 8192:
            raise Refusal("child_failure")
        return summary((runtime / "stdout").read_bytes())


def run(args):
    work = Path(args.work)
    work.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = work.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_mode & 0o077
        or info.st_uid != os.getuid()
    ):
        raise Refusal("permissions")
    target = identity(args.db)
    live = identity(args.live_db)
    protected = [Path(args.db), Path(args.live_db)]
    protected += [
        Path(value) for value in (args.config, args.binary, args.identity) if value
    ]
    outputs = [work / name for name in ("history.json", "checkpoint")]
    if args.write_receipt:
        outputs.append(Path(args.write_receipt))
    for output in outputs:
        if any(
            output.resolve() == source.resolve()
            or (
                output.exists() and source.exists() and os.path.samefile(output, source)
            )
            for source in protected
        ):
            raise Refusal("live_alias")
    alias = target["device"] == live["device"] and target["inode"] == live["inode"]
    if args.role == "replica" and (
        alias or target["path"].startswith(("/opt/", "/var/lib/"))
    ):
        raise Refusal("live_alias")
    if args.role == "live" and (
        not alias or not args.allow_live or not args.replica_receipt
    ):
        raise Refusal("replica_required")
    marker = Path(args.identity)
    if args.init_identity:
        if marker.exists():
            raise Refusal("identity")
        if args.role == "replica":
            write_private(marker, {"version": VERSION, "role": "replica", **target})
            return {
                "version": VERSION,
                "phase": "inventory",
                "ready": False,
                "rows": 0,
            }, 0
    else:
        expected = private_json(marker)
        if (
            any(expected.get(key) != value for key, value in target.items())
            or expected.get("role") != args.role
            or expected.get("version") != VERSION
        ):
            raise Refusal("identity")
    config = private_json(args.config)
    if set(config) - {"ak", "sk", "bucket", "domain", "rs_host", "ca_bundle"} or any(
        not isinstance(config.get(k), str) or not config[k]
        for k in ("ak", "sk", "bucket", "domain", "rs_host")
    ):
        raise Refusal("configuration")
    domain = urlsplit(config["domain"])
    if (
        domain.scheme != "https"
        or not domain.hostname
        or domain.path not in {"", "/"}
        or domain.query
        or domain.fragment
        or domain.username
    ):
        raise Refusal("configuration")
    for value, lo, hi in [
        (args.page_size, 1, 1000),
        (args.max_requests, 1, 10000000),
        (args.max_seconds, 21, 86400),
        (args.interval_ms, 0, 60000),
    ]:
        if not lo <= value <= hi:
            raise Refusal("budget")
    binary = Path(args.binary).resolve(strict=True)
    package = digest(binary)
    profile = (
        "identity-v1:"
        + hashlib.sha256(config["domain"].rstrip("/").encode()).hexdigest()
    )
    ca = config.pop("ca_bundle", "")
    checkpoint = work / "checkpoint"
    if checkpoint.exists() and (
        checkpoint.is_symlink() or checkpoint.stat().st_mode & 0o077
    ):
        raise Refusal("permissions")
    payload = {
        **config,
        "db": target["path"],
        "phase": args.phase,
        "page_size": args.page_size,
        "max_requests": args.max_requests,
        "max_seconds": args.max_seconds,
        "interval_ms": args.interval_ms,
        "checkpoint": str(checkpoint.resolve()),
        "temp_parent": str(work.resolve()),
    }
    if args.role == "live":
        receipt = private_json(args.replica_receipt)
        if (
            receipt.get("version") != VERSION
            or receipt.get("role") != "replica"
            or receipt.get("package") != package
            or receipt.get("profile") != profile
            or receipt.get("ready") is not True
            or set(receipt.get("stages", []))
            != {"dry-run", "apply", "repeat", "resume", "verify"}
        ):
            raise Refusal("replica_required")
        preflight = child(
            binary, work, {**payload, "phase": "inventory"}, ca, args.sqlite_library
        )
        if any(
            preflight.get(name) != receipt.get(name)
            for name in ("schema_fingerprint", "engine_fingerprint", "sqlite_version")
        ):
            raise Refusal("replica_required")
        if args.init_identity:
            write_private(marker, {"version": VERSION, "role": "live", **target})
            return {
                "version": VERSION,
                "phase": "inventory",
                "ready": False,
                "rows": 0,
            }, 0
    result = child(binary, work, payload, ca, args.sqlite_library)
    if result.get("profile") not in {None, profile}:
        raise Refusal("output_schema")
    errors = sum(
        result.get(key, 0)
        for key in (
            "metadata_failures",
            "object_missing",
            "database_failures",
            "internal_failures",
            "missing_key",
            "negative_size",
            "duplicate_keys",
            "unscanned",
        )
    )
    code = (
        7
        if result.get("internal_failures")
        else 6
        if result.get("budget_exhausted")
        else 5
        if result.get("conflicts")
        else 4
        if errors
        else 0
    )
    if (
        args.phase in {"apply", "resume", "verify"}
        and not result["ready"]
        and code == 0
    ):
        code = 4
    if (
        args.phase == "dry-run"
        and result.get("planned") != result.get("initial_missing")
        and code == 0
    ):
        code = 4
    history_file = work / "history.json"
    history = (
        private_json(history_file)
        if history_file.exists()
        else {
            "identity": target,
            "package": package,
            "profile": profile,
            "stages": [],
            **{
                name: result.get(name)
                for name in (
                    "schema_fingerprint",
                    "engine_fingerprint",
                    "sqlite_version",
                )
            },
        }
    )
    if (
        history.get("identity") != target
        or history.get("package") != package
        or history.get("profile") != profile
        or any(
            history.get(name) != result.get(name)
            for name in ("schema_fingerprint", "engine_fingerprint", "sqlite_version")
        )
    ):
        raise Refusal("receipt_identity")
    if code == 0 and args.phase != "inventory":
        stage = (
            "repeat"
            if args.phase == "apply"
            and "apply" in history["stages"]
            and result.get("updated") == 0
            else args.phase
        )
        history["stages"] = sorted(set(history["stages"]) | {stage})
        write_private(history_file, history)
    if args.write_receipt:
        if (
            args.role != "replica"
            or args.phase != "verify"
            or not result["ready"]
            or set(history["stages"])
            != {"dry-run", "apply", "repeat", "resume", "verify"}
        ):
            raise Refusal("replica_incomplete")
        write_private(
            args.write_receipt,
            {
                "version": VERSION,
                "role": "replica",
                "ready": True,
                "package": package,
                "profile": profile,
                "schema_fingerprint": result["schema_fingerprint"],
                "engine_fingerprint": result["engine_fingerprint"],
                "sqlite_version": result["sqlite_version"],
                "stages": history["stages"],
            },
        )
    return result, code


def main():
    def interrupted(signum, frame):
        raise InterruptedError()

    signal.signal(signal.SIGTERM, interrupted)
    parser = Parser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument(
        "--live-db", required=True, help="Explicit excluded live identity"
    )
    parser.add_argument("--identity", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--binary")
    parser.add_argument("--config")
    parser.add_argument(
        "--sqlite-library",
        help="Explicit approved SQLite shared library, otherwise package default",
    )
    parser.add_argument("--role", choices=["replica", "live"], default="replica")
    parser.add_argument(
        "--phase",
        choices=["inventory", "dry-run", "apply", "verify", "resume"],
        default="dry-run",
    )
    parser.add_argument("--init-identity", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--replica-receipt")
    parser.add_argument("--write-receipt")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-requests", type=int, default=10000)
    parser.add_argument("--max-seconds", type=int, default=3600)
    parser.add_argument("--interval-ms", type=int, default=500)
    previous = os.umask(0o077)
    try:
        args = parser.parse_args()
        result, code = run(args)
    except Refusal as error:
        allowed = {
            "arguments",
            "permissions",
            "input_limit",
            "identity",
            "live_alias",
            "replica_required",
            "configuration",
            "budget",
            "child_failure",
            "output_schema",
            "receipt_identity",
            "replica_incomplete",
        }
        category = str(error) if str(error) in allowed else "internal"
        result, code = (
            {
                "version": VERSION,
                "ready": False,
                "failure_category": category,
                "failures": 1,
            },
            3,
        )
    except BaseException:
        result, code = (
            {
                "version": VERSION,
                "ready": False,
                "failure_category": "internal",
                "failures": 1,
            },
            7,
        )
    finally:
        os.umask(previous)
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())

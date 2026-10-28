"""Prove batch negative controls red, then run both real transport owners."""

import argparse
import contextlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BACKEND = ROOT / "backend-dawn"
SPEC = importlib.util.spec_from_file_location(
    "sqlite_acceptance", HERE.parent / "native-sqlite/run.py"
)
SQL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SQL)
run, ok = SQL.run, SQL.ok


def build(work, arm, backend=BACKEND):
    work.mkdir(parents=True, exist_ok=True)
    project = work / "project"
    (project / "src").mkdir(parents=True)
    manifest = (backend / f"app/{arm}/dawn.toml").read_text()
    # Native sources belong to the dependency, not this consumer root.
    manifest = re.sub(r"(?ms)^\[native\]\n.*?(?=^\[|\Z)", "", manifest)
    manifest = manifest.replace('name = "site_sqlite"', 'name = "monitor_probe"')
    manifest = manifest.replace(
        'site_core = "../../core"', f'site_core = "{backend / "core"}"'
    )
    manifest = manifest.replace(
        "[deps]\n", f'[deps]\nmonitor_scenarios = "{backend / "test/monitor"}"\n'
    )
    if arm == "native":
        manifest = manifest.replace(
            "[deps]\n", f'[deps]\nsite_sqlite = "{backend / "app/native"}"\n'
        )
        owner = "use site_sqlite/util/http_curl.{with_upstream, CurlConfig}"
        body = "with_upstream(CurlConfig { temp_parent = argv[3] }, () => main_scenario(argv))"
        compiler = Path(run([ROOT / "scripts/fetch-dawn-native.sh"])[0].stdout.strip())
        artifact = work / "consumer"
    else:
        (project / "src/util").mkdir()
        shutil.copy2(
            backend / "app/jvm/src/util/http_jvm.dawn",
            project / "src/util/http_jvm.dawn",
        )
        owner = "use util/http_jvm.{with_upstream, new_client}"
        body = "with_upstream(new_client(), () => main_scenario(argv))"
        compiler = Path(run([ROOT / "scripts/fetch-dawn.sh"])[0].stdout.strip())
        artifact = work / "consumer.jar"
    (project / "dawn.toml").write_text(manifest)
    (project / "src/main.dawn").write_text(
        f"{owner}\nuse monitor_scenarios/scenarios.{{main_scenario}}\n"
        f"pub fn main() -> Unit !io = {{ let argv = args()\n  {body}\n}}\n"
    )
    result, wall = run([compiler, "build", project, "-o", artifact])
    (work / "build.log").write_text(result.stdout + result.stderr)
    ok(f"{arm} consumer compiled; wall_s={wall:.3f}")
    return (
        [str(artifact)]
        if arm == "native"
        else ["java", "-XX:-UsePerfData", "-Xms32m", "-Xmx256m", "-jar", str(artifact)]
    )


@contextlib.contextmanager
def peer(work):
    events = work / "events.jsonl"
    process = subprocess.Popen(
        ["python3", str(HERE / "fake.py"), "--events", str(events)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port = process.stdout.readline().strip()
        assert port.isdigit(), process.stderr.read()
        yield f"http://127.0.0.1:{port}", events
    finally:
        process.terminate()
        _, errors = process.communicate(timeout=10)
        (work / "peer.log").write_text(errors)
        assert not errors, errors


def invoke(command, work, base, name, env, mode="", owner=None):
    staging = work / "staging"
    staging.mkdir(exist_ok=True)
    calls = Path(env["BATCH_CURL_CALLS"])
    before = len(calls.read_text().splitlines()) if calls.exists() else 0
    events = Path(env["BATCH_EVENTS"])
    event_start = len(events.read_text().splitlines()) if events.exists() else 0
    result, wall = run(
        [*command, name, base, mode, staging], env=env, good=False, timeout=40
    )
    (work / (name + ("-" + mode if mode else "") + ".log")).write_text(
        result.stdout + result.stderr
    )
    assert not list(staging.iterdir()), "batch-temp-cleanup"
    if owner:
        assert (
            result.returncode != 0
            and f"assertion: {owner}" in result.stdout + result.stderr
        ), result.stdout + result.stderr
        ok(
            f"negative {work.name}: compiled successfully; red by {owner}; process_wall_s={wall:.3f}"
        )
        return None
    assert result.returncode == 0, result.stdout + result.stderr
    if name == "order":
        rows = [
            json.loads(line) for line in events.read_text().splitlines()[event_start:]
        ]
        completed = [e["path"].split("/")[2] for e in rows if e["kind"] == "finish"]
        assert completed.index("1") < completed.index("0"), (
            "fake completion order must differ"
        )
    if work.name == "native":
        rows = calls.read_text().splitlines()[before:]
        expected = (
            2
            if name == "monitor" and mode not in ("domains-fail", "empty", "warm")
            else 1
        )
        assert len(rows) == expected, ("one-curl-per-round", name, mode, rows)
        for row in rows:
            argv = json.loads(row)
            assert argv[0] == "-q" and "--parallel" in argv
            assert not any(
                "http://" in arg or "https://" in arg or "secret" in arg for arg in argv
            ), "private-curl-argv"
    payload = json.loads(result.stdout)
    ok(
        f"{work.name} {name} {mode}: process_wall_s={wall:.3f}"
        + (f" batch_wall_ms={payload['wall_ms']}" if "wall_ms" in payload else "")
    )
    return payload


def mutate(work, relative, old, new):
    backend = work / "backend-dawn"
    for name in ("core", "app/native", "app/jvm", "test/monitor"):
        shutil.copytree(BACKEND / name, backend / name)
    (backend / SQL.GUARD.SENTINEL).touch()
    SQL.GUARD.require_workdir(backend)
    path = backend / relative
    text = path.read_text()
    assert text.count(old) == 1, (relative, old, text.count(old))
    path.write_text(text.replace(old, new))
    return backend


def controls(work, base, env):
    native = "app/native/src/util/http_curl.dawn"
    jvm = "app/jvm/src/util/http_jvm.dawn"
    cases = [
        (
            "serial",
            "native",
            native,
            '"--parallel", "--parallel-immediate", ',
            "",
            "parallel",
            "parallel-wall",
        ),
        (
            "completion-order",
            "native",
            native,
            'let lines = filter(str.split(metadata, "\\n"), line => str.starts_with(line, to_string(item.index) ++ "\\t"))',
            'let lines = [str.split(metadata, "\\n")[item.index]]',
            "order",
            "completion-ID",
        ),
        (
            "drop-error",
            "native",
            native,
            "elapsed_ms = duration, error = error",
            "elapsed_ms = duration, error = if code == 28 { error } else { None }",
            "mixed",
            "item-error",
        ),
        (
            "drop-timeout",
            "native",
            native,
            "elapsed_ms = duration, error = error",
            "elapsed_ms = duration, error = if code == 28 { None } else { error }",
            "mixed",
            "item-timeout",
        ),
        (
            "serial",
            "jvm",
            jvm,
            "Ok(BatchStarted { response = future, completion = completed })",
            "let _ = completed.get()!\n    Ok(BatchStarted { response = future, completion = completed })",
            "parallel",
            "parallel-wall",
        ),
        (
            "drop-error",
            "jvm",
            jvm,
            "elapsed_ms = elapsed, error = Some(real)",
            "elapsed_ms = elapsed, error = None",
            "mixed",
            "item-error",
        ),
        (
            "drop-timeout",
            "jvm",
            jvm,
            "elapsed_ms = elapsed, error = Some(real)",
            'elapsed_ms = elapsed, error = if str.ends_with(real.kind, "TimeoutException") { None } else { Some(real) }',
            "mixed",
            "item-timeout",
        ),
    ]
    for arm in ("native", "jvm"):
        cases.append(
            (
                "batch-latency",
                arm,
                "core/src/svc/monitor_batch.dawn",
                "Ok(item) -> item.elapsed_ms",
                "Ok(item) -> fold(replies, 0, (n, r) => if r.elapsed_ms > n { r.elapsed_ms } else { n })",
                "vault",
                "vault-item-duration",
            )
        )
    for name, arm, file, old, new, scenario, owner in cases:
        target = work / f"{arm}-{name}"
        backend = mutate(target, file, old, new)
        command = build(target, arm, backend)
        invoke(command, target, base, scenario, env, owner=owner)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path)
    parser.add_argument("--skip-controls", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    work = args.work or Path(tempfile.mkdtemp(prefix="monitor-batch-"))
    work.mkdir(parents=True, exist_ok=True)
    assert not list(work.iterdir()), "work must be empty"
    home = work / "curl-home"
    home.mkdir()
    (home / ".curlrc").write_text("proxy = http://127.0.0.1:1\nlocation\ninsecure\n")
    env = dict(
        os.environ,
        CURL_HOME=str(home),
        HTTP_PROXY="http://127.0.0.1:1",
        HTTPS_PROXY="http://127.0.0.1:1",
    )
    real_curl = shutil.which("curl")
    shim = work / "bin"
    shim.mkdir()
    wrapper = shim / "curl"
    wrapper.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        "with open(os.environ['BATCH_CURL_CALLS'], 'a') as out:\n"
        "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "os.execv(os.environ['BATCH_REAL_CURL'], [os.environ['BATCH_REAL_CURL'], *sys.argv[1:]])\n"
    )
    wrapper.chmod(0o755)
    env.update(
        PATH=str(shim) + os.pathsep + env["PATH"],
        BATCH_REAL_CURL=real_curl,
        BATCH_CURL_CALLS=str(work / "curl-calls.jsonl"),
    )
    with peer(work) as (base, events):
        env["BATCH_EVENTS"] = str(events)
        if not args.skip_controls:
            controls(work, base, env)
        numbers = {}
        for arm in ("native", "jvm"):
            target = work / arm
            command = build(target, arm)
            rows = {}
            for scenario in ("parallel", "order", "mixed", "vault", "boundaries"):
                rows[scenario] = invoke(command, target, base, scenario, env)
            for mode in (
                "normal",
                "failure",
                "timeout",
                "domains-fail",
                "tune-fail",
                "billing-fail",
                "empty",
                "packs",
                "warm",
            ):
                invoke(command, target, base, "monitor", env, mode)
            numbers[arm] = rows
        entries = [json.loads(line) for line in events.read_text().splitlines()]
        assert not any(e["path"] == "/must-not-contact" for e in entries), (
            "invalid-input-reached-peer"
        )
        (work / "results.json").write_text(json.dumps(numbers, indent=2) + "\n")
    ok(f"monitor batch dual arm acceptance; wall_s={time.monotonic() - started:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

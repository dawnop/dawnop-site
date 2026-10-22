"""Require real Clock/Fs behavior and compiling negative controls on both arms."""

import argparse
import importlib.util
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BACKEND = ROOT / "backend-dawn"
sys.path.insert(0, str(HERE.parent))
from mutant_workdir import require_workdir  # noqa: E402
from test_project import stage_project  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "sqlite_acceptance", HERE.parent / "native-sqlite/run.py"
)
SQL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SQL)
run, ok = SQL.run, SQL.ok


def replace(path, old, new):
    text = path.read_text()
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new))


def fixture(work):
    root = work / "files"
    root.mkdir()
    (root / "empty").write_bytes(b"")
    (root / "data").write_bytes(b"\x00\x80\xffx")
    (root / "directory").mkdir()
    denied = root / "denied"
    denied.mkdir()
    (denied / "data").write_bytes(b"no access")
    denied.chmod(0)
    try:
        os.stat(denied / "data")
    except PermissionError:
        pass
    else:
        raise AssertionError("permission fixture must genuinely deny stat")
    return root, denied


def build_probe(work, backend, compiler, arm):
    project = work / arm
    (project / "src").mkdir(parents=True)
    shutil.copy2(backend / "test/clock-fs/src/main.dawn", project / "src/main.dawn")
    manifest = (backend / "test/clock-fs/dawn.toml").read_text()
    manifest = manifest.replace('"../../core"', f'"{backend / "core"}"')
    manifest = manifest.replace('"../sql"', f'"{backend / "test/sql"}"')
    (project / "dawn.toml").write_text(manifest)
    artifact = work / (arm + (".jar" if arm == "jvm" else ".bin"))
    result, elapsed = run([compiler, "build", project, "-o", artifact])
    (work / (arm + ".build.log")).write_text(result.stdout + result.stderr)
    ok(f"{work.name}/{arm} compiled; wall_s={elapsed:.3f}")
    return ["java", "-jar", artifact] if arm == "jvm" else [artifact]


def invoke(work, command, arm, files, owner=None):
    result, elapsed = run([*command, files], good=False)
    output = result.stdout + result.stderr
    (work / (arm + ".run.log")).write_text(output)
    if owner:
        assert result.returncode != 0 and f"assertion: {owner}" in output, output
        ok(
            f"negative {work.name}/{arm}: compiled, red by {owner}; wall_s={elapsed:.3f}"
        )
    else:
        assert result.returncode == 0, output
        for name in ("directory", "missing", "permission", "invalid-path"):
            assert f"PASS {name}-size-error:" in output, output
        print(output, end="", flush=True)
        ok(f"{arm} real Clock/Fs acceptance; wall_s={elapsed:.3f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    work = args.work or Path(tempfile.mkdtemp(prefix="dawnop-clock-fs-"))
    work.mkdir(parents=True, exist_ok=True)
    assert not any(work.iterdir()), "work directory must be empty"
    (work / ".mutant-workdir").touch()
    require_workdir(work)
    compilers = {
        "native": Path(run([ROOT / "scripts/fetch-dawn-native.sh"])[0].stdout.strip()),
        "jvm": Path(run([ROOT / "scripts/fetch-dawn.sh"])[0].stdout.strip()),
    }
    files, denied = fixture(work)
    try:
        for name, relative, old, new, owner in (
            (
                "wall-thousandfold",
                "util/clock.dawn",
                "io.now_wall_ns() / 1000000",
                "io.now_wall_ns() / 1000",
                "wall-ns-to-ms",
            ),
            (
                "directory-as-file",
                "util/fs.dawn",
                "Err(e) -> Err(fe_text(e))",
                "Err(e) -> if io.is_dir(path) { Ok(0) } else { Err(fe_text(e)) }",
                "directory-size-error",
            ),
        ):
            mutation = work / name
            backend = mutation / "backend-dawn"
            for rel in ("core", "test/sql", "test/clock-fs"):
                shutil.copytree(BACKEND / rel, backend / rel)
            replace(backend / "core/src" / relative, old, new)
            for arm, compiler in compilers.items():
                command = build_probe(mutation, backend, compiler, arm)
                invoke(mutation, command, arm, files, owner)

        mutation = work / "wall-elapsed"
        project = mutation / "project"
        stage_project(project)
        monitor = project / "src/svc/monitor.dawn"
        replace(monitor, "let t0 = io.now()", "let t0 = io.now_wall_ns()")
        replace(
            monitor, "jint(elapsed_ms(t0))", "jint((io.now_wall_ns() - t0) / 1000000)"
        )
        compiler = compilers["jvm"]
        built, _ = run([compiler, "build", project, "-o", mutation / "probe.jar"])
        (mutation / "build.log").write_text(built.stdout + built.stderr)
        tested, elapsed = run([compiler, "test", project], good=False)
        output = tested.stdout + tested.stderr
        (mutation / "test.log").write_text(output)
        red = [
            line.split(" :: ", 1)[1]
            for line in output.splitlines()
            if line.startswith("FAIL  ")
        ]
        assert tested.returncode != 0 and red == [
            "vault latency ignores a backwards wall clock"
        ], output
        ok(
            f"negative wall-elapsed/jvm: compiled, red exactly by backwards wall latency; wall_s={elapsed:.3f}"
        )

        positive = work / "positive"
        for arm, compiler in compilers.items():
            command = build_probe(positive, BACKEND, compiler, arm)
            invoke(positive, command, arm, files)
    finally:
        denied.chmod(0o700)
    ok(f"Clock/Fs acceptance; wall_s={time.monotonic() - started:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Mix the fixed native and JDBC engines under WAL write/read/checkpoint churn."""

import argparse
import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path

import package

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True)
    started = time.monotonic()
    manifest = package.verify(args.release)
    native = args.work / "stress"
    subprocess.run(
        [
            "cc",
            "-O2",
            "-I",
            str(args.source),
            str(HERE / "stress.c"),
            "-o",
            str(native),
            "-L",
            str(args.release / "lib"),
            "-l:libsqlite3.so.0",
            "-Wl,-rpath," + str(args.release / "lib"),
        ],
        check=True,
    )
    jar = ROOT / "backend-dawn/lib/sqlite-jdbc-3.53.4.0.jar"
    assert (
        hashlib.sha256(jar.read_bytes()).hexdigest()
        == "bcb1f51e36f940867e83342f9efbf5968ac44a6bef4d397bb4af7b17b45cd2fb"
    )
    subprocess.run(
        ["javac", "-d", str(args.work), str(HERE / "Stress.java")], check=True
    )
    db = args.work / "stress.db"
    native_cmd = [str(native), str(db)]
    java_cmd = ["java", "-cp", str(jar) + ":" + str(args.work), "Stress", str(db)]
    records = []

    def finish(process, label):
        out, err = process.communicate(timeout=90)
        (args.work / (label + ".log")).write_text(out + err)
        assert process.returncode == 0, label + ": " + out + err
        result = json.loads(out.strip().splitlines()[-1])
        if result["engine"] == "3.54.1":
            assert "sqlite_source_id=" + manifest["sqlite"]["source_id"] in err
        records.append(result)
        return result

    def launch(argv):
        return subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

    finish(launch(native_cmd + ["setup", "setup", "1", "-"]), "setup")
    ready = args.work / "held"
    holder = launch(native_cmd + ["hold", "hold", "1", str(ready)])
    children = [holder]
    try:
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert holder.poll() is None and time.monotonic() < deadline
            threading.Event().wait(0.01)
        waiter = launch(java_cmd + ["write", "busy-proof", "1", "-"])
        children.append(waiter)
        finish(holder, "hold")
        result = finish(waiter, "busy-proof")
        assert result["busy"] > 0, "real cross engine BUSY required"
        jobs = []
        for arm, cmd in [("native", native_cmd), ("jvm", java_cmd)]:
            for mode, copies in [("write", 2), ("read", 1), ("checkpoint", 1)]:
                for index in range(copies):
                    label = f"{arm}-{mode}-{index}"
                    child = launch(cmd + [mode, label, "300", "-"])
                    jobs.append((label, child))
                    children.append(child)
        for label, child in jobs:
            finish(child, label)
        # A final reset after contention proves successful reset as well as BUSY attempts.
        finish(
            launch(native_cmd + ["checkpoint", "final-native", "10", "-"]),
            "final-native",
        )
        finish(launch(java_cmd + ["checkpoint", "final-jvm", "10", "-"]), "final-jvm")
        finish(launch(java_cmd + ["verify", "integrity", "1", "1201"]), "integrity")
        finish(
            launch(native_cmd + ["verify", "native-integrity", "1", "1201"]),
            "native-integrity",
        )
        assert sum(r["writes"] for r in records) == 1201
        for engine in ("3.54.1", "3.53.4"):
            rows = [r for r in records if r["engine"] == engine]
            assert sum(r["reads"] for r in rows) > 0
            assert sum(r["complete"] for r in rows) >= 20
            assert sum(r["resets"] for r in rows) >= 10
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate()
    result = {
        "workers": records,
        "wall_s": round(time.monotonic() - started, 3),
        "limit": "未复现不证明补丁存在，版本 provenance 是硬条件",
    }
    (args.work / "summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    )
    print(
        f"PASS O1 mixed engines: 1201 committed writes, read invariants, repeated FULL, WAL resets, close/reopen, BUSY, integrity; wall_s={result['wall_s']}",
        flush=True,
    )
    print(result["limit"], flush=True)


if __name__ == "__main__":
    main()

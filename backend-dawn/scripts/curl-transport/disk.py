"""Run real ENOSPC controls on a 128 KiB tmpfs in a private mount namespace."""

import errno
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("curl_acceptance", HERE / "run.py")
RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN)


def main():
    settings = json.loads(Path(sys.argv[1]).read_text())
    mount = Path(settings["mount"])
    subprocess.run(
        ["mount", "-t", "tmpfs", "-o", "size=128k,nosuid,nodev", "tmpfs", str(mount)],
        check=True,
    )
    rows = []
    try:
        # Prove the mounted filesystem really returns ENOSPC, rather than
        # replacing a successful consumer result with a synthetic disk error.
        try:
            (mount / "probe").write_bytes(b"x" * 262144)
        except OSError as error:
            assert error.errno == errno.ENOSPC, error
        else:
            raise AssertionError("private tmpfs did not fill")
        (mount / "probe").unlink()
        for arm, command in settings["commands"].items():
            for mode, path in [("upload", "/upload"), ("stream", "/large")]:
                row = RUN.request(
                    command,
                    mode,
                    settings["base"] + path,
                    Path(settings["source"]),
                    1048576,
                    mount / "staging",
                    env={**os.environ, **settings["env"]},
                )
                assert row["status"] == (
                    502 if arm == "native" else 201 if mode == "upload" else 200
                ), row
                rows.append({"arm": arm, "case": "ENOSPC-" + mode, **row})
        Path(settings["results"]).write_text(json.dumps(rows, indent=2))
    finally:
        subprocess.run(["umount", str(mount)], check=True)


if __name__ == "__main__":
    main()

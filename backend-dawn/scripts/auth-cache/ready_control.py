"""Inject startup diagnostics and require exact, bounded process barriers."""

import contextlib
import importlib.util
import io
import subprocess
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "auth_acceptance", Path(__file__).with_name("run.py")
)
AUTH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUTH)
WARNING = (
    "[0.001s][warning][perf,memops] Cannot use file /tmp/hsperfdata_runner/5787 "
    "because it is locked by another process (errno = 11)\n"
)


@contextlib.contextmanager
def child(command):
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def python(source):
    return [sys.executable, "-c", source]


def rejected(command, reason, timeout=5):
    with child(command) as process:
        try:
            AUTH.ready([process], "ready", timeout=timeout)
        except AssertionError as error:
            assert reason in str(error), error
        else:
            raise AssertionError("startup failure passed the barrier")


def main():
    for token in ("ready", "holding"):
        # Write both lines together to expose TextIO read-ahead. Keep the child
        # alive at the barrier and check that the following transcript survives.
        source = (
            f"import os, sys; os.write(1, {WARNING + token + chr(10)!r}.encode()); "
            "assert sys.stdin.readline() == 'go\\n'; "
            "print('accepted'); print('application stderr', file=sys.stderr)"
        )
        # The source contains a repr, so replace that literal rather than the
        # decoded warning. One noisy child and one clean child share a barrier.
        quiet = source.replace(repr(WARNING + token + "\n"), repr(token + "\n"))
        with child(python(source)) as noisy, child(python(quiet)) as other:
            diagnostics = io.StringIO()
            with contextlib.redirect_stderr(diagnostics):
                AUTH.ready([noisy, other], token, timeout=5)
            assert diagnostics.getvalue() == WARNING
            for process in (noisy, other):
                out, err = process.communicate("go\n", timeout=5)
                assert (process.returncode, out, err) == (
                    0,
                    "accepted\n",
                    "application stderr\n",
                ), (process.returncode, out, err)
        print(f"PASS leading warning finds exact {token} and preserves both streams")

    rejected(python("print('ready-ish')"), "exited before barrier")
    rejected(
        python(f"import sys; print({WARNING!r}, end=''); sys.exit(7)"),
        "exited before barrier",
    )
    rejected(
        [
            "java",
            "-XX:-UsePerfData",
            "-jar",
            str(Path(__file__).with_name("missing.jar")),
        ],
        "exited before barrier",
    )
    print(
        "PASS wrong token, warning followed by exit, and real JVM startup failure stay red"
    )
    for output in ("", "ready", "ready-ish\n"):
        rejected(
            python(
                f"import os, time; os.write(1, {output!r}.encode()); time.sleep(10)"
            ),
            "barrier timed out",
            timeout=0.2,
        )
    rejected(
        python("import os, time\nwhile True:\n os.write(1, b'x'); time.sleep(0.01)"),
        "barrier timed out",
        timeout=0.2,
    )
    print("PASS silent, partial line, and wrong token children obey the deadline")


if __name__ == "__main__":
    main()

"""Local native deploy downloads and real source mutation controls."""

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from contextlib import contextmanager, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "backend-dawn/deploy"
SHA = "a" * 40
PROXY_KEYS = "http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY".split()


def load_helper(source):
    spec = importlib.util.spec_from_file_location("deploy_native_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def server(callback):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = callback(self.path)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)


class Downloads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-proxy-test-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.app = self.work / "app"
        self.app.mkdir()
        (self.app / ".github-token").write_text("local-test-token")
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.calls = self.work / "curl.jsonl"
        self.proxy_paths = []
        self.github_paths = []
        self.failures = {}
        self.routes = {}
        self.environment = {
            **os.environ,
            **{key: "http://127.0.0.1:1" for key in PROXY_KEYS},
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "DEPLOY_PROXY": "",
            "NO_PROXY": "",
            "no_proxy": "",
        }
        self.environment.pop("DEPLOY_PROXY")
        (self.bin / "id").write_text("#!/bin/sh\necho 0\n")
        (self.bin / "id").chmod(0o755)
        self.helper = load_helper(DEPLOY / "deploy_native.py")
        self.helper.APP = self.app / "native"

    @contextmanager
    def network(self):
        def github(path):
            self.github_paths.append(path)
            return self.routes.get(path, (404, {}, b"unknown fixture"))

        def forward(path):
            # The probe opens a socket without sending HTTP and is not a GET.
            self.proxy_paths.append(path)
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                path, timeout=5
            ) as response:
                return response.status, dict(response.headers), response.read()

        with server(github) as origin, server(forward) as proxy:
            self.origin, self.proxy = origin, proxy
            self.write_curl_wrapper()
            yield

    def write_curl_wrapper(self):
        # URL changes and deterministic transport errors exist only in this fixture.
        source = f"""#!{shutil.which("python3")}
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
with open({str(self.calls)!r}, "a") as log:
    log.write(json.dumps(dict(args=args, env=dict((k, os.environ.get(k)) for k in {PROXY_KEYS!r}))) + "\\n")
for i, arg in enumerate(args):
    if arg.startswith("https://raw.githubusercontent.com/"):
        args[i] = arg.replace("https://raw.githubusercontent.com", {self.origin!r} + "/raw")
    elif arg.startswith("https://api.github.com/"):
        args[i] = arg.replace("https://api.github.com", {self.origin!r} + "/api")
url = next((a for a in args if a.startswith("http")), "")
state = Path({str(self.work / "failures.json")!r})
failures = json.loads(state.read_text())
for key, codes in failures.items():
    if key in url and codes:
        code = codes.pop(0)
        state.write_text(json.dumps(failures))
        sys.stderr.write("fixture curl error " + str(code) + "\\n")
        sys.exit(code)
sys.exit(subprocess.run([{shutil.which("curl")!r}, *args]).returncode)
"""
        (self.work / "failures.json").write_text(json.dumps(self.failures))
        wrapper = self.bin / "curl"
        wrapper.write_text(source)
        wrapper.chmod(0o755)

    def configure_proxy(self):
        (self.app / ".deploy-proxy").write_text(
            " \t" + self.proxy.removeprefix("http://") + "\r\n"
        )

    def records(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def bootstrap(self, *, tamper=False, mutation=None):
        self.routes["/health"] = (200, {}, b"healthy")
        helper = (
            "import subprocess\n"
            f"subprocess.run(['curl', '-qfsS', '{self.origin}/health'], check=True)\n"
            f"open({str(self.work / 'executed')!r}, 'w').write('yes')\n"
        ).encode()
        expected = hashlib.sha1(b"blob " + str(len(helper)).encode() + b"\0" + helper)
        self.routes[
            f"/raw/dawnop/dawnop-site/{SHA}/backend-dawn/deploy/deploy_native.py"
        ] = (200, {}, helper + (b"\n# altered in transit\n" if tamper else b""))
        source = (DEPLOY / "deploy-native.sh").read_text()
        source = source.replace("/opt/dawnop-dawn", str(self.app))
        source = re.sub(
            r'EXPECTED_HELPER_BLOB="[^"]+"',
            f'EXPECTED_HELPER_BLOB="{expected.hexdigest()}"',
            source,
        )
        if mutation:
            original = source
            source = mutation(source)
            self.assertNotEqual(source, original, "mutation anchor missing")
        return subprocess.run(
            ["bash", "-s", "--", SHA],
            input=source,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def assert_health_direct(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.work / "executed").exists())
        self.assertIn("/health", self.github_paths)
        self.assertFalse(
            any(urlsplit(p).path == "/health" for p in self.proxy_paths),
            "local health check reached proxy",
        )

    def test_entrypoint_pins_current_helper_blob(self):
        content = (DEPLOY / "deploy_native.py").read_bytes()
        expected = hashlib.sha1(
            b"blob " + str(len(content)).encode() + b"\0" + content
        ).hexdigest()
        self.assertIn(
            f'EXPECTED_HELPER_BLOB="{expected}"',
            (DEPLOY / "deploy-native.sh").read_text(),
        )

    def test_python_deadline_does_not_expose_token(self):
        destination = self.work / "download"
        destination.write_text("partial")
        with (
            patch.object(
                self.helper.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["curl", "private-token"], 610),
            ) as run,
            redirect_stderr(io.StringIO()) as errors,
        ):
            with self.assertRaises(SystemExit) as error:
                self.helper.download(
                    "https://api.github.com/local",
                    destination,
                    "private-token",
                    "",
                    max_time=600,
                )
        self.assertEqual(error.exception.code, 124)
        self.assertEqual(run.call_args.kwargs["timeout"], 610)
        self.assertNotIn("private-token", errors.getvalue())
        self.assertFalse(destination.exists())

    def test_python_direct_and_final_curl_exit(self):
        with self.network(), patch.dict(os.environ, self.environment, clear=True):
            self.routes["/api/local"] = (200, {}, b"direct")
            self.assertEqual(self.helper.download_proxy(), "")
            (self.app / ".deploy-proxy").write_text("127.0.0.1:1")
            self.assertEqual(self.helper.download_proxy(), "")
            destination = self.work / "download"
            self.helper.download(
                "https://api.github.com/local", destination, "test", ""
            )
            self.assertEqual(destination.read_bytes(), b"direct")
            self.assertEqual(self.proxy_paths, [])
            self.failures = {"/api/local": [28, 35, 7]}
            self.write_curl_wrapper()
            with self.assertRaises(SystemExit) as error:
                self.helper.download(
                    "https://api.github.com/local", destination, "test", ""
                )
            self.assertEqual(error.exception.code, 7)
            self.assertFalse(destination.exists())

    def test_bootstrap_proxy_and_local_health(self):
        with self.network():
            self.configure_proxy()
            result = self.bootstrap()
            self.assert_health_direct(result)
            self.assertEqual(len(self.proxy_paths), 1)
            self.assertTrue(all(not any(c["env"].values()) for c in self.records()))

    def test_bootstrap_missing_proxy(self):
        with self.network():
            result = self.bootstrap()
            self.assert_health_direct(result)
            self.assertEqual(self.proxy_paths, [])
            self.assertIn("no proxy configured", result.stderr)

    def test_bootstrap_unreachable_proxy(self):
        with self.network():
            (self.app / ".deploy-proxy").write_text("127.0.0.1:1")
            result = self.bootstrap()
            self.assert_health_direct(result)
            self.assertIn("proxy unreachable", result.stderr)

    def test_bootstrap_retries_and_exhaustion(self):
        self.failures = {"/raw/": [28, 35]}
        with self.network():
            self.configure_proxy()
            result = self.bootstrap()
            self.assert_health_direct(result)
            self.assertIn("curl exit 28", result.stderr)
            self.assertIn("curl exit 35", result.stderr)
            raw = [
                c for c in self.records() if any("raw.github" in a for a in c["args"])
            ]
            self.assertEqual(len(raw), 3)
        (self.work / "executed").unlink()
        self.failures = {"/raw/": [28, 35, 7]}
        with self.network():
            result = self.bootstrap()
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse((self.work / "executed").exists())
            self.assertIn("curl exit 7", result.stderr)

    def assert_bad_digest(self, result):
        self.assertNotEqual(result.returncode, 0, "altered helper was executed")
        self.assertIn("helper Git blob digest mismatch", result.stderr)
        self.assertFalse((self.work / "executed").exists())

    def test_helper_digest_rejects_before_execution(self):
        with self.network():
            self.configure_proxy()
            self.assert_bad_digest(self.bootstrap(tamper=True))

    def test_negative_controls(self):
        mutations = [
            (
                "global proxy export",
                False,
                lambda s: s.replace(
                    'python3 "$NATIVE_TMP/install.py"',
                    'export http_proxy="$PROXY"\npython3 "$NATIVE_TMP/install.py"',
                ),
                self.assert_health_direct,
                "local health check reached proxy",
            ),
            (
                "helper digest bypass",
                True,
                lambda s: s.replace("if actual != expected:", "if False:"),
                self.assert_bad_digest,
                "altered helper was executed",
            ),
        ]
        for name, tamper, mutation, verdict, message in mutations:
            with self.subTest(name=name), self.network():
                (self.work / "executed").unlink(missing_ok=True)
                self.configure_proxy()
                result = self.bootstrap(tamper=tamper, mutation=mutation)
                with self.assertRaisesRegex(AssertionError, message):
                    verdict(result)
                print(f"RED control: {name}: {message}")
                self.proxy_paths.clear()

    def test_python_api_and_zip_use_proxy(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("fixture.txt", "verified local package")
        data = archive.getvalue()
        run = {
            "head_sha": SHA,
            "head_branch": "main",
            "event": "push",
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
            "id": 123,
        }
        names = [
            "backend",
            "native-acceptance",
            "native-package",
            "mutants-complete",
            "secrets",
            "frontend",
            "python-backend",
        ] + [f"mutant {i}" for i in range(19)]
        jobs = [dict(name=n, status="completed", conclusion="success") for n in names]
        with self.network():
            self.configure_proxy()
            routes = {
                f"actions/workflows/ci.yml/runs?head_sha={SHA}&event=push&per_page=100": {
                    "workflow_runs": [run]
                },
                "actions/runs/123/jobs?per_page=100": {"jobs": jobs},
                "actions/runs/123/artifacts?per_page=100": {
                    "artifacts": [
                        {
                            "name": "backend-native-" + SHA,
                            "expired": False,
                            "archive_download_url": "https://api.github.com/repos/dawnop/dawnop-site/zip",
                            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                        }
                    ]
                },
            }
            for path, value in routes.items():
                self.routes["/api/repos/dawnop/dawnop-site/" + path] = (
                    200,
                    {},
                    json.dumps(value).encode(),
                )
            self.routes["/api/repos/dawnop/dawnop-site/zip"] = (200, {}, data)
            self.failures = {"/runs?": [28, 35], "/zip": [35, 28]}
            self.write_curl_wrapper()
            installed = []
            self.routes["/health"] = (200, {}, b"healthy")

            def install_fixture(release, sha):
                installed.append(((release / "fixture.txt").read_text(), sha))
                subprocess.run(
                    ["curl", "-qfsS", self.origin + "/health"],
                    check=True,
                    capture_output=True,
                    timeout=5,
                )

            with (
                patch.dict(os.environ, self.environment, clear=True),
                patch.object(os, "geteuid", return_value=0),
                patch("sys.argv", ["deploy_native.py", "--sha", SHA]),
                patch.object(
                    self.helper,
                    "install",
                    side_effect=install_fixture,
                ),
                redirect_stderr(io.StringIO()) as errors,
            ):
                self.helper.main()
            self.assertEqual(installed, [("verified local package", SHA)])
            self.assertEqual(len(self.proxy_paths), 4)
            self.assertIn("/health", self.github_paths)
            self.assertFalse(
                any(urlsplit(p).path == "/health" for p in self.proxy_paths)
            )
            self.assertEqual(errors.getvalue().count("curl exit 28"), 2)
            self.assertEqual(errors.getvalue().count("curl exit 35"), 2)
            self.assertTrue(all(not any(c["env"].values()) for c in self.records()))


if __name__ == "__main__":
    unittest.main(verbosity=2)

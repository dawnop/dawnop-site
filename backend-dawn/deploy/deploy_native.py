"""Install a verified native candidate without changing traffic or the JVM."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

APP = Path("/opt/dawnop-dawn/native")
REPO = "dawnop/dawnop-site"


def command(argv):
    result = subprocess.run(
        list(map(str, argv)), check=True, capture_output=True, text=True, timeout=120
    )
    if argv[0] == "systemd-analyze" and re.search(
        r"Unknown (key|lvalue)|not executable|Invalid", result.stderr
    ):
        raise ValueError("invalid unit settings: " + result.stderr)
    return result.stdout.strip()


def successful_run(run, sha):
    if (
        run.get("head_sha") != sha
        or run.get("head_branch") != "main"
        or run.get("event") != "push"
        or run.get("path") != ".github/workflows/ci.yml"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise ValueError("entire main CI has not succeeded for the exact SHA")


def successful_jobs(jobs):
    required = {
        "backend",
        "native-acceptance",
        "native-package",
        "mutants-complete",
        "secrets",
        "frontend",
        "python-backend",
    }
    names = {job["name"] for job in jobs}
    if (
        not required <= names
        or len(jobs) < 26
        or any(j["status"] != "completed" or j["conclusion"] != "success" for j in jobs)
    ):
        raise ValueError("full CI jobs are incomplete or unsuccessful")


def check_zip(archive, expected):
    actual = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    if not expected or actual != expected:
        raise ValueError("GitHub artifact ZIP digest mismatch")


def extract(archive, directory):
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            path = Path(member.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or stat.S_ISLNK(member.external_attr >> 16)
            ):
                raise ValueError("unsafe artifact member")
        z.extractall(directory)


def verifier(release, sha):
    spec = importlib.util.spec_from_file_location(
        "native_package", release / "tools/package.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify(release, sha)


def atomic_link(target, destination):
    pending = destination.with_name(destination.name + ".new")
    pending.unlink(missing_ok=True)
    pending.symlink_to(target)
    pending.replace(destination)


def active_instances():
    units = command(
        [
            "systemctl",
            "list-units",
            "--state=active,activating,deactivating",
            "--plain",
            "--no-legend",
            "dawnop-native@*.service",
        ]
    )
    if units:
        raise ValueError("drain existing native exchanges before changing releases")


def install(release, sha):
    verifier(release, sha)
    if not shutil.which("curl") or not command(["systemctl", "--version"]).startswith(
        "systemd "
    ):
        raise ValueError("missing platform dependencies")
    version = int(command(["systemctl", "--version"]).split()[1])
    if version < 249:
        raise ValueError("systemd 249 or newer required")
    if shutil.disk_usage("/tmp").free < 8 * 1024**3:
        raise ValueError("at least 8 GiB staging space required")
    active_instances()
    APP.mkdir(parents=True, exist_ok=True)
    dest = APP / "releases" / sha
    dest.parent.mkdir(exist_ok=True)
    if not dest.exists():
        pending = dest.with_name(sha + ".new")
        if pending.exists():
            shutil.rmtree(pending)
        shutil.copytree(release, pending)
        for path in pending.rglob("*"):
            os.chown(path, 0, 0)
            path.chmod(
                0o755
                if path.is_dir()
                or path.name.endswith(".sh")
                or path.name == "dawnop-native"
                else 0o644
            )
        os.chown(pending, 0, 0)
        pending.chmod(0o755)
        pending.replace(dest)
    verifier(dest, sha)
    command([dest / "dawnop-native", "--engine-info"])
    current = APP / "current"
    old = current.resolve() if current.exists() else None
    # Stop acceptance before the final no-instance check, closing the race.
    was_active = (
        subprocess.run(
            ["systemctl", "is-active", "--quiet", "dawnop-native.socket"]
        ).returncode
        == 0
    )
    if was_active:
        command(["systemctl", "stop", "dawnop-native.socket"])
    units = [
        p
        for p in (dest / "deploy").iterdir()
        if p.suffix in {".socket", ".service", ".timer", ".slice"}
    ]
    system_files = [Path("/etc/systemd/system") / p.name for p in units]
    system_files.append(Path("/etc/tmpfiles.d/dawnop-native.conf"))
    saved = {p: p.read_bytes() if p.exists() else None for p in system_files}
    try:
        active_instances()
        atomic_link(dest, current)
        command(["systemd-analyze", "verify", *units])
        for unit in units:
            shutil.copy2(unit, Path("/etc/systemd/system") / unit.name)
        shutil.copy2(
            dest / "deploy/dawnop-native.tmpfiles.conf",
            "/etc/tmpfiles.d/dawnop-native.conf",
        )
        command(["systemd-tmpfiles", "--create", "/etc/tmpfiles.d/dawnop-native.conf"])
        command(["systemctl", "daemon-reload"])
        # Candidate installation always leaves cleanup with the existing JVM.
        command(["systemctl", "disable", "--now", "dawnop-native-cleanup.timer"])
        command(["systemctl", "enable", "--now", "dawnop-paste-cleanup.timer"])
        command(["systemctl", "enable", "--now", "dawnop-native.socket"])
    except BaseException:
        subprocess.run(
            ["systemctl", "stop", "dawnop-native.socket"],
            capture_output=True,
            timeout=30,
        )
        if old:
            atomic_link(old, current)
        else:
            current.unlink(missing_ok=True)
        for path, content in saved.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        command(["systemctl", "daemon-reload"])
        if was_active:
            command(["systemctl", "start", "dawnop-native.socket"])
        raise
    if old and old != dest:
        atomic_link(old, APP / ".prev")
    print(f"Installed native candidate {sha}; JVM and nginx traffic unchanged")
    print(f"Undo: python3 {dest}/deploy/deploy_native.py --sha {sha} --undo")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--undo", action="store_true")
    args = parser.parse_args()
    if os.geteuid() or not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        raise ValueError("root and an explicit complete SHA required")
    if args.undo:
        active_instances()
        previous = APP / ".prev"
        if not previous.exists():
            command(
                [
                    "systemctl",
                    "disable",
                    "--now",
                    "dawnop-native.socket",
                    "dawnop-native-cleanup.timer",
                ]
            )
            print("First candidate removed; existing JVM remains the traffic owner")
            return
        release = previous.resolve()
        sha = json.loads((release / "manifest.json").read_text())["commit"]
        install(release, sha)
        return
    token = Path("/opt/dawnop-dawn/.github-token").read_text().strip()

    def api(path):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    runs = api(
        f"actions/workflows/ci.yml/runs?head_sha={args.sha}&event=push&per_page=100"
    )["workflow_runs"]
    candidates = [
        r for r in runs if r["head_sha"] == args.sha and r["head_branch"] == "main"
    ]
    if not candidates:
        raise ValueError("no main CI run for SHA")
    run = candidates[0]
    successful_run(run, args.sha)
    jobs = api(f"actions/runs/{run['id']}/jobs?per_page=100")["jobs"]
    successful_jobs(jobs)
    artifacts = api(f"actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
    artifact = next(
        a
        for a in artifacts
        if a["name"] == "backend-native-" + args.sha and not a["expired"]
    )
    with tempfile.TemporaryDirectory(prefix="native-deploy-") as temp:
        archive = Path(temp) / "artifact.zip"
        proxy_file = Path("/opt/dawnop-dawn/.deploy-proxy")
        download = [
            "curl",
            "-qfsSL",
            "--max-time",
            "600",
            "--speed-limit",
            "1024",
            "--speed-time",
            "60",
        ]
        if proxy_file.exists():
            download += ["--proxy", proxy_file.read_text().strip()]
        command(
            [
                *download,
                "-H",
                f"Authorization: Bearer {token}",
                artifact["archive_download_url"],
                "-o",
                archive,
            ]
        )
        check_zip(archive, artifact.get("digest"))
        release = Path(temp) / "release"
        extract(archive, release)
        install(release, args.sha)


if __name__ == "__main__":
    main()

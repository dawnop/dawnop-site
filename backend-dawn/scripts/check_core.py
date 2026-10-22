"""Check the complete portable source closure with the pinned native compiler."""

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ffi", BACKEND / "scripts/check-ffi-boundaries.py"
)
ffi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ffi)


def closure_errors(core: Path) -> tuple[list[str], int, int]:
    errors = []
    seen = set()
    files = 0

    def visit(package: Path, boundary: Path) -> None:
        nonlocal files
        package = package.resolve()
        if package in seen:
            return
        seen.add(package)
        manifest = (package / "dawn.toml").read_text()
        sections = re.split(r"(?m)^\[([^\n]+)\]\s*$", manifest)
        dependencies = {}
        for index in range(1, len(sections), 2):
            section, body = sections[index : index + 2]
            if section == "java-deps":
                errors.append(f"{package}: forbidden java-deps")
            elif section == "deps":
                for name, value in re.findall(r'(?m)^\s*(\w+)\s*=\s*"([^"\n]+)"', body):
                    target = (package / value).resolve()
                    # Local core packages must remain inside the portable root.
                    if target != boundary and boundary not in target.parents:
                        errors.append(
                            f"{package}: forbidden local dependency {name}: {value}"
                        )
                    else:
                        dependencies[name] = (target, boundary)
            elif section.startswith("deps."):
                name = section.removeprefix("deps.")
                values = dict(re.findall(r'(?m)^\s*(\w+)\s*=\s*"([^"\n]+)"', body))
                digest = values.get("hash", "")
                if not re.fullmatch(r"d1:[0-9a-f]{64}", digest):
                    errors.append(
                        f"{package}: dependency {name} has no pinned tree hash"
                    )
                    continue
                cache = Path(
                    os.environ.get("DAWN_PKG_CACHE") or Path.home() / ".dawn/pkg"
                ) / digest.replace(":", "-")
                target = (cache / values.get("subdir", "")).resolve()
                if cache.resolve() not in (target, *target.parents):
                    errors.append(f"{package}: dependency {name} escapes its archive")
                else:
                    dependencies[name] = (target, cache.resolve())
        for name, (target, allowed) in dependencies.items():
            if name == "web_jvm":
                errors.append(f"{package}: forbidden web_jvm dependency")
            visit(target, allowed)
        sources = sorted((package / "src").rglob("*.dawn"))
        if not sources:
            errors.append(f"{package}: empty source root")
        for source in sources:
            files += 1
            tokens = [
                token
                for token, _ in ffi.DawnCodeTokenizer(source.read_text()).tokenize()
            ]
            for index, token in enumerate(tokens[:-1]):
                if token == "use" and tokens[index + 1] == "java":
                    errors.append(f"{source}: forbidden use java")
            for module in re.findall(
                r"(?m)^\s*use\s+([a-z_][a-z0-9_/]*)", source.read_text()
            ):
                first = module.split("/")[0]
                if first in {"std", "java"}:
                    continue
                if first == "web_jvm":
                    errors.append(f"{source}: forbidden web_jvm import")
                if (
                    first not in dependencies
                    and not (package / "src" / (module + ".dawn")).is_file()
                ):
                    errors.append(
                        f"{source}: import outside portable closure: {module}"
                    )

    visit(core, core.resolve())
    return errors, len(seen), files


def check(core: Path, dawnc: Path) -> bool:
    # Native check also verifies/fetches the URL dependencies before the scan.
    native = subprocess.run([dawnc, "check", core], check=False)
    errors, packages, files = closure_errors(core)
    for error in errors:
        print(f"FAIL  core.closure: {error}", flush=True)
    if native.returncode != 0 or errors:
        return False
    print(
        f"PASS  core.closure: {packages} packages, {files} source files, zero Java dependencies",
        flush=True,
    )
    return True


def negative_controls(dawnc: Path) -> bool:
    for name in (
        "java-import",
        "app-import",
        "upstream-input-stream",
        "upstream-body-publisher",
    ):
        with tempfile.TemporaryDirectory(prefix="dawnop-core-negative-") as temporary:
            backend = Path(temporary) / "backend-dawn"
            shutil.copytree(BACKEND / "core", backend / "core")
            shutil.copytree(BACKEND / "app/jvm", backend / "app/jvm")
            source = backend / "core/src/config.dawn"
            if name.startswith("upstream-"):
                source = backend / "core/src/util/http.dawn"
                java_type = (
                    "java.io.InputStream"
                    if name == "upstream-input-stream"
                    else "java.net.http.HttpRequest.BodyPublisher"
                )
                short = java_type.rsplit(".", 1)[1]
                injection = (
                    f'use java "{java_type}"\npub alias LeakedUpstream = {short}\n'
                )
            else:
                injection = (
                    'use java "java.lang.System"\n'
                    if name == "java-import"
                    else "use backend_dawn/db/jdbc.{DbConn}\n"
                )
            source.write_text(injection + source.read_text())
            if name == "app-import":
                manifest = backend / "core/dawn.toml"
                manifest.write_text(
                    manifest.read_text() + '\n[deps]\nbackend_dawn = "../app/jvm"\n'
                )
            errors, _, _ = closure_errors(backend / "core")
            expected = (
                "forbidden use java"
                if name != "app-import"
                else "forbidden local dependency backend_dawn"
            )
            if not any(expected in error for error in errors):
                print(f"FAIL  core.negative.{name}: missing boundary diagnostic")
                return False
            if check(backend / "core", dawnc):
                print(f"FAIL  core.negative.{name}: accepted forbidden dependency")
                return False
            print(
                f"PASS  core.negative.{name}: rejected real source mutation", flush=True
            )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, default=BACKEND / "core")
    parser.add_argument("--negative-controls", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    dawnc = Path(
        subprocess.check_output(
            [BACKEND.parent / "scripts/fetch-dawn-native.sh"], text=True
        ).strip()
    )
    if args.negative_controls and not negative_controls(dawnc):
        return 1
    success = check(args.core, dawnc)
    print(f"core check wall_s={time.monotonic() - started:.3f}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())

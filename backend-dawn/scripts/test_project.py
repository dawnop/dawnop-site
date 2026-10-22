"""Assemble module-local white-box tests in a disposable project."""

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def combine(source: str, fragment: str) -> str:
    """Merge selective imports because Dawn permits each module path only once."""
    pattern = r"(?m)^use ([a-z_][a-z0-9_/]*)\.\{([^}]+)\}"
    imports = {match[1]: match[2] for match in re.finditer(pattern, source)}

    def merge(match: re.Match[str]) -> str:
        nonlocal source
        module, names = match[1], match[2]
        if module not in imports:
            return match[0]
        old = imports[module]
        source = source.replace(
            f"use {module}.{{{old}}}", f"use {module}.{{{old}, {names}}}"
        )
        imports[module] = old + ", " + names
        return ""

    fragment = re.sub(pattern, merge, fragment)
    return source + "\n" + fragment


def source_path(project: Path, legacy: str) -> Path:
    """Resolve existing mutation anchors by module across the two source roots."""
    app = project / legacy
    core = project / "core" / legacy
    if app.is_file():
        return app
    if core.is_file():
        return core
    raise FileNotFoundError(f"missing module: {legacy}")


def stage_project(destination: Path, backend: Path = BACKEND) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError(f"test destination must be empty: {destination}")
    shutil.copytree(backend / "app/jvm/src", destination / "src")
    shutil.copytree(backend / "core", destination / "core")
    manifest = (backend / "app/jvm/dawn.toml").read_text()
    manifest = manifest.replace('site_core = "../../core"', 'site_core = "core"')
    (destination / "dawn.toml").write_text(manifest)
    shutil.copy2(backend / "app/jvm/dawn.lock", destination / "dawn.lock")
    for kind, target in (("jvm", destination), ("core", destination / "core")):
        fragments = backend / "test" / kind
        for fragment in sorted(fragments.rglob("*.dawn")):
            source = target / "src" / fragment.relative_to(fragments)
            if not source.is_file():
                raise ValueError(f"test fragment has no production module: {fragment}")
            source.write_text(combine(source.read_text(), fragment.read_text()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--dawn", type=Path)
    args = parser.parse_args()
    if args.stage is not None:
        stage_project(args.stage)
        return 0
    dawn = args.dawn
    if dawn is None:
        dawn = Path(
            subprocess.check_output(
                [BACKEND.parent / "scripts/fetch-dawn.sh"], text=True
            ).strip()
        )
    with tempfile.TemporaryDirectory(prefix="dawnop-tests-") as temporary:
        project = Path(temporary) / "backend-dawn"
        stage_project(project)
        return subprocess.call([dawn, "test", project])


if __name__ == "__main__":
    raise SystemExit(main())

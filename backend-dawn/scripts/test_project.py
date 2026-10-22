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
        merged = ", ".join(
            dict.fromkeys(name.strip() for name in (old + ", " + names).split(","))
        )
        source = source.replace(f"use {module}.{{{old}}}", f"use {module}.{{{merged}}}")
        imports[module] = merged
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
    # The disposable JVM mirror reunites modules so core white-box tests can
    # import the adapter without introducing a production package cycle.
    shutil.copytree(backend / "app/jvm/src", destination / "src")
    shutil.copytree(backend / "core/src", destination / "src", dirs_exist_ok=True)
    manifest = (backend / "app/jvm/dawn.toml").read_text()
    manifest = manifest.replace('[deps]\nsite_core = "../../core"', "")
    (destination / "dawn.toml").write_text(manifest)
    shutil.copy2(backend / "app/jvm/dawn.lock", destination / "dawn.lock")
    for source in (destination / "src").rglob("*.dawn"):
        source.write_text(source.read_text().replace("use site_core/", "use "))
    cache_cases = (backend / "test/sql/src/cache_scenarios.dawn").read_text()
    cache_cases = cache_cases.replace("use site_core/", "use ").replace(
        "use scenarios.{check}",
        'fn check(ok: Bool, name: String) -> Unit = if not ok { panic("assertion: ${name}") }',
    )
    (destination / "src/cache_scenarios.dawn").write_text(cache_cases)
    for kind in ("jvm", "core"):
        fragments = backend / "test" / kind
        for fragment in sorted(fragments.rglob("*.dawn")):
            source = destination / "src" / fragment.relative_to(fragments)
            if not source.is_file():
                raise ValueError(f"test fragment has no production module: {fragment}")
            source.write_text(
                combine(
                    source.read_text(),
                    fragment.read_text().replace("use site_core/", "use "),
                )
            )


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

#!/usr/bin/env python3
"""The gate every mutate.py passes its <project> through before writing.

A mutator rewrites source files in place with no undo. The runners always hand
it a fresh copy under mktemp, so for a long time nothing checked that the
argument really was a copy -- and both ways of getting that wrong happened here
on the same day (#269):

  * aimed at the live tree, an agent neutralised two of the four write helpers
    in one mutator and the other two appended a leak function to a tracked
    source file, caught only by a routine `git status`;
  * aimed at the repo root, the mutator died on a missing anchor and the build
    and contract suite behind it ran on unmutated sources and reported PASS.

So the check lives here, once, and it is applied to the *argument* rather than
to each write. That ordering is the point: the number of write helpers in a
mutator is exactly the detail a careful reader missed, and a guard that has to
be repeated at every write has the same failure mode as the monkey-patch did.

Two conditions, both required:

  1. <project> is not the repository the mutator itself lives in, nor anything
     inside it, nor anything containing it. No override.
  2. <project> carries a `.mutant-workdir` sentinel file. The runners create
     it in the copy they just made; a human debugging one mutant by hand
     creates it once in their own scratch dir.

The sentinel *is* the escape hatch, which is why there is no --force flag and
no environment variable. Allowing a directory to be rewritten is a per-
directory act that leaves a visible mark in that directory, not a habit
someone can export into their shell profile and forget.

Rejected alternatives:

  * "refuse unless <project> is outside the repository" -- this repo does real
    work in git worktrees under ~/workspace, and every one of them is outside
    the repository the mutator lives in. The check would wave through exactly
    the trees an agent is most likely to be holding.
  * "refuse anything inside a git working tree" -- blocks `git clone` to a
    scratch dir, which is a legitimate way to debug a mutant, and the only way
    back would be a flag. A guard people route around is worse than none.

Exit status on refusal is 3, distinct from argparse's 2 and from the 1 an
anchor miss produces, so a caller can tell the three apart.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
import sys
import tempfile
from pathlib import Path

SENTINEL = ".mutant-workdir"
REFUSED = 3


def _who() -> str:
    """Name the mutator in the refusal, e.g. `http-timeout-mutants/mutate.py`."""
    argv0 = Path(sys.argv[0]).resolve()
    return f"{argv0.parent.name}/{argv0.name}"


def _refuse(project: Path, reason: str, remedy: str = "") -> None:
    lines = [f"{_who()}: refusing to mutate {project}", f"  {reason}"]
    if remedy:
        lines.append(remedy)
    print("\n".join(lines), file=sys.stderr)
    raise SystemExit(REFUSED)


def protected_root(start: Path) -> Path:
    """The checkout `start` lives in: nearest ancestor holding a `.git`.

    Falls back to the repo layout (scripts/ -> backend-dawn/ -> root) when the
    module has been copied somewhere without one, so a stray copy still refuses
    to mutate the tree around it.
    """
    for candidate in [start] + list(start.parents):
        if (candidate / ".git").exists():
            return candidate
    return Path(__file__).resolve().parents[2]


def require_workdir(project: Path, root: Path | None = None) -> Path:
    """Return the resolved project dir, or exit 3 explaining why not.

    The result is absolute, so every anchor path a mutator builds from it names
    the tree it was aimed at. A relative `src/api/api_pages.dawn` in a
    FileNotFoundError is what made the second failure above easy to misread.
    """
    resolved = project.resolve()
    if root is None:
        root = protected_root(Path(__file__).resolve())
    protected = root.resolve()

    if resolved == protected:
        _refuse(
            resolved,
            f"that is the repository the mutator lives in ({protected}).",
            "  Mutants are only ever applied to a throwaway copy of it.",
        )
    if protected in resolved.parents:
        _refuse(
            resolved,
            f"that is inside the repository the mutator lives in ({protected}).",
            "  Mutants are only ever applied to a throwaway copy of it.",
        )
    if resolved in protected.parents:
        _refuse(
            resolved,
            f"that contains the repository the mutator lives in ({protected}).",
        )

    if not (resolved / SENTINEL).is_file():
        _refuse(
            resolved,
            f"no {SENTINEL} sentinel there, so this is not a disposable copy.",
            "  The harness runners drop one into the temp copy they make. If this\n"
            "  really is a scratch tree you are willing to have rewritten in place,\n"
            f"  mark it once:\n      touch {resolved}/{SENTINEL}\n"
            "  Never create it in a checkout whose contents you want to keep.",
        )
    return resolved


# ---------------------------------------------------------------- self-test


def _harness_mutators() -> list[Path]:
    scripts = Path(__file__).resolve().parent
    found = sorted(p for p in scripts.glob("*/mutate.py"))
    if not found:
        raise SystemExit("self-test: found no */mutate.py next to this module")
    return found


def _first_mutant_name(matrix: Path) -> str:
    for line in matrix.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("version="):
            continue
        return line.split("|", 1)[0]
    raise SystemExit(f"self-test: no mutant names in {matrix}")


def _run(mutator: Path, mutant: str, project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(mutator), mutant, str(project)],
        capture_output=True,
        text=True,
        check=False,
    )


def _self_test() -> int:
    """Prove the guard can go red, and that every harness is wired to it.

    Each mutator is aimed at an empty directory -- unsentinelled first, then
    sentinelled. Empty because that keeps the negative safe: if a mutator were
    *not* wired to the guard, the write it attempts lands on a path that does
    not exist and fails, instead of on somebody's source. The paired run is
    what pins the reason for the refusal: with the sentinel present the same
    command must get past the guard and fail on its missing anchor instead.
    """
    failures = 0
    with tempfile.TemporaryDirectory(prefix="mutant-workdir-selftest.") as tmp:
        root = Path(tmp)

        # 1. the repository check, on a fabricated checkout so the real one is
        #    never the subject. Sentinel present: it must not buy anything.
        fake_repo = root / "fake-repo"
        (fake_repo / ".git").mkdir(parents=True)
        inside = fake_repo / "backend-dawn"
        inside.mkdir()
        (inside / SENTINEL).touch()
        for target, label in (
            (fake_repo, "the checkout itself"),
            (inside, "inside it"),
        ):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    require_workdir(target, root=fake_repo)
            except SystemExit as exit_code:
                if exit_code.code != REFUSED:
                    print(f"FAIL  {label}: exit {exit_code.code}, want {REFUSED}")
                    failures += 1
            else:
                print(f"FAIL  {label}: allowed even with a sentinel")
                failures += 1
        print("PASS  the mutator's own checkout is refused, sentinel or not")

        # 2. every harness's mutate.py actually calls the guard
        for mutator in _harness_mutators():
            name = f"{mutator.parent.name}/mutate.py"
            mutant = _first_mutant_name(mutator.parent / "matrix.txt")
            probe = root / mutator.parent.name
            probe.mkdir()

            bare = _run(mutator, mutant, probe)
            if bare.returncode != REFUSED or SENTINEL not in bare.stderr:
                print(f"FAIL  {name} did not refuse an unsentinelled dir")
                print(f"      exit {bare.returncode}: {bare.stderr.strip()[:400]}")
                failures += 1
                continue
            if list(probe.iterdir()):
                print(f"FAIL  {name} wrote into a directory it claimed to refuse")
                failures += 1
                continue

            (probe / SENTINEL).touch()
            marked = _run(mutator, mutant, probe)
            if marked.returncode == REFUSED:
                print(f"FAIL  {name} still refuses a sentinelled dir")
                print(f"      {marked.stderr.strip()[:400]}")
                failures += 1
                continue
            print(f"PASS  {name} refuses without {SENTINEL}, proceeds with it")

    if failures:
        print(f"FAIL  {failures} guard check(s) failed")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if not args.self_test:
        parser.error("nothing to do without --self-test")
    return _self_test()


if __name__ == "__main__":
    raise SystemExit(main())

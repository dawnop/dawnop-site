#!/usr/bin/env python3
"""Hold the sharded mutation runs to the full matrix.

Sharding the mutation harnesses across CI jobs bought back an hour and a half
of wall clock, and it introduced exactly one new way to be wrong: a mutant that
no shard runs. Nothing else notices. Each shard prints its own PASS lines and
exits 0 whether it covered its slice or skipped it, the job goes green, and the
matrix silently shrinks. That is the "no silent caps" failure this repo has hit
before, and it is worse here because the thing being skipped is the check.

So the shards do not get to vouch for themselves. Each writes down the mutants
it actually executed, and this reassembles them and compares the union with
the harness's own matrix.txt — the same file its runner iterates. A missing
shard, a wrong `--shard I/N`, a matrix entry nobody picked up, or a shard that
ran a mutant twice all show up here as a named difference.

The set of harnesses is asked of the tree too, not inferred from which coverage
files turned up. Inferring it was the same bug one level up: a harness dropped
from ci.yml's `mutants` matrix contributes no coverage file, so it was absent
from the reassembly and nothing missed it — the run went green with six
harnesses and reported "every harness covered". Every scripts/<harness>/
directory holding a matrix.txt is expected to report, by name.

Usage:
    mutants_coverage.py --coverage-dir DIR [--root BACKEND_DAWN]
    mutants_coverage.py --self-test
"""

import argparse
import pathlib
import tempfile
from collections import defaultdict


def parse_coverage_file(path):
    """One shard's record: (harness, index, total, [mutant, ...])."""
    harness = None
    shard = None
    ran = []
    for lineno, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line:
            continue
        key, _, value = line.partition("=")
        if key == "harness":
            harness = value
        elif key == "shard":
            index, _, total = value.partition("/")
            shard = (int(index), int(total))
        elif key == "ran":
            ran.append(value)
        else:
            raise SystemExit(f"{path}:{lineno}: unknown key {key!r}")
    if harness is None or shard is None:
        raise SystemExit(f"{path}: missing harness= or shard= header")
    return harness, shard[0], shard[1], ran


def expected_mutants(root, harness):
    """The harness's own list, asked of the harness rather than of the shards.

    Read from matrix.txt rather than `mutate.py --list`, for two reasons: only
    two of the six mutators grew a --list, and more importantly matrix.txt is
    what the runners actually iterate. An oracle should be the thing the code
    under test reads, not a second list that happens to agree today.
    """
    matrix = root / "scripts" / harness / "matrix.txt"
    if not matrix.is_file():
        raise SystemExit(f"no matrix.txt for harness {harness} at {matrix}")
    names = []
    for raw in matrix.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("version="):
            continue
        names.append(line.split("|", 1)[0])
    if not names:
        raise SystemExit(f"{matrix} lists no mutants")
    return names


def expected_harnesses(root):
    """Which harnesses are supposed to report, asked of the tree.

    Membership is "the directory has a matrix.txt", and nothing else. A
    matrix.txt *is* a list of mutants somebody promised to run; if one exists
    and no shard reports against it, that is the failure this file is for.
    scripts/golden/ and scripts/__pycache__/ have none and so are not harnesses.

    Deliberately not derived from ci.yml's `mutants` matrix, though that is the
    list that actually dispatches the jobs: checking ci.yml against ci.yml
    cannot notice a harness missing from ci.yml. The tree is the independent
    witness, so a row deleted there surfaces here as a named absence.

    run.sh is *not* part of the test for membership, on purpose. Requiring both
    files would mean deleting run.sh quietly shrinks the expected set — the
    exact move being defended against. A harness that has a matrix.txt and no
    runner stays expected and is reported as broken (see run()).
    """
    scripts = root / "scripts"
    names = sorted(matrix.parent.name for matrix in scripts.glob("*/matrix.txt"))
    if not names:
        raise SystemExit(
            f"no scripts/*/matrix.txt under {scripts}; refusing to conclude "
            "anything about coverage from a tree with no harnesses in it"
        )
    return names


def check_harness_set(expected, reported):
    """Return the problems with *which* harnesses reported, before what they ran.

    Kept separate from check() because check() only ever sees a harness that
    turned up. This is the half that can see one that did not.
    """
    problems = []

    missing = sorted(set(expected) - set(reported))
    if missing:
        problems.append(
            f"no shard reported any coverage for harness(es): {missing} — "
            "each has a matrix.txt, so each is expected in ci.yml's mutants matrix"
        )
    unknown = sorted(set(reported) - set(expected))
    if unknown:
        problems.append(
            f"coverage from harness(es) with no matrix.txt in the tree: {unknown}"
        )

    return problems


def check(harness, shards, expected):
    """Return the problems with one harness's reassembled coverage."""
    problems = []

    totals = {total for _index, total, _ran in shards}
    if len(totals) != 1:
        problems.append(f"shards disagree on the shard count: {sorted(totals)}")
        return problems
    total = totals.pop()

    seen_indices = sorted(index for index, _total, _ran in shards)
    if seen_indices != list(range(total)):
        missing = sorted(set(range(total)) - set(seen_indices))
        extra = sorted(set(seen_indices) - set(range(total)))
        if missing:
            problems.append(f"no coverage file from shard(s) {missing} of {total}")
        if extra:
            problems.append(f"coverage from out-of-range shard(s) {extra}")
        duplicated = sorted({i for i in seen_indices if seen_indices.count(i) > 1})
        if duplicated:
            problems.append(f"more than one coverage file for shard(s) {duplicated}")

    ran = [mutant for _index, _total, mutants in shards for mutant in mutants]
    dupes = sorted({m for m in ran if ran.count(m) > 1})
    if dupes:
        problems.append(f"run by more than one shard: {dupes}")

    missing = sorted(set(expected) - set(ran))
    if missing:
        problems.append(f"no shard ran: {missing}")
    unknown = sorted(set(ran) - set(expected))
    if unknown:
        problems.append(f"ran a mutant the harness does not list: {unknown}")

    return problems


def run(coverage_dir, root):
    harnesses = expected_harnesses(root)
    print(
        f"      expecting {len(harnesses)} harness(es) from {root / 'scripts'}: {harnesses}"
    )

    files = sorted(coverage_dir.rglob("*.coverage"))
    if not files:
        raise SystemExit(
            f"no *.coverage files under {coverage_dir}; the shards recorded nothing, "
            "which is indistinguishable from having run nothing"
        )

    by_harness = defaultdict(list)
    for path in files:
        harness, index, total, ran = parse_coverage_file(path)
        by_harness[harness].append((index, total, ran))

    failed = False
    for problem in check_harness_set(harnesses, by_harness):
        failed = True
        print(f"FAIL  {problem}")

    for harness in harnesses:
        if harness not in by_harness:
            continue  # already named by check_harness_set
        problems = []
        if not (root / "scripts" / harness / "run.sh").is_file():
            problems.append("has a matrix.txt but no run.sh, so no CI job can run it")
        expected = expected_mutants(root, harness)
        problems += check(harness, by_harness[harness], expected)
        if problems:
            failed = True
            for problem in problems:
                print(f"FAIL  {harness}: {problem}")
        else:
            shards = len(by_harness[harness])
            print(
                f"PASS  {harness}: {len(expected)} mutant(s) covered "
                f"across {shards} shard(s)"
            )
    if failed:
        raise SystemExit(1)
    print(f"PASS  every mutation harness fully covered ({len(harnesses)} harness(es))")


def self_test():
    """The checker has to fail on the shapes it exists to catch."""
    full = ["a", "b", "c", "d"]

    cases = [
        (
            "complete two-shard split",
            [(0, 2, ["a", "c"]), (1, 2, ["b", "d"])],
            full,
            0,
        ),
        (
            "a shard's job never ran",
            [(0, 2, ["a", "c"])],
            full,
            2,  # missing shard 1, and its mutants uncovered
        ),
        (
            "one mutant dropped from an otherwise complete split",
            [(0, 2, ["a", "c"]), (1, 2, ["b"])],
            full,
            1,
        ),
        (
            "shards disagree about how many there are",
            [(0, 2, ["a", "c"]), (1, 3, ["b", "d"])],
            full,
            1,
        ),
        (
            "the same mutant run twice",
            [(0, 2, ["a", "b", "c"]), (1, 2, ["b", "d"])],
            full,
            1,
        ),
        (
            "a mutant the harness does not list",
            [(0, 1, ["a", "b", "c", "d", "e"])],
            full,
            1,
        ),
        (
            "matrix grew but the shards are from before it did",
            [(0, 2, ["a", "c"]), (1, 2, ["b", "d"])],
            full + ["e"],
            1,
        ),
    ]

    # The other half: which harnesses reported at all. check() cannot see a
    # harness that contributed nothing, because it is only ever called with one
    # that did — so before this block the "six harnesses instead of seven" case
    # had no test, and the job that runs this self-test claimed otherwise.
    seven = [f"h{i}" for i in range(7)]

    set_cases = [
        ("every harness reported", seven, list(seven), 0),
        ("a harness dropped from ci.yml's matrix", seven, seven[:-1], 1),
        ("only one harness's jobs ran at all", seven, seven[:1], 1),
        ("coverage from a harness with no matrix.txt", seven, seven + ["ghost"], 1),
        (
            "a harness swapped for one the tree does not know",
            seven,
            seven[:-1] + ["x"],
            2,
        ),
    ]

    bad = 0
    for name, shards, expected, want in cases:
        problems = check("demo", shards, expected)
        got = len(problems)
        if got != want:
            bad += 1
            print(f"FAIL  self-test {name!r}: expected {want} problem(s), got {got}")
            for problem in problems:
                print(f"          {problem}")
        else:
            print(f"PASS  self-test: {name}")

    for name, expected, reported, want in set_cases:
        problems = check_harness_set(expected, reported)
        got = len(problems)
        if got != want:
            bad += 1
            print(f"FAIL  self-test {name!r}: expected {want} problem(s), got {got}")
            for problem in problems:
                print(f"          {problem}")
        else:
            print(f"PASS  self-test: {name}")

    # And the derivation feeding it, since "expected" is only trustworthy if the
    # glob finds harnesses and skips the directories that merely sit next to
    # them (golden/, __pycache__/). Built in a temp tree: a self-test that reads
    # the real checkout would pass for as long as the checkout happens to agree.
    with tempfile.TemporaryDirectory() as tmp:
        scripts = pathlib.Path(tmp) / "scripts"
        for name in ("alpha-mutants", "beta-mutants"):
            (scripts / name).mkdir(parents=True)
            (scripts / name / "matrix.txt").write_text("m1\n", encoding="utf-8")
        (scripts / "alpha-mutants" / "run.sh").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )
        (scripts / "golden").mkdir()
        (scripts / "golden" / "read.json").write_text("{}\n", encoding="utf-8")
        (scripts / "__pycache__").mkdir()

        got = expected_harnesses(pathlib.Path(tmp))
        want_names = ["alpha-mutants", "beta-mutants"]
        if got != want_names:
            bad += 1
            print(f"FAIL  self-test 'harnesses derived from the tree': {got}")
        else:
            print("PASS  self-test: harnesses derived from the tree, not from reports")

    total = len(cases) + len(set_cases) + 1
    if bad:
        raise SystemExit(1)
    print(f"PASS  coverage checker self-test ({total} cases)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coverage-dir", type=pathlib.Path)
    ap.add_argument(
        "--root",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parent.parent,
        help="backend-dawn/ (holds scripts/<harness>/mutate.py)",
    )
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return
    if args.coverage_dir is None:
        ap.error("--coverage-dir is required unless --self-test")
    run(args.coverage_dir, args.root)


if __name__ == "__main__":
    main()

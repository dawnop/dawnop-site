#!/usr/bin/env python3
"""Apply one compiling src/svc/monitor.dawn mutant in place.

The monitor's CPU reading is the delta between this call's /proc/stat sample and
the snapshot the previous call left in a cell, with a short sampling window as
the fallback for the calls that have nothing to difference against. Every part
of that reads as already true in the source -- cpu_delta_pct is called with the
previous snapshot, the snapshot is fetched through an age-bounded cell, the
fallback is right there -- and the answer is a plausible percentage either way.
Only running says which path produced it.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mutant_workdir import require_workdir  # noqa: E402

MUTANTS = (
    "accept-backwards-counters",
    "always-sample-a-window",
    "never-refresh-snapshot",
    "unbounded-snapshot-age",
)


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mutant", choices=MUTANTS)
    parser.add_argument("project", type=Path)
    args = parser.parse_args()

    project = require_workdir(args.project)
    monitor = project / "src/svc/monitor.dawn"

    if args.mutant == "always-sample-a-window":
        # The state this task found the code in, reached without deleting
        # anything: the current sample stops being one of the two the delta is
        # taken over, so the pair is always a zero-width window, always refused,
        # and every call falls through to sampling one of its own. The snapshot
        # still moves (the fallback writes it), the reading is still a sane
        # percentage, and the source still hands cpu_delta_pct the previous
        # snapshot. Only the clock knows.
        replace_once(
            monitor,
            "    Some(p) -> cpu_delta_pct(p, cur)\n",
            "    Some(p) -> cpu_delta_pct(p, p)\n",
        )
    elif args.mutant == "never-refresh-snapshot":
        # The delta is taken but the cell is left where it was found. Every
        # reading is then measured from the first call rather than the last, so
        # the window widens without bound until the snapshot ages out -- and on
        # a dashboard that is refreshed every few minutes it never does. The
        # numbers stay plausible the whole time, which is why this is asserted
        # by reading the cell back rather than by looking at an answer.
        replace_once(
            monitor,
            "    Some(x) -> {\n      cpu_put(cell, cur)\n      x\n    }\n",
            "    Some(x) -> x\n",
        )
    elif args.mutant == "unbounded-snapshot-age":
        # A bound that is present but useless -- the same shape as
        # http-timeout-mutants' inflate-deadline. cache_get is still called with
        # an age, so anything that greps the source for one is satisfied; the
        # caller's bound is simply not the one used, and a snapshot from a day
        # ago is served as a current reading. What that produces is the average
        # CPU load since the dashboard was last opened, presented as now.
        replace_once(
            monitor,
            "fn cpu_prev(cell: TtlCell, max_age_s: Int) -> Option[(Int, Int)] !io =\n"
            "  match cache_get(cell, max_age_s) {\n",
            "fn cpu_prev(cell: TtlCell, _max_age_s: Int) -> Option[(Int, Int)] !io =\n"
            "  match cache_get(cell, 86400) {\n",
        )
    else:
        # The monotonicity guard goes, leaving only the divide-by-zero one. The
        # counters it defends against do not move backwards, so nothing on a
        # real host changes -- which is exactly why the rule has to be checked
        # by a test that can hand it a pair no host would produce, rather than
        # trusted because production has never disagreed with it.
        replace_once(
            monitor,
            "  if dtotal <= 0 || dbusy < 0 || dbusy > dtotal {\n",
            "  if dtotal <= 0 {\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

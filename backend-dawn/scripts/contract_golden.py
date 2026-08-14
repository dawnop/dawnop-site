#!/usr/bin/env python3
"""Golden-file machinery shared by contract_{read,edge,webdav}.py.

Why these scripts changed shape
-------------------------------
They were written during M6 as *differential* harnesses: fire the same request
at Dawn (:8001) and FastAPI (:8000) and diff the answers. That needed two
backends up at once, so CI never ran them — and `backend/` is frozen, so the
reference was going to rot. The case lists, though, are the only byte-level
record of this API's wire shape; M6 cut production over on their evidence.

So the reference moved from "the other process" to "a file in git". One backend
plus a pinned fixture reproduces the same request set, and the expected answers
are reviewable in a diff. The scripts still work against FastAPI (`--base
http://127.0.0.1:8000`): the golden is the contract, and either backend may be
held to it.

What this module enforces
-------------------------
Three guards, all of them there because a golden run is easy to make green and
worthless:

  * **env fingerprint** — the recorded environment (fixture data hash, TZ,
    whether object-storage credentials are configured, which search path the
    backend will take) must match the run. Comparing bytes recorded under a
    different fixture or timezone is not a test, it is noise.
  * **case set** — a case in the golden that did not run, or a case that ran
    without a golden entry, is a failure. Cases cannot quietly disappear.
  * **skip set** — cases skipped for a missing capability are named in the
    golden. Skipping a *new* case fails the run. A skip nobody reviewed is
    indistinguishable from a case that never worked.

`--record` rewrites the golden; the diff in review is the diff in behaviour.
"""

import json
import os
import pathlib

import contract_fixture

GOLDEN_DIR = pathlib.Path(__file__).resolve().parent / "golden"
MACHINE_REPORT_ENV = "CONTRACT_GOLDEN_REPORT"

# The search path the Dawn backend takes in a contract run, and why it is not
# the production one. Dawn calls simple_query() (the wangfenjin/simple FTS
# tokenizer) and falls back to LIKE when that function is absent. The .so is
# deliberately not in git and the dev laptop cannot reach GitHub releases, so a
# contract run would take a different path on a runner that fetched it than on
# a laptop that could not — goldens recorded on one would be red on the other.
# The runner therefore pins DAWN_SIMPLE_EXT="" and records the LIKE path.
#
# Honest accounting of what that costs: the FTS id-collection and bm25 ranking
# are NOT covered by these goldens. Everything downstream of id collection is —
# escaping, <mark> wrapping, the excerpt window, word_count, tags, the paging
# envelope — because Dawn computes those itself whichever path found the ids.
SEARCH_BACKEND = "like"

# A request that never got an HTTP answer at all — connection refused, DNS
# failure, timeout, a truncated read — still has to land in the (status, body)
# shape the golden compares, so it gets encoded rather than raised.
#
# It is here because the three scripts each invented their own spelling for the
# same event: `repr(e)`, `f"__ERR__ {type(e).__name__}: {e}"`, and
# `repr(e).encode()`. Same failure, three renderings in the golden, and a reader
# had to know which script wrote a line to know whether it was one. Worse for
# `--record`: whichever spelling was live got baked in, so a golden recorded
# while the backend was still coming up recorded a *different string* per script
# for the identical cause.
#
# The status is -1, not a 5xx: a 5xx is an answer from the server, and "never
# reached it" must not be able to masquerade as one.
TRANSPORT_STATUS = -1


def transport_error(e):
    """The recorded body for a request that got no HTTP answer."""
    return f"__ERR__ {type(e).__name__}: {e}"


# --- two observations of one row, recorded as a relation ---------------------
#
# The write paths stamp `updated_at` with `datetime('now')`. A golden cannot own
# that value, which is why every write case used to drop it — and why the whole
# updated_at family (#262 view counter, #264 reorder / page delete, #266 no-op
# re-save, #267 rename & move) was invisible at the contract layer: the goldens
# held no successful PUT at all, and would have masked the stamp if they had.
#
# What a golden CAN own is the relation between two observations of the same
# row: read it, write, read it again, record only "same as before" / "newer than
# before". No wall clock enters the file.
#
# One trap that shape has to answer: the stamps are second-resolution, so two
# equal readings do not by themselves mean nothing was written — a rewrite
# inside the same second reads identical. So each fact also records where the
# BEFORE reading stood: `seed` means the row still carried the value
# contract_fixture planted (2026-01-xx), which any `datetime('now')` rewrite
# would have replaced with something months later. "unchanged, starting from
# seed" is therefore a real claim; "unchanged" alone would not be.


def stamp_change(before, after):
    """How the second reading relates to the first. Never a timestamp."""
    if before is None or after is None:
        return "absent"
    if after == before:
        return "unchanged"
    # the wire format is ISO-8601 seconds, so lexical order is chronological
    return "advanced" if after > before else "receded"


def stamp_facts(before_row, after_row, seeded):
    """Per-field {start, change} for the stamps named in `seeded`.

    `seeded` maps a field name to the value contract_fixture planted in that
    row, rendered the way the API emits it. `start` says whether the pre-write
    reading was still that planted value.
    """
    facts = {}
    for field, seed in sorted(seeded.items()):
        before = (before_row or {}).get(field)
        after = (after_row or {}).get(field)
        if before is None:
            start = "absent"
        elif before == seed:
            start = "seed"
        else:
            start = "not-seed"
        facts[field] = {"start": start, "change": stamp_change(before, after)}
    return facts


RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"


def env_fingerprint(extra=None) -> dict:
    """The environment facts a golden is only valid under."""
    env = {
        "fixture": contract_fixture.fingerprint(),
        "tz": os.environ.get("TZ", "<unset>"),
        "search_backend": SEARCH_BACKEND,
        "qiniu_configured": bool(os.environ.get("CONTRACT_QINIU_CONFIGURED")),
    }
    env.update(extra or {})
    return env


def json_paths_diff(want, got, prefix="", out=None, limit=12):
    """Structural diff: the JSON paths where want and got disagree.

    A body dump truncated at 400 characters was the old failure output; on a
    long list it showed the same prefix twice and left the reader to spot the
    difference. This points at the field.
    """
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if type(want) is not type(got):
        out.append(
            f"{prefix or '$'}: type {type(want).__name__} -> {type(got).__name__}"
        )
        return out
    if isinstance(want, dict):
        for k in sorted(set(want) | set(got)):
            if k not in want:
                out.append(
                    f"{prefix}.{k}: (absent) -> {json.dumps(got[k], ensure_ascii=False)[:120]}"
                )
            elif k not in got:
                out.append(
                    f"{prefix}.{k}: {json.dumps(want[k], ensure_ascii=False)[:120]} -> (absent)"
                )
            else:
                json_paths_diff(want[k], got[k], f"{prefix}.{k}", out, limit)
        return out
    if isinstance(want, list):
        if len(want) != len(got):
            out.append(f"{prefix or '$'}: length {len(want)} -> {len(got)}")
        for i in range(min(len(want), len(got))):
            json_paths_diff(want[i], got[i], f"{prefix}[{i}]", out, limit)
        return out
    if want != got:
        w = json.dumps(want, ensure_ascii=False)
        g = json.dumps(got, ensure_ascii=False)
        out.append(f"{prefix or '$'}: {w[:160]} -> {g[:160]}")
    return out


class Golden:
    """One script's golden file: load, compare or record, then report."""

    def __init__(self, name, record=False, env_extra=None):
        self.name = name
        self.path = GOLDEN_DIR / f"{name}.json"
        self.record = record
        self.env = env_fingerprint(env_extra)
        self.cases = {}  # name -> observed value (recording) / compared (verify)
        self.skipped = {}  # name -> reason
        self.order = []
        self.problems = []
        self.tally = {"match": 0, "diff": 0, "new": 0, "skip": 0}
        self.expected = {}
        self.expected_skips = {}
        self.fatal = None
        if not record:
            self._load()

    def _load(self):
        if not self.path.exists():
            self.fatal = f"no golden at {self.path} — record one with --record"
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        recorded_env = data.get("_env", {})
        if recorded_env != self.env:
            self.fatal = (
                "environment does not match the golden.\n"
                f"    recorded: {json.dumps(recorded_env, ensure_ascii=False, sort_keys=True)}\n"
                f"    current:  {json.dumps(self.env, ensure_ascii=False, sort_keys=True)}\n"
                "  Comparing across environments proves nothing; re-record or fix the run."
            )
            return
        self.expected = data.get("cases", {})
        self.expected_skips = data.get("skipped", {})

    # -- case entry points ----------------------------------------------------

    def case(self, name, value):
        """Record or verify one case. `value` must be JSON-serializable."""
        if name in self.cases or name in self.skipped:
            raise SystemExit(f"duplicate contract case name: {name}")
        self.order.append(name)
        if self.record:
            self.cases[name] = value
            self.tally["match"] += 1
            print(f"  [{GREEN}REC {RESET}] {name}")
            return True
        if name not in self.expected:
            self.cases[name] = value
            self.tally["new"] += 1
            self.problems.append(
                (name, ["case is not in the golden — run with --record"])
            )
            print(f"  [{RED}NEW {RESET}] {name}")
            return False
        want = self.expected[name]
        if want == value:
            self.cases[name] = value
            self.tally["match"] += 1
            print(f"  [{GREEN}OK  {RESET}] {name}")
            return True
        self.cases[name] = value
        self.tally["diff"] += 1
        self.problems.append((name, json_paths_diff(want, value)))
        print(f"  [{RED}DIFF{RESET}] {name}")
        return False

    def skip(self, name, reason):
        """A case deliberately not exercised here. Pinned in the golden."""
        if name in self.cases or name in self.skipped:
            raise SystemExit(f"duplicate contract case name: {name}")
        self.order.append(name)
        self.skipped[name] = reason
        self.tally["skip"] += 1
        print(f"  [{YELLOW}SKIP{RESET}] {name}  ({reason})")

    # -- finish ---------------------------------------------------------------

    def _write_machine_report(
        self,
        *,
        complete,
        missing,
        skip_added,
        skip_gone,
        bad,
    ):
        """Write one atomic, stable report when a harness asks for it.

        Human output remains intentionally descriptive. Mutation runners need
        a closed machine shape so a real owner plus NEW, missing, skip drift,
        or a truncated run cannot be accepted by grepping one DIFF line.
        """
        raw_path = os.environ.get(MACHINE_REPORT_ENV)
        if not raw_path:
            return
        path = pathlib.Path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        problem_names = [name for name, _ in self.problems]
        payload = {
            "schema": "dawnop.contract-golden.v1",
            "name": self.name,
            "record": self.record,
            "complete": complete,
            "fatal": self.fatal,
            "match": self.tally["match"],
            "diff": [name for name in problem_names if name in self.expected],
            "new": [name for name in problem_names if name not in self.expected],
            "missing": missing,
            "skipped": self.skipped,
            "skip_added": skip_added,
            "skip_gone": skip_gone,
            "bad": bad,
        }
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def finish(self) -> int:
        if self.fatal:
            self._write_machine_report(
                complete=False,
                missing=[],
                skip_added=[],
                skip_gone=[],
                bad=True,
            )
            print(f"\n{RED}FATAL{RESET}: {self.fatal}")
            return 2

        missing = [n for n in self.expected if n not in self.cases]
        # recording defines the golden, so there is nothing to have drifted from
        skip_added = (
            [] if self.record else sorted(set(self.skipped) - set(self.expected_skips))
        )
        skip_gone = (
            [] if self.record else sorted(set(self.expected_skips) - set(self.skipped))
        )

        if self.record:
            GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "_note": (
                    f"Recorded by contract_{self.name}.py --record via contract_run.py. "
                    "Do not hand-edit: re-record and review the diff."
                ),
                "_env": self.env,
                "skipped": self.skipped,
                "cases": {n: self.cases[n] for n in self.order if n in self.cases},
            }
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False)
                + "\n",
                encoding="utf-8",
            )
            print(f"\n{CYAN}recorded{RESET} {len(self.cases)} cases -> {self.path}")

        print(f"\n=== {self.name}: tally ===")
        print(f"  compared  {self.tally['match']}")
        print(f"  diff      {self.tally['diff']}")
        print(f"  new       {self.tally['new']}")
        # Skips are printed by name, always. A skip count buried in a green run
        # is how "CI ran it" turns into "CI compared nothing" without anyone
        # noticing; the reasons are the part worth reading.
        print(f"  skipped   {self.tally['skip']}")
        for n, why in self.skipped.items():
            print(f"            - {n}: {why}")

        if self.problems:
            print(f"\n=== {self.name}: differences ===")
            for n, lines in self.problems:
                print(f"\n[{n}]")
                for ln in lines:
                    print(f"  {ln}")
                if not lines:
                    print(
                        "  (values differ but the structural diff found nothing — check types)"
                    )
        if missing:
            print(f"\n{RED}missing{RESET}: in the golden but not run: {missing}")
        if skip_added:
            print(f"\n{RED}new skips{RESET}: not in the golden: {skip_added}")
            print("  A case may not start skipping itself without review.")
        if skip_gone:
            print(
                f"\n{RED}skips resolved{RESET}: golden still lists {skip_gone} — re-record."
            )

        bad = bool(self.problems or missing or skip_added or skip_gone)
        if self.record:
            bad = bool(missing)  # recording defines the golden; nothing to violate
        self._write_machine_report(
            complete=True,
            missing=missing,
            skip_added=skip_added,
            skip_gone=skip_gone,
            bad=bad,
        )
        print(f"\n{self.name}: {(RED + 'FAIL' if bad else GREEN + 'PASS')}{RESET}")
        return 1 if bad else 0

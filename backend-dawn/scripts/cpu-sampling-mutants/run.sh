#!/usr/bin/env bash
# Negative control for the monitor's CPU reading in src/svc/monitor.dawn.
#
# The reading is a delta between this call's /proc/stat sample and the snapshot
# the previous call left behind, with a short sampling window as the fallback.
# Every way it can break produces the same thing on the wire: a Float between 0
# and 100. The contract goldens assert the monitor's shape, not its values, so
# they are blind to all of it by construction. The assertions that are not blind
# work off the clock (a call that fell back slept for it) and off the cell (a
# call that differenced must have left its own snapshot), and this harness
# exists to prove those two can still fail.
#
# Every dawn invocation is wrapped in `timeout`: an assertion about a sleep that
# regressed into a hang has to come back as a red, not as a wedged pipeline.
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH='' cd -- "$HERE/../../.." && pwd)"
BACKEND="$ROOT/backend-dawn"
MUTATOR="$HERE/mutate.py"
MATRIX="$HERE/matrix.txt"
DAWN="${DAWN_BIN:-}"

# shellcheck source=backend-dawn/scripts/mutant-shard.sh
source "$BACKEND/scripts/mutant-shard.sh"
shard_parse "$@"
if [[ ${#shard_rest[@]} -ne 0 ]]; then
  printf 'usage: %s [--shard I/N]\n' "$0" >&2
  exit 2
fi
shard_begin cpu-sampling-mutants

# The mutators only write into a directory carrying a .mutant-workdir sentinel,
# and this proves that gate can still go red -- for every harness, not just this
# one. Without it a mutate.py that lost the guard would look exactly like one
# that has it, right up until somebody aimed it at a checkout.
python3 "$BACKEND/scripts/mutant_workdir.py" --self-test

# Generous next to a ~40s suite, but finite: this is the wedge guard.
STEP_TIMEOUT="${STEP_TIMEOUT:-600}"

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-cpu-sampling-mutants.XXXXXX")"
trap 'rm -rf "$work"' EXIT

run_tests() {
  # $1 project dir, $2 log path; returns dawn's status, 124 on wedge
  local status=0
  timeout "$STEP_TIMEOUT" "$DAWN" test "$1" >"$2" 2>&1 || status=$?
  return "$status"
}

reds() {
  # normalise `FAIL  <module> :: <name>` down to `<name>`, sorted
  sed -n 's/^FAIL  //p' "$1" | sed 's/^.* :: //' | LC_ALL=C sort
}

base_log="$work/base.test.log"
base_status=0
run_tests "$BACKEND" "$base_log" || base_status=$?
if [[ $base_status -eq 124 ]]; then
  printf 'FAIL  base suite exceeded %ss -- a timing assertion is hanging\n' \
    "$STEP_TIMEOUT" >&2
  exit 1
fi
if [[ $base_status -ne 0 ]]; then
  cat "$base_log" >&2
  exit 1
fi
printf 'PASS  base CPU sampling assertions\n'

declare -a mutants=()
declare -a owner_sets=()
while IFS='|' read -r mutant owners; do
  [[ -z "$mutant" || "$mutant" == \#* ]] && continue
  if [[ -z "$owners" ]]; then
    printf 'FAIL  matrix entry has no owner: %s\n' "$mutant" >&2
    exit 1
  fi
  mutants+=("$mutant")
  owner_sets+=("$owners")
done <"$MATRIX"

expected_mutants=(
  accept-backwards-counters
  always-sample-a-window
  never-refresh-snapshot
  unbounded-snapshot-age
)
if [[ "${mutants[*]}" != "${expected_mutants[*]}" ]]; then
  printf 'FAIL  matrix mutant order or membership changed\n' >&2
  exit 1
fi

for index in "${!mutants[@]}"; do
  if shard_skips "$index"; then continue; fi
  mutant="${mutants[$index]}"
  shard_record "$mutant"
  owners="${owner_sets[$index]}"
  project="$work/$mutant/backend-dawn"
  mkdir -p "$project"
  cp "$BACKEND/dawn.toml" "$project/dawn.toml"
  cp -R "$BACKEND/src" "$project/src"
  # mutate.py refuses a directory without this; see scripts/mutant_workdir.py
  : >"$project/.mutant-workdir"
  python3 "$MUTATOR" "$mutant" "$project"

  build_log="$work/$mutant.build.log"
  if ! timeout "$STEP_TIMEOUT" "$DAWN" build "$project" -o "$work/$mutant.jar" \
    >"$build_log" 2>&1; then
    printf 'FAIL  %s does not build; a mutant must be live code\n' "$mutant" >&2
    cat "$build_log" >&2
    exit 1
  fi
  printf 'PASS  %s fully builds\n' "$mutant"

  test_log="$work/$mutant.test.log"
  test_status=0
  run_tests "$project" "$test_log" || test_status=$?

  if [[ $test_status -eq 124 ]]; then
    printf 'FAIL  %s wedged the suite past %ss instead of turning red\n' \
      "$mutant" "$STEP_TIMEOUT" >&2
    exit 1
  fi
  if [[ $test_status -eq 0 ]]; then
    printf 'FAIL  %s did not turn its owners red\n' "$mutant" >&2
    exit 1
  fi

  expected="$(printf '%s\n' "$owners" | sed 's/;;/\n/g' | LC_ALL=C sort)"
  actual="$(reds "$test_log")"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL  %s turned the wrong assertions red\n' "$mutant" >&2
    printf '  expected:\n%s\n  actual:\n%s\n' "$expected" "$actual" >&2
    exit 1
  fi
  red_count="$(printf '%s\n' "$actual" | grep -c .)"
  printf 'PASS  %s turns red exactly its %s owner(s)\n' "$mutant" "$red_count"
done

shard_report "${#mutants[@]}"
